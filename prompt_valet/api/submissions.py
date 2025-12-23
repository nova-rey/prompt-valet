"""Job submission helpers for the Prompt Valet API."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import HTTPException

from prompt_valet.api.config import APISettings
from prompt_valet.api.discovery import InboxTarget, list_targets

MAX_JOB_ID_ATTEMPTS = 5
PROMPT_SUFFIX = ".prompt.md"
MD_SUFFIX = ".md"
FRONTMATTER_DELIMITER = "---"


def _iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _collect_targets(settings: APISettings) -> list[InboxTarget]:
    targets = list_targets(settings)
    if not targets:
        raise HTTPException(
            status_code=404,
            detail="No inbox targets discovered; review tree builder configuration.",
        )
    return targets


def resolve_target(settings: APISettings, repo: str, branch: str) -> InboxTarget:
    repo = repo.strip()
    branch = branch.strip()
    if not repo or not branch:
        raise HTTPException(
            status_code=400, detail="Both repo and branch are required."
        )

    targets = _collect_targets(settings)
    matches: list[InboxTarget] = []
    for target in targets:
        if target.branch != branch:
            continue
        if repo == target.full_repo or repo == target.repo:
            matches.append(target)
            continue
        if "/" in repo:
            owner, name = repo.split("/", 1)
            if owner == (target.owner or "") and name == target.repo:
                matches.append(target)

    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"No inbox found for repo={repo!r} branch={branch!r}.",
        )
    if len(matches) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Ambiguous repo specification {repo!r}; "
                "specify owner if multiple targets exist."
            ),
        )

    target = matches[0]
    inbox_path = Path(target.inbox_path)
    if not inbox_path.is_dir():
        raise HTTPException(
            status_code=500,
            detail=f"Inbox directory {inbox_path} is missing.",
        )
    if not os.access(inbox_path, os.W_OK):
        raise HTTPException(
            status_code=500,
            detail=f"Inbox directory {inbox_path} is not writable.",
        )

    return target


def _split_frontmatter(content: str) -> tuple[str | None, str]:
    if not content.startswith(FRONTMATTER_DELIMITER):
        return None, content

    lines = content.splitlines(keepends=True)
    if not lines:
        return None, content
    if lines[0].strip() != FRONTMATTER_DELIMITER:
        return None, content

    for index in range(1, len(lines)):
        if lines[index].strip() == FRONTMATTER_DELIMITER:
            frontmatter = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return frontmatter, body

    raise HTTPException(
        status_code=400,
        detail="Frontmatter opening marker found but closing '---' missing.",
    )


def _ensure_markdown_frontmatter(
    content: str,
    job_id: str,
    repo: str,
    branch: str,
    created_at: str,
) -> str:
    frontmatter, body = _split_frontmatter(content)
    mapping: dict[str, object]
    if frontmatter is None or not frontmatter.strip():
        mapping = {}
    else:
        try:
            loaded = yaml.safe_load(frontmatter)
        except yaml.YAMLError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid frontmatter: {exc}",
            )
        if loaded is None:
            mapping = {}
        elif not isinstance(loaded, dict):
            raise HTTPException(
                status_code=400,
                detail="Frontmatter must be a YAML mapping.",
            )
        else:
            mapping = dict(loaded)

    pv_block = mapping.get("pv") or {}
    if not isinstance(pv_block, dict):
        pv_block = {}
    pv_block.update(
        {
            "job_id": job_id,
            "repo": repo,
            "branch": branch,
            "created_at": created_at,
            "source": "api",
        }
    )
    mapping["pv"] = pv_block
    dumped = yaml.safe_dump(mapping, sort_keys=False).rstrip()
    separator = "\n" if body and not body.startswith(("\n", "\r")) else ""
    suffix = separator + body if body else "\n"
    return f"{FRONTMATTER_DELIMITER}\n{dumped}\n{FRONTMATTER_DELIMITER}{suffix}"


def _normalize_filename(base_name: str) -> str:
    name = base_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Filename cannot be empty.")
    if Path(name).name != name:
        raise HTTPException(
            status_code=400,
            detail="Filename must not contain directory segments.",
        )
    lower = name.lower()
    if not lower.endswith(MD_SUFFIX):
        raise HTTPException(
            status_code=400,
            detail="Filename must end with '.md'.",
        )
    if not lower.endswith(PROMPT_SUFFIX):
        prefix = name[: -len(MD_SUFFIX)]
        name = f"{prefix}{PROMPT_SUFFIX}"
    return name


def _inject_job_id(name: str, job_id: str) -> str:
    if job_id in name:
        return name
    prefix = name[: -len(PROMPT_SUFFIX)]
    return f"{prefix}-{job_id}{PROMPT_SUFFIX}"


def _build_unique_path(inbox_dir: Path, base_name: str | None) -> tuple[Path, str]:
    normalized_base = _normalize_filename(base_name) if base_name else None
    attempts = 0
    while attempts < MAX_JOB_ID_ATTEMPTS:
        job_id = uuid.uuid4().hex
        if normalized_base:
            candidate_name = _inject_job_id(normalized_base, job_id)
        else:
            candidate_name = _default_name(job_id)
        candidate_path = inbox_dir / candidate_name
        if candidate_path.exists():
            attempts += 1
            continue
        return candidate_path, job_id
    raise HTTPException(
        status_code=409,
        detail="Unable to materialize unique job filename; try again.",
    )


def _default_name(job_id: str) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{job_id}{PROMPT_SUFFIX}"


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write inbox file {path}: {exc}",
        )


def submit_job(
    settings: APISettings,
    repo: str,
    branch: str,
    markdown_text: str,
    filename: str | None = None,
) -> dict[str, str]:
    target = resolve_target(settings, repo, branch)
    inbox_dir = Path(target.inbox_path)
    content = markdown_text
    if not isinstance(content, str):
        raise HTTPException(
            status_code=400,
            detail="markdown_text must be a string.",
        )

    path, job_id = _build_unique_path(inbox_dir, filename)
    created_at = _iso_now()
    frontmatter_text = _ensure_markdown_frontmatter(
        content, job_id, target.full_repo, target.branch, created_at
    )
    _atomic_write(path, frontmatter_text)
    return {
        "job_id": job_id,
        "inbox_path": str(path.resolve()),
        "created_at": created_at,
    }


def submit_job_from_upload(
    settings: APISettings,
    repo: str,
    branch: str,
    filename: str,
    content: str,
) -> dict[str, str]:
    return submit_job(settings, repo, branch, content, filename=filename)

"""TreeBuilder discovery helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Set, Tuple

from prompt_valet.api.config import APISettings


@dataclass(frozen=True)
class InboxTarget:
    owner: str | None
    repo: str
    branch: str
    inbox_path: str

    @property
    def full_repo(self) -> str:
        if self.owner:
            return f"{self.owner}/{self.repo}"
        return self.repo

    def to_dict(self) -> dict[str, str | None]:
        data: dict[str, str | None] = {
            "repo": self.repo,
            "branch": self.branch,
            "inbox_path": self.inbox_path,
        }
        if self.owner:
            data["owner"] = self.owner
            data["full_repo"] = self.full_repo
        else:
            data["owner"] = None
        return data


def list_targets(settings: APISettings) -> List[InboxTarget]:
    root = settings.tree_builder_root
    if not root.is_dir():
        return []

    depth = _branch_depth(settings.inbox_mode)
    branches = _collect_branch_dirs(root, depth)

    seen: Set[Tuple[str | None, str, str]] = set()
    targets: List[InboxTarget] = []
    for branch_dir in sorted(branches):
        rel = branch_dir.relative_to(root)
        owner, repo, branch = _resolve_target_parts(rel.parts, settings)
        if repo is None or branch is None:
            continue
        key = (owner, repo, branch)
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            InboxTarget(
                owner=owner, repo=repo, branch=branch, inbox_path=str(branch_dir)
            )
        )

    return sorted(
        targets, key=lambda target: (target.owner or "", target.repo, target.branch)
    )


def _branch_depth(inbox_mode: str) -> int:
    return 3 if inbox_mode == "multi_owner" else 2


def _collect_branch_dirs(root: Path, depth: int) -> Set[Path]:
    markers = _collect_marker_dirs(root)
    if markers:
        return markers

    result: Set[Path] = set()
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        rel = path.relative_to(root)
        if len(rel.parts) == depth:
            result.add(path)
    return result


def _collect_marker_dirs(root: Path) -> Set[Path]:
    return {marker.parent for marker in root.rglob(".pv_inbox") if marker.is_file()}


def _resolve_target_parts(
    parts: Sequence[str], settings: APISettings
) -> Tuple[str | None, str | None, str | None]:
    if settings.inbox_mode == "multi_owner":
        if len(parts) < 3:
            return (None, None, None)
        owner, repo, branch = parts[0], parts[1], parts[2]
        return owner, repo, branch

    if len(parts) < 2:
        return (settings.git_owner, None, None)

    owner = settings.git_owner or None
    repo = parts[0]
    branch = parts[1]
    return owner, repo, branch

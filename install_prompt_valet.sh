#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[install] %s\n' "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

# ---------------------------------------------------------------------------
# Configuration defaults
# ---------------------------------------------------------------------------
PV_GIT_OWNER="${PV_GIT_OWNER:-nova-rey}"
PV_GIT_HOST="${PV_GIT_HOST:-github.com}"
PV_GIT_PROTOCOL="${PV_GIT_PROTOCOL:-https}"
PV_FILE_SERVER_MODE="${PV_FILE_SERVER_MODE:-copyparty}"
PV_FILE_SERVER_PORT="${PV_FILE_SERVER_PORT:-3923}"
PV_BASE_DIR="${PV_BASE_DIR:-/srv/prompt-valet}"
PV_INBOX_DIR="${PV_INBOX_DIR:-${PV_BASE_DIR}/inbox}"
PV_PROCESSED_DIR="${PV_PROCESSED_DIR:-${PV_BASE_DIR}/processed}"
PV_CONFIG_DIR="${PV_CONFIG_DIR:-${PV_BASE_DIR}/config}"
PV_SCRIPTS_DIR="${PV_SCRIPTS_DIR:-${PV_BASE_DIR}/scripts}"
PV_LOGS_DIR="${PV_LOGS_DIR:-${PV_BASE_DIR}/logs}"
PV_REPOS_DIR="${PV_REPOS_DIR:-/srv/repos}"
PV_RUNNER_CMD="${PV_RUNNER_CMD:-codex}"
PV_RUNNER_EXTRA="${PV_RUNNER_EXTRA:-}"
PV_VALIDATE_ONLY="${PV_VALIDATE_ONLY:-0}"
PV_FINISHED_DIR="${PV_FINISHED_DIR:-${PV_BASE_DIR}/finished}"

export PV_GIT_OWNER PV_GIT_HOST PV_GIT_PROTOCOL PV_FILE_SERVER_MODE PV_FILE_SERVER_PORT
export PV_BASE_DIR PV_INBOX_DIR PV_PROCESSED_DIR PV_CONFIG_DIR PV_SCRIPTS_DIR PV_LOGS_DIR
export PV_REPOS_DIR PV_RUNNER_CMD PV_RUNNER_EXTRA PV_VALIDATE_ONLY PV_FINISHED_DIR
export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
case "$PV_VALIDATE_ONLY" in
  0) DRY_RUN=0 ;;
  1) DRY_RUN=1 ;;
  *)
    die "PV_VALIDATE_ONLY must be 0 or 1, got '$PV_VALIDATE_ONLY'"
    ;;
esac

case "$PV_FILE_SERVER_MODE" in
  copyparty|none) ;;
  *)
    die "PV_FILE_SERVER_MODE must be 'copyparty' or 'none', got '$PV_FILE_SERVER_MODE'"
    ;;
esac

case "$PV_GIT_PROTOCOL" in
  https|ssh) ;;
  *)
    die "PV_GIT_PROTOCOL must be 'https' or 'ssh', got '$PV_GIT_PROTOCOL'"
    ;;
esac

if ! [[ "$PV_FILE_SERVER_PORT" =~ ^[0-9]+$ ]]; then
  die "PV_FILE_SERVER_PORT must be an integer, got '$PV_FILE_SERVER_PORT'"
fi

if ((PV_FILE_SERVER_PORT < 1 || PV_FILE_SERVER_PORT > 65535)); then
  die "PV_FILE_SERVER_PORT must be between 1 and 65535"
fi

INSTALLER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_OWNER_DIR="${PV_REPOS_DIR}/${PV_GIT_OWNER}"
REPO_DEST_DIR="${REPO_OWNER_DIR}/prompt-valet"

if [[ "$PV_GIT_PROTOCOL" == "ssh" ]]; then
  REPO_URL="git@${PV_GIT_HOST}:${PV_GIT_OWNER}/prompt-valet.git"
else
  REPO_URL="${PV_GIT_PROTOCOL}://${PV_GIT_HOST}/${PV_GIT_OWNER}/prompt-valet.git"
fi

CONFIG_PATH="$PV_CONFIG_DIR/prompt-valet.yaml"
COPYPARTY_CONFIG_PATH="${PV_BASE_DIR}/copyparty.yaml"

run_cmd() {
  local cmd=("$@")
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: would run: ${cmd[*]}"
    return
  fi

  log "Running: ${cmd[*]}"
  "${cmd[@]}"
}

systemctl_exec() {
  local allow_fail="$1"
  shift

  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: systemctl $*"
    return
  fi

  if [[ "$allow_fail" == "true" ]]; then
    if ! systemctl "$@"; then
      log "systemctl $* failed (ignored)"
    fi
  else
    systemctl "$@"
  fi
}

ensure_directories() {
  local dirs=(
    "$PV_BASE_DIR"
    "$PV_INBOX_DIR"
    "$PV_PROCESSED_DIR"
    "$PV_FINISHED_DIR"
    "$PV_CONFIG_DIR"
    "$PV_SCRIPTS_DIR"
    "$PV_LOGS_DIR"
    "$PV_REPOS_DIR"
    "$REPO_OWNER_DIR"
  )
  for dir in "${dirs[@]}"; do
    if [[ "$DRY_RUN" == "1" ]]; then
      log "DRY RUN: would create directory $dir"
      continue
    fi
    mkdir -p "$dir"
  done
}

install_dependencies() {
  log "Installing system packages"
  run_cmd apt-get update
  run_cmd apt-get install -y git python3 python3-venv systemd curl wget python3-pip python3-yaml

  if [[ "$PV_FILE_SERVER_MODE" == "copyparty" ]]; then
    run_cmd python3 -m pip install --upgrade copyparty
  fi
}

clone_or_update_repo() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: would clone/update Prompt Valet repository from $REPO_URL into $REPO_DEST_DIR"
    return
  fi

  if [[ -d "$REPO_DEST_DIR/.git" ]]; then
    log "Updating existing Prompt Valet repository at $REPO_DEST_DIR"
    git -C "$REPO_DEST_DIR" remote set-url origin "$REPO_URL"
    git -C "$REPO_DEST_DIR" fetch --prune origin
    git -C "$REPO_DEST_DIR" checkout main
    git -C "$REPO_DEST_DIR" reset --hard origin/main
  else
    log "Cloning Prompt Valet repository into $REPO_DEST_DIR"
    run_cmd git -C "$REPO_OWNER_DIR" clone "$REPO_URL" prompt-valet
  fi
}

deploy_scripts() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: would copy watcher scripts into $PV_SCRIPTS_DIR"
    return
  fi

  for script in codex_watcher.py rebuild_inbox_tree.py; do
    local src="$REPO_DEST_DIR/scripts/$script"
    local dest="$PV_SCRIPTS_DIR/$script"

    if [[ ! -f "$src" ]]; then
      die "Missing script $src in cloned repository"
    fi

    cp "$src" "$dest"
    chmod +x "$dest"
  done
}

write_prompt_valet_config() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: would write $CONFIG_PATH"
    return
  fi

  python3 <<PY > "$CONFIG_PATH"
import os
import sys
import yaml

config = {
    "inbox": os.environ["PV_INBOX_DIR"],
    "processed": os.environ["PV_PROCESSED_DIR"],
    "finished": os.environ["PV_FINISHED_DIR"],
    "repos_root": os.environ["PV_REPOS_DIR"],
    "tree_builder": {
        "greedy_inboxes": False,
        "branch_mode": "all",
        "branch_whitelist": [],
        "branch_blacklist": [],
        "branch_name_blacklist": ["HEAD"],
        "placeholder_branches": ["main", "devel", "api", "phase5"],
        "scan_interval_seconds": 60,
        "eager_repos": False,
    },
    "watcher": {
        "auto_clone_missing_repos": True,
        "git_default_owner": os.environ["PV_GIT_OWNER"],
        "git_default_host": os.environ["PV_GIT_HOST"],
        "git_protocol": os.environ["PV_GIT_PROTOCOL"],
        "cleanup_non_git_dirs": True,
        "runner_cmd": os.environ["PV_RUNNER_CMD"],
        "runner_model": "gpt-5.1-codex-mini",
        "runner_sandbox": "danger-full-access",
        "runner_extra": os.environ.get("PV_RUNNER_EXTRA", ""),
    },
}

yaml.safe_dump(config, sys.stdout, sort_keys=False)
PY
}

write_copyparty_config() {
  if [[ "$PV_FILE_SERVER_MODE" == "copyparty" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      log "DRY RUN: would write $COPYPARTY_CONFIG_PATH"
      return
    fi

    cat <<EOF > "$COPYPARTY_CONFIG_PATH"
service:
  name: "Prompt Valet Inbox"
  mode: copyparty
  port: $PV_FILE_SERVER_PORT
  inbox: "$PV_INBOX_DIR"
  processed: "$PV_PROCESSED_DIR"
EOF
    return
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: would remove $COPYPARTY_CONFIG_PATH"
    return
  fi

  rm -f "$COPYPARTY_CONFIG_PATH"
}

install_unit_file() {
  local name="$1"
  local src="$INSTALLER_ROOT/$name"
  local dest="/etc/systemd/system/$name"

  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: would install $name -> $dest"
    return
  fi

  if [[ ! -f "$src" ]]; then
    die "Installer is missing $src"
  fi

  cp "$src" "$dest"
  chmod 644 "$dest"

  if [[ "$name" == "copyparty.service" ]]; then
    sed -i "s/__PV_FILE_SERVER_PORT__/$PV_FILE_SERVER_PORT/g" "$dest"
  fi
}

disable_copyparty_service() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: would disable and stop copyparty.service"
    return
  fi

  if ! systemctl disable --now copyparty.service >/dev/null 2>&1; then
    log "copyparty.service disable/stop reported a non-fatal error (ignored)"
  fi
}

log "Starting Prompt Valet installer"

ensure_directories
install_dependencies
clone_or_update_repo
deploy_scripts
write_prompt_valet_config
write_copyparty_config

install_unit_file prompt-valet-watcher.service
install_unit_file prompt-valet-tree-builder.service
install_unit_file prompt-valet-tree-builder.timer
install_unit_file copyparty.service

systemctl_exec false daemon-reload
systemctl_exec false enable --now prompt-valet-watcher.service
systemctl_exec false enable --now prompt-valet-tree-builder.service
systemctl_exec false enable --now prompt-valet-tree-builder.timer

if [[ "$PV_FILE_SERVER_MODE" == "copyparty" ]]; then
  systemctl_exec false enable --now copyparty.service
else
  disable_copyparty_service
fi

log "Prompt Valet installation finished"

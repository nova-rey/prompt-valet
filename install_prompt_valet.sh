#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PV_GIT_OWNER="${PV_GIT_OWNER:-nova-rey}"
PV_GIT_HOST="${PV_GIT_HOST:-github.com}"
PV_GIT_PROTOCOL="${PV_GIT_PROTOCOL:-https}"

PV_FILE_SERVER_MODE="${PV_FILE_SERVER_MODE:-copyparty}"
PV_FILE_SERVER_PORT="${PV_FILE_SERVER_PORT:-3923}"

PV_INBOX_DIR="${PV_INBOX_DIR:-/srv/prompt-valet/inbox}"
PV_PROCESSED_DIR="${PV_PROCESSED_DIR:-/srv/prompt-valet/processed}"
PV_FINISHED_DIR="${PV_FINISHED_DIR:-/srv/prompt-valet/finished}"
PV_CONFIG_DIR="${PV_CONFIG_DIR:-/srv/prompt-valet/config}"
PV_SCRIPTS_DIR="${PV_SCRIPTS_DIR:-/srv/prompt-valet/scripts}"
PV_LOGS_DIR="${PV_LOGS_DIR:-/srv/prompt-valet/logs}"
PV_BASE_DIR="${PV_BASE_DIR:-/srv/prompt-valet}"
PV_REPOS_DIR="${PV_REPOS_DIR:-/srv/repos}"
CONFIG_PATH="${CONFIG_PATH:-$PV_CONFIG_DIR/prompt-valet.yaml}"
COPYPARTY_CONFIG_PATH="${COPYPARTY_CONFIG_PATH:-$PV_BASE_DIR/copyparty.yaml}"

PV_RUNNER_CMD="${PV_RUNNER_CMD:-codex}"
PV_RUNNER_EXTRA="${PV_RUNNER_EXTRA:-}"
PV_VALIDATE_ONLY="${PV_VALIDATE_ONLY:-0}"

DRY_RUN=0
if [[ "${PV_VALIDATE_ONLY}" != "0" ]]; then
  DRY_RUN=1
fi

log() {
  printf "[prompt-valet-installer] %s\n" "$*"
}

maybe_run() {
  if ((DRY_RUN)); then
    log "dry-run: $*"
    return 0
  fi
  "$@"
}

ensure_directory() {
  local dir="$1"
  if ((DRY_RUN)); then
    log "dry-run: mkdir -p $dir"
    return 0
  fi
  mkdir -p "$dir"
}

install_packages() {
  if ((DRY_RUN)); then
    log "dry-run: apt-get update"
  else
    DEBIAN_FRONTEND=noninteractive apt-get update
  fi

  local packages=(git python3 python3-venv python3-pip python3-yaml systemd curl)
  if ((DRY_RUN)); then
    log "dry-run: apt-get install -y ${packages[*]}"
  else
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
  fi

  if [[ "$PV_FILE_SERVER_MODE" == "copyparty" ]]; then
    if ((DRY_RUN)); then
      log "dry-run: python3 -m pip install --upgrade copyparty"
    else
      python3 -m pip install --upgrade copyparty
    fi
  fi
}

validate_file_server_mode() {
  if [[ "$PV_FILE_SERVER_MODE" != "copyparty" && "$PV_FILE_SERVER_MODE" != "none" ]]; then
    log "error: PV_FILE_SERVER_MODE must be 'copyparty' or 'none'"
    exit 1
  fi
}

clone_or_update_repo() {
  local repo_name="prompt-valet"
  local repo_dir="$PV_REPOS_DIR/$repo_name"
  local repo_url="$PV_GIT_PROTOCOL://$PV_GIT_HOST/$PV_GIT_OWNER/$repo_name.git"

  if [[ -d "$repo_dir/.git" ]]; then
    log "Updating prompt-valet repo at $repo_dir"
    maybe_run git -C "$repo_dir" fetch --all --prune
    if ((DRY_RUN)); then
      log "dry-run: git -C $repo_dir reset --hard origin/HEAD"
    else
      git -C "$repo_dir" reset --hard origin/HEAD >/dev/null
    fi
  else
    log "Cloning prompt-valet repo into $repo_dir"
    maybe_run git clone "$repo_url" "$repo_dir"
  fi
}

deploy_scripts() {
  local source_dir="$SCRIPT_ROOT/scripts"
  local scripts=(codex_watcher.py rebuild_inbox_tree.py)

  for script_name in "${scripts[@]}"; do
    local source_file="$source_dir/$script_name"
    local target_file="$PV_SCRIPTS_DIR/$script_name"
    if ((DRY_RUN)); then
      log "dry-run: copy $source_file -> $target_file"
      continue
    fi
    cp "$source_file" "$target_file"
    chmod 755 "$target_file"
  done
}

write_prompt_valet_config() {
  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY RUN: would write $CONFIG_PATH"
    return
  fi

  log "Writing prompt-valet config to $CONFIG_PATH"
  python3 <<'PY' > "$CONFIG_PATH"
import os
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required to write prompt-valet.yaml\n")
    sys.exit(1)

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
    ensure_directory "$PV_LOGS_DIR/copyparty"
    if [[ "$DRY_RUN" == "1" ]]; then
      log "DRY RUN: would write $COPYPARTY_CONFIG_PATH"
      return
    fi

    log "Writing Copyparty config to $COPYPARTY_CONFIG_PATH"
    cat > "$COPYPARTY_CONFIG_PATH" <<EOF
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

  log "Removing Copyparty config $COPYPARTY_CONFIG_PATH"
  rm -f "$COPYPARTY_CONFIG_PATH"
}

write_service_units() {
  local watcher_unit="/etc/systemd/system/prompt-valet-watcher.service"
  local tree_unit="/etc/systemd/system/prompt-valet-tree-builder.service"
  local timer_unit="/etc/systemd/system/prompt-valet-tree-builder.timer"

  if ((DRY_RUN)); then
    log "dry-run: write $watcher_unit"
  else
    log "Writing watcher unit to $watcher_unit"
    cat <<EOF > "$watcher_unit"
[Unit]
Description=Prompt Valet — Watcher
After=network-online.target

[Service]
ExecStart=/usr/bin/env python3 $PV_SCRIPTS_DIR/codex_watcher.py
WorkingDirectory=$PV_BASE_DIR
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
  fi

  if ((DRY_RUN)); then
    log "dry-run: write $tree_unit"
  else
    log "Writing tree builder unit to $tree_unit"
    cat <<EOF > "$tree_unit"
[Unit]
Description=Prompt Valet — Inbox Tree Builder

[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 $PV_SCRIPTS_DIR/rebuild_inbox_tree.py

[Install]
WantedBy=multi-user.target
EOF
  fi

  if ((DRY_RUN)); then
    log "dry-run: write $timer_unit"
  else
    log "Writing tree builder timer unit to $timer_unit"
    cat <<EOF > "$timer_unit"
[Unit]
Description=Prompt Valet — Tree Builder Timer

[Timer]
OnCalendar=*:0/5
Unit=prompt-valet-tree-builder.service

[Install]
WantedBy=timers.target
EOF
  fi

  if [[ "$PV_FILE_SERVER_MODE" == "copyparty" ]]; then
    local copyparty_unit="/etc/systemd/system/copyparty.service"
    if ((DRY_RUN)); then
      log "dry-run: write $copyparty_unit"
    else
      log "Writing Copyparty service unit to $copyparty_unit"
      cat <<EOF > "$copyparty_unit"
[Unit]
Description=Copyparty — Prompt Valet Inbox
After=network-online.target

[Service]
ExecStart=/usr/bin/env copyparty serve --root $PV_INBOX_DIR --port $PV_FILE_SERVER_PORT --log-dir $PV_LOGS_DIR/copyparty --readonly
WorkingDirectory=$PV_BASE_DIR
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    fi
  else
    local copyparty_unit="/etc/systemd/system/copyparty.service"
    if ((DRY_RUN)); then
      log "dry-run: remove $copyparty_unit"
    else
      log "Removing Copyparty service unit $copyparty_unit"
      rm -f "$copyparty_unit"
    fi
  fi
}

reload_and_enable_units() {
  if ((DRY_RUN)); then
    log "dry-run: systemctl daemon-reload"
  else
    log "Running systemctl daemon-reload"
    systemctl daemon-reload
  fi

  if ((DRY_RUN)); then
    log "dry-run: systemctl enable --now prompt-valet-watcher.service"
    log "dry-run: systemctl enable prompt-valet-tree-builder.service"
    log "dry-run: systemctl enable --now prompt-valet-tree-builder.timer"
    log "dry-run: systemctl start prompt-valet-tree-builder.service"
  else
    log "Enabling watcher service"
    systemctl enable --now prompt-valet-watcher.service
    log "Enabling tree builder service"
    systemctl enable prompt-valet-tree-builder.service
    log "Enabling tree builder timer"
    systemctl enable --now prompt-valet-tree-builder.timer
    log "Starting tree builder service"
    systemctl start prompt-valet-tree-builder.service
  fi

  if [[ "$PV_FILE_SERVER_MODE" == "copyparty" ]]; then
    if ((DRY_RUN)); then
      log "dry-run: systemctl enable --now copyparty.service"
    else
      log "Enabling Copyparty service"
      systemctl enable --now copyparty.service
    fi
  else
    if ((DRY_RUN)); then
      log "dry-run: systemctl stop --now copyparty.service"
      log "dry-run: systemctl disable copyparty.service"
      log "dry-run: systemctl mask copyparty.service"
    else
      log "Disabling Copyparty service"
      systemctl stop --now copyparty.service >/dev/null 2>&1 || true
      systemctl disable copyparty.service >/dev/null 2>&1 || true
      systemctl mask copyparty.service >/dev/null 2>&1 || true
    fi
  fi
}

main() {
  log "Starting Prompt Valet installer"
  validate_file_server_mode

  local dirs=(
    "$PV_BASE_DIR"
    "$PV_INBOX_DIR"
    "$PV_PROCESSED_DIR"
    "$PV_FINISHED_DIR"
    "$PV_CONFIG_DIR"
    "$PV_SCRIPTS_DIR"
    "$PV_LOGS_DIR"
    "$PV_REPOS_DIR"
  )

  log "Creating directory hierarchy"
  for dir in "${dirs[@]}"; do
    ensure_directory "$dir"
  done

  install_packages
  clone_or_update_repo
  deploy_scripts
  write_prompt_valet_config
  write_copyparty_config
  write_service_units
  reload_and_enable_units

  log "Prompt Valet installer completed"
}

main "$@"

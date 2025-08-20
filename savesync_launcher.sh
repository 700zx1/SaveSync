# ...existing code...
#!/usr/bin/env bash
# SaveSync Launcher - MOTD
# ----------------------------------------
# SaveSync launcher helps you start popular Linux game launchers (Heroic, Lutris,
# Bottles, Faugus) or any executable, and it will start SaveSync automatically so
# your saves are backed up while you play. The script also requests SaveSync to
# close and run its autosync when the launched program exits.
#
# Quick examples:
#   ./savesync_launcher.sh            # interactive menu
#   ./savesync_launcher.sh --pid 1234 # watch an existing PID
#   ./savesync_launcher.sh /path/to/game --arg1
#
# Tips:
# - For graceful SaveSync closing, install wmctrl or xdotool.
# - To autostart SaveSync at login, run the included installer (if present).
# - Use SAVESYNC_GAME environment variable to pre-select a game from SaveSync
#   config before launching a game.
# ----------------------------------------
# Place in the repo and make executable:
#   chmod +x savesync_launcher.sh
# Usage:
#   ./savesync_launcher.sh
#   ./savesync_launcher.sh --pid 1234
#   ./savesync_launcher.sh /path/to/game [args...]

# Print a compact MOTD when running interactively with no arguments
show_motd() {
  cat <<'MOTD'
========================================
 SaveSync Launcher
 Auto-starts SaveSync and runs autosync when your game exits.

Usage:
  ./savesync_launcher.sh          (interactive)
  ./savesync_launcher.sh --pid N  (monitor PID N)
  ./savesync_launcher.sh /path/to/exe [args]

Tips: install wmctrl/xdotool for a graceful SaveSync close.
Set SAVESYNC_GAME to pre-select a game.
========================================
MOTD
}


set -euo pipefail

# Colors
RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[0;33m' CYAN='\033[0;36m' BOLD='\033[1m' NC='\033[0m'

SAVESYNC_TITLE="SaveSync - Game Save Backup Tool"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

print() { echo -e "${CYAN}$1${NC}"; }
error() { echo -e "${RED}$1${NC}" >&2; }
info()  { echo -e "${GREEN}$1${NC}"; }

menu() {
  echo -e "${BOLD}Select a launcher or option:${NC}"
  echo "  1) Heroic"
  echo "  2) Lutris"
  echo "  3) Bottles"
  echo "  4) Faugus"
  echo "  5) Browse for executable"
  echo "  6) Enter custom command"
  echo "  7) Monitor existing PID"
  echo "  8) Create .venv in project (and optionally install requirements)"
  echo "  q) Quit"
  printf "> "
}

# Find a native binary from candidate names (return first found)
find_native() {
  for c in "$@"; do
    if command -v "$c" >/dev/null 2>&1; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

# Search flatpak apps by keyword (return first matching app-id)
find_flatpak() {
  local kw="$1"
  if ! command -v flatpak >/dev/null 2>&1; then
    return 1
  fi
  flatpak list --app --columns=application 2>/dev/null | grep -i "$kw" | head -n1 || return 1
}

request_window_close() {
  if command -v wmctrl >/dev/null 2>&1; then
    wmctrl -c "$SAVESYNC_TITLE" >/dev/null 2>&1 && return 0 || return 1
  fi
  if command -v xdotool >/dev/null 2>&1; then
    wid="$(xdotool search --name --limit 1 "$SAVESYNC_TITLE" 2>/dev/null || true)"
    if [ -n "$wid" ]; then
      xdotool windowclose "$wid" >/dev/null 2>&1 && return 0 || return 1
    fi
  fi
  return 1
}

terminate_savesync() {
  pids="$(pgrep -f "savesync_gui.py" || true)"
  if [ -z "$pids" ]; then
    pids="$(pgrep -f "SaveSync" || true)"
  fi
  if [ -z "$pids" ]; then
    info "No running SaveSync process found."
    return 0
  fi

  info "Requesting SaveSync to close..."
  if request_window_close; then
    info "Graceful close requested. Waiting up to 8s..."
    for i in {1..8}; do
      sleep 1
      if ! pgrep -f "savesync_gui.py" >/dev/null 2>&1 && ! pgrep -f "SaveSync" >/dev/null 2>&1; then
        info "SaveSync exited gracefully."
        return 0
      fi
    done
    error "SaveSync did not exit in time; sending TERM."
  else
    error "Could not request graceful close (wmctrl/xdotool missing or failed). Sending TERM."
  fi

  for pid in $pids; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  return 0
}

# Ensure SaveSync GUI is running. If not, start it in background.
ensure_savesync_running() {
  if pgrep -f "savesync_gui.py" >/dev/null 2>&1 || pgrep -f "SaveSync" >/dev/null 2>&1; then
    return 0
  fi
  info "Starting SaveSync GUI..."
  # Prefer using a local virtualenv if present (.venv or venv), otherwise system python3
  if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/.venv/bin/python"
  elif [ -x "$SCRIPT_DIR/venv/bin/python" ]; then
    PYTHON_EXEC="$SCRIPT_DIR/venv/bin/python"
  else
    PYTHON_EXEC="$(command -v python3 || command -v python || true)"
  fi
  if [ -z "$PYTHON_EXEC" ]; then
    error "No Python interpreter found to run SaveSync. Ensure python3 is installed or create a .venv with the app dependencies."
    return 1
  fi
  # Preserve SAVESYNC_GAME if caller set it. Run detached in background using chosen python.
  ( env SAVESYNC_GAME="${SAVESYNC_GAME:-}" "$PYTHON_EXEC" "$SCRIPT_DIR/savesync_gui.py" >/dev/null 2>&1 & )
  # give GUI a moment to initialize and create window (for wmctrl/window close later)
  sleep 0.8
}

# Launch a shell command string (for custom commands)
launch_and_wait_cmd() {
  local cmd="$1"
  info "Launching (shell): $cmd"
  bash -c -- "$cmd" &
  local pid=$!
  info "Launched process PID: $pid"
  wait "$pid" 2>/dev/null || true
  info "Process $pid exited."
  terminate_savesync
}

# Launch an executable with optional args (array-safe)
launch_and_wait_exec() {
  info "Launching executable: $*"
  "$@" &
  local pid=$!
  info "Launched process PID: $pid"
  wait "$pid" 2>/dev/null || true
  info "Process $pid exited."
  terminate_savesync
}

monitor_existing_pid() {
  read -rp "Enter PID to monitor: " pid
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    error "PID $pid not found."
    return 1
  fi
  info "Monitoring PID $pid. Waiting for it to exit..."
  while kill -0 "$pid" >/dev/null 2>&1; do sleep 1; done
  info "Monitored PID $pid exited."
  terminate_savesync
}

# Create a Python virtualenv in the project root and optionally install requirements
create_venv() {
  if [ -d "$SCRIPT_DIR/.venv" ]; then
    read -rp ".venv already exists. Overwrite? [y/N]: " ans
    case "${ans,,}" in
      y|yes) rm -rf "$SCRIPT_DIR/.venv" ;;
      *) info "Aborting venv creation."; return 0 ;;
    esac
  fi

  # Choose python executable
  PYEXEC=$(command -v python3 || command -v python || true)
  if [ -z "$PYEXEC" ]; then
    error "No system python found to create virtualenv. Install python3 first."
    return 1
  fi

  info "Creating virtualenv at $SCRIPT_DIR/.venv using $PYEXEC..."
  "$PYEXEC" -m venv "$SCRIPT_DIR/.venv" || { error "Failed to create venv."; return 1; }
  info "Virtualenv created."

  # Optionally install requirements
  if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    read -rp "Install requirements.txt into .venv now? [Y/n]: " resp
    resp=${resp:-Y}
    if [[ "${resp,,}" =~ ^(y|yes)$ ]]; then
      info "Installing requirements into .venv..."
      "$SCRIPT_DIR/.venv/bin/python" -m pip install --upgrade pip
      "$SCRIPT_DIR/.venv/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt" || {
        error "pip install failed. You can try to run: $SCRIPT_DIR/.venv/bin/python -m pip install -r requirements.txt"
        return 1
      }
      info "Requirements installed."
    else
      info "Skipping requirements installation."
    fi
  else
    info "No requirements.txt found in project root; virtualenv is ready."
  fi
}

# Robust arg handling: use ${1:-} so set -u doesn't fail when no args provided
first="${1:-}"
if [ "$first" = "--pid" ]; then
  if [ -z "${2:-}" ]; then
    error "Missing PID after --pid"
    exit 2
  fi
  GAME_PID="$2"
  if ! kill -0 "$GAME_PID" >/dev/null 2>&1; then
    error "PID $GAME_PID not found"
    exit 2
  fi
  info "Monitoring existing PID $GAME_PID..."
  ensure_savesync_running
  while kill -0 "$GAME_PID" >/dev/null 2>&1; do sleep 1; done
  info "Monitored PID $GAME_PID exited."
  terminate_savesync
  exit 0
fi

# If user passed a command to launch directly: treat all args as executable+args
if [ "$#" -gt 0 ]; then
  # use array execution to preserve arguments/spaces
  ensure_savesync_running
  launch_and_wait_exec "$@"
  exit 0
fi

# If running interactively, show MOTD then present menu
if [ -t 1 ]; then
  show_motd
fi

# Interactive menu
while true; do
  menu
  read -r choice
  case "$choice" in
  1)
      native="$(find_native heroic heroic-qt heroiclauncher || true)"
      if [ -n "$native" ]; then
    ensure_savesync_running
    launch_and_wait_exec "$native"
        break
      fi
      fk=$(find_flatpak heroic || true)
      if [ -n "$fk" ]; then
    ensure_savesync_running
    launch_and_wait_cmd "flatpak run $fk"
        break
      fi
      echo "No Heroic binary/flatpak found."
      ;;
  2)
      native="$(find_native lutris || true)"
      if [ -n "$native" ]; then
    ensure_savesync_running
    launch_and_wait_exec "$native"
        break
      fi
      fk=$(find_flatpak lutris || true)
      if [ -n "$fk" ]; then
    ensure_savesync_running
    launch_and_wait_cmd "flatpak run $fk"
        break
      fi
      echo "No Lutris binary/flatpak found."
      ;;
  3)
      native="$(find_native bottles bottles-gtk bottles-cli || true)"
      if [ -n "$native" ]; then
    ensure_savesync_running
    launch_and_wait_exec "$native"
        break
      fi
      fk=$(find_flatpak bottles || true)
      if [ -n "$fk" ]; then
    ensure_savesync_running
    launch_and_wait_cmd "flatpak run $fk"
        break
      fi
      echo "No Bottles binary/flatpak found."
      ;;
  4)
      native="$(find_native faugus faugus-launcher || true)"
      if [ -n "$native" ]; then
    ensure_savesync_running
    launch_and_wait_exec "$native"
        break
      fi
      fk=$(find_flatpak faugus || true)
      if [ -n "$fk" ]; then
    ensure_savesync_running
    launch_and_wait_cmd "flatpak run $fk"
        break
      fi
      echo "No Faugus binary/flatpak found."
      ;;
  5)
      read -ep "Enter full path to executable: " exe
      if [ -z "$exe" ]; then error "No path entered."; continue; fi
      if [ ! -x "$exe" ]; then error "File is not executable or not found."; continue; fi
      # allow optional args after path
      read -rp "Optional args (leave empty for none): " extra
      if [ -z "$extra" ]; then
    ensure_savesync_running
    launch_and_wait_exec "$exe"
      else
        # split extra into words via bash -c -- "set -- $extra; exec \"$exe\" \"$@\"" is complex;
        # simplest: run through shell if extra supplied
    ensure_savesync_running
    launch_and_wait_cmd "\"$exe\" $extra"
      fi
      break
      ;;
    6)
      read -rp "Enter full command line to run: " cmdline
      if [ -z "$cmdline" ]; then error "No command entered."; continue; fi
      ensure_savesync_running
      launch_and_wait_cmd "$cmdline"
      break
      ;;
    7)
      ensure_savesync_running
      monitor_existing_pid
      break
      ;;
    8)
      create_venv
      break
      ;;
    q|Q)
      info "Aborting."
      exit 0
      ;;
    *)
      echo "Invalid choice."
      ;;
  esac

  # loop back for another choice
done

exit 0
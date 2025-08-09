#!/bin/bash

SAVESYNC_DIR="$HOME/Applications/SaveSync"
SAVESYNC_SCRIPT="$SAVESYNC_DIR/savesync_gui.py"
VENV_PY="$SAVESYNC_DIR/.venv/bin/python"
PY_BIN="${VENV_PY:-python3}"
[ -x "$VENV_PY" ] || PY_BIN="python3"

# Start SaveSync GUI in background
"$PY_BIN" "$SAVESYNC_SCRIPT" &
SS_PID=$!

# If xdotool is present, wait briefly for the SaveSync window
if command -v xdotool >/dev/null 2>&1; then
  for i in {1..50}; do
    WIN_ID=$(xdotool search --name "SaveSync - Game Save Backup Tool" 2>/dev/null | head -n1 || true)
    [ -n "$WIN_ID" ] && break
    sleep 0.2
  done
fi

# Launch Faugus; pass through any args
"faugus-launcher" "$@"
FAUGUS_STATUS=$?

# Close SaveSync gracefully via WM_DELETE if possible
if command -v xdotool >/dev/null 2>&1; then
  # Re-locate window in case it wasn't found earlier
  if [ -z "${WIN_ID:-}" ]; then
    WIN_ID=$(xdotool search --name "SaveSync - Game Save Backup Tool" 2>/dev/null | head -n1 || true)
  fi
  if [ -n "$WIN_ID" ]; then
    xdotool windowclose "$WIN_ID"
  fi
fi

# Fallback: TERM the process if still running
if kill -0 "$SS_PID" 2>/dev/null; then
  kill -TERM "$SS_PID"
  # Wait up to ~10s for clean exit
  for i in {1..100}; do
    kill -0 "$SS_PID" 2>/dev/null || break
    sleep 0.1
  done
fi

exit $FAUGUS_STATUS

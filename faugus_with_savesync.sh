#!/bin/bash

# Path to Faugus Launcher executable
FAUGUS_LAUNCHER="/usr/bin/FaugusLauncher"  # Adjust if different

# Path to SaveSync GUI script
SAVESYNC_SCRIPT="$HOME/Applications/savesync/savesync_gui.py"

# Run Faugus Launcher
"$FAUGUS_LAUNCHER" "$@"
faugus_status=$?

# After Faugus exits, run SaveSync
echo "Faugus exited with code $faugus_status. Launching SaveSync..."
python3 "$SAVESYNC_SCRIPT"

exit $faugus_status

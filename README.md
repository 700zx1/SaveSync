
# SaveSync

SaveSync is a lightweight GUI-based backup manager for game save files. It supports both local and MEGA cloud backups, automatic differential backups, compression, and backup rotation.

---

## 🛠 Features

- GUI interface using Tkinter
- Per-game configuration with toggle for:
  - Local backup (on/off)
  - Cloud backup to MEGA (on/off)
- Auto-backup when game saves change
- Log file: `./.gamesaves/savesync.log`
- Config: `./.gamesaves/gamesaves.json`

---

## 🚀 Requirements

- Python 3.7+
- Dependencies:
  ```bash
  pip install mega.py
  ```

---

## 🗂 Directory Structure

### Local backups:
```
./.gamesaves/backup/<GameName>/<timestamp>/
```

### Cloud backups:
```
MEGA:/SaveSync/<GameName>/<timestamp>/
```

---

## 📦 Config Format (gamesaves.json)

```json
{
  "Skyrim": {
    "save_path": "~/SkyrimSaves",
    "local_backup": false,
    "cloud_backup": true
  },
  "Red Dead 2": {
    "save_path": "~/.wine/drive_c/users/you/Documents/RDR2/Profiles",
    "local_backup": false,
    "cloud_backup": true
  }
}
```

---

## ✅ How to Use

1. Set up your `gamesaves.json` and `mega_credentials.json`
2. Launch the app:
   ```bash
   python3 savesync_gui.py
   ```
3. Select a game, toggle backup modes, and click Backup/Restore.

---

## 🔐 MEGA Credentials

Create this file:

`~/.gamesaves/mega_credentials.json`

```json
{
  "email": "your-mega-email@example.com",
  "password": "your-password"
}
```

---

## 🧹 Cleanup

- Local and MEGA backups are rotated to the latest 3 per game.
- Old `.zip` files are deleted automatically from MEGA.

---

## 📋 License

MIT License

---

## Install binary (Linux)

When prebuilt native binaries are published in the GitHub Releases for this
project, you can install the Linux binary with the included installer script.
Run this command to download and execute the installer directly from the
repository:

```bash
curl -fsSL https://raw.githubusercontent.com/700zx1/SaveSync/main/install_savesync.sh | sh
```

To install a specific release tag (for example `v1.2.3`) set the VERSION
environment variable:

```bash
VERSION=v1.2.3 sh install_savesync.sh
```

If you don't have root privileges the script will install to `$HOME/.local/bin`.
For fish users make sure `$HOME/.local/bin` is in your PATH; add this to
`~/.config/fish/config.fish` if needed:

```fish
set -U fish_user_paths $HOME/.local/bin $fish_user_paths
```


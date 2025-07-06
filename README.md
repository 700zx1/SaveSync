
# SaveSync

SaveSync is a lightweight GUI-based backup manager for game save files. It supports both local and MEGA cloud backups, automatic differential backups, compression, and backup rotation.

---

## 🛠 Features

- GUI interface using Tkinter
- Per-game configuration with toggle for:
  - Local backup (on/off)
  - Cloud backup to MEGA (on/off)
- Differential backups (only changed files are zipped and uploaded)
- Auto-backup when game saves change
- Backup rotation:
  - Keeps only the 3 most recent local and cloud backups
- Log file: `~/.gamesaves/savesync.log`
- Config: `~/.gamesaves/gamesaves.json`

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
~/.gamesaves/backup/<GameName>/<timestamp>/
```

### Cloud backups:
```
MEGA:/SaveSync/<GameName>/<timestamp>.zip
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

## 🧠 How Differential Backup Works

Only files that differ (based on SHA256 hash) from the previous backup are included in the `.zip`. If no previous backup is found, all files are included.

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


import os
import json
import shutil
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, simpledialog
from mega import Mega

CONFIG_FILE = os.path.expanduser("~/.gamesaves/gamesaves.json")
BACKUP_ROOT = os.path.expanduser("~/.gamesaves/backup/")
LOG_FILE = os.path.expanduser("~/.gamesaves/savesync.log")

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def folder_differs(folder1, folder2):
    for root, _, files in os.walk(folder1):
        rel_root = os.path.relpath(root, folder1)
        for file in files:
            f1 = os.path.join(root, file)
            f2 = os.path.join(folder2, rel_root, file)
            if not os.path.exists(f2):
                return True
            if os.path.getsize(f1) != os.path.getsize(f2):
                return True
            if int(os.path.getmtime(f1)) != int(os.path.getmtime(f2)):
                return True
    return False

def upload_to_mega(game_name, folder_path, log_callback):
    creds_path = os.path.expanduser("~/.gamesaves/mega_credentials.json")
    if not os.path.exists(creds_path):
        log_callback("[!] MEGA credentials not found.")
        return

    with open(creds_path) as f:
        creds = json.load(f)

    mega = Mega()
    m = mega.login(creds["email"], creds["password"])

    cloud_base = m.find('SaveSync') or m.create_folder('SaveSync')
    game_folder = m.find(f'SaveSync/{game_name}') or m.create_folder(game_name, parent=cloud_base)

    for root, _, files in os.walk(folder_path):
        rel_path = os.path.relpath(root, folder_path)
        cloud_target = game_folder
        if rel_path != ".":
            cloud_target = m.create_folder(rel_path, parent=game_folder)

        for file in files:
            local_file = os.path.join(root, file)
            m.upload(local_file, dest=cloud_target)
            log_callback(f"[↑] Uploaded {file} to MEGA:{game_name}/{rel_path}")

def restore_from_mega(game_name, log_callback):
    creds_path = os.path.expanduser("~/.gamesaves/mega_credentials.json")
    if not os.path.exists(creds_path):
        log_callback("[!] MEGA credentials not found.")
        return

    with open(creds_path) as f:
        creds = json.load(f)

    mega = Mega()
    m = mega.login(creds["email"], creds["password"])

    cloud_base = m.find('SaveSync')
    if not cloud_base:
        log_callback("[!] SaveSync folder not found on MEGA.")
        return

    game_folder = m.find(f'SaveSync/{game_name}')
    if not game_folder:
        log_callback(f"[!] No backups found for {game_name} on MEGA.")
        return

    subfolders = [f for f in m.get_files_in_node(game_folder) if f['t'] == 1]
    subfolders.sort(key=lambda x: x['ts'], reverse=True)
    if not subfolders:
        log_callback(f"[!] No cloud backups available for {game_name}")
        return

    backup_names = [f['a']['n'] for f in subfolders]
    selected = simpledialog.askstring("Restore from MEGA",
        f"Available cloud backups:\n" + "\n".join(backup_names) + "\n\nType backup name:")

    if not selected or selected not in backup_names:
        log_callback(f"[!] Invalid or cancelled cloud backup selection.")
        return

    selected_node = next(f for f in subfolders if f['a']['n'] == selected)

    config = load_config()
    restore_to = os.path.expanduser(config[game_name]['save_path'])
    if os.path.exists(restore_to):
        shutil.rmtree(restore_to)

    os.makedirs(restore_to, exist_ok=True)
    temp_dir = os.path.join("/tmp", f"{game_name}_{selected}")
    os.makedirs(temp_dir, exist_ok=True)

    files = [f for f in m.get_files_in_node(selected_node) if f['t'] == 0]
    for fobj in files:
        m.download(fobj, dest_path=temp_dir)
        shutil.move(os.path.join(temp_dir, fobj['a']['n']), os.path.join(restore_to, fobj['a']['n']))
        log_callback(f"[↓] Restored {fobj['a']['n']} to {restore_to}")

    shutil.rmtree(temp_dir)
    log_callback(f"[✓] Cloud restore complete to {restore_to}")

def backup_game(game_name, log_callback):
    config = load_config()
    game_info = config.get(game_name)
    if not game_info:
        log_callback(f"[!] Game '{game_name}' not in config.")
        return

    src = os.path.expanduser(game_info['save_path'])
    if not os.path.exists(src):
        log_callback(f"[!] Save path not found: {src}")
        return

    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    dest = os.path.join(BACKUP_ROOT, game_name, timestamp)
    shutil.copytree(src, dest)
    log_callback(f"[✓] Backed up to {dest}")
    upload_to_mega(game_name, dest, log_callback)

def restore_game(game_name, log_callback):
    backup_path = os.path.join(BACKUP_ROOT, game_name)
    if not os.path.exists(backup_path):
        log_callback(f"[!] No backups found for {game_name}")
        return

    backups = sorted(os.listdir(backup_path), reverse=True)
    if not backups:
        log_callback(f"[!] No backups available.")
        return

    selected = simpledialog.askstring("Choose Backup",
        f"Available backups:\n" + "\n".join(backups) + "\n\nType backup name:")

    if not selected or selected in ("", None) or selected not in backups:
        log_callback(f"[!] Invalid or cancelled backup selection.")
        return

    config = load_config()
    restore_to = os.path.expanduser(config[game_name]['save_path'])
    full_backup_path = os.path.join(backup_path, selected)

    if os.path.exists(restore_to):
        shutil.rmtree(restore_to)

    shutil.copytree(full_backup_path, restore_to)
    log_callback(f"[✓] Restored to {restore_to}")

class SaveSyncApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SaveSync - Game Save Backup Tool")
        self.geometry("450x230")
        self.config = load_config()
        self.create_widgets()
        self.after(500, self.check_and_auto_backup)

    def create_widgets(self):
        tk.Label(self, text="Select Game:").pack(pady=10)
        self.game_var = tk.StringVar(self)
        self.game_var.set(next(iter(self.config)))

        tk.OptionMenu(self, self.game_var, *self.config.keys()).pack()

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)

        tk.Button(btn_frame, text="Backup", command=self.backup).pack(side="left", padx=10)
        tk.Button(btn_frame, text="Restore", command=self.restore).pack(side="left", padx=10)
        tk.Button(btn_frame, text="Restore from MEGA", command=self.restore_from_cloud).pack(side="left", padx=10)

        self.status = tk.Label(self, text="Ready.", anchor="w", justify="left")
        self.status.pack(fill="x", padx=10, pady=10)

    def log(self, message):
        timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        full_msg = f"{timestamp} {message}"
        self.status.config(text=message)
        print(full_msg)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(full_msg + "\n")

    def backup(self):
        game = self.game_var.get()
        backup_game(game, self.log)

    def restore(self):
        game = self.game_var.get()
        restore_game(game, self.log)

    def restore_from_cloud(self):
        game = self.game_var.get()
        restore_from_mega(game, self.log)

    def check_and_auto_backup(self):
        self.log("Checking for save changes...")
        for game, info in self.config.items():
            save_path = os.path.expanduser(info['save_path'])
            if not os.path.exists(save_path):
                self.log(f"[!] Save path missing for {game}")
                continue

            backup_path = os.path.join(BACKUP_ROOT, game)
            os.makedirs(backup_path, exist_ok=True)
            backups = sorted(os.listdir(backup_path), reverse=True)
            latest_backup = os.path.join(backup_path, backups[0]) if backups else None

            if not latest_backup or folder_differs(save_path, latest_backup):
                self.log(f"[✓] Change detected in {game}, creating backup...")
                backup_game(game, self.log)
            else:
                self.log(f"[=] No changes in {game}")

if __name__ == "__main__":
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump({
                "Skyrim": {"save_path": "~/SkyrimSaves"},
                "Red Dead 2": {"save_path": "~/.wine/drive_c/users/you/Documents/RDR2/Profiles"}
            }, f, indent=4)
        print(f"Created default config at {CONFIG_FILE}")

    app = SaveSyncApp()
    app.mainloop()

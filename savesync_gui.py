import os
import json
import shutil
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, simpledialog
from tkinter import ttk
import threading
from mega import Mega

HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME, ".gamesaves")
CONFIG_FILE = os.path.join(CONFIG_DIR, "gamesaves.json")
BACKUP_ROOT = os.path.join(CONFIG_DIR, "backup")
LOG_FILE = os.path.join(CONFIG_DIR, "savesync.log")
MEGA_CREDS = os.path.join(CONFIG_DIR, "mega_credentials.json")

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

def _get_child_folder_id(m, parent_id, name):
    children = m.get_files_in_node(parent_id)
    for nid, n in children.items():
        if n['t'] == 1 and n['a']['n'] == name:
            return nid
    return None

def _ensure_child_folder(m, parent_id, name):
    cid = _get_child_folder_id(m, parent_id, name)
    if cid:
        return cid
    created = m._mkdir(name, parent_id)
    return created['f'][0]['h']

def _ensure_path(m, parent_id, rel_path):
    current = parent_id
    for seg in rel_path.replace(os.sep, '/').split('/'):
        if not seg or seg == '.':
            continue
        current = _ensure_child_folder(m, current, seg)
    return current

def enforce_mega_retention(m, game_name, keep, log_callback):
    # Ensure SaveSync/game exists; if not, nothing to prune
    root_id = m.get_node_by_type(2)[0]
    savesync_id = _get_child_folder_id(m, root_id, 'SaveSync')
    if not savesync_id:
        return
    game_id = _get_child_folder_id(m, savesync_id, game_name)
    if not game_id:
        return

    nodes = m.get_files_in_node(game_id)
    ts_folders = [(nid, n) for nid, n in nodes.items() if n['t'] == 1]
    # Sort newest first by name (YYYY-MM-DD_HH-MM-SS sorts lexicographically)
    ts_folders.sort(key=lambda x: x[1]['a']['n'], reverse=True)

    for nid, n in ts_folders[keep:]:
        m.destroy(nid)
        if log_callback:
            log_callback(f"[x] Pruned old cloud backup: {n['a']['n']}")

def upload_to_mega(game_name, folder_path, log_callback):
    creds_path = MEGA_CREDS
    if not os.path.exists(creds_path):
        log_callback("[!] MEGA credentials not found.")
        return

    try:
        with open(creds_path) as f:
            creds = json.load(f)

        mega = Mega()
        m = mega.login(creds["email"], creds["password"])

        # Ensure SaveSync/game/timestamp hierarchy
        root_id = m.get_node_by_type(2)[0]  # Cloud Drive
        savesync_id = _ensure_child_folder(m, root_id, 'SaveSync')
        game_folder_id = _ensure_child_folder(m, savesync_id, game_name)
        ts_name = os.path.basename(os.path.normpath(folder_path))
        ts_path = f"SaveSync/{game_name}/{ts_name}"
        ts_id = m.find_path_descriptor(ts_path)
        if not ts_id:
            m.create_folder(ts_name, dest=game_folder_id)
            ts_id = m.find_path_descriptor(ts_path)

        for root, _, files in os.walk(folder_path):
            rel_path = os.path.relpath(root, folder_path)
            if rel_path == ".":
                target_id = ts_id
                display_path = f"{game_name}/{ts_name}"
            else:
                target_id = _ensure_path(m, ts_id, rel_path)
                display_path = f"{game_name}/{ts_name}/{rel_path}"
            for file in files:
                local_file = os.path.join(root, file)
                m.upload(local_file, dest=target_id)
                log_callback(f"[↑] Uploaded {file} to MEGA:{display_path}")

        log_callback(f"[✓] Uploaded {game_name} {ts_name} to MEGA.")

        # Keep only latest 3 timestamped backups
        enforce_mega_retention(m, game_name, keep=3, log_callback=log_callback)

    except Exception as e:
        log_callback(f"[!] MEGA upload failed: {e}")

def restore_from_mega(game_name, log_callback):
    creds_path = MEGA_CREDS
    if not os.path.exists(creds_path):
        log_callback("[!] MEGA credentials not found.")
        return

    try:
        with open(creds_path) as f:
            creds = json.load(f)

        mega = Mega()
        m = mega.login(creds["email"], creds["password"])

        cloud_base_id = m.find_path_descriptor('SaveSync')
        if not cloud_base_id:
            log_callback("[!] SaveSync folder not found on MEGA.")
            return

        game_folder_id = m.find_path_descriptor(f'SaveSync/{game_name}')
        if not game_folder_id:
            log_callback(f"[!] No backups found for {game_name} on MEGA.")
            return

        # Get folders under the game folder
        nodes = m.get_files_in_node(game_folder_id)
        subfolders = [(nid, n) for nid, n in nodes.items() if n['t'] == 1]
        subfolders.sort(key=lambda x: x[1].get('ts', 0), reverse=True)
        if not subfolders:
            log_callback(f"[!] No cloud backups available for {game_name}")
            return

        backup_names = [n['a']['n'] for _, n in subfolders]
        selected = simpledialog.askstring(
            "Restore from MEGA",
            "Available cloud backups:\n" + "\n".join(backup_names) + "\n\nType backup name:"
        )

        if not selected or selected not in backup_names:
            log_callback(f"[!] Invalid or cancelled cloud backup selection.")
            return

        selected_id, selected_node = next((nid, n) for nid, n in subfolders if n['a']['n'] == selected)

        config = load_config()
        restore_to = os.path.expanduser(config[game_name]['save_path'])
        if os.path.exists(restore_to):
            shutil.rmtree(restore_to)
        os.makedirs(restore_to, exist_ok=True)

        temp_dir = os.path.join("/tmp", f"{game_name}_{selected}")
        os.makedirs(temp_dir, exist_ok=True)

        files_map = m.get_files_in_node(selected_id)
        file_items = [(fid, n) for fid, n in files_map.items() if n['t'] == 0]
        for fid, fobj in file_items:
            # download expects a (id, dict) tuple
            m.download((fid, fobj), dest_path=temp_dir)
            src = os.path.join(temp_dir, fobj['a']['n'])
            dst = os.path.join(restore_to, fobj['a']['n'])
            shutil.move(src, dst)
            log_callback(f"[↓] Restored {fobj['a']['n']} to {restore_to}")

        shutil.rmtree(temp_dir)
        log_callback(f"[✓] Cloud restore complete to {restore_to}")
    except Exception as e:
        log_callback(f"[!] MEGA restore failed: {e}")

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
        self.geometry("900x230")
        self.config = load_config()
        self.create_widgets()
        self.after(500, self.check_and_auto_backup)
        self.protocol("WM_DELETE_WINDOW", self.on_exit)

    def on_exit(self):
        self.log(f"[*] Performing auto-sync and exiting SaveSync GUI.")
        self.check_and_auto_backup()
        self.destroy()

    def create_widgets(self):
        tk.Label(self, text="Select Game:").pack(pady=10)
        tk.Label(
            self,
            text="Save sync performed automatically upon open and close of SaveSync.",
            font=("TkDefaultFont", 9, "italic"),
            fg="gray"
        ).pack(pady=(0, 10))

        self.game_var = tk.StringVar(self)
        self.game_var.set(next(iter(self.config)))

        # Save a reference to the OptionMenu widget
        self.option_menu = tk.OptionMenu(self, self.game_var, *self.config.keys())
        self.option_menu.pack()

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)

        # Buttons (keep references)
        self.btn_backup = tk.Button(btn_frame, text="Backup to local and MEGA", command=self.backup)
        self.btn_backup.pack(side="left", padx=10)

        self.btn_restore_local = tk.Button(btn_frame, text="Restore from local", command=self.restore)
        self.btn_restore_local.pack(side="left", padx=10)

        self.btn_restore_cloud = tk.Button(btn_frame, text="Restore from MEGA", command=self.restore_from_cloud)
        self.btn_restore_cloud.pack(side="left", padx=10)

        self.btn_reload = tk.Button(btn_frame, text="Reload Config", command=self.reload_json)
        self.btn_reload.pack(side="left", padx=20)

        self.btn_exit = tk.Button(btn_frame, text="Exit and Sync", command=self.on_exit)
        self.btn_exit.pack(side="left", padx=10)

        self.status = tk.Label(self, text="Ready.", anchor="w", justify="left")
        self.status.pack(fill="x", padx=10, pady=10)

        # Progress bar
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=10, pady=(0, 10))

    def log(self, message):
        def do():
            timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
            full_msg = f"{timestamp} {message}"
            self.status.config(text=message)
            print(full_msg)
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            with open(LOG_FILE, "a") as f:
                f.write(full_msg + "\n")
        if threading.current_thread() is threading.main_thread():
            do()
        else:
            self.after(0, do)
    def reload_json(self):
        try:
            self.config = load_config()
            # Update OptionMenu items
            menu = self.option_menu['menu']
            menu.delete(0, 'end')
            for game in self.config.keys():
                menu.add_command(label=game, command=lambda value=game: self.game_var.set(value))
            self.game_var.set(next(iter(self.config)))
            self.log("[✓] Configuration reloaded successfully.")
        except Exception as e:
            self.log(f"[!] Error reloading config: {e}")
            messagebox.showerror("Error", f"Failed to reload configuration: {e}")
    def backup(self):
        game = self.game_var.get()
        self.run_in_bg(lambda: backup_game(game, self.log))

    def restore(self):
        game = self.game_var.get()
        restore_game(game, self.log)

    def restore_from_cloud(self):
        game = self.game_var.get()
        self.run_in_bg(lambda: restore_from_mega(game, self.log))

    def set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for b in (self.btn_backup, self.btn_restore_local, self.btn_restore_cloud, self.btn_reload, self.btn_exit):
            b.config(state=state)
        if busy:
            self.progress.start(10)  # 10ms step
        else:
            self.progress.stop()

    def run_in_bg(self, fn):
        def worker():
            try:
                fn()
            finally:
                self.after(0, lambda: self.set_busy(False))
        self.set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

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
                self.run_in_bg(lambda: backup_game(game, self.log))
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

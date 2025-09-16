import os
import json
import shutil
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from tkinter import ttk
import threading
from mega import Mega

try:
    import ttkbootstrap as ttkb  # optional modern theme
    _HAVE_TTKB = True
except Exception:
    ttkb = None
    _HAVE_TTKB = False

HOME = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME, ".gamesaves")
CONFIG_FILE = os.path.join(CONFIG_DIR, "gamesaves.json")
BACKUP_ROOT = os.path.join(CONFIG_DIR, "backup")
LOG_FILE = os.path.join(CONFIG_DIR, "savesync.log")
MEGA_CREDS = os.path.join(CONFIG_DIR, "mega_credentials.json")

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=4)

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
        # Apply modern theme if available
        if _HAVE_TTKB:
            self.style = ttkb.Style(theme="darkly")
        else:
            self.style = ttk.Style()
            try:
                self.style.theme_use("clam")
            except Exception:
                pass

        self.title("SaveSync - Game Save Backup Tool")
        self.geometry("1000x600")
        self.minsize(820, 520)

        self.config = load_config()
        self._init_styles()
        self._build_layout()

        self.after(500, self.check_and_auto_backup)
        self.protocol("WM_DELETE_WINDOW", self.on_exit)

    def _init_styles(self):
        # Fallback label styles when ttkbootstrap is not present
        if not _HAVE_TTKB:
            try:
                self.style.configure("Title.TLabel", font=("TkDefaultFont", 14, "bold"))
                self.style.configure("Muted.TLabel", foreground="gray")
            except Exception:
                pass

    def on_exit(self):
        self.log("[*] Performing auto-sync and exiting SaveSync GUI.")
        self.check_and_auto_backup()
        self.destroy()

    def _build_layout(self):
        content = ttk.Frame(self, padding=16)
        content.pack(fill="both", expand=True)

        # Header
        title = ttk.Label(content, text="SaveSync", style="Title.TLabel")
        subtitle = ttk.Label(
            content,
            text="Save sync is performed automatically when SaveSync opens and closes.",
            style="Muted.TLabel"
        )
        title.grid(row=0, column=0, sticky="w")
        subtitle.grid(row=1, column=0, sticky="w", pady=(2, 16))

        # Controls row
        ctrls = ttk.Frame(content)
        ctrls.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ctrls.columnconfigure(3, weight=1)

        ttk.Label(ctrls, text="Select Game:").grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.game_var = tk.StringVar(self)
        # safe default if config is empty
        if self.config:
            self.game_var.set(next(iter(self.config)))
        else:
            self.game_var.set("")
        self.game_select = ttk.Combobox(
            ctrls,
            textvariable=self.game_var,
            values=list(self.config.keys()),
            state="readonly",
            width=28,
        )
        self.game_select.grid(row=0, column=1, sticky="w", padx=(0, 12))

        # Button styles for ttkbootstrap
        primary = "primary.TButton" if _HAVE_TTKB else None
        secondary = "secondary.TButton" if _HAVE_TTKB else None
        info = "info.TButton" if _HAVE_TTKB else None
        warning = "warning.TButton" if _HAVE_TTKB else None
        danger = "danger.TButton" if _HAVE_TTKB else None

        # Buttons arranged in two horizontal rows for better layout
        btn_frame = ttk.Frame(ctrls)
        btn_frame.grid(row=1, column=0, columnspan=8, sticky="w", pady=(8, 0))

        # First row: backup / restore / cloud restore / reload
        self.btn_backup = ttk.Button(
            btn_frame, text="Backup to local and MEGA", command=self.backup, **({"style": primary} if primary else {})
        )
        self.btn_restore_local = ttk.Button(
            btn_frame, text="Restore from local", command=self.restore, **({"style": secondary} if secondary else {})
        )
        self.btn_restore_cloud = ttk.Button(
            btn_frame, text="Restore from MEGA", command=self.restore_from_cloud, **({"style": info} if info else {})
        )
        self.btn_reload = ttk.Button(
            btn_frame, text="Reload Config", command=self.reload_json, **({"style": warning} if warning else {})
        )

        self.btn_backup.grid(row=0, column=0, padx=(0, 8), pady=(0, 6))
        self.btn_restore_local.grid(row=0, column=1, padx=(0, 8), pady=(0, 6))
        self.btn_restore_cloud.grid(row=0, column=2, padx=(0, 8), pady=(0, 6))
        self.btn_reload.grid(row=0, column=3, padx=(0, 8), pady=(0, 6))

        # Second row: add / remove / exit
        self.btn_add = ttk.Button(
            btn_frame, text="Add Game", command=self.add_game, **({"style": primary} if primary else {})
        )
        self.btn_remove = ttk.Button(
            btn_frame, text="Remove Game", command=self.remove_game, **({"style": danger} if danger else {})
        )
        self.btn_exit = ttk.Button(
            btn_frame, text="Exit and Sync", command=self.on_exit, **({"style": danger} if danger else {})
        )
        self.btn_list = ttk.Button(
            btn_frame,
            text='List Backups',
            command=self.list_backups,
            **({"style": info} if info else {})
        )
        self.btn_add.grid(row=1, column=0, padx=(0, 8))
        self.btn_remove.grid(row=1, column=1, padx=(0, 8))
        self.btn_exit.grid(row=1, column=2, padx=(0, 8))
        self.btn_list.grid(row=1, column=3, padx=(0, 8))

        # Activity log panel
        log_frame = ttk.LabelFrame(content, text="Activity")
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(6, 10))
        content.rowconfigure(3, weight=1)
        content.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame, height=14, wrap="word", state="disabled", borderwidth=0, highlightthickness=0
        )
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        log_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        # Status bar
        status_bar = ttk.Frame(self, padding=(16, 0, 16, 16))
        status_bar.pack(fill="x", side="bottom")

        self.status = ttk.Label(status_bar, text="Ready.", anchor="w")
        self.status.pack(fill="x", side="left", expand=True)

        self.progress = ttk.Progressbar(status_bar, mode="indeterminate", length=220)
        self.progress.pack(side="right", padx=(12, 0))

    def log(self, message):
        def do():
            timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
            full_msg = f"{timestamp} {message}"
            self.status.config(text=message)
            # Append to log view
            if hasattr(self, "log_text"):
                self.log_text.configure(state="normal")
                self.log_text.insert("end", full_msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
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
            # Update game selector values
            self.game_select['values'] = list(self.config.keys())
            if self.config:
                self.game_var.set(next(iter(self.config)))
            else:
                self.game_var.set("")
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
    
    def list_backups(self):
        # Run listing in background to avoid blocking UI
        self.run_in_bg(self._list_backups_worker)

    def _list_backups_worker(self):
        lines = []
        # Local backups
        for game, info in self.config.items():
            lines.append(f"{game}:")
            backup_dir = os.path.join(BACKUP_ROOT, game)
            if os.path.exists(backup_dir):
                items = sorted(os.listdir(backup_dir), reverse=True)
                if items:
                    for b in items:
                        lines.append(f"  (local) - {b}")
                else:
                    lines.append("  (local) (no local backups)")
            else:
                lines.append("  (local) (no local backups)")
            lines.append("")  # blank line between games

        # Cloud backups via MEGA (if credentials present)
        if os.path.exists(MEGA_CREDS):
            try:
                with open(MEGA_CREDS) as f:
                    creds = json.load(f)
                mega = Mega()
                m = mega.login(creds.get("email"), creds.get("password"))
                # Ensure SaveSync root exists
                base = m.find_path_descriptor('SaveSync')
                for game, _ in self.config.items():
                    # find game folder under SaveSync
                    game_id = m.find_path_descriptor(f"SaveSync/{game}")
                    lines.append(f"{game} (cloud):")
                    if not game_id:
                        lines.append("  (cloud) (no cloud backups)")
                        lines.append("")
                        continue
                    nodes = m.get_files_in_node(game_id)
                    ts_folders = [(nid, n) for nid, n in nodes.items() if n['t'] == 1]
                    # prefer lexicographic timestamp sorting (newest first)
                    ts_folders.sort(key=lambda x: x[1]['a']['n'], reverse=True)
                    if ts_folders:
                        for _, n in ts_folders:
                            lines.append(f"  (cloud) - {n['a']['n']}")
                    else:
                        lines.append("  (cloud) (no cloud backups)")
                    lines.append("")
            except Exception as e:
                # Append error note for cloud listing
                lines.append(f"(cloud) Unable to query MEGA: {e}")
                lines.append("")
        else:
            lines.append("(cloud) MEGA credentials not configured.")
            lines.append("")

        message = "\n".join(lines) if lines else "No games configured."

        # Show result in GUI thread
        self.after(0, lambda: self._show_backup_message(message))

    def _show_backup_message(self, message: str):
        # If message is long, show it in a scrollable Toplevel; otherwise use messagebox
        if len(message) > 1000:
            win = tk.Toplevel(self)
            win.title("Backups (Local + MEGA)")
            txt = tk.Text(win, wrap="word", height=30, width=80)
            txt.insert("1.0", message)
            txt.configure(state="disabled")
            txt.pack(fill="both", expand=True, padx=8, pady=8)
            btn = ttk.Button(win, text="Close", command=win.destroy)
            btn.pack(pady=(0, 8))
        else:
            messagebox.showinfo("Backups (Local + MEGA)", message)

    def set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        for b in (self.btn_backup, self.btn_restore_local, self.btn_restore_cloud,
                  self.btn_reload, self.btn_exit, self.btn_add,
                  getattr(self, "btn_remove", None), getattr(self, "btn_list", None)):
            if b:
                b.config(state=state)
        if busy:
            self.progress.start(10)
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

    def add_game(self):
        name = simpledialog.askstring("Add Game", "Enter game name:")
        if not name:
            self.log("[!] Add game cancelled or no name provided.")
            return

        folder = filedialog.askdirectory(title=f"Select save folder for {name}")
        if not folder:
            self.log("[!] Add game cancelled or no folder selected.")
            return

        # store as provided; optionally collapse home to ~
        if folder.startswith(HOME):
            folder = folder.replace(HOME, "~", 1)

        # Update in-memory config and write to disk
        self.config[name] = {"save_path": folder}
        try:
            save_config(self.config)
            # refresh UI
            self.game_select['values'] = list(self.config.keys())
            self.game_var.set(name)
            self.log(f"[✓] Added {name} -> {folder} to config.")
        except Exception as e:
            self.log(f"[!] Failed to save new game to config: {e}")
            messagebox.showerror("Error", f"Failed to add game: {e}")

    def remove_game(self):
        selected = self.game_var.get()
        if not selected:
            messagebox.showinfo("Remove Game", "No game selected to remove.")
            return

        if selected not in self.config:
            self.log(f"[!] Selected game '{selected}' not present in config.")
            messagebox.showerror("Error", f"Game '{selected}' not found in configuration.")
            return

        confirm = messagebox.askyesno("Confirm Remove", f"Remove '{selected}' from configuration? This will not delete backups unless you do so manually.")
        if not confirm:
            self.log("[!] Remove game cancelled.")
            return

        try:
            # remove from in-memory config and persist
            del self.config[selected]
            save_config(self.config)
            # update UI values
            values = list(self.config.keys())
            self.game_select['values'] = values
            if values:
                self.game_var.set(values[0])
            else:
                self.game_var.set("")
            self.log(f"[✓] Removed '{selected}' from configuration.")
        except Exception as e:
            self.log(f"[!] Failed to remove game: {e}")
            messagebox.showerror("Error", f"Failed to remove game: {e}")

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

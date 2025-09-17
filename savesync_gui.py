import os
import json
import shutil
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, simpledialog, filedialog
from tkinter import ttk
import threading
try:
    from mega import Mega
    _HAVE_MEGA = True
except Exception:
    Mega = None
    _HAVE_MEGA = False
import tempfile
import traceback
try:
    import pystray
    from PIL import Image, ImageDraw
    _HAVE_PYSTRAY = True
except Exception:
    pystray = None
    Image = None
    ImageDraw = None
    _HAVE_PYSTRAY = False

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


class _Tooltip:
    """Simple tooltip implementation for tkinter widgets.

    Usage: attach_tooltip(widget, 'text'). Tooltip shows on hover.
    """
    def __init__(self, widget, text=''):
        self.widget = widget
        self.text = text
        self.top = None
        self.id_enter = widget.bind('<Enter>', self._on_enter, add='+')
        self.id_leave = widget.bind('<Leave>', self._on_leave, add='+')

    def _on_enter(self, event=None):
        # only show if widget is disabled
        try:
            state = str(self.widget.cget('state'))
        except Exception:
            state = 'normal'
        if state != 'disabled':
            return
        if not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.top = tk.Toplevel(self.widget)
        self.top.wm_overrideredirect(True)
        # small label
        lbl = ttk.Label(self.top, text=self.text, relief='solid', background='lightyellow')
        lbl.pack(ipadx=6, ipady=3)
        try:
            self.top.wm_geometry(f'+{x}+{y}')
        except Exception:
            pass

    def _on_leave(self, event=None):
        if self.top:
            try:
                self.top.destroy()
            except Exception:
                pass
            self.top = None

    def set_text(self, text):
        self.text = text


def attach_tooltip(widget, text):
    """Attach a tooltip to a widget (idempotent)."""
    if getattr(widget, '_tooltip_obj', None) is None:
        widget._tooltip_obj = _Tooltip(widget, text)
    else:
        widget._tooltip_obj.set_text(text)

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

    if not _HAVE_MEGA:
        log_callback("[!] python-mega library not available; cannot upload to MEGA.")
        return

    try:
        with open(creds_path) as f:
            creds = json.load(f)

        if not _HAVE_MEGA:
            log_callback("[!] python-mega library not available; cannot upload to MEGA.")
            return
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

        if not _HAVE_MEGA:
            log_callback("[!] python-mega library not available.")
            return
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

        # use a secure temporary directory for downloads
        temp_dir = tempfile.mkdtemp(prefix=f"{game_name}_{selected}_")
        try:
            files_map = m.get_files_in_node(selected_id)
            file_items = [(fid, n) for fid, n in files_map.items() if n['t'] == 0]
            for fid, fobj in file_items:
                # download expects a (id, dict) tuple
                m.download((fid, fobj), dest_path=temp_dir)
                src = os.path.join(temp_dir, fobj['a']['n'])
                dst = os.path.join(restore_to, fobj['a']['n'])
                shutil.move(src, dst)
                log_callback(f"[↓] Restored {fobj['a']['n']} to {restore_to}")
        finally:
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        log_callback(f"[✓] Cloud restore complete to {restore_to}")
    except Exception as e:
        log_callback(f"[!] MEGA restore failed: {e}")

def backup_game(game_name, log_callback):
    try:
        config = load_config()
        game_info = config.get(game_name)
        if not game_info:
            log_callback(f"[!] Game '{game_name}' not in config.")
            return

        settings = config.get("_settings", {})
        sync_local = settings.get("sync_local", True)
        sync_mega = settings.get("sync_mega", True)

        if not sync_local and not sync_mega:
            log_callback("[!] Both local and MEGA sync are disabled; skipping backup.")
            return

        src = os.path.expanduser(game_info['save_path'])
        if not os.path.exists(src):
            log_callback(f"[!] Save path not found: {src}")
            return

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        # If local sync is enabled, create persistent backup under BACKUP_ROOT.
        if sync_local:
            dest_parent = os.path.join(BACKUP_ROOT, game_name)
            os.makedirs(dest_parent, exist_ok=True)
            dest = os.path.join(dest_parent, timestamp)
            shutil.copytree(src, dest)
            log_callback(f"[✓] Backed up to {dest}")
            # Upload uses this persistent folder.
            if sync_mega:
                upload_to_mega(game_name, dest, log_callback)
        else:
            # Local disabled: create a temporary folder, upload if MEGA enabled, then remove.
            temp_parent = tempfile.mkdtemp(prefix=f"{game_name}_")
            try:
                temp_dest = os.path.join(temp_parent, timestamp)
                shutil.copytree(src, temp_dest)
                log_callback(f"[✓] Created temporary backup for upload: {temp_dest}")
                if sync_mega:
                    upload_to_mega(game_name, temp_dest, log_callback)
            finally:
                try:
                    shutil.rmtree(temp_parent)
                except Exception:
                    pass
    except Exception as e:
        log_callback(f"[!] Backup failed: {e}")

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

    restore_game_selected(game_name, selected, log_callback)

def restore_game_selected(game_name, selected, log_callback):
    """Perform the actual local restore given a selected backup name."""
    try:
        backup_path = os.path.join(BACKUP_ROOT, game_name)
        full_backup_path = os.path.join(backup_path, selected)
        if not os.path.exists(full_backup_path):
            log_callback(f"[!] Selected backup not found: {full_backup_path}")
            return

        config = load_config()
        restore_to = os.path.expanduser(config[game_name]['save_path'])
        if os.path.exists(restore_to):
            shutil.rmtree(restore_to)
        shutil.copytree(full_backup_path, restore_to)
        log_callback(f"[✓] Restored to {restore_to}")
    except Exception as e:
        log_callback(f"[!] Local restore failed: {e}")

def _mega_get_backups(game_name):
    """Return list of (name, node_id) for cloud backups; raises on failure."""
    if not os.path.exists(MEGA_CREDS):
        raise RuntimeError("MEGA credentials not found.")
    if not _HAVE_MEGA:
        raise RuntimeError("python-mega library not available.")
    with open(MEGA_CREDS) as f:
        creds = json.load(f)
    mega = Mega()
    m = mega.login(creds["email"], creds["password"])
    game_folder_id = m.find_path_descriptor(f"SaveSync/{game_name}")
    if not game_folder_id:
        return []
    nodes = m.get_files_in_node(game_folder_id)
    subfolders = [(nid, n) for nid, n in nodes.items() if n['t'] == 1]
    subfolders.sort(key=lambda x: x[1].get('ts', 0), reverse=True)
    return [(n['a']['n'], nid) for nid, n in subfolders]

def restore_from_mega_by_id(game_name, node_id, log_callback):
    """Download files from the MEGA node_id and restore into the game's save path."""
    try:
        if not os.path.exists(MEGA_CREDS):
            log_callback("[!] MEGA credentials not found.")
            return
        with open(MEGA_CREDS) as f:
            creds = json.load(f)
        if not _HAVE_MEGA:
            log_callback("[!] python-mega library not available.")
            return
        mega = Mega()
        m = mega.login(creds["email"], creds["password"])

        config = load_config()
        restore_to = os.path.expanduser(config[game_name]['save_path'])
        if os.path.exists(restore_to):
            shutil.rmtree(restore_to)
        os.makedirs(restore_to, exist_ok=True)

        temp_dir = tempfile.mkdtemp(prefix=f"{game_name}_restore_")
        try:
            files_map = m.get_files_in_node(node_id)
            file_items = [(fid, n) for fid, n in files_map.items() if n['t'] == 0]
            for fid, fobj in file_items:
                m.download((fid, fobj), dest_path=temp_dir)
                src = os.path.join(temp_dir, fobj['a']['n'])
                dst = os.path.join(restore_to, fobj['a']['n'])
                if os.path.exists(src):
                    shutil.move(src, dst)
                    log_callback(f"[↓] Restored {fobj['a']['n']} to {restore_to}")
        finally:
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

        log_callback(f"[✓] Cloud restore complete to {restore_to}")
    except Exception as e:
        log_callback(f"[!] MEGA restore failed: {e}")

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

        # Sync toggles (persisted under _settings in config)
        settings = self.config.get("_settings", {})
        self.sync_local_var = tk.BooleanVar(value=settings.get("sync_local", True))
        self.sync_mega_var = tk.BooleanVar(value=settings.get("sync_mega", True))
        self.minimize_tray_var = tk.BooleanVar(value=settings.get("minimize_to_tray", False))

        self._init_styles()
        self._build_layout()
        # Update MEGA-related UI state (disable if mega.py or creds missing)
        try:
            self._update_mega_ui_state()
        except Exception:
            pass
        # Bind minimize/iconify to tray handler
        try:
            self.bind("<Unmap>", self._on_iconify)
        except Exception:
            pass

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
        # safe default if config is empty; exclude internal "_settings" key
        game_keys = [k for k in self.config.keys() if k != "_settings"]
        if game_keys:
            self.game_var.set(game_keys[0])
        else:
            self.game_var.set("")
        self.game_select = ttk.Combobox(
            ctrls,
            textvariable=self.game_var,
            values=game_keys,
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
        btn_kwargs = {}
        if primary:
            btn_kwargs["style"] = primary
        self.btn_backup = ttk.Button(btn_frame, text="Backup to local and MEGA", command=self.backup, **btn_kwargs)

        btn_kwargs = {}
        if secondary:
            btn_kwargs["style"] = secondary
        self.btn_restore_local = ttk.Button(btn_frame, text="Restore from local", command=self.restore, **btn_kwargs)

        btn_kwargs = {}
        if info:
            btn_kwargs["style"] = info
        self.btn_restore_cloud = ttk.Button(btn_frame, text="Restore from MEGA", command=self.restore_from_cloud, **btn_kwargs)

        btn_kwargs = {}
        if warning:
            btn_kwargs["style"] = warning
        self.btn_reload = ttk.Button(btn_frame, text="Reload Config", command=self.reload_json, **btn_kwargs)

        self.btn_backup.grid(row=0, column=0, padx=(0, 8), pady=(0, 6))
        self.btn_restore_local.grid(row=0, column=1, padx=(0, 8), pady=(0, 6))
        self.btn_restore_cloud.grid(row=0, column=2, padx=(0, 8), pady=(0, 6))
        self.btn_reload.grid(row=0, column=3, padx=(0, 8), pady=(0, 6))

        # Sync toggle checkbuttons
        ttk.Label(ctrls, text="Sync:").grid(row=0, column=4, sticky="e", padx=(12, 4))
        self.chk_local = ttk.Checkbutton(
            ctrls,
            text="Local",
            variable=self.sync_local_var,
            command=lambda: self._on_toggle_sync("sync_local", self.sync_local_var.get())
        )
        self.chk_mega = ttk.Checkbutton(
            ctrls,
            text="MEGA",
            variable=self.sync_mega_var,
            command=lambda: self._on_toggle_sync("sync_mega", self.sync_mega_var.get())
        )
        self.chk_local.grid(row=0, column=5, sticky="w", padx=(0, 8))
        self.chk_mega.grid(row=0, column=6, sticky="w", padx=(0, 8))

        # Second row: add / remove / exit
        btn_kwargs = {}
        if primary:
            btn_kwargs["style"] = primary
        self.btn_add = ttk.Button(btn_frame, text="Add Game", command=self.add_game, **btn_kwargs)

        btn_kwargs = {}
        if danger:
            btn_kwargs["style"] = danger
        self.btn_remove = ttk.Button(btn_frame, text="Remove Game", command=self.remove_game, **btn_kwargs)

        btn_kwargs = {}
        if danger:
            btn_kwargs["style"] = danger
        self.btn_exit = ttk.Button(btn_frame, text="Exit and Sync", command=self.on_exit, **btn_kwargs)

        btn_kwargs = {}
        if info:
            btn_kwargs["style"] = info
        self.btn_list = ttk.Button(btn_frame, text='List Backups', command=self.list_backups, **btn_kwargs)
        self.btn_add.grid(row=1, column=0, padx=(0, 8))
        self.btn_remove.grid(row=1, column=1, padx=(0, 8))
        self.btn_exit.grid(row=1, column=2, padx=(0, 8))
        self.btn_list.grid(row=1, column=3, padx=(0, 8))
        # Options button
        btn_kwargs = {}
        if info:
            btn_kwargs["style"] = info
        self.btn_options = ttk.Button(btn_frame, text="Options", command=self.show_options_window, **btn_kwargs)
        self.btn_options.grid(row=1, column=4, padx=(0, 8))

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

        # Menu bar so Options is always reachable
        try:
            menubar = tk.Menu(self)
            file_menu = tk.Menu(menubar, tearoff=0)
            file_menu.add_command(label="Options...", command=self.show_options_window, accelerator="Alt+O")
            file_menu.add_separator()
            file_menu.add_command(label="Exit", command=self.on_exit)
            menubar.add_cascade(label="File", menu=file_menu)
            # Attach menubar
            self.config(menu=menubar)
            # Bind accelerator (Alt+O) to open options
            try:
                self.bind_all('<Alt-o>', lambda e: self.show_options_window())
                self.bind_all('<Alt-O>', lambda e: self.show_options_window())
            except Exception:
                pass
        except Exception:
            pass

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
            # Update game selector values (exclude internal _settings)
            game_keys = [k for k in self.config.keys() if k != "_settings"]
            self.game_select['values'] = game_keys
            if game_keys:
                self.game_var.set(game_keys[0])
            else:
                self.game_var.set("")
            self.log("[✓] Configuration reloaded successfully.")
            try:
                self._update_mega_ui_state()
            except Exception:
                pass
        except Exception as e:
            self.log(f"[!] Error reloading config: {e}")
            messagebox.showerror("Error", f"Failed to reload configuration: {e}")

    def backup(self):
        game = self.game_var.get()
        self.run_in_bg(lambda: backup_game(game, self.log))

    def ask_selection(self, title, prompt, options):
        """Show a modal selection list and return the selected item (or None)."""
        sel = {"value": None}
        win = tk.Toplevel(self)
        win.transient(self)
        win.title(title)
        win.grab_set()
        ttk.Label(win, text=prompt, wraplength=560).pack(padx=12, pady=(12, 6))
        lb = tk.Listbox(win, height=min(12, max(3, len(options))), exportselection=False)
        for opt in options:
            lb.insert("end", opt)
        lb.pack(padx=12, pady=(0, 12), fill="both", expand=True)
        lb.focus_set()

        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=(0, 12))
        def on_ok():
            sel_idx = lb.curselection()
            if sel_idx:
                sel["value"] = lb.get(sel_idx[0])
            win.destroy()
        def on_cancel():
            win.destroy()

        ttk.Button(btn_frame, text="OK", command=on_ok).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="left", padx=6)

        self.wait_window(win)
        return sel["value"]

    def restore(self):
        game = self.game_var.get()
        backup_path = os.path.join(BACKUP_ROOT, game)
        if not os.path.exists(backup_path):
            self.log(f"[!] No backups found for {game}")
            return
        backups = sorted(os.listdir(backup_path), reverse=True)
        if not backups:
            self.log(f"[!] No backups available.")
            return

        selected = self.ask_selection("Choose Backup", "Select a backup to restore:", backups)
        if not selected:
            self.log("[!] Local restore cancelled.")
            return

        # Run the actual restore in background
        self.run_in_bg(lambda: restore_game_selected(game, selected, self.log))

    def restore_from_cloud(self):
        game = self.game_var.get()
        settings = self.config.get("_settings", {})
        if not settings.get("sync_mega", True):
            self.log("[!] MEGA sync is disabled; cloud restore blocked.")
            messagebox.showinfo("MEGA Disabled", "MEGA sync is disabled in settings.")
            return

        # Fetch backup list in a background thread, prompt selection on main thread,
        # then perform restore in background.
        def fetch_worker():
            try:
                pairs = _mega_get_backups(game)  # list of (name, node_id)
            except Exception as e:
                self.after(0, lambda: self.log(f"[!] Could not list MEGA backups: {e}"))
                self.after(0, lambda: self.set_busy(False))
                return

            if not pairs:
                self.after(0, lambda: self.log(f"[!] No cloud backups available for {game}"))
                self.after(0, lambda: self.set_busy(False))
                return

            names = [n for n, _ in pairs]
            def on_main():
                selected = self.ask_selection("Restore from MEGA", "Select a cloud backup to restore:", names)
                if not selected:
                    self.log("[!] Cloud restore cancelled.")
                    return
                node_map = dict(pairs)
                node_id = node_map.get(selected)
                if not node_id:
                    self.log("[!] Selected cloud backup not found.")
                    return
                # Run the restore in background
                self.run_in_bg(lambda: restore_from_mega_by_id(game, node_id, self.log))
            self.after(0, on_main)
            self.after(0, lambda: self.set_busy(False))

        self.set_busy(True)
        threading.Thread(target=fetch_worker, daemon=True).start()

    def list_backups(self):
        # Run listing in background to avoid blocking UI
        self.run_in_bg(self._list_backups_worker)

    def _list_backups_worker(self):
        lines = []
        settings = self.config.get("_settings", {})
        sync_local = settings.get("sync_local", True)
        sync_mega = settings.get("sync_mega", True)

        # Local backups
        for game, info in self.config.items():
            if game == "_settings":
                continue
            lines.append(f"{game}:")
            backup_dir = os.path.join(BACKUP_ROOT, game)
            if not sync_local:
                lines.append("  (local) DISABLED")
            else:
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

        # Cloud backups via MEGA (if credentials present and enabled)
        if not sync_mega:
            lines.append("(cloud) MEGA sync is DISABLED.")
            lines.append("")
        else:
            if os.path.exists(MEGA_CREDS):
                try:
                    with open(MEGA_CREDS) as f:
                        creds = json.load(f)
                    if not _HAVE_MEGA:
                        lines.append("(cloud) python-mega library not installed; cannot list cloud backups.")
                        lines.append("")
                    else:
                        mega = Mega()
                        m = mega.login(creds.get("email"), creds.get("password"))
                        # Ensure SaveSync root exists
                        base = m.find_path_descriptor('SaveSync')
                        for game, _ in self.config.items():
                            if game == "_settings":
                                continue
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
                  getattr(self, "btn_remove", None), getattr(self, "btn_list", None),
                  getattr(self, "btn_options", None),
                  getattr(self, "chk_local", None), getattr(self, "chk_mega", None)):
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
        settings = self.config.get("_settings", {})
        if not settings.get("sync_local", True) and not settings.get("sync_mega", True):
            self.log("[!] Both local and MEGA sync disabled; skipping auto backups.")
            return

        for game, info in self.config.items():
            if game == "_settings":
                continue
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
                self.run_in_bg(lambda g=game: backup_game(g, self.log))
            else:
                self.log(f"[=] No changes in {game}")

    # --- Tray and Options UI ---
    def show_options_window(self):
        win = tk.Toplevel(self)
        win.title("Options")
        win.transient(self)
        win.grab_set()

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        # Minimize to tray toggle
        ttk.Label(frm, text="Minimize to tray:").grid(row=0, column=0, sticky="w")
        chk = ttk.Checkbutton(frm, text="Enable minimize to tray", variable=self.minimize_tray_var)
        chk.grid(row=0, column=1, sticky="w", padx=(8, 0))

        # MEGA credentials button
        ttk.Label(frm, text="MEGA:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        btn_creds = ttk.Button(frm, text="Set MEGA Credentials", command=self.set_mega_credentials)
        btn_creds.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        # Save/Close
        def on_save():
            s = self.config.setdefault("_settings", {})
            s["minimize_to_tray"] = bool(self.minimize_tray_var.get())
            try:
                save_config(self.config)
                self.log("[✓] Options saved.")
            except Exception as e:
                self.log(f"[!] Failed to save options: {e}")
                messagebox.showerror("Error", f"Failed to save options: {e}")
            win.destroy()

        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn_frame, text="Save", command=on_save).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="left", padx=6)

    def set_mega_credentials(self):
        # Prompt for email and password and save securely to MEGA_CREDS
        email = simpledialog.askstring("MEGA Credentials", "Email:", parent=self)
        if not email:
            self.log("[!] MEGA email entry cancelled.")
            return
        password = simpledialog.askstring("MEGA Credentials", "Password:", parent=self, show="*")
        if password is None:
            self.log("[!] MEGA password entry cancelled.")
            return

        try:
            os.makedirs(os.path.dirname(MEGA_CREDS), exist_ok=True)
            with open(MEGA_CREDS, "w") as f:
                json.dump({"email": email, "password": password}, f, indent=4)
            # restrict permissions
            try:
                os.chmod(MEGA_CREDS, 0o600)
            except Exception:
                pass
            self.log("[✓] Saved MEGA credentials.")
            try:
                self._update_mega_ui_state()
            except Exception:
                pass
        except Exception as e:
            self.log(f"[!] Failed to save MEGA credentials: {e}")
            messagebox.showerror("Error", f"Failed to save MEGA credentials: {e}")

    def _update_mega_ui_state(self):
        """Enable/disable MEGA-related widgets depending on availability.

        Disables the MEGA checkbox and cloud-restore button when the
        python-mega library isn't installed or credentials file is missing.
        """
        have_lib = bool(_HAVE_MEGA)
        creds_ok = os.path.exists(MEGA_CREDS)
        enabled = have_lib and creds_ok
        state = "normal" if enabled else "disabled"
        # chk_mega is a Checkbutton (ttk) and btn_restore_cloud is a Button
        try:
            if getattr(self, 'chk_mega', None):
                self.chk_mega.config(state=state)
            if getattr(self, 'btn_restore_cloud', None):
                self.btn_restore_cloud.config(state=state)
            # Also grey out the List Backups button when MEGA cloud features aren't available
            if getattr(self, 'btn_list', None):
                self.btn_list.config(state=state)
            # attach dynamic tooltip explaining why disabled
            if not enabled:
                if not have_lib:
                    reason = 'python-mega not installed. Install with: pip3 install mega-x'
                elif not creds_ok:
                    reason = 'MEGA credentials missing. Open Options → Set MEGA Credentials.'
                else:
                    reason = 'MEGA unavailable.'
            else:
                reason = ''
            # The main backup button still performs local backups; attach a tooltip when MEGA unavailable
            if getattr(self, 'btn_backup', None):
                attach_tooltip(self.btn_backup, reason if not enabled else '')
            try:
                if getattr(self, 'chk_mega', None):
                    attach_tooltip(self.chk_mega, reason)
                if getattr(self, 'btn_restore_cloud', None):
                    attach_tooltip(self.btn_restore_cloud, reason)
            except Exception:
                pass
        except Exception:
            pass

    def _make_tray_image(self):
        # Create a simple 64x64 icon programmatically
        if Image is None or ImageDraw is None:
            return None
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((4, 4, 60, 60), fill=(40, 120, 200, 255))
        draw.rectangle((20, 24, 44, 40), fill=(255, 255, 255, 255))
        return img

    def _start_tray(self):
        self.log("[Tray] _start_tray() called")
        if not _HAVE_PYSTRAY:
            self.log("[Tray] pystray not available; tray icon disabled.")
            return
        if getattr(self, 'tray_icon', None):
            self.log("[Tray] tray_icon already exists; skipping start.")
            return

        # Build an icon image (fallback to a simple placeholder if possible)
        img = self._make_tray_image()
        if img is None:
            self.log("[Tray] _make_tray_image() returned None")
            if Image is not None:
                try:
                    img = Image.new('RGBA', (64, 64), (40, 120, 200, 255))
                    self.log("[Tray] Created placeholder PIL image")
                except Exception as e:
                    self.log(f"[Tray] Failed to create placeholder image: {e}")
                    self.log(traceback.format_exc())
                    img = None

        # Handlers (pystray expects signature (icon, item) for menu callbacks)
        def _on_restore(icon, item=None):
            self.log("[Tray] menu callback: Restore invoked")
            try:
                self.after(0, self._restore_from_tray)
            except Exception as e:
                self.log(f"[!] Tray restore schedule failed: {e}")
                self.log(traceback.format_exc())

        def _on_quit(icon, item=None):
            self.log("[Tray] menu callback: Quit invoked")
            try:
                self.after(0, self._quit_from_tray)
            except Exception as e:
                self.log(f"[!] Tray quit schedule failed: {e}")
                self.log(traceback.format_exc())

        try:
            self.log("[Tray] Creating pystray.Icon...")
            # Create icon and assign menu explicitly (some backends require this)
            icon = pystray.Icon('savesync', img, 'SaveSync')
            menu = pystray.Menu(
                pystray.MenuItem('Restore', _on_restore),
                pystray.MenuItem('Quit', _on_quit)
            )
            icon.menu = menu
            # Some backends pick up title/visible attributes better when set explicitly
            try:
                icon.title = "SaveSync"
            except Exception:
                pass
            try:
                icon.visible = True
            except Exception:
                pass

            self.tray_icon = icon
            self.log("[Tray] Icon object created and stored on self.tray_icon")
        except Exception as e:
            self.log(f"[!] Failed to create tray icon: {e}")
            self.log(traceback.format_exc())
            return

        def run_icon():
            self.log("[Tray] Tray loop thread starting")
            try:
                # Use run() in a dedicated thread for broader backend compatibility
                icon.run()
            except Exception as e:
                self.log(f"[!] Tray icon loop failed: {e}")
                self.log(traceback.format_exc())
            finally:
                self.log("[Tray] Tray loop ended")

        # Start the icon loop in a daemon thread so it won't block exit
        self.tray_thread = threading.Thread(target=run_icon, daemon=True)
        self.tray_thread.start()
        self.log("[Tray] Tray icon thread started")

    def _stop_tray(self):
        self.log("[Tray] _stop_tray() called")
        icon = getattr(self, 'tray_icon', None)
        if icon:
            try:
                stop_fn = getattr(icon, "stop", None)
                if callable(stop_fn):
                    try:
                        self.log("[Tray] calling icon.stop()")
                        stop_fn()
                        self.log("[Tray] icon.stop() returned")
                    except Exception:
                        # some backends may require calling icon.visible = False first
                        try:
                            self.log("[Tray] icon.stop() raised; attempting icon.visible=False")
                            icon.visible = False
                        except Exception:
                            pass
                else:
                    try:
                        self.log("[Tray] icon.stop() not present; setting icon.visible = False")
                        icon.visible = False
                    except Exception:
                        pass
            except Exception as e:
                self.log(f"[Tray] Unexpected error stopping tray icon: {e}")
                self.log(traceback.format_exc())
        else:
            self.log("[Tray] No tray icon to stop")
        self.tray_icon = None
        self.log("[Tray] tray_icon reference cleared")

    def _restore_from_tray(self):
        self.log("[Tray] _restore_from_tray() called")
        try:
            # Bring the window back, raise it and give it focus.
            if getattr(self, 'tray_icon', None):
                self.log("[Tray] Stopping tray icon before restore")
            else:
                self.log("[Tray] No tray_icon found at restore time")
            self.deiconify()
            self.log("[Tray] deiconify() called")
            try:
                self.lift()
                self.log("[Tray] lift() called")
            except Exception as e:
                self.log(f"[Tray] lift() failed: {e}")
                self.log(traceback.format_exc())
            try:
                self.focus_force()
                self.log("[Tray] focus_force() called")
            except Exception as e:
                self.log(f"[Tray] focus_force() failed: {e}")
                self.log(traceback.format_exc())
            try:
                self.attributes('-topmost', True)
                self.after(120, lambda: self.attributes('-topmost', False))
                self.log("[Tray] temporary -topmost toggled")
            except Exception as e:
                self.log(f"[Tray] topmost toggle failed: {e}")
                self.log(traceback.format_exc())
            try:
                self._stop_tray()
            except Exception as e:
                self.log(f"[Tray] _stop_tray() during restore failed: {e}")
                self.log(traceback.format_exc())
        except Exception as e:
            self.log(f"[Tray] _restore_from_tray unexpected error: {e}")
            self.log(traceback.format_exc())

    def _quit_from_tray(self):
        self.log("[Tray] _quit_from_tray() called")
        try:
            try:
                self._stop_tray()
            except Exception as e:
                self.log(f"[Tray] _stop_tray() during quit failed: {e}")
                self.log(traceback.format_exc())
        finally:
            try:
                self.log("[Tray] destroying main window")
                self.destroy()
            except Exception as e:
                self.log(f"[Tray] destroy() failed: {e}")
                self.log(traceback.format_exc())

    def _on_iconify(self, event=None):
        # Called when window is minimized/iconified
        try:
            self.log("[Tray] _on_iconify() event fired")
            settings = self.config.get("_settings", {})
            if settings.get("minimize_to_tray", False):
                # hide main window and start tray
                self.log("[Tray] minimize_to_tray enabled; withdrawing window and starting tray")
                self.withdraw()
                self._start_tray()
            else:
                self.log("[Tray] minimize_to_tray disabled; normal iconify")
        except Exception as e:
            self.log(f"[Tray] _on_iconify error: {e}")
            self.log(traceback.format_exc())


    def add_game(self):
        name = simpledialog.askstring("Add Game", "Enter game name:")
        if not name:
            self.log("[!] Add game cancelled or no name provided.")
            return

        if name == "_settings":
            messagebox.showerror("Invalid name", "The name '_settings' is reserved.")
            self.log("[!] Attempted to add reserved name '_settings'.")
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
            self.game_select['values'] = [k for k in self.config.keys() if k != "_settings"]
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

    def _on_toggle_sync(self, key, value):
        # Persist toggle to config under "_settings"
        s = self.config.setdefault("_settings", {})
        s[key] = bool(value)
        try:
            save_config(self.config)
            self.log(f"[✓] Set {key} = {s[key]}")
        except Exception as e:
            self.log(f"[!] Failed to save setting {key}: {e}")
            messagebox.showerror("Error", f"Failed to save setting {key}: {e}")

# Initialize default config if not present
if not os.path.exists(CONFIG_FILE):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump({
            "Skyrim": {"save_path": "~/SkyrimSaves"},
            "Red Dead 2": {"save_path": "~/.wine/drive_c/users/you/Documents/RDR2/Profiles"},
            "_settings": {"sync_local": True, "sync_mega": True}
        }, f, indent=4)
    print(f"Created default config at {CONFIG_FILE}")

if __name__ == "__main__":
    try:
        app = SaveSyncApp()
        app.mainloop()
    except Exception as e:
        print(f"Fatal error starting GUI: {e}")
        raise

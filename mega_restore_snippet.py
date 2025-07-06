
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

    # List all timestamped folders
    subfolders = [f for f in m.get_files_in_node(game_folder) if f['t'] == 1]  # type=1 = folder
    subfolders.sort(key=lambda x: x['ts'], reverse=True)
    if not subfolders:
        log_callback(f"[!] No cloud backups available for {game_name}")
        return

    backup_names = [f['a']['n'] for f in subfolders]
    selected = simpledialog.askstring("Restore from MEGA",
        f"Available cloud backups:
" + "\n".join(backup_names) + "\n\nType backup name:")

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

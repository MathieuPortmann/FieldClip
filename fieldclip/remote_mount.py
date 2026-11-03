#!/usr/bin/env python3
# =============================================================================
#  FieldClip — Remote drive mount manager                  remote_mount.py
#
#  One script to mount / unmount / diagnose an SSHFS share on
#  Windows, macOS and Linux.
#
#  Double-click  :  launchers/MOUNT_DRIVE.bat        (Windows)
#                   launchers/MOUNT_DRIVE.command    (macOS)
#                   launchers/mount_drive.sh         (Linux)
#
#  Command line (used by FieldClip) :
#      python -m fieldclip.remote_mount status [--json]
#      python -m fieldclip.remote_mount mount | unmount | remount | doctor | repair
#
#  Server settings come from the active profile (remote.mount); the credential
#  from the per-user secrets file or FIELDCLIP_REMOTE_SECRET. Never printed.
#  No pip packages needed. Python 3.8+.
# =============================================================================
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time

# --- Robust console output (Windows cp437 / cp1252) --------------------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from . import settings as S
except ImportError:                       # run as a plain script
    import settings as S

HERE     = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(S.user_config_dir(), "logs", "remote_mount.log")

IS_WIN   = platform.system() == "Windows"
IS_MAC   = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# Mount point states
ABSENT  = "ABSENT"     # nothing mounted
HEALTHY = "MOUNTED"    # mounted and responding
ZOMBIE  = "ZOMBIE"     # drive/folder exists but every I/O blocks



# =============================================================================
#  Config & log
# =============================================================================

def load_config() -> dict:
    """Flat view of the active profile's remote.mount + the credential."""
    s = S.active()
    r = s["remote"]
    cfg = dict(r["mount"])
    cfg["name"] = r["display_name"]
    cfg["user"], cfg["password"] = S.remote_credentials(s)
    cfg["auth"] = r["auth"]
    cfg["secret_label"] = r.get("secret_label", "Password")
    return cfg


def save_credential(cfg: dict, secret: str) -> None:
    """Store a new credential in the per-user secrets file (never the profile)."""
    S.set_secret("remote_secret", secret)
    cfg["user"], cfg["password"] = S.remote_credentials()


def _redact(text: str) -> str:
    """Strip every known secret out of a line before it reaches the log."""
    for secret in {S.get_secret("remote_secret"), S.get_secret("dji_api_key")}:
        if secret:
            text = text.replace(secret, "***")
    return text


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {_redact(msg)}"
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def mountpoint(cfg: dict) -> str:
    """Mount point path for the current OS."""
    if IS_WIN:
        return cfg["windows_drive"].rstrip(":").upper() + ":\\"
    return os.path.expanduser(cfg["unix_mountpoint"])


# =============================================================================
#  Command execution
# =============================================================================

def run(args, timeout=60, stdin_data=None):
    """Run a command. Returns (returncode, stdout+stderr). Never raises."""
    log("run: " + " ".join(str(a) for a in args))
    try:
        p = subprocess.run(
            args,
            input=stdin_data.encode() if stdin_data is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        out = p.stdout.decode("utf-8", errors="replace").strip()
        log(f"  -> rc={p.returncode} {out[:400]}")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        log("  -> TIMEOUT")
        return 124, f"timed out after {timeout}s"
    except FileNotFoundError:
        log("  -> not found")
        return 127, f"command not found: {args[0]}"
    except Exception as e:
        log(f"  -> exception {e}")
        return 1, str(e)


# =============================================================================
#  Health probe
#
#  The key point: on a zombie mount (SSHFS holding a dead socket), even
#  os.path.exists() blocks in the kernel in an UNINTERRUPTIBLE wait. The
#  probing thread cannot be killed — which is why it is a daemon and why we
#  never join() without a timeout. We stop waiting; we do not kill. This is the
#  only reliable way to tell "absent" from "zombie".
# =============================================================================

def probe(path: str, timeout: float = 6.0) -> str:
    result = {}

    def _worker():
        try:
            os.listdir(path)
            result["state"] = HEALTHY
        except FileNotFoundError:
            result["state"] = ABSENT
        except NotADirectoryError:
            result["state"] = ABSENT
        except OSError:
            # Drive letter present but unreadable: typical of a broken mount
            result["state"] = ZOMBIE

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return ZOMBIE          # stuck in the kernel
    return result.get("state", ABSENT)


def status(cfg: dict) -> str:
    return probe(mountpoint(cfg))


# =============================================================================
#  WINDOWS
# =============================================================================

def win_force_clean(drive_letter: str) -> None:
    """Break a zombie mount: kill sshfs, then release the drive letter."""
    print("  Force-cleaning the mount...")
    run(["taskkill", "/f", "/im", "sshfs.exe"], timeout=20)
    run(["taskkill", "/f", "/im", "sshfs-win.exe"], timeout=20)
    run(["net", "use", drive_letter + ":", "/delete", "/y"], timeout=30)
    time.sleep(2)


def win_mount(cfg: dict) -> bool:
    drive = cfg["windows_drive"].rstrip(":").upper()
    user, pw = cfg["user"], cfg["password"]

    st = probe(drive + ":\\")
    if st == HEALTHY:
        print(f"  {drive}: is already mounted and responding.")
        return True
    if st == ZOMBIE:
        print(f"  {drive}: is mounted but not responding (zombie).")
        win_force_clean(drive)

    # \\sshfs\<id>@<host>!<port>
    #
    # Built here in Python rather than in a .bat: the "!" in the port number is
    # an escape character in cmd when EnableDelayedExpansion is on. That trap is
    # what froze the old watchdog onto a single hard-coded share.
    unc = r"\\sshfs\{user}@{host}!{port}".format(
        user=user, host=cfg["host"], port=cfg["sftp_port"])

    print(f"  Mounting {cfg['host']} on {drive}: ...")
    # /persistent:no on purpose: "yes" makes Windows retry the reconnection at
    # every boot before the network is up, which slows down logon and hangs
    # Explorer.
    rc, out = run(
        ["net", "use", drive + ":", unc, pw,
         "/user:" + user, "/persistent:no"],
        timeout=90)

    if rc != 0:
        print(f"  MOUNT FAILED.\n  {out}")
        print("\n  Things to check:")
        print(f"    - Is the {cfg['secret_label'].lower()} correct? (case sensitive)")
        print(f"    - Is port {cfg['sftp_port']} blocked by a firewall or VPN? Try on 4G.")
        print("    - Are WinFsp and SSHFS-Win installed? Run diagnostics [5].")
        return False

    if probe(drive + ":\\") != HEALTHY:
        print(f"  Mount reported success but {drive}: cannot be read. Broken mount.")
        return False

    print(f"  OK - {cfg['name']} mounted on {drive}:")
    return True


def win_unmount(cfg: dict) -> bool:
    drive = cfg["windows_drive"].rstrip(":").upper()
    run(["net", "use", drive + ":", "/delete", "/y"], timeout=30)
    if probe(drive + ":\\") == ABSENT:
        print(f"  OK - {drive}: unmounted.")
        return True
    print(f"  {drive}: still present - force-cleaning.")
    win_force_clean(drive)
    return probe(drive + ":\\") == ABSENT


def win_repair_registry(cfg: dict) -> bool:
    """
    Fix the WinFsp sshfs service definition.

    Two bugs repaired here:
      1. The SSH options were written to GlobalOptions, which is not a key the
         WinFsp launcher reads -> never applied.
      2. They went into the 64-bit registry view, while the service definition
         lives in the 32-bit view (WOW6432Node) -> wrong place entirely.

    Without ServerAliveInterval, SSH never notices a dead connection: sshfs
    keeps a zombie socket alive and every I/O on the drive enters an
    uninterruptible kernel wait. That is what freezes the whole of Windows.
    """
    if not IS_WIN:
        print("  Registry repair: Windows only.")
        return False

    key = r"HKLM\SOFTWARE\WinFsp\Services\sshfs"

    rc, out = run(["reg", "query", key, "/reg:32", "/v", "Executable"], timeout=20)
    if rc != 0:
        print("  sshfs service not found in the registry.")
        print("  SSHFS-Win is not installed correctly - reinstall it.")
        return False

    exe = ""
    for line in out.splitlines():
        if "Executable" in line:
            exe = line.split("REG_SZ")[-1].strip()
    print(f"  Executable found: {exe or '(unknown)'}")

    # Options handed to sshfs by the launcher. ServerAliveInterval is the one
    # that matters: it makes SSH detect a dead link in ~45 s instead of never.
    opts = (
        "svc %1 %2 %U "
        "-oServerAliveInterval=15 -oServerAliveCountMax=3 "
        "-oConnectTimeout=10 -oreconnect "
        "-oFileInfoTimeout=5000 -oDirInfoTimeout=5000 -oVolumeInfoTimeout=5000 "
        "-oidmap=user -ouid=-1 -ogid=-1 -oumask=000 "
        "-oCompression=no -ocache_timeout=10"
    )

    rc1, o1 = run(["reg", "add", key, "/reg:32", "/v", "CommandLine",
                   "/t", "REG_SZ", "/d", opts, "/f"], timeout=20)
    if rc1 != 0:
        print(f"  REGISTRY WRITE FAILED: {o1}")
        print("  Re-run this script as ADMINISTRATOR.")
        return False
    print("  OK - CommandLine fixed (SSH keepalive + reconnect now active).")

    # Purge the placebo key from the 64-bit view
    run(["reg", "delete", key, "/reg:64", "/v", "GlobalOptions", "/f"], timeout=20)
    print("  OK - GlobalOptions (inert) removed from the 64-bit view.")

    print("\n  Do this now: unmount [2], then mount [1], to apply the change.")
    return True


# =============================================================================
#  macOS / Linux
# =============================================================================

def find_sshfs() -> str:
    for c in ("sshfs",
              "/opt/homebrew/bin/sshfs",     # Apple Silicon
              "/usr/local/bin/sshfs",        # Intel
              "/usr/bin/sshfs"):
        p = shutil.which(c) if os.sep not in c else (c if os.path.exists(c) else None)
        if p:
            return p
    return ""


def mac_has_macfuse() -> bool:
    return (os.path.exists("/Library/Filesystems/macfuse.fs")
            or os.path.exists("/Library/Filesystems/osxfuse.fs"))


def unix_mount(cfg: dict) -> bool:
    mp  = mountpoint(cfg)
    user, pw = cfg["user"], cfg["password"]

    st = probe(mp)
    if st == HEALTHY and os.path.ismount(mp):
        print(f"  {mp} is already mounted and responding.")
        return True
    if st == ZOMBIE:
        print(f"  {mp} is not responding (zombie) - forcing unmount.")
        unix_unmount(cfg, force=True)

    sshfs = find_sshfs()
    if not sshfs:
        print("  sshfs not found.")
        if IS_MAC:
            print("    1) Install macFUSE: https://osxfuse.github.io")
            print("       (System Settings > Privacy & Security > Allow, then reboot)")
            print("    2) brew install gromgit/fuse/sshfs-mac")
        else:
            print("    sudo apt install sshfs      (Debian/Ubuntu)")
            print("    sudo dnf install fuse-sshfs (Fedora)")
        return False

    if IS_MAC and not mac_has_macfuse():
        print("  macFUSE is not installed (or its kernel extension is not allowed).")
        print("    https://osxfuse.github.io  then System Settings > Privacy & Security")
        return False

    os.makedirs(mp, exist_ok=True)

    opts = [
        "password_stdin",              # avoids the prompt: pasting often fails
        "reconnect",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=3",
        "ConnectTimeout=10",
        f"port={cfg['sftp_port']}",
        "Compression=no",
        "dir_cache=yes",
        # First mount on a new machine: there is no terminal to answer the
        # host-key prompt, so ssh would just fail. Accept a NEW key, still
        # refuse a CHANGED one.
        "StrictHostKeyChecking=accept-new",
    ]
    if IS_MAC:
        # volname is a macFUSE option; Linux fuse rejects the whole mount
        # with "unknown option(s)".
        opts += [f"volname={cfg['volume_label']}"]
        # Without these, macOS scatters .DS_Store and ._* files across the
        # share.
        opts += ["noappledouble", "noapplexattr", "defer_permissions"]

    args = [sshfs, f"{user}@{cfg['host']}:.", mp, "-o", ",".join(opts)]

    print(f"  Mounting {cfg['host']} on {mp} ...")
    rc, out = run(args, timeout=90, stdin_data=pw + "\n")

    if rc != 0:
        print(f"  MOUNT FAILED.\n  {out}")
        print("\n  Things to check:")
        print(f"    - Is the {cfg['secret_label'].lower()} correct? (case sensitive)")
        print(f"    - Is port {cfg['sftp_port']} blocked by a firewall or VPN? Try on 4G.")
        if IS_MAC:
            print("    - Is the macFUSE extension allowed in System Settings?")
        return False

    if probe(mp) != HEALTHY:
        print(f"  Mount reported success but {mp} cannot be read.")
        return False

    print(f"  OK - {cfg['name']} mounted on {mp}")
    return True


def unix_unmount(cfg: dict, force: bool = False) -> bool:
    mp = mountpoint(cfg)
    if IS_MAC:
        run(["umount", mp], timeout=30)
        if probe(mp) != ABSENT and force:
            run(["diskutil", "unmount", "force", mp], timeout=30)
    else:
        run(["fusermount", "-u", mp], timeout=30)
        if probe(mp) != ABSENT and force:
            run(["fusermount", "-uz", mp], timeout=30)   # lazy unmount

    if not os.path.ismount(mp):
        print(f"  OK - {mp} unmounted.")
        return True
    print(f"  {mp} is still mounted. Close whatever is using it, then retry.")
    return False


# =============================================================================
#  Shared actions
# =============================================================================

def do_mount(cfg):
    if not cfg.get("host"):
        print("  No server in the active profile (remote.mount.host).")
        return False
    return win_mount(cfg) if IS_WIN else unix_mount(cfg)


def do_unmount(cfg):
    return win_unmount(cfg) if IS_WIN else unix_unmount(cfg, force=True)


def do_remount(cfg):
    print("  Remounting...")
    do_unmount(cfg)
    time.sleep(2)
    return do_mount(cfg)


def do_open(cfg):
    mp = mountpoint(cfg)
    if probe(mp) != HEALTHY:
        print("  Nothing mounted - mount it first [1].")
        return False
    if IS_WIN:
        subprocess.Popen(["explorer", mp])
    elif IS_MAC:
        subprocess.Popen(["open", mp])
    else:
        subprocess.Popen(["xdg-open", mp])
    return True


def do_doctor(cfg):
    print("\n  ---- Diagnostics ---------------------------------------------")
    print(f"  OS            : {platform.system()} {platform.release()}")
    print(f"  Python        : {sys.version.split()[0]}")
    print(f"  Server        : {cfg['host'] or '(not set in the profile!)'}")
    print(f"  Credential    : {S.mask(cfg['password'])}")
    print(f"  Mount point   : {mountpoint(cfg)}")
    print(f"  State         : {status(cfg)}")

    # Network: SFTP then WebDAV
    import socket
    import urllib.parse
    targets = [("SFTP  ", cfg["host"], cfg["sftp_port"])]
    dav = urllib.parse.urlparse(S.active()["remote"]["webdav"]["url"] or "")
    if dav.hostname:
        targets.append(("WebDAV", dav.hostname,
                        dav.port or (443 if dav.scheme == "https" else 80)))
    for label, host, port in targets:
        if not host:
            continue
        s = socket.socket()
        s.settimeout(8)
        try:
            s.connect((host, int(port)))
            print(f"  {label} {host}:{port} : reachable")
        except Exception as e:
            print(f"  {label} {host}:{port} : UNREACHABLE ({e})")
        finally:
            s.close()

    if IS_WIN:
        rc, out = run(["reg", "query", r"HKLM\SOFTWARE\WinFsp\Services\sshfs",
                       "/reg:32"], timeout=20)
        if rc != 0:
            print("  SSHFS-Win     : NOT INSTALLED")
        else:
            has_alive = "ServerAliveInterval" in out
            print("  SSHFS-Win     : installed")
            print(f"  SSH keepalive : {'ACTIVE' if has_alive else 'MISSING -> run [6]'}")
        rc, out = run(["reg", "query", r"HKLM\SOFTWARE\WinFsp", "/reg:32",
                       "/v", "SxsDir"], timeout=20)
        if rc == 0:
            print("  WinFsp        : " + out.splitlines()[-1].split("REG_SZ")[-1].strip())
    else:
        sshfs = find_sshfs()
        print(f"  sshfs         : {sshfs or 'NOT FOUND'}")
        if IS_MAC:
            print(f"  macFUSE       : {'present' if mac_has_macfuse() else 'ABSENT'}")

    print(f"  Auto-start    : {'ON' if autostart_is_on() else 'off'}")
    print(f"  Log file      : {LOG_FILE}")
    print("  -------------------------------------------------------------\n")
    return True


# =============================================================================
#  BOOT MODE  —  what runs automatically at login
#
#  Two rules learned from the old watchdog:
#
#    1. WAIT for the network. At login, Wi-Fi and VPN are not up yet. The old
#       script mounted immediately, failed, and then deleted its own config.
#
#    2. Try, then STOP. No infinite loop. The old watchdog polled every 30 s
#       forever, in a console window, for nothing: its config folder was empty.
#       If the network is not there after a few minutes, we log it and quit.
#       The user mounts manually when they need it.
# =============================================================================

def wait_for_network(cfg: dict, max_wait: int = 300) -> bool:
    """Wait for the SFTP port to answer. Returns False on timeout."""
    import socket
    host, port = cfg["host"], int(cfg["sftp_port"])
    deadline = time.time() + max_wait
    attempt  = 0
    while time.time() < deadline:
        attempt += 1
        s = socket.socket()
        s.settimeout(5)
        try:
            s.connect((host, port))
            log(f"boot: network up after {attempt} attempt(s)")
            return True
        except Exception:
            pass
        finally:
            s.close()
        time.sleep(10)
    log(f"boot: {host}:{port} still unreachable after {max_wait}s - giving up")
    return False


def do_boot(cfg: dict) -> bool:
    """Non-interactive entry point for auto-start. Never prompts."""
    log("boot: start")

    if not cfg["password"] and cfg["auth"] != "none":
        log("boot: no credential - abort")
        return False

    st = status(cfg)
    if st == HEALTHY:
        log("boot: already mounted and healthy - nothing to do")
        return True

    if not wait_for_network(cfg):
        return False

    if st == ZOMBIE:
        log("boot: zombie mount detected - cleaning up first")
        do_unmount(cfg)
        time.sleep(2)

    ok = do_mount(cfg)
    log(f"boot: end ok={ok}")
    return ok


# =============================================================================
#  AUTO-START AT LOGIN
#
#  Windows: a .vbs in the Startup folder, NOT a scheduled task.
#  The old task used `schtasks /rl highest`, i.e. elevated. A network drive
#  mapped from an elevated session is INVISIBLE to the normal user session:
#  the mount "succeeds" and W: does not exist for the user. The Startup folder
#  runs in the right session, with the right token.
# =============================================================================

WIN_STARTUP_NAME = "FieldClip_AutoMount.vbs"
MAC_PLIST_LABEL  = "io.github.fieldclip.mount"
LINUX_DESKTOP    = "fieldclip-mount.desktop"


def _win_startup_path() -> str:
    return os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
        WIN_STARTUP_NAME)


def _mac_plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{MAC_PLIST_LABEL}.plist")


def _linux_desktop_path() -> str:
    return os.path.expanduser(f"~/.config/autostart/{LINUX_DESKTOP}")


def autostart_path() -> str:
    if IS_WIN:
        return _win_startup_path()
    if IS_MAC:
        return _mac_plist_path()
    return _linux_desktop_path()


def autostart_is_on() -> bool:
    return os.path.exists(autostart_path())


def autostart_enable(cfg: dict) -> bool:
    py     = sys.executable or "python3"
    script = os.path.join(HERE, "remote_mount.py")
    path   = autostart_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if IS_WIN:
        # Run 0 = hidden window, False = do not wait. No console flash at login.
        vbs = (
            "' FieldClip - auto-mount at login\n"
            "' Generated by remote_mount.py - delete this file to disable.\n"
            'Set sh = CreateObject("WScript.Shell")\n'
            'sh.Run """{py}"" ""{script}"" boot", 0, False\n'
        ).format(py=py, script=script)
        with open(path, "w", encoding="utf-8") as f:
            f.write(vbs)

    elif IS_MAC:
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{MAC_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py}</string>
        <string>{script}</string>
        <string>boot</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>StandardOutPath</key><string>{LOG_FILE}</string>
    <key>StandardErrorPath</key><string>{LOG_FILE}</string>
</dict>
</plist>
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(plist)
        run(["launchctl", "unload", path], timeout=20)
        rc, out = run(["launchctl", "load", "-w", path], timeout=20)
        if rc != 0:
            print(f"  ! launchctl load: {out}")

    else:
        desktop = f"""[Desktop Entry]
Type=Application
Name=FieldClip auto-mount
Comment=Mounts the FieldClip remote drive at login
Exec={py} "{script}" boot
Terminal=false
X-GNOME-Autostart-enabled=true
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(desktop)
        os.chmod(path, 0o755)

    print("  OK - auto-start ENABLED.")
    print(f"       {path}")
    print("\n  At login it waits for the network (up to 5 min), mounts, then quits.")
    print("  No console window, no permanent background loop.")
    log(f"autostart enabled: {path}")
    return True


def autostart_disable(cfg: dict) -> bool:
    path = autostart_path()
    if not os.path.exists(path):
        print("  Auto-start was already off.")
        return True
    if IS_MAC:
        run(["launchctl", "unload", "-w", path], timeout=20)
    try:
        os.remove(path)
        print("  OK - auto-start disabled.")
        log(f"autostart disabled: {path}")
        return True
    except Exception as e:
        print(f"  Could not remove {path}: {e}")
        return False


# =============================================================================
#  Interactive menu (double-click)
# =============================================================================

BANNER = r"""
  +------------------------------------------------------+
  |          FieldClip  --  Remote drive manager         |
  +------------------------------------------------------+
"""

STATE_HINT = {
    HEALTHY: "mounted, responding normally",
    ABSENT:  "nothing is mounted",
    ZOMBIE:  "mounted but BLOCKED -> use [3] Remount",
}


def menu(cfg: dict) -> None:
    while True:
        st = status(cfg)
        print(BANNER)
        print(f"   Server      : {cfg['name']}  ({cfg['host'] or 'host not set in the profile'})")
        print(f"   {cfg['secret_label']:<11} : {S.mask(cfg['password'])}")
        print(f"   Mount point : {mountpoint(cfg)}")
        print(f"   State       : {st}  -- {STATE_HINT[st]}")
        print(f"   Auto-start  : {'ON - mounts at every login' if autostart_is_on() else 'off'}")
        print()
        print(f"   [1] Mount {cfg['name']}")
        print("   [2] Unmount")
        print("   [3] Remount  (fixes a blocked mount)")
        print("   [4] Open in file manager")
        print("   [5] Diagnostics")
        if IS_WIN:
            print("   [6] Repair Windows config  (needs admin)")
        print("   [7] Enable auto-start at login")
        print("   [8] Disable auto-start")
        print(f"   [9] Change {cfg['secret_label'].lower()}")
        print("   [0] Quit")
        print()

        try:
            choice = input("   Your choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            return

        print()
        if   choice == "1": do_mount(cfg)
        elif choice == "2": do_unmount(cfg)
        elif choice == "3": do_remount(cfg)
        elif choice == "4": do_open(cfg)
        elif choice == "5": do_doctor(cfg)
        elif choice == "6" and IS_WIN: win_repair_registry(cfg)
        elif choice == "7": autostart_enable(cfg)
        elif choice == "8": autostart_disable(cfg)
        elif choice == "9":
            new = input(f"   New {cfg['secret_label'].lower()} (Enter to cancel): ").strip()
            if new:
                save_credential(cfg, new)
                print("   Saved in your user settings.")
        elif choice == "0":
            return
        else:
            print("   Invalid choice.")

        input("\n   -- Press Enter to return to the menu --")
        print("\n" * 2)


# =============================================================================
#  Entry point
# =============================================================================

USAGE = """Usage (python -m fieldclip.remote_mount ...):
  (no argument)       interactive menu
  status [--json]     mount state
  mount               mount
  unmount             unmount
  remount             unmount then mount
  boot                wait for network then mount (used at login)
  autostart on        mount automatically at every login
  autostart off       disable it
  doctor              diagnostics
  repair              fix the WinFsp config (Windows, admin)
  path                print the mount point
"""


def main(argv) -> int:
    cfg = load_config()

    if not argv:
        if not cfg["host"]:
            print("  The active profile has no remote.mount.host - see docs/configuration.md.")
        if not cfg["password"] and cfg["auth"] != "none":
            secret = input(f"  {cfg['secret_label']}: ").strip()
            if secret:
                save_credential(cfg, secret)
        menu(cfg)
        return 0

    cmd = argv[0].lower()

    if cmd in ("mount", "remount", "boot", "autostart") and not cfg["host"]:
        print("  The active profile has no remote.mount.host: nothing to mount.\n"
              "  Pick a profile with a server (--profile NAME) or see docs/configuration.md.")
        return 2

    if cmd == "status":
        st = status(cfg)
        if "--json" in argv:
            print(json.dumps({"state": st, "path": mountpoint(cfg),
                              "credential_set": bool(cfg["password"])}))
        else:
            print(st)
        return 0 if st == HEALTHY else 1

    if cmd == "path":
        print(mountpoint(cfg));                    return 0
    if cmd == "mount":
        return 0 if do_mount(cfg) else 1
    if cmd == "unmount":
        return 0 if do_unmount(cfg) else 1
    if cmd == "remount":
        return 0 if do_remount(cfg) else 1
    if cmd == "boot":
        return 0 if do_boot(cfg) else 1
    if cmd == "autostart":
        arg = (argv[1].lower() if len(argv) > 1 else "status")
        if arg in ("on", "enable"):
            return 0 if autostart_enable(cfg) else 1
        if arg in ("off", "disable"):
            return 0 if autostart_disable(cfg) else 1
        print("ON" if autostart_is_on() else "off")
        return 0 if autostart_is_on() else 1
    if cmd == "doctor":
        do_doctor(cfg);                            return 0
    if cmd == "repair":
        return 0 if win_repair_registry(cfg) else 1

    print(USAGE)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        sys.exit(130)

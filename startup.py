"""Start menu entry and start-with-Windows for Agent Watch (per-user, no admin needed).

    python startup.py app | unapp           Start menu entry + start with Windows (or remove both)
    python startup.py on | off | status     start with Windows only

Start with Windows is a shortcut named "Agent Watch" in your Startup folder, which is what
Settings > Apps > Startup lists (with the Agent Watch name and icon).
"""
import os
import subprocess
import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # older versions used this
RUN_NAME = "AgentWatch"
SCRIPT = Path(__file__).with_name("agent_watch.py").resolve()
ICON = SCRIPT.with_name("assets") / "agent-watch.ico"


def _programs():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def menu_path():
    return _programs() / "Agent Watch.lnk"


def startup_path():
    return _programs() / "Startup" / "Agent Watch.lnk"


def launcher():
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return pythonw if pythonw.exists() else exe


def make_shortcut(lnk):
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:AW_LNK);"
          "$s.TargetPath=$env:AW_EXE;$s.Arguments=$env:AW_ARGS;$s.WorkingDirectory=$env:AW_DIR;"
          "$s.IconLocation=$env:AW_ICON;"
          "$s.Description='Always-on-top status for your AI coding agents';$s.Save()")
    env = dict(os.environ, AW_LNK=str(lnk), AW_EXE=str(launcher()), AW_ARGS=f'"{SCRIPT}"',
               AW_DIR=str(SCRIPT.parent), AW_ICON=str(ICON))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True,
                   creationflags=0x08000000)


def points_here(lnk):
    """Does this shortcut launch this copy? (Shortcuts store their target path as plain text.)"""
    try:
        data = lnk.read_bytes()
    except OSError:
        return False
    s = str(SCRIPT)
    return s.encode("utf-16le") in data or s.encode("mbcs", "ignore") in data


# -- Start menu --------------------------------------------------------------
def has_shortcut():
    return menu_path().exists()


def add_shortcut():
    make_shortcut(menu_path())


def remove_shortcut():
    menu_path().unlink(missing_ok=True)


# -- start with Windows ------------------------------------------------------
def _legacy_run():
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0]
    except OSError:
        return None


def _drop_legacy_run():
    if winreg is None:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_NAME)
    except OSError:
        pass


def is_enabled():
    return startup_path().exists() or _legacy_run() is not None


def enable():
    make_shortcut(startup_path())
    _drop_legacy_run()


def disable():
    startup_path().unlink(missing_ok=True)
    _drop_legacy_run()


def refresh():
    """Keep the boot entry healthy: move old registry entries to the Startup folder, and repoint
    it if this folder moved."""
    if is_enabled() and (_legacy_run() is not None or not points_here(startup_path())):
        enable()
    if has_shortcut() and not points_here(menu_path()):
        add_shortcut()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "on":
        enable()
    elif action == "off":
        disable()
    elif action == "app":
        add_shortcut()
        enable()
    elif action == "unapp":
        remove_shortcut()
        disable()
    print("Start menu:", "yes" if has_shortcut() else "no", "| starts with Windows:",
          "yes" if is_enabled() else "no")

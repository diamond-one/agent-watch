"""Start Agent Watch when you sign in to Windows (per-user, no admin needed).

    python startup.py on | off | status | app | unapp

It writes one value under HKCU\\...\\Run, which is what Settings > Apps > Startup shows.
"""
import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "AgentWatch"
SCRIPT = Path(__file__).with_name("agent_watch.py").resolve()


def command():
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return f'"{pythonw if pythonw.exists() else exe}" "{SCRIPT}"'


def current():
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            return winreg.QueryValueEx(key, NAME)[0]
    except OSError:
        return None


def is_enabled():
    return current() is not None


def enable():
    if winreg is None:
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
        winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())


def disable():
    if winreg is None:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, NAME)
    except OSError:
        pass


def shortcut_path():
    import os
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Agent Watch.lnk"


def has_shortcut():
    return shortcut_path().exists()


def add_shortcut():
    """A Start menu entry ("Agent Watch") that launches the widget with its own icon."""
    import subprocess
    exe = command().split('" "')[0].strip('"')
    icon = SCRIPT.with_name("assets") / "agent-watch.ico"
    lnk = shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:AW_LNK);"
        "$s.TargetPath=$env:AW_EXE;$s.Arguments=$env:AW_ARGS;"
        "$s.WorkingDirectory=$env:AW_DIR;$s.IconLocation=$env:AW_ICON;"
        "$s.Description='Always-on-top status for your AI coding agents';$s.Save()"
    )
    import os
    env = dict(os.environ, AW_LNK=str(lnk), AW_EXE=exe, AW_ARGS=f'"{SCRIPT}"',
               AW_DIR=str(SCRIPT.parent), AW_ICON=str(icon))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True,
                   creationflags=0x08000000)


def remove_shortcut():
    try:
        shortcut_path().unlink()
    except OSError:
        pass


def refresh():
    """If it's on but points somewhere else (the folder moved), repoint it here."""
    if is_enabled() and current() != command():
        enable()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "on":
        enable()
    elif action == "off":
        disable()
    elif action == "app":  # Start menu entry + start with Windows
        add_shortcut()
        enable()
    elif action == "unapp":
        remove_shortcut()
        disable()
    print("starts with Windows:", "yes" if is_enabled() else "no", "|", current() or "")

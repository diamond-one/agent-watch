"""Start Agent Watch when you sign in to Windows (per-user, no admin needed).

    python startup.py on | off | status

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
    print("starts with Windows:", "yes" if is_enabled() else "no", "|", current() or "")

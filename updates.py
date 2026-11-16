"""Is there a newer Agent Watch? Compares the VERSION file here with the one on GitHub."""
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "diamond-one/agent-watch"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/VERSION"
PAGE = f"https://github.com/{REPO}"


def local_version():
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


def parse(v):
    out = []
    for part in str(v).strip().lstrip("v").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def newer(latest, current):
    return bool(latest) and parse(latest) > parse(current)


def fetch_latest(timeout=6):
    """The newest version on GitHub's main branch, or None if it can't be reached."""
    try:
        req = urllib.request.Request(RAW, headers={"User-Agent": "agent-watch-update-check"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read(64).decode("utf-8", "ignore").strip()
        return text if text and parse(text) != (0,) else None
    except Exception:
        return None


def is_git_clone():
    return (ROOT / ".git").exists() and shutil.which("git") is not None


def pull():
    """Fast-forward this copy to GitHub. Returns (ok, message)."""
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only"], capture_output=True,
                           text=True, timeout=60, creationflags=0x08000000)
        return r.returncode == 0, (r.stdout + r.stderr).strip()[-200:]
    except Exception as exc:
        return False, repr(exc)

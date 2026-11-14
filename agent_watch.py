"""Agent Watch: a small always-on-top panel listing your running AI coding agents.

Works with Claude Code and OpenAI Codex (CLI and desktop apps) through hooks.
Click the header to expand. Per agent you can see its step-by-step activity,
allow/deny pending permission requests, jump to the app, or dismiss it.
The cog opens settings (sound, position, opacity, pin).
"""
import ctypes
import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import startup  # noqa: E402
from watch_hook import clean_text, last_assistant_entry  # noqa: E402

try:
    import winsound
except ImportError:  # not on Windows
    winsound = None

HOME = Path(os.environ.get("AGENT_WATCH_DIR", Path.home() / ".agent-watch"))
SESSIONS = HOME / "sessions"
REQUESTS = HOME / "requests"
CONFIG = HOME / "config.json"
STALE_SECONDS = 6 * 3600

# Graphite surfaces, hairline border, one accent per status.
BORDER, BG, CARD, CARD_HOVER = "#2b2b33", "#131316", "#1c1c21", "#25252c"
FG, DIM, FAINT = "#ececf1", "#8d8d99", "#5c5c68"
COLORS = {"running": "#b8f23a", "waiting": "#ff9f43", "done": "#4cd694", "idle": "#6b6b78"}
LABELS = {"running": "RUNNING", "waiting": "NEEDS YOU", "done": "DONE", "idle": "IDLE"}
UI, MONO = "Segoe UI", "Consolas"
WIDTH = 340
MARGIN = 20  # gap to the usable screen edge (the taskbar is excluded)

DEFAULTS = {
    "pinned": True,
    "sound_finish": True,
    "sound_attention": True,
    "sound_name": "Chime",
    "opacity": 0.97,
    "auto_expand": True,
    "peek_finish": True,  # briefly expand when an agent finishes
    "peek_seconds": 3.0,
    "hotkey": {"ctrl": True, "alt": False, "shift": False, "vk": 0xC0},  # Ctrl+`
    "jump_on_finish": False,  # force focus: switch to the agent's chat when it finishes
    "collapsed": [],  # ids of agents folded to a single row
    "anchor": "br",  # tl | tc | tr | bl | bc | br | free
    "x": None,  # free spot: coordinate of the anchored edge
    "y": None,
    "ax": "l",  # free spot: which horizontal edge is fixed (l | r)
    "ay": "t",  # free spot: which vertical edge is fixed (t | b)
}
SOUND_FILES = {
    "Chime": "chimes.wav",
    "Ding": "Ding.wav",
    "Notify": "Windows Notify System Generic.wav",
    "Calendar": "Windows Notify Calendar.wav",
    "Tada": "tada.wav",
}
MEDIA = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT),
                ("dwFlags", wintypes.DWORD)]


user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE


def work_area(x, y):
    """(left, top, right, bottom) of the usable area, taskbar excluded, of the monitor at x,y."""
    try:
        hmon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # nearest monitor
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


# --- anti-aliased shapes ----------------------------------------------------
# Tk canvas primitives are jagged, so small controls are drawn into images instead.
def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(c):
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in c)


def sdf_box(cx, cy, hw, hh, r):
    def f(px, py):
        qx, qy = abs(px - cx) - (hw - r), abs(py - cy) - (hh - r)
        return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    return f


def sdf_circle(cx, cy, r):
    return lambda px, py: math.hypot(px - cx, py - cy) - r


def sdf_stroke(points, width):
    segs = list(zip(points, points[1:]))

    def f(px, py):
        best = 1e9
        for (x1, y1), (x2, y2) in segs:
            dx, dy = x2 - x1, y2 - y1
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)))
        return best - width / 2
    return f


_IMAGES = {}


def aa_image(key, w, h, bg, layers):
    """Paint (colour, sdf) layers over bg with 1px soft edges. Cached by key."""
    if key in _IMAGES:
        return _IMAGES[key]
    img = tk.PhotoImage(width=w, height=h)
    base, rows = _rgb(bg), []
    for y in range(h):
        row = []
        for x in range(w):
            col = base
            for colour, sdf in layers:
                a = min(1.0, 0.5 - sdf(x + 0.5, y + 0.5))
                if a > 0:
                    c = _rgb(colour)
                    col = tuple(col[i] + (c[i] - col[i]) * a for i in range(3))
            row.append(_hex(col))
        rows.append("{" + " ".join(row) + "}")
    img.put(" ".join(rows))
    _IMAGES[key] = img
    return img


# --- config ---------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg), encoding="utf-8")
    except Exception:
        pass


def available_sounds():
    names = [n for n, f in SOUND_FILES.items() if (MEDIA / f).exists()]
    return names or ["Chime"]


def play_sound(name):
    if winsound is None:
        return
    path = MEDIA / SOUND_FILES.get(name, "")
    try:
        if path.is_file():
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass


# --- global shortcut ---------------------------------------------------------
DEFAULT_HOTKEY = dict(DEFAULTS["hotkey"])
VK_NAMES = {0xC0: "`", 0x20: "Space", 0xBD: "-", 0xBB: "=", 0xBC: ",", 0xBE: ".", 0xBF: "/",
            0xBA: ";", 0xDE: "'", 0xDB: "[", 0xDD: "]", 0xDC: "\\", 0x09: "Tab"}


def vk_name(vk):
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return VK_NAMES.get(vk, f"Key {vk}")


def hotkey_label(hk):
    if not hk or not hk.get("vk"):
        return "Off"
    mods = [n for n, k in (("Ctrl", "ctrl"), ("Alt", "alt"), ("Shift", "shift")) if hk.get(k)]
    return " + ".join(mods + [vk_name(hk["vk"])])


class Hotkey:
    """A system-wide shortcut. RegisterHotKey is bound to the thread that pumps its messages,
    so each registration lives on its own small thread."""

    WM_HOTKEY, WM_QUIT, NOREPEAT = 0x0312, 0x0012, 0x4000
    MODS = {"alt": 1, "ctrl": 2, "shift": 4}

    def __init__(self):
        self.pressed = queue.Queue()
        self.thread = None
        self.tid = None
        self.ok = None  # None: no shortcut set, True: registered, False: another app owns it

    def start(self, hk):
        self.stop()
        self.ok = None
        if not hk or not hk.get("vk"):
            return
        mods = sum(v for k, v in self.MODS.items() if hk.get(k)) | self.NOREPEAT
        ready = threading.Event()

        def run():
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create this thread's queue
            self.tid = kernel32.GetCurrentThreadId()
            self.ok = bool(user32.RegisterHotKey(None, 1, mods, hk["vk"]))
            ready.set()
            if not self.ok:
                return
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == self.WM_HOTKEY:
                    self.pressed.put(1)
            user32.UnregisterHotKey(None, 1)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(1.0)

    def stop(self):
        if self.thread and self.thread.is_alive() and self.tid:
            user32.PostThreadMessageW(self.tid, self.WM_QUIT, 0, 0)
            self.thread.join(1.0)
        self.thread = None


# --- sessions -------------------------------------------------------------
def read_sessions():
    out = []
    SESSIONS.mkdir(parents=True, exist_ok=True)
    for p in SESSIONS.glob("*.json"):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if time.time() - s.get("updated", 0) > STALE_SECONDS:
            p.unlink(missing_ok=True)
            continue
        out.append(s)
    order = {"waiting": 0, "running": 1, "done": 2, "idle": 3}
    out.sort(key=lambda s: (order.get(s.get("status"), 9), -s.get("updated", 0)))
    return out


# --- what the agent is saying ----------------------------------------------
_SAY = {}  # transcript path -> (text, size, scanned_at)


def narration(s):
    """The latest thing the agent said this turn (what it's doing, in its own words)."""
    source = s.get("source", "claude")
    if source == "cursor":
        return s.get("summary", "")
    path = s.get("transcript")
    if source != "claude" or not path:
        return ""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    text, last_size, scanned = _SAY.get(path, ("", -1, 0.0))
    if size == last_size or time.time() - scanned < 1.5:
        return text
    said, when = last_assistant_entry(path)
    started = s.get("turn_started", 0)
    # ignore anything the agent said in an earlier turn
    text = clean_text(said, 240) if said and when >= started - 2 else ""
    _SAY[path] = (text, size, time.time())
    return text


def nice_step(step):
    """A tool step as a short readable line; '' for the app's own plumbing tools."""
    name, _, detail = step.partition(":")
    if name.startswith("mcp__ccd_"):
        return ""
    name = name.split("__")[-1]
    return f"{name}:{detail}" if detail else name


def latest_step(s):
    for step in reversed(s.get("steps", [])):
        shown = nice_step(step)
        if shown:
            return shown
    return ""


# --- choices the agent is waiting on ----------------------------------------
_CHOICES = {}  # transcript path -> (choices, size, scanned_at)


def _tail_lines(path, size=400_000):
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - size))
        return fh.read().decode("utf-8", "ignore").splitlines()


def pending_choices(s):
    """The question and reply buttons the agent ended its turn with (its wrap_up call), as long
    as nobody has answered yet. Returns {"ask": str, "actions": [{"label", "text"}]} or None."""
    path = s.get("transcript")
    if s.get("source", "claude") != "claude" or not path:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    found, last_size, scanned = _CHOICES.get(path, (None, -1, 0.0))
    if size == last_size or time.time() - scanned < 1.5:
        return found
    found = None
    try:
        for line in reversed(_tail_lines(path)):
            try:
                entry = json.loads(line)
            except Exception:
                continue
            msg = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
            if entry.get("type") == "user":
                typed = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
                if any(t.strip() and not t.lstrip().startswith("<") for t in typed):
                    break  # a person has replied since: nothing is pending
            elif entry.get("type") == "assistant":
                for b in reversed(blocks):
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and str(b.get("name", "")).endswith("wrap_up"):
                        inp = b.get("input") or {}
                        acts = [{"label": a["label"], "text": a.get("message") or a["label"]}
                                for a in inp.get("actions") or [] if isinstance(a, dict) and a.get("label")]
                        if acts:
                            found = {"ask": inp.get("ask", ""), "actions": acts}
                        raise StopIteration
                    if b.get("type") in ("tool_use", "text"):
                        raise StopIteration  # the agent carried on after any question
    except StopIteration:
        pass
    except Exception:
        found = None
    _CHOICES[path] = (found, size, time.time())
    return found


# --- session titles ---------------------------------------------------------
_TITLES = {}  # transcript path -> (title, time of last scan)


def session_title(s):
    """The title Claude gives a session (shown in its own app), read from the transcript tail."""
    path = s.get("transcript")
    if s.get("source", "claude") != "claude" or not path:
        return ""
    title, scanned = _TITLES.get(path, ("", 0.0))
    if time.time() - scanned < (60 if title else 8):  # cheap: rescan rarely
        return title
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 600_000))
            tail = fh.read().decode("utf-8", "ignore").splitlines()
        for line in reversed(tail):
            if '"custom-title"' in line:
                try:
                    found = json.loads(line).get("customTitle")
                except Exception:
                    continue
                if found:
                    title = found
                    break
    except Exception:
        pass
    _TITLES[path] = (title, time.time())
    return title


def short(text, n):
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# --- jump to app ----------------------------------------------------------
def _exe_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def _windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(enum, 0)
    return found


BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}


def _matches(source, title, exe):
    t = title.lower()
    if source == "codex":
        if exe in {"chatgpt.exe", "codex.exe"}:
            return True
        return exe not in BROWSERS and ("chatgpt" in t or "codex" in t)
    if source == "cursor":
        return exe == "cursor.exe"
    return exe == "claude.exe" and "claude" in t


def raise_window(hwnd, plain=False):
    """Bring a window to the front. Windows blocks SetForegroundWindow from a process that
    doesn't own the foreground (our borderless panel never takes focus when clicked), so
    borrow the foreground thread's input state and tap Alt first."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if plain:
        return bool(user32.SetForegroundWindow(hwnd))
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
    try:
        user32.keybd_event(0x12, 0, 0, 0)  # Alt down/up unlocks the foreground switch
        user32.keybd_event(0x12, 0, 2, 0)
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)
    return ok


def log(msg):
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with open(HOME / "agent-watch.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


_LOCAL_IDS = {}  # hook (CLI) session id -> the Claude desktop app's local_ session id
_LOCAL_SCAN = [0.0]


def local_session_id(cli_id):
    """Claude desktop keeps one record per Code session; map a hook session id to its app id."""
    if cli_id in _LOCAL_IDS:
        return _LOCAL_IDS[cli_id]
    if time.time() - _LOCAL_SCAN[0] < 3:  # don't rescan the disk on every click
        return None
    _LOCAL_SCAN[0] = time.time()
    roots = [Path(os.environ.get("APPDATA", "")) / "Claude"]
    roots += list((Path(os.environ.get("LOCALAPPDATA", "")) / "Claude-Data").glob("*"))
    for root in roots:
        for p in (root / "claude-code-sessions").rglob("local_*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for key in ("cliSessionId", "lastAssistantCliSessionId"):
                if rec.get(key) and rec.get("sessionId"):
                    _LOCAL_IDS[rec[key]] = rec["sessionId"]
    return _LOCAL_IDS.get(cli_id)


def open_claude_session(s):
    """Ask the Claude desktop app to show this agent's own chat (its claude:// deep link)."""
    cli_id = s["id"].split("-", 1)[1] if "-" in s.get("id", "") else ""
    local = local_session_id(cli_id)
    if not local:
        log(f"jump: no app session found for {cli_id}")
        return False
    os.startfile(f"claude://code/continue?session={local}&source=desktop_action")
    log(f"jump: opened {local}")
    return True


def send_to_chat(root, s, text):
    """Reply in the agent's own chat: open it, click the composer, paste, press Enter.
    (The app has no link that sends a message, so this drives it like a person would.)"""
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()

    def run():
        try:
            open_claude_session(s)
            time.sleep(0.8)
            target = next((h for h, t in _windows() if _matches("claude", t, _exe_name(h))), None)
            if not target:
                log("send: Claude window not found")
                return
            raise_window(target)
            time.sleep(0.4)
            r = wintypes.RECT()
            user32.GetWindowRect(target, ctypes.byref(r))
            x, y = (r.left + r.right) // 2, r.bottom - int((r.bottom - r.top) * 0.05)  # the composer
            here = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(here))
            user32.SetCursorPos(x, y)
            time.sleep(0.15)
            user32.mouse_event(2, 0, 0, 0, 0)  # click into the composer
            user32.mouse_event(4, 0, 0, 0, 0)
            time.sleep(0.25)
            for vk in (0x11, 0x56):  # Ctrl+V
                user32.keybd_event(vk, 0, 0, 0)
            for vk in (0x56, 0x11):
                user32.keybd_event(vk, 0, 2, 0)
            time.sleep(0.3)
            user32.keybd_event(0x0D, 0, 0, 0)  # Enter
            user32.keybd_event(0x0D, 0, 2, 0)
            user32.SetCursorPos(here.x, here.y)
            log(f"send: replied {text!r}")
        except Exception as exc:
            log(f"send: failed {exc!r}")

    threading.Thread(target=run, daemon=True).start()


def focus_app(source, session=None):
    """Bring the desktop app that owns this agent (Claude, ChatGPT/Codex, Cursor) to the front,
    and for Claude open that agent's own chat."""
    try:
        if source == "claude" and session:
            try:
                if open_claude_session(session):
                    time.sleep(0.4)  # let the app switch before we raise the window
            except Exception as exc:
                log(f"jump: deep link failed {exc!r}")
        wins = _windows()
        for hwnd, title in wins:
            if "agent watch" in title.lower():
                continue
            if _matches(source, title, _exe_name(hwnd)):
                ok = raise_window(hwnd)
                time.sleep(0.15)
                now = user32.GetForegroundWindow()
                log(f"jump {source}: window {hex(hwnd)} {title!r} raise={ok} foreground_now={hex(now)} "
                    f"({_exe_name(now)}) success={now == hwnd}")
                return True
        log(f"jump {source}: no matching window among {len(wins)} visible windows")
    except Exception as exc:  # a click must never fail silently
        log(f"jump {source}: error {exc!r}")
    return False


# --- widget ---------------------------------------------------------------
class AgentWatch:
    def __init__(self):
        self.cfg = load_config()
        self.root = tk.Tk()
        self.root.title("Agent Watch")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.cfg["pinned"])
        self.root.attributes("-alpha", self.cfg["opacity"])
        self.root.configure(bg=BORDER)  # 1px hairline: border colour shows around inner frame
        self.expanded = False
        self.show_settings = False
        self.open_cards = set()
        self.collapsed = set(self.cfg.get("collapsed", []))  # agents folded to one row
        self.capturing = False  # recording a new shortcut
        self.answered = set()  # (session, question) pairs already replied to from here
        self.capture_msg = ""
        self.hotkey = Hotkey()
        self.sig = None
        self.prev_status = None  # None until the first read, so startup is silent
        self.peek_job = None
        self.peek_collapse = False  # True while a finish-peek opened the panel
        self.peek_hovered = False
        self.peek_cards = set()
        self._dx = self._dy = 0
        self._moved = False
        self.pulse_dots = []
        self.pulse_on = True
        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag)
        self.root.bind("<ButtonRelease-1>", self._drag_end, add="+")
        self.root.update_idletasks()
        self._round_corners()
        try:
            startup.refresh()  # keep the boot entry pointing at this copy if the folder moved
        except Exception:
            pass
        self.hotkey.start(self.cfg.get("hotkey"))
        self._poll_hotkey()
        self.tick()
        self.pulse()

    # --- config helpers -------------------------------------------------
    def set_cfg(self, **kw):
        self.cfg.update(kw)
        save_config(self.cfg)
        self.root.attributes("-topmost", self.cfg["pinned"])
        self.root.attributes("-alpha", self.cfg["opacity"])
        self.sig = None

    def save_opacity(self, pct):
        self.cfg["opacity"] = int(pct) / 100
        save_config(self.cfg)

    @staticmethod
    def own_input(widget):
        """Drop the toplevel bindtag so clicks/drags on this widget don't drag the window."""
        widget.bindtags(tuple(t for t in widget.bindtags() if t != widget.winfo_toplevel()._w))

    def _round_corners(self):
        try:  # Windows 11: ask DWM for rounded corners on the borderless window
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:
            pass

    # --- dragging -------------------------------------------------------
    DRAG_THRESHOLD = 5  # px; smaller pointer wobble during a click is still a click

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._press = (e.x_root, e.y_root)
        self._moved = False
    def _drag(self, e):
        if not self._moved:
            px, py = self._press
            if math.hypot(e.x_root - px, e.y_root - py) < self.DRAG_THRESHOLD:
                return
            self._moved = True
        x, y = e.x_root - self._dx, e.y_root - self._dy
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, e):
        if self._moved:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            left, top, right, bottom = work_area(x + w // 2, y + h // 2)
            ax = "r" if x + w / 2 > (left + right) / 2 else "l"
            ay = "b" if y + h / 2 > (top + bottom) / 2 else "t"
            self.set_cfg(anchor="free", ax=ax, ay=ay,
                         x=x + w if ax == "r" else x, y=y + h if ay == "b" else y)

    # --- loop -----------------------------------------------------------
    def tick(self):
        sessions = read_sessions()
        self.detect_changes(sessions)
        live = {s["id"] for s in sessions}
        if not self.collapsed <= live:  # forget agents that are gone
            self.set_collapsed(self.collapsed & live)
        sig = json.dumps([self.expanded, self.show_settings, self.cfg, sorted(self.open_cards),
                          self.capturing, self.capture_msg, self.hotkey.ok, sessions], sort_keys=True)
        if sig != self.sig:
            self.sig = sig
            self.render(sessions)
        self.root.after(700, self.tick)

    def detect_changes(self, sessions):
        current = {s["id"]: s.get("status") for s in sessions}
        if self.prev_status is not None:
            for sid, status in current.items():
                old = self.prev_status.get(sid)
                if status == "done" and old in ("running", "waiting"):
                    if self.cfg["sound_finish"]:
                        play_sound(self.cfg["sound_name"])
                    if self.cfg["peek_finish"]:
                        self.start_peek(sid)
                    if self.cfg["jump_on_finish"]:
                        done = next((x for x in sessions if x["id"] == sid), None)
                        if done:
                            focus_app(done.get("source", "claude"), done)
                elif status == "waiting" and old != "waiting":
                    if sid in self.collapsed:  # a request must never hide inside a folded card
                        self.set_collapsed(self.collapsed - {sid})
                    if self.cfg["sound_attention"]:
                        play_sound(self.cfg["sound_name"])
                    if self.cfg["auto_expand"] and not self.expanded:
                        self.expanded = True
                    self.cancel_peek()  # keep the request visible
        self.prev_status = current

    # --- finish peek ------------------------------------------------------
    def start_peek(self, sid):
        """Open the panel (with that agent's steps) for 3 seconds, then minimize again."""
        if self.expanded and not self.peek_collapse:
            return  # the user already has it open; leave it alone
        if self.peek_job:
            self.root.after_cancel(self.peek_job)
        self.expanded = True
        self.show_settings = False
        self.peek_collapse = True
        self.peek_job = self.root.after(int(self.cfg["peek_seconds"] * 1000), self.end_peek)
        self.sig = None

    def pointer_over(self):
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        r = self.root
        return (r.winfo_rootx() <= pt.x < r.winfo_rootx() + r.winfo_width()
                and r.winfo_rooty() <= pt.y < r.winfo_rooty() + r.winfo_height())

    def end_peek(self):
        # Don't fold the panel away while the pointer is on it; once it leaves, wait a beat.
        if self.peek_collapse:
            if self.pointer_over():
                self.peek_hovered = True
                self.peek_job = self.root.after(250, self.end_peek)
                return
            if self.peek_hovered:
                self.peek_hovered = False
                self.peek_job = self.root.after(800, self.end_peek)
                return
        if self.peek_collapse:
            self.expanded = False
            self.open_cards -= self.peek_cards
        self.peek_cards.clear()
        self.peek_collapse = False
        self.peek_job = None
        self.sig = None

    def cancel_peek(self):
        """The user (or a permission request) took over: stop the auto-minimize."""
        if self.peek_job:
            self.root.after_cancel(self.peek_job)
        self.peek_job = None
        self.peek_collapse = False
        self.peek_hovered = False
        self.peek_cards.clear()

    def pulse(self):
        self.pulse_on = not self.pulse_on
        for dot in self.pulse_dots:
            try:
                dot.configure(fg=COLORS["running"] if self.pulse_on else "#5d7a1c")
            except tk.TclError:
                pass
        self.root.after(650, self.pulse)

    # --- widgets --------------------------------------------------------
    def chip(self, parent, text, cmd, fg=DIM, bg=CARD, hover=CARD_HOVER, font=None, padx=8, pady=2):
        b = tk.Label(parent, text=text, fg=fg, bg=bg, cursor="hand2", padx=padx, pady=pady,
                     font=font or (UI, 8))
        b.bind("<Enter>", lambda e: b.configure(bg=hover, fg=FG))
        b.bind("<Leave>", lambda e: b.configure(bg=bg, fg=fg))
        b.bind("<ButtonRelease-1>", lambda e: None if self._moved else cmd())
        return b

    # --- icon buttons -----------------------------------------------------
    @staticmethod
    def _round_rect(c, x1, y1, x2, y2, r, fill):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return c.create_polygon(pts, smooth=True, fill=fill, outline="")

    CHEVRONS = {  # offsets from the button centre
        "up": [(-5, 2.5), (0, -2.5), (5, 2.5)],
        "down": [(-5, -2.5), (0, 2.5), (5, -2.5)],
        "right": [(-2.5, -5), (2.5, 0), (-2.5, 5)],
    }

    def icon_button(self, parent, kind, cmd, active=False, size=28, bg=BG):
        """A real button: the drawn surface is exactly the clickable area.
        kind: cog | up | down | right."""
        c = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, cursor="hand2")
        mid = size / 2

        def paint(hover=False):
            c.delete("all")
            surface = CARD_HOVER if hover else (BORDER if active else bg)
            ink = FG if (hover or active) else DIM
            layers = [(surface, sdf_box(mid, mid, mid - 1, mid - 1, 8 if size >= 28 else 6))]
            if kind != "cog":
                pts = [(mid + dx, mid + dy) for dx, dy in self.CHEVRONS[kind]]
                layers.append((ink, sdf_stroke(pts, 2)))
            c.create_image(0, 0, anchor="nw",
                           image=aa_image(("ib", kind, size, surface, ink, bg), size, size, bg, layers))
            if kind == "cog":
                c.create_text(mid, mid, text="⚙", fill=ink, font=("Segoe UI Symbol", 13))

        paint()
        c.bind("<Enter>", lambda e: paint(True))
        c.bind("<Leave>", lambda e: paint())
        c.bind("<ButtonRelease-1>", lambda e: None if self._moved else cmd())
        return c

    def grows_up(self):
        a = self.cfg["anchor"]
        return a in ("bl", "bc", "br") or (a == "free" and self.cfg.get("ay") == "b")

    def render(self, sessions):
        for w in self.body.winfo_children():
            w.destroy()
        self.pulse_dots = []
        running = sum(1 for s in sessions if s.get("status") == "running")
        waiting = sum(1 for s in sessions if s.get("status") == "waiting")
        if waiting:
            label, state = f"{waiting} need{'s' if waiting == 1 else ''} you", "waiting"
        elif running:
            label, state = f"{running} running", "running"
        elif sessions:
            label, state = "all done", "done"
        else:
            label, state = "no agents", "idle"

        bar = tk.Frame(self.body, bg=BG)
        bar.pack(fill="x", padx=10, pady=(6, 6))
        dot = tk.Label(bar, text="●", fg=COLORS[state], bg=BG, font=(UI, 9))
        dot.pack(side="left", padx=(2, 0))
        if state == "running":
            self.pulse_dots.append(dot)
        head = tk.Label(bar, text=label, fg=FG, bg=BG, font=(UI, 10, "bold"), cursor="hand2")
        head.pack(side="left", padx=(7, 0))
        brand = tk.Label(bar, text="AGENT WATCH", fg=FAINT, bg=BG, font=(UI, 7, "bold"), cursor="hand2")
        brand.pack(side="left", padx=(10, 0))
        # the chevron points the way the panel will open (or, when open, where it folds)
        chev = self.icon_button(bar, "up" if self.expanded != self.grows_up() else "down",
                                lambda: self._toggle(None))
        chev.pack(side="right")
        cog = self.icon_button(bar, "cog", self.toggle_settings, active=self.show_settings)
        cog.pack(side="right", padx=(0, 4))
        for w in (bar, dot, head, brand):
            w.bind("<ButtonRelease-1>", self._toggle)

        if self.expanded:
            if self.show_settings:
                self.settings_panel()
            else:
                if not sessions:
                    tk.Label(self.body, text="Waiting for agent sessions…", fg=FAINT, bg=BG,
                             font=(UI, 9)).pack(padx=12, pady=(0, 12), anchor="w")
                if len(sessions) > 1:
                    ids = {s["id"] for s in sessions}
                    all_closed = ids <= self.collapsed
                    tools = tk.Frame(self.body, bg=BG)
                    tools.pack(fill="x", padx=12, pady=(0, 6))
                    tk.Label(tools, text=f"{len(sessions)} AGENTS", fg=FAINT, bg=BG,
                             font=(UI, 7, "bold")).pack(side="left")
                    self.chip(tools, "expand all" if all_closed else "collapse all",
                              (lambda: self.set_collapsed(set())) if all_closed
                              else (lambda: self.set_collapsed(self.collapsed | ids)),
                              bg=BG, hover=CARD).pack(side="right")
                for s in sessions:
                    self.card(s)
        self.place()

    def _toggle(self, e):
        if not self._moved:
            self.toggle_expanded()

    def toggle_expanded(self):
        self.cancel_peek()
        self.expanded = not self.expanded
        if not self.expanded:
            self.show_settings = False
        self.sig = None

    # --- shortcut ---------------------------------------------------------
    def _poll_hotkey(self):
        try:
            while True:
                self.hotkey.pressed.get_nowait()
                self.toggle_expanded()
        except queue.Empty:
            pass
        self.root.after(60, self._poll_hotkey)

    def set_hotkey(self, hk):
        self.set_cfg(hotkey=hk)
        self.hotkey.start(hk)

    def begin_capture(self):
        self.hotkey.stop()  # so pressing the current shortcut doesn't toggle the panel
        self.capturing, self.capture_msg = True, ""
        self.root.bind("<KeyPress>", self._capture_key)
        try:
            raise_window(user32.GetParent(self.root.winfo_id()) or self.root.winfo_id())
            self.root.focus_force()
        except Exception:
            pass
        self.sig = None

    def end_capture(self):
        self.capturing = False
        self.root.unbind("<KeyPress>")
        self.hotkey.start(self.cfg.get("hotkey"))
        self.sig = None

    def _capture_key(self, e):
        if not self.capturing:
            return
        if e.keysym in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
                        "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock"):
            return  # wait for the real key
        if e.keysym == "Escape":
            self.end_capture()
        elif e.keysym in ("BackSpace", "Delete"):
            self.end_capture()
            self.set_hotkey({})  # turn the shortcut off
        else:
            hk = {"ctrl": bool(e.state & 0x4), "shift": bool(e.state & 0x1),
                  "alt": bool(e.state & 0x20000), "vk": e.keycode}  # keycode is the virtual key on Windows
            if not (hk["ctrl"] or hk["alt"] or hk["shift"] or 0x70 <= e.keycode <= 0x87):
                self.capture_msg = "Add Ctrl, Alt or Shift"
                self.sig = None
                return
            self.end_capture()
            self.set_hotkey(hk)

    def toggle_settings(self):
        self.cancel_peek()
        self.show_settings = not self.show_settings
        if self.show_settings:
            self.expanded = True
        self.sig = None

    # --- settings controls ---------------------------------------------------
    def switch(self, parent, on, cmd, enabled=True):
        c = tk.Canvas(parent, width=36, height=20, bg=CARD, highlightthickness=0,
                      cursor="hand2" if enabled else "arrow")
        track = ("#34343d" if not on else COLORS["done"]) if enabled else "#2a2a31"
        knob = "#f4f4f6" if enabled else "#6b6b78"
        img = aa_image(("sw", on, enabled), 36, 20, CARD,
                       [(track, sdf_box(18, 10, 18, 10, 10)), (knob, sdf_circle(27 if on else 9, 10, 7.5))])
        c.create_image(0, 0, anchor="nw", image=img)
        if enabled:
            c.bind("<ButtonRelease-1>", lambda e: None if self._moved else cmd())
        return c

    def slider(self, parent, value, lo, hi, on_change, on_commit, width=130):
        """Anti-aliased slider (Tk's Scale is jagged and hard to style)."""
        c = tk.Canvas(parent, width=width, height=20, bg=CARD, highlightthickness=0, cursor="hand2")
        c.create_image(0, 7, anchor="nw", image=aa_image(("trk", width), width, 6, CARD,
                                                         [("#3a3a44", sdf_box(width / 2, 3, width / 2, 3, 3))]))
        thumb = aa_image(("thumb",), 16, 16, CARD, [(COLORS["running"], sdf_circle(8, 8, 7.5))])
        pad = 8
        item = c.create_image(0, 2, anchor="nw", image=thumb)

        def place(v):
            c.coords(item, pad + (v - lo) / (hi - lo) * (width - 2 * pad) - 8, 2)

        def at(e):
            v = round(lo + (min(max(e.x, pad), width - pad) - pad) / (width - 2 * pad) * (hi - lo))
            place(v)
            on_change(v)
            return v

        place(value)
        c.bind("<ButtonPress-1>", at)
        c.bind("<B1-Motion>", at)
        c.bind("<ButtonRelease-1>", lambda e: on_commit(at(e)))
        self.own_input(c)  # keep drags on the slider from moving the window
        return c

    def segmented(self, parent, options, current, cmd, enabled=True):
        f = tk.Frame(parent, bg=BG, padx=2, pady=2)
        for label, value in options:
            on = value == current
            fg = "#0d1a10" if on else (DIM if enabled else FAINT)
            bg = (COLORS["running"] if enabled else "#4a5a22") if on else BG
            b = tk.Label(f, text=label, fg=fg, bg=bg, padx=7, pady=3, cursor="hand2" if enabled else "arrow",
                         font=(UI, 8, "bold" if on else "normal"))
            if enabled:
                if not on:
                    b.bind("<Enter>", lambda e, b=b: b.configure(bg=CARD_HOVER, fg=FG))
                    b.bind("<Leave>", lambda e, b=b: b.configure(bg=BG, fg=DIM))
                b.bind("<ButtonRelease-1>", lambda e, v=value: None if self._moved else cmd(v))
            b.pack(side="left", padx=1)
        return f

    # --- settings panel ---------------------------------------------------
    def settings_panel(self):
        panel = tk.Frame(self.body, bg=CARD, padx=14, pady=10)
        panel.pack(fill="x", padx=8, pady=(0, 8))

        top = tk.Frame(panel, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text="Settings", fg=FG, bg=CARD, font=(UI, 11, "bold")).pack(side="left")
        self.chip(top, "Done", self.toggle_settings, fg="#0d1a10", bg=COLORS["running"],
                  hover="#d4ff7a", padx=14, pady=3, font=(UI, 9, "bold")).pack(side="right")

        def section(title):
            tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", pady=(10, 0))
            tk.Label(panel, text=title, fg=FAINT, bg=CARD, font=(UI, 7, "bold")).pack(anchor="w", pady=(8, 2))

        def row(label, enabled=True):
            r = tk.Frame(panel, bg=CARD)
            r.pack(fill="x", pady=4)
            tk.Label(r, text=label, fg=FG if enabled else FAINT, bg=CARD, font=(UI, 9)).pack(side="left")
            return r

        def switch_row(label, key, enabled=True):
            on = self.cfg[key]
            self.switch(row(label, enabled), on, lambda: self.set_cfg(**{key: not on}),
                        enabled).pack(side="right")

        # -- alerts --------------------------------------------------------
        section("ALERTS")
        switch_row("Sound when an agent finishes", "sound_finish")
        switch_row("Sound when an agent needs you", "sound_attention")

        sound_on = self.cfg["sound_finish"] or self.cfg["sound_attention"]
        sounds = available_sounds()
        cur = self.cfg["sound_name"] if self.cfg["sound_name"] in sounds else sounds[0]
        tk.Label(panel, text="Alert sound  ·  tap to preview", fg=FG if sound_on else FAINT, bg=CARD,
                 font=(UI, 9)).pack(anchor="w", pady=(4, 2))
        self.segmented(panel, [(n, n) for n in sounds], cur,
                       lambda v: (self.set_cfg(sound_name=v), play_sound(v)), sound_on
                       ).pack(anchor="w")

        peek_on = self.cfg["peek_finish"]
        switch_row("Open the panel when an agent finishes", "peek_finish")
        r = row("Stay open for", peek_on)
        secs = self.cfg["peek_seconds"]
        tk.Label(r, text="sec", fg=DIM if peek_on else FAINT, bg=CARD, font=(UI, 8)).pack(side="right")
        entry = tk.Entry(r, width=4, justify="center", bg=BG, fg=FG, insertbackground=FG, relief="flat",
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=COLORS["running"], font=(UI, 9),
                         disabledbackground=BG, disabledforeground=FAINT)
        entry.insert(0, f"{secs:g}")
        self.own_input(entry)
        if peek_on:
            entry.bind("<Button-1>", lambda e: entry.focus_force())  # borderless windows need this
        else:
            entry.configure(state="disabled")

        def commit(_=None):
            try:
                self.set_cfg(peek_seconds=min(60.0, max(1.0, float(entry.get()))))
            except ValueError:
                self.set_cfg()  # re-render with the stored value
            except tk.TclError:
                pass  # the panel was re-rendered under us

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.pack(side="right", padx=(6, 4))
        self.segmented(r, [("3", 3.0), ("5", 5.0), ("10", 10.0)], secs,
                       lambda v: self.set_cfg(peek_seconds=v), peek_on).pack(side="right")
        switch_row("Open the panel when it needs you", "auto_expand")
        switch_row("Jump to the chat when an agent finishes", "jump_on_finish")
        tk.Label(panel, text="Takes focus from whatever you're working on.", fg=FAINT, bg=CARD,
                 font=(UI, 8)).pack(anchor="w")

        # -- window ----------------------------------------------------------
        section("WINDOW")
        switch_row("Keep on top of other windows", "pinned")

        boot = startup.is_enabled()  # the registry is the source of truth, not the config file
        self.switch(row("Start with Windows"), boot,
                    lambda: (startup.disable() if boot else startup.enable(), setattr(self, "sig", None))
                    ).pack(side="right")

        r = row("Show or hide the panel")
        hk = self.cfg.get("hotkey")
        if self.capturing:
            self.chip(r, "Press keys…", self.end_capture, fg="#0d1a10", bg=COLORS["running"],
                      hover="#d4ff7a", padx=10, pady=3, font=(UI, 8, "bold")).pack(side="right")
        else:
            self.chip(r, hotkey_label(hk), self.begin_capture, bg=BG, padx=10, pady=3,
                      font=(UI, 8, "bold")).pack(side="right")
            if hk != DEFAULT_HOTKEY:
                self.chip(r, "reset", lambda: self.set_hotkey(dict(DEFAULT_HOTKEY)),
                          fg=FAINT, padx=6).pack(side="right", padx=(0, 4))
        note = (self.capture_msg or "Press the new shortcut. Esc cancels, Backspace turns it off.") \
            if self.capturing else ("That shortcut is taken by another app." if self.hotkey.ok is False else "")
        if note:
            tk.Label(panel, text=note, fg=COLORS["waiting"] if (self.capture_msg or not self.capturing)
                     else FAINT, bg=CARD, font=(UI, 8), wraplength=WIDTH - 60, justify="left",
                     anchor="w").pack(anchor="w")

        r = row("Opacity")
        pct = tk.Label(r, text=f"{int(self.cfg['opacity'] * 100)}%", fg=DIM, bg=CARD,
                       font=(UI, 8), width=4, anchor="e")
        pct.pack(side="right")

        def on_slide(v):  # live preview; no re-render so the slider survives the drag
            self.root.attributes("-alpha", v / 100)
            pct.configure(text=f"{v}%")

        self.slider(r, int(self.cfg["opacity"] * 100), 40, 100, on_slide,
                    self.save_opacity).pack(side="right")

        tk.Label(panel, text="Position", fg=FG, bg=CARD, font=(UI, 9)).pack(anchor="w", pady=(6, 2))
        body = tk.Frame(panel, bg=CARD)
        body.pack(fill="x")
        cv = tk.Canvas(body, width=112, height=68, bg=CARD, highlightthickness=0, cursor="hand2")
        cv.pack(side="left")
        cv.create_rectangle(1, 1, 111, 67, outline=BORDER, fill=BG)
        cv.create_rectangle(2, 61, 110, 66, fill=CARD_HOVER, outline="")  # taskbar
        anchor = self.cfg["anchor"]
        spots = {"tl": (16, 14), "tc": (56, 14), "tr": (96, 14),
                 "bl": (16, 48), "bc": (56, 48), "br": (96, 48)}
        for a, (x, y) in spots.items():
            on = anchor == a

            def dot(colour):
                return aa_image(("dot", colour), 14, 14, BG, [(colour, sdf_circle(7, 7, 6))])

            idle = dot(COLORS["running"] if on else "#34343d")
            lit = dot(COLORS["running"] if on else "#7a7a88")
            item = cv.create_image(x - 7, y - 7, anchor="nw", image=idle)
            cv.tag_bind(item, "<Enter>", lambda e, i=item, im=lit: cv.itemconfigure(i, image=im))
            cv.tag_bind(item, "<Leave>", lambda e, i=item, im=idle: cv.itemconfigure(i, image=im))
            cv.tag_bind(item, "<ButtonRelease-1>", lambda e, a=a:
                        None if self._moved else self.set_cfg(anchor=a))
        tk.Label(body, justify="left", anchor="nw", wraplength=150, fg=DIM, bg=CARD, font=(UI, 8),
                 text="Custom spot. Pick a dot to snap back to an edge." if anchor == "free"
                 else "Drag the panel to place it anywhere. It remembers where you leave it."
                 ).pack(side="left", padx=(12, 0), anchor="n")

    # --- agent card -------------------------------------------------------
    def card(self, s):
        status = s.get("status", "idle")
        color = COLORS.get(status, FAINT)
        outer = tk.Frame(self.body, bg=CARD)
        outer.pack(fill="x", padx=8, pady=(0, 8))
        tk.Frame(outer, bg=color, width=3).pack(side="left", fill="y")
        f = tk.Frame(outer, bg=CARD, padx=10, pady=8)
        f.pack(side="left", fill="both", expand=True)
        wrap = WIDTH - 60
        source = s.get("source", "claude")
        title = session_title(s)
        project = s.get("project", "?")

        if s["id"] in self.collapsed:  # one slim row; click anywhere on it to open the card
            f.configure(pady=5)
            row = tk.Frame(f, bg=CARD, cursor="hand2")
            row.pack(fill="x")
            self.icon_button(row, "right", lambda: self.toggle_collapse(s), size=22, bg=CARD
                             ).pack(side="left", padx=(0, 6))
            name = tk.Label(row, text=short(title or project, 22), fg=FG, bg=CARD,
                            font=(UI, 9, "bold"), cursor="hand2")
            name.pack(side="left")
            state = tk.Label(row, text=LABELS.get(status, ""), fg=color, bg=CARD,
                             font=(UI, 7, "bold"), cursor="hand2")
            state.pack(side="right", padx=(0, 2))
            for w in (row, name, state):
                w.bind("<ButtonRelease-1>", lambda e, s=s: None if self._moved else self.toggle_collapse(s))
            return

        head = tk.Frame(f, bg=CARD)
        head.pack(fill="x")
        self.icon_button(head, "down", lambda: self.toggle_collapse(s), size=22, bg=CARD
                         ).pack(side="left", padx=(0, 6))
        tk.Label(head, text=short(title or project, 22), fg=FG, bg=CARD,
                 font=(UI, 10, "bold")).pack(side="left")
        self.chip(head, "✕", lambda: self.dismiss(s), fg=FAINT, padx=5).pack(side="right")
        self.chip(head, "jump", lambda: focus_app(source, s)).pack(side="right")
        self.chip(head, "steps ▴" if s["id"] in self.open_cards else "steps",
                  lambda: self.toggle_card(s)).pack(side="right")
        # second line: status, which app, and the folder when the title replaced it
        meta = tk.Frame(f, bg=CARD)
        meta.pack(fill="x", pady=(1, 0))
        tk.Label(meta, text=LABELS.get(status, ""), fg=color, bg=CARD,
                 font=(UI, 7, "bold")).pack(side="left")
        tk.Label(meta, text=("  ·  " + source.upper() + (f"  ·  {short(project, 22)}" if title else "")),
                 fg=FAINT, bg=CARD, font=(UI, 7)).pack(side="left")

        if s.get("task") and not s["task"].startswith("<"):  # the ask: context, so quieter
            tk.Label(f, text="“" + short(s["task"], 70) + "”", fg=FAINT, bg=CARD, wraplength=wrap,
                     justify="left", anchor="w", font=(UI, 8)).pack(fill="x", pady=(2, 0))

        say = narration(s) if status == "running" else ""
        if say:  # what the agent is doing, in its own words
            tk.Label(f, text="NOW", fg=FAINT, bg=CARD, font=(UI, 7, "bold"),
                     anchor="w").pack(fill="x", pady=(8, 1))
            tk.Label(f, text=say, fg="#d6d6df", bg=CARD, wraplength=wrap, justify="left",
                     anchor="w", font=(UI, 9)).pack(fill="x")

        if status == "done" and s.get("summary"):
            tk.Label(f, text="RESULT", fg=FAINT, bg=CARD, font=(UI, 7, "bold"),
                     anchor="w").pack(fill="x", pady=(8, 1))
            tk.Label(f, text=s["summary"], fg=FG, bg=CARD, wraplength=wrap, justify="left",
                     anchor="w", font=(UI, 9)).pack(fill="x")

        choices = pending_choices(s) if status == "done" else None
        if choices and (s["id"], choices["ask"]) not in self.answered:
            if choices["ask"]:
                tk.Label(f, text=choices["ask"], fg=FG, bg=CARD, wraplength=wrap, justify="left",
                         anchor="w", font=(UI, 9, "bold")).pack(fill="x", pady=(8, 4))
            row = tk.Frame(f, bg=CARD)
            row.pack(fill="x", pady=(0 if choices["ask"] else 8, 0))
            for i, act in enumerate(choices["actions"][:3]):
                first = i == 0
                self.chip(row, act["label"], lambda a=act, s=s, c=choices: self.reply(s, c, a),
                          fg="#0d1a10" if first else FG, bg=COLORS["running"] if first else BG,
                          hover="#d4ff7a" if first else CARD_HOVER, padx=12, pady=4,
                          font=(UI, 9, "bold")).pack(side="left", padx=(0, 6))
        elif choices:
            tk.Label(f, text="Replied from Agent Watch", fg=FAINT, bg=CARD, font=(UI, 8),
                     anchor="w").pack(fill="x", pady=(6, 0))

        pending = s.get("pending")
        if pending:
            box = tk.Frame(f, bg="#2a2118", padx=8, pady=6)
            box.pack(fill="x", pady=(8, 0))
            tk.Label(box, text="PERMISSION REQUEST", fg=COLORS["waiting"], bg="#2a2118",
                     font=(UI, 7, "bold")).pack(anchor="w")
            tk.Label(box, text=pending["tool"], fg=FG, bg="#2a2118", wraplength=wrap - 16,
                     justify="left", anchor="w", font=(MONO, 9)).pack(fill="x", pady=(2, 6))
            row = tk.Frame(box, bg="#2a2118")
            row.pack(fill="x")
            self.chip(row, "Allow", lambda p=pending: self.answer(p["id"], "allow"),
                      fg="#0d1a10", bg=COLORS["done"], hover="#7ae6b0", padx=14, pady=3,
                      font=(UI, 9, "bold")).pack(side="left", padx=(0, 6))
            self.chip(row, "Deny", lambda p=pending: self.answer(p["id"], "deny"),
                      fg=FG, bg="#3a2326", hover="#4d2c30", padx=14, pady=3,
                      font=(UI, 9, "bold")).pack(side="left")
        elif s.get("note") and status == "waiting":
            tk.Label(f, text=s["note"], fg=COLORS["waiting"], bg=CARD, wraplength=wrap,
                     justify="left", anchor="w", font=(UI, 9)).pack(fill="x", pady=(4, 0))

        if s["id"] in self.open_cards:  # the raw activity, one click away
            for step in [nice_step(x) for x in s.get("steps", [])][-12:]:
                if step:
                    tk.Label(f, text=f"› {short(step, 90)}", fg=DIM, bg=CARD, wraplength=wrap,
                             justify="left", anchor="w", font=(MONO, 8)).pack(fill="x")
        elif status == "running" and not say and latest_step(s):  # nothing said yet this turn
            tk.Label(f, text=f"› {short(latest_step(s), 90)}", fg=FAINT, bg=CARD, wraplength=wrap,
                     justify="left", anchor="w", font=(MONO, 8)).pack(fill="x", pady=(4, 0))

    # --- actions --------------------------------------------------------
    def set_collapsed(self, ids):
        self.collapsed = set(ids)
        self.set_cfg(collapsed=sorted(self.collapsed))

    def toggle_collapse(self, s):
        self.set_collapsed(self.collapsed ^ {s["id"]})

    def reply(self, s, choices, action):
        self.answered.add((s["id"], choices["ask"]))
        self.sig = None
        send_to_chat(self.root, s, action["text"])

    def toggle_card(self, s):
        self.open_cards ^= {s["id"]}
        self.sig = None

    def dismiss(self, s):
        (SESSIONS / f"{s['id']}.json").unlink(missing_ok=True)
        self.sig = None

    def answer(self, req_id, answer):
        REQUESTS.mkdir(parents=True, exist_ok=True)
        (REQUESTS / f"{req_id}.json").write_text(json.dumps({"answer": answer}), encoding="utf-8")
        self.sig = None

    # --- placement ------------------------------------------------------
    def place(self):
        self.root.update_idletasks()
        w = WIDTH if self.expanded else max(self.root.winfo_reqwidth(), 230)
        h = self.root.winfo_reqheight()
        a = self.cfg["anchor"]
        if a == "free" and self.cfg["x"] is not None:
            ex, ey, ax, ay = self.cfg["x"], self.cfg["y"], self.cfg["ax"], self.cfg["ay"]
            left, top, right, bottom = work_area(ex - 1 if ax == "r" else ex, ey - 1 if ay == "b" else ey)
            # the fixed edge stays put, so the panel grows away from it (up when at the bottom)
            x = ex - w if ax == "r" else ex
            y = ey - h if ay == "b" else ey
        else:
            left, top, right, bottom = work_area(0, 0)
            if a in ("tc", "bc"):
                x = (left + right - w) // 2
            elif a in ("tl", "bl"):
                x = left + MARGIN
            else:
                x = right - w - MARGIN
            y = top + MARGIN if a in ("tl", "tc", "tr") else bottom - h - MARGIN
        x = max(left, min(x, right - w))
        y = max(top, min(y, bottom - h))
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    AgentWatch().run()

"""Agent Watch: a small always-on-top panel listing your running AI coding agents.

Works with Claude Code and OpenAI Codex (CLI and desktop apps) through hooks.
Click the header to expand. Per agent you can see its step-by-step activity,
allow/deny pending permission requests, jump to the app, or dismiss it.
The cog opens settings (sound, position, opacity, pin).
"""
import ctypes
import json
import os
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

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
MARGIN, BOTTOM_MARGIN = 24, 56  # leave room for the taskbar

DEFAULTS = {
    "pinned": True,
    "sound_finish": True,
    "sound_attention": True,
    "sound_name": "Chime",
    "opacity": 0.97,
    "auto_expand": True,
    "anchor": "br",  # tl | tr | bl | br | free
    "x": None,
    "y": None,
}
OPACITIES = [1.0, 0.97, 0.9, 0.8]
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
    return exe == "claude.exe" and "claude" in t


def focus_app(source):
    """Bring the desktop app that owns this agent (Claude or ChatGPT/Codex) to the front."""
    for hwnd, title in _windows():
        if "agent watch" in title.lower():
            continue
        if _matches(source, title, _exe_name(hwnd)):
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
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
        self.sig = None
        self.prev_status = None  # None until the first read, so startup is silent
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
        self.tick()
        self.pulse()

    # --- config helpers -------------------------------------------------
    def set_cfg(self, **kw):
        self.cfg.update(kw)
        save_config(self.cfg)
        self.root.attributes("-topmost", self.cfg["pinned"])
        self.root.attributes("-alpha", self.cfg["opacity"])
        self.sig = None

    def _round_corners(self):
        try:  # Windows 11: ask DWM for rounded corners on the borderless window
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:
            pass

    # --- dragging -------------------------------------------------------
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._moved = False

    def _drag(self, e):
        self._moved = True
        x, y = e.x_root - self._dx, e.y_root - self._dy
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, e):
        if self._moved:
            self.set_cfg(anchor="free", x=self.root.winfo_x(), y=self.root.winfo_y())

    # --- loop -----------------------------------------------------------
    def tick(self):
        sessions = read_sessions()
        self.detect_changes(sessions)
        sig = json.dumps([self.expanded, self.show_settings, self.cfg, sorted(self.open_cards),
                          sessions], sort_keys=True)
        if sig != self.sig:
            self.sig = sig
            self.render(sessions)
        self.root.after(700, self.tick)

    def detect_changes(self, sessions):
        current = {s["id"]: s.get("status") for s in sessions}
        if self.prev_status is not None:
            for sid, status in current.items():
                old = self.prev_status.get(sid)
                if status == "done" and old in ("running", "waiting") and self.cfg["sound_finish"]:
                    play_sound(self.cfg["sound_name"])
                elif status == "waiting" and old != "waiting":
                    if self.cfg["sound_attention"]:
                        play_sound(self.cfg["sound_name"])
                    if self.cfg["auto_expand"] and not self.expanded:
                        self.expanded = True
        self.prev_status = current

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
        bar.pack(fill="x", padx=12, pady=(9, 9))
        dot = tk.Label(bar, text="●", fg=COLORS[state], bg=BG, font=(UI, 9))
        dot.pack(side="left")
        if state == "running":
            self.pulse_dots.append(dot)
        head = tk.Label(bar, text=label, fg=FG, bg=BG, font=(UI, 10, "bold"), cursor="hand2")
        head.pack(side="left", padx=(7, 0))
        brand = tk.Label(bar, text="AGENT WATCH", fg=FAINT, bg=BG, font=(UI, 7, "bold"), cursor="hand2")
        brand.pack(side="left", padx=(10, 0))
        cog = self.chip(bar, "⚙", self.toggle_settings,
                        fg=FG if self.show_settings else FAINT, bg=BG, hover=CARD,
                        font=(UI, 11), padx=6, pady=0)
        cog.pack(side="right")
        caret = tk.Label(bar, text="▾" if self.expanded else "▸", fg=FAINT, bg=BG, font=(UI, 9))
        caret.pack(side="right", padx=(0, 4))
        for w in (bar, dot, head, brand, caret):
            w.bind("<ButtonRelease-1>", self._toggle)

        if self.expanded:
            if self.show_settings:
                self.settings_panel()
            elif not sessions:
                tk.Label(self.body, text="Waiting for agent sessions…", fg=FAINT, bg=BG,
                         font=(UI, 9)).pack(padx=12, pady=(0, 12), anchor="w")
            if not self.show_settings:
                for s in sessions:
                    self.card(s)
        self.place()

    def _toggle(self, e):
        if not self._moved:
            self.expanded = not self.expanded
            if not self.expanded:
                self.show_settings = False
            self.sig = None

    def toggle_settings(self):
        self.show_settings = not self.show_settings
        if self.show_settings:
            self.expanded = True
        self.sig = None

    # --- settings panel ---------------------------------------------------
    def settings_panel(self):
        panel = tk.Frame(self.body, bg=CARD, padx=12, pady=8)
        panel.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(panel, text="SETTINGS", fg=FAINT, bg=CARD, font=(UI, 7, "bold")).pack(anchor="w")

        def row(label):
            r = tk.Frame(panel, bg=CARD)
            r.pack(fill="x", pady=3)
            tk.Label(r, text=label, fg=FG, bg=CARD, font=(UI, 9)).pack(side="left")
            return r

        def toggle(label, key):
            on = self.cfg[key]
            r = row(label)
            self.chip(r, "On" if on else "Off", lambda: self.set_cfg(**{key: not on}),
                      fg="#0d1a10" if on else DIM, bg=COLORS["done"] if on else BG,
                      hover="#7ae6b0" if on else CARD_HOVER, padx=10, font=(UI, 8, "bold")
                      ).pack(side="right")

        toggle("Pin on top of windows", "pinned")
        toggle("Sound when an agent finishes", "sound_finish")
        toggle("Sound when an agent needs you", "sound_attention")
        toggle("Auto-expand when it needs you", "auto_expand")

        sounds = available_sounds()
        r = row("Sound")
        self.chip(r, "▶ test", lambda: play_sound(self.cfg["sound_name"]), padx=6).pack(side="right")
        cur = self.cfg["sound_name"] if self.cfg["sound_name"] in sounds else sounds[0]
        nxt = sounds[(sounds.index(cur) + 1) % len(sounds)]
        self.chip(r, f"{cur} ↻", lambda: (self.set_cfg(sound_name=nxt), play_sound(nxt)),
                  bg=BG, padx=8).pack(side="right", padx=(0, 4))

        r = row("Opacity")
        op = self.cfg["opacity"]
        nxt_op = OPACITIES[(OPACITIES.index(op) + 1) % len(OPACITIES)] if op in OPACITIES else 1.0
        self.chip(r, f"{int(op * 100)}% ↻", lambda: self.set_cfg(opacity=nxt_op), bg=BG,
                  padx=8).pack(side="right")

        r = row("Position")
        anchor = self.cfg["anchor"]
        for sym, a in (("↘", "br"), ("↙", "bl"), ("↗", "tr"), ("↖", "tl")):
            on = anchor == a
            self.chip(r, sym, lambda a=a: self.set_cfg(anchor=a), font=(UI, 10),
                      fg=COLORS["running"] if on else DIM, bg=BG, padx=6, pady=0
                      ).pack(side="right", padx=1)
        tk.Label(panel, text="Drag the panel anywhere to set a custom spot; it is remembered."
                 if anchor != "free" else "Custom spot saved. Pick a corner to snap back.",
                 fg=FAINT, bg=CARD, font=(UI, 8), wraplength=WIDTH - 60, justify="left",
                 anchor="w").pack(fill="x", pady=(2, 0))

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

        head = tk.Frame(f, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=s.get("project", "?"), fg=FG, bg=CARD, font=(UI, 10, "bold")).pack(side="left")
        tk.Label(head, text=LABELS.get(status, ""), fg=color, bg=CARD,
                 font=(UI, 7, "bold")).pack(side="left", padx=(8, 0))
        tk.Label(head, text=source.upper(), fg=FAINT, bg=CARD,
                 font=(UI, 7)).pack(side="left", padx=(6, 0))
        self.chip(head, "✕", lambda: self.dismiss(s), fg=FAINT, padx=5).pack(side="right")
        self.chip(head, "jump", lambda: focus_app(source)).pack(side="right")
        self.chip(head, "steps ▴" if s["id"] in self.open_cards else "steps",
                  lambda: self.toggle_card(s)).pack(side="right")

        if s.get("task"):
            tk.Label(f, text=s["task"], fg=DIM, bg=CARD, wraplength=wrap, justify="left",
                     anchor="w", font=(UI, 9)).pack(fill="x", pady=(2, 0))

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

        if s["id"] in self.open_cards:
            for step in s.get("steps", [])[-12:]:
                tk.Label(f, text=f"› {step}", fg=DIM, bg=CARD, wraplength=wrap, justify="left",
                         anchor="w", font=(MONO, 8)).pack(fill="x")
        elif s.get("steps") and status == "running":
            tk.Label(f, text=f"› {s['steps'][-1]}", fg=FAINT, bg=CARD, wraplength=wrap,
                     justify="left", anchor="w", font=(MONO, 8)).pack(fill="x", pady=(4, 0))

    # --- actions --------------------------------------------------------
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
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        a = self.cfg["anchor"]
        if a == "free" and self.cfg["x"] is not None:
            x, y = self.cfg["x"], self.cfg["y"]
        else:
            x = MARGIN if a in ("tl", "bl") else sw - w - MARGIN
            y = MARGIN if a in ("tl", "tr") else sh - h - BOTTOM_MARGIN
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    AgentWatch().run()

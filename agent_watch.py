"""Agent Watch: a small always-on-top panel listing your running Claude Code agents.

Click the header to expand. Per agent you can see its step-by-step activity,
allow/deny pending permission requests, jump to the Claude desktop window,
or dismiss it.
"""
import ctypes
import json
import os
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

HOME = Path(os.environ.get("AGENT_WATCH_DIR", Path.home() / ".agent-watch"))
SESSIONS = HOME / "sessions"
REQUESTS = HOME / "requests"
STALE_SECONDS = 6 * 3600

# Graphite surfaces, hairline border, one accent per status.
BORDER, BG, CARD, CARD_HOVER = "#2b2b33", "#131316", "#1c1c21", "#25252c"
FG, DIM, FAINT = "#ececf1", "#8d8d99", "#5c5c68"
COLORS = {"running": "#b8f23a", "waiting": "#ff9f43", "done": "#4cd694", "idle": "#6b6b78"}
LABELS = {"running": "RUNNING", "waiting": "NEEDS YOU", "done": "DONE", "idle": "IDLE"}
UI, MONO = "Segoe UI", "Consolas"
WIDTH = 340

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


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


def focus_claude():
    """Bring the Claude desktop window to the front."""
    windows = [(h, t) for h, t in _windows() if "agent watch" not in t.lower()]
    for hwnd, title in windows:
        if "claude" in title.lower() and _exe_name(hwnd) == "claude.exe":
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
    return False


class AgentWatch:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Agent Watch")
        self.root.overrideredirect(True)
        self.pinned = self._load_pref()
        self.root.attributes("-topmost", self.pinned)
        self.root.attributes("-alpha", 0.97)
        self.root.configure(bg=BORDER)  # 1px hairline: border colour shows around inner frame
        self.expanded = False
        self.open_cards = set()
        self.sig = None
        self.pos = None
        self._dx = self._dy = 0
        self.pulse_dots = []
        self.pulse_on = True
        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag)
        self.root.update_idletasks()
        self._round_corners()
        self.tick()
        self.pulse()

    # --- chrome ---------------------------------------------------------
    def _round_corners(self):
        try:  # Windows 11: ask DWM for rounded corners on the borderless window
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:
            pass

    @staticmethod
    def _load_pref():
        try:
            return json.loads((HOME / "config.json").read_text(encoding="utf-8")).get("pinned", True)
        except Exception:
            return True

    def toggle_pin(self):
        self.pinned = not self.pinned
        self.root.attributes("-topmost", self.pinned)
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            (HOME / "config.json").write_text(json.dumps({"pinned": self.pinned}), encoding="utf-8")
        except Exception:
            pass
        self.sig = None

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self._moved = False

    def _drag(self, e):
        self._moved = True
        self.pos = (e.x_root - self._dx, e.y_root - self._dy)
        self.root.geometry(f"+{self.pos[0]}+{self.pos[1]}")

    # --- loop -----------------------------------------------------------
    def tick(self):
        sessions = read_sessions()
        sig = json.dumps([self.expanded, self.pinned, sorted(self.open_cards), sessions], sort_keys=True)
        if sig != self.sig:
            self.sig = sig
            self.render(sessions)
        self.root.after(700, self.tick)

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
        b.bind("<ButtonRelease-1>", lambda e: cmd())
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
        pin = self.chip(bar, "pinned" if self.pinned else "pin", self.toggle_pin,
                        fg=FG if self.pinned else FAINT, bg=BG, hover=CARD, padx=6, pady=1)
        pin.pack(side="right")
        caret = tk.Label(bar, text="▾" if self.expanded else "▸", fg=FAINT, bg=BG, font=(UI, 9))
        caret.pack(side="right", padx=(0, 6))
        for w in (bar, dot, head, brand, caret):
            w.bind("<ButtonRelease-1>", self._toggle)

        if self.expanded:
            if not sessions:
                tk.Label(self.body, text="Waiting for Claude Code sessions…", fg=FAINT, bg=BG,
                         font=(UI, 9)).pack(padx=12, pady=(0, 12), anchor="w")
            for s in sessions:
                self.card(s)
        self.place()

    def _toggle(self, e):
        if not getattr(self, "_moved", False):
            self.expanded = not self.expanded
            self.sig = None

    def card(self, s):
        status = s.get("status", "idle")
        color = COLORS.get(status, FAINT)
        outer = tk.Frame(self.body, bg=CARD)
        outer.pack(fill="x", padx=8, pady=(0, 8))
        tk.Frame(outer, bg=color, width=3).pack(side="left", fill="y")
        f = tk.Frame(outer, bg=CARD, padx=10, pady=8)
        f.pack(side="left", fill="both", expand=True)
        wrap = WIDTH - 60

        head = tk.Frame(f, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=s.get("project", "?"), fg=FG, bg=CARD, font=(UI, 10, "bold")).pack(side="left")
        tk.Label(head, text=LABELS.get(status, ""), fg=color, bg=CARD,
                 font=(UI, 7, "bold")).pack(side="left", padx=(8, 0))
        self.chip(head, "✕", lambda: self.dismiss(s), fg=FAINT, padx=5).pack(side="right")
        self.chip(head, "jump", focus_claude).pack(side="right")
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

    # --- placement ------------------------------------------------------
    def place(self):
        self.root.update_idletasks()
        w, h = (WIDTH if self.expanded else max(self.root.winfo_reqwidth(), 230)), self.root.winfo_reqheight()
        if self.pos is None:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.pos = (sw - w - 24, sh - h - 64)
        self.root.geometry(f"{w}x{h}+{self.pos[0]}+{self.pos[1]}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    AgentWatch().run()

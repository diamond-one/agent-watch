# Agent Watch

A small always-on-top widget for Windows that shows every AI coding agent you have running, in Claude Code, OpenAI Codex and Cursor.

- **At a glance:** a pill in the corner says "2 running", "1 needs you" or "all done".
- **Per agent:** each card shows its session title, what you asked, what it's doing right now (in its own words), and later its result. Raw tool steps are one click away.
- **Reply from the widget:** permission requests get Allow / Deny buttons. When an agent ends with options ("Push to GitHub" / "Not yet"), those appear as buttons too, and a click replies in that agent's chat.
- **Jump:** opens that agent's own chat in Claude desktop (other apps: brings them forward).
- **Fold agents** to a single row to watch one while the rest stay out of the way.
- **Alerts:** a sound when an agent finishes or needs you, a short peek that opens the panel with the result (it stays open while your pointer is on it), and an optional "jump to the chat when an agent finishes".
- **Shortcut:** Ctrl+\` shows or hides the panel from anywhere. Change it in settings.
- **Settings (⚙):** sounds, peek length, keep on top, opacity, shortcut and position. It remembers where you drag it and grows away from the nearest screen edge, never over the taskbar.

## How it works

Hooks (`watch_hook.py`) write each session's state to `~/.agent-watch/sessions/`. The widget (`agent_watch.py`, plain tkinter) reads that folder, plus the session transcript for titles, narration and reply options. For permission requests the hook blocks until you answer in the widget (about 100 seconds), then falls back to the app's normal dialog. Settings are stored in `~/.agent-watch/config.json`, and jump/reply activity is logged to `~/.agent-watch/agent-watch.log`.

## Install

Requires Python 3.9+ on Windows (no packages needed).

```bash
python install.py      # registers hooks for Claude Code, Codex and Cursor (whichever is present)
pythonw agent_watch.py # launch the widget (python agent_watch.py to see errors)
```

Use `--claude`, `--codex` or `--cursor` to install for one only, and `--remove` to uninstall. Restart open sessions so they pick up the hooks.

**As an app:** `python install.py --app` adds "Agent Watch" to the Start menu (with its icon) and starts it with Windows. Only one copy ever runs, so launching it again does nothing. `--remove-app` undoes both.

**Start with Windows:** turn it on in settings (⚙ > Start with Windows) or run `python install.py --startup` (`--no-startup` to undo). It adds one per-user entry under `HKCU\...\Run`, so no admin rights are needed, and it repoints itself if you move the folder.

**Codex:** hooks go to `~/.codex/hooks.json`. Codex asks you to review and trust new hooks once: run `/hooks` in the CLI, or use "Review hooks" / Settings > Hooks in the desktop app. Whether the desktop app reads `~/.codex/hooks.json` is not documented, so check that the hooks show up there.

**Cursor:** hooks go to `~/.cursor/hooks.json` and Cursor reloads them on save. By default Cursor sessions show status and sound alerts. Widget approvals for Cursor are opt-in, because Cursor's shell hook fires for every command, including ones it would auto-allow, and each would wait for you in the widget:

```bash
python install.py --cursor-approvals
```

## Limits

- Hooks only exist in the coding agents (Claude Code's Code tab, Codex, Cursor), not regular chat.
- Opening a specific chat and sending reply buttons currently work for **Claude desktop** only. Codex and Cursor cards just bring their app forward.
- **Reply buttons drive the app like a person would**: they open the chat, click the message box (near the bottom of the window), paste and press Enter. They move your pointer briefly and won't work if the window layout puts the message box somewhere unusual.
- Ctrl+\` is also VS Code's and Cursor's terminal shortcut. A global shortcut takes the key from every app, so change it in settings if you use that.
- Permission replies from the widget can't answer an agent's multiple-choice questions, only permission prompts.

## License

MIT

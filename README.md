# Agent Watch

A small always-on-top widget for Windows that shows every AI coding agent you have running, in Claude Code and OpenAI Codex (CLI and desktop apps).

- A pill in the corner: "2 running", "1 needs you", "all done".
- Expand it to see each agent's task and step-by-step activity.
- **Approve or deny permission requests from the widget**, without switching to the app.
- A sound plays when an agent finishes or needs you.
- **jump** brings the owning desktop app (Claude or ChatGPT/Codex) to the front; ✕ dismisses a session.
- ⚙ settings: pin on top, sounds (on/off, choice, test), auto-expand, opacity, and position.
- Position is remembered: drag it anywhere, or snap it to a corner in settings.

## How it works

Hooks (`watch_hook.py`) write each session's state to `~/.agent-watch/sessions/`. The widget (`agent_watch.py`, plain tkinter) reads that folder. For permission requests the hook blocks until you answer in the widget (about 100 seconds), then falls back to the app's normal dialog. Settings are stored in `~/.agent-watch/config.json`.

## Install

Requires Python 3.9+ on Windows (no packages needed).

```bash
python install.py      # registers hooks for Claude Code and Codex (whichever is present)
pythonw agent_watch.py # launch the widget (python agent_watch.py to see errors)
```

Use `--claude` or `--codex` to install for one only, and `--remove` to uninstall. Restart open sessions so they pick up the hooks.

**Codex:** hooks are written to `~/.codex/hooks.json`. Codex asks you to review and trust new hooks once: run `/hooks` in the CLI, or use "Review hooks" / Settings > Hooks in the desktop app. Whether the desktop app reads `~/.codex/hooks.json` is not documented, so check that the hooks show up there.

## Limits

- Hooks only exist in the coding agents (Claude Code's Code tab and Codex), not regular chat or ChatGPT Work's non-Codex agent tasks.
- Can't answer an agent's multiple-choice questions yet, only permission prompts. Codex only calls the permission hook when it would otherwise ask you.
- **jump** focuses the app window; it can't select a specific session inside it.
- Steps come from tool calls, not the model's reasoning.

## License

MIT

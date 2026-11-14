# Agent Watch

A small always-on-top widget for Windows that shows every Claude Code agent you have running.

- A pill in the corner: "2 running", "1 needs you", "all done".
- Expand it to see each agent's task and step-by-step activity.
- Allow or deny permission requests from the widget, without opening the app.
- **jump** brings the Claude desktop window to the front; ✕ dismisses a finished session.
- Drag it anywhere, and pin or unpin it on top of other windows.

## How it works

Claude Code hooks (`watch_hook.py`) write each session's state to `~/.agent-watch/sessions/`. The widget (`agent_watch.py`, plain tkinter) reads that folder. For permission requests the hook blocks until you answer in the widget (about 100 seconds), then falls back to Claude Code's normal dialog.

## Install

Requires Python 3.9+ on Windows (no packages needed).

```bash
python install.py      # registers the hooks in ~/.claude/settings.json
pythonw agent_watch.py # launch the widget (python agent_watch.py to see errors)
```

Restart any open Claude Code sessions so they pick up the hooks. To remove the hooks: `python install.py --remove`.

## Limits

- Works with Claude Code sessions (CLI and the desktop app's Code tab), not regular chat.
- Can't answer an agent's multiple-choice questions yet, only permission prompts.
- **jump** focuses the Claude window; it can't select a specific session inside it.
- Steps come from tool calls, not Claude's reasoning.

## License

MIT

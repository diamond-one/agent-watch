"""Register the Agent Watch hooks (idempotent).

    python install.py                 Claude Code + Codex (whichever is present)
    python install.py --claude        Claude Code only   (~/.claude/settings.json)
    python install.py --codex         Codex only         (~/.codex/hooks.json)
    python install.py --remove        uninstall (combine with --claude / --codex)

Codex requires you to review and trust new hooks once: open /hooks in the CLI,
or "Review hooks" / Settings > Hooks in the desktop app.
"""
import json
import sys
from pathlib import Path

HOOK = Path(__file__).with_name("watch_hook.py").resolve()
MARK = "watch_hook.py"

TARGETS = {
    "claude": {
        "file": Path.home() / ".claude" / "settings.json",
        "events": ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                   "Notification", "Stop", "SessionEnd", "PermissionRequest"],
        "timeouts": {"PermissionRequest": 110, "SessionEnd": 5},
    },
    "codex": {
        "file": Path.home() / ".codex" / "hooks.json",
        "events": ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                   "Stop", "Interrupt", "SessionEnd", "PermissionRequest"],
        # Codex timeouts are seconds; SessionEnd/Interrupt max out at 3.
        "timeouts": {"PermissionRequest": 110, "SessionEnd": 3, "Interrupt": 3},
    },
}


def is_ours(group):
    return any(MARK in h.get("command", "") or MARK in h.get("commandWindows", "")
               for h in group.get("hooks", []))


def handler(name, event, timeouts):
    cmd = f'python "{HOOK}" {name}'
    h = {"type": "command", "command": cmd, "timeout": timeouts.get(event, 10)}
    if name == "codex":
        h["commandWindows"] = cmd
    return {"hooks": [h]}


def configure(name, remove):
    cfg = TARGETS[name]
    path = cfg["file"]
    if not path.parent.exists() and name == "codex" and not remove:
        print("codex: ~/.codex not found, skipping")
        return
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    for event in cfg["events"]:
        groups = [g for g in hooks.get(event, []) if not is_ours(g)]
        if not remove:
            groups.append(handler(name, event, cfg["timeouts"]))
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"{name}: {'removed' if remove else 'installed'} -> {path}")


def main():
    args = set(sys.argv[1:])
    names = [n for n in TARGETS if f"--{n}" in args] or list(TARGETS)
    for name in names:
        configure(name, "--remove" in args)


if __name__ == "__main__":
    main()

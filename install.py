"""Register the Agent Watch hooks (idempotent).

    python install.py                  every agent app that is present (Claude Code, Codex, Cursor)
    python install.py --claude         Claude Code only   (~/.claude/settings.json)
    python install.py --codex          Codex only         (~/.codex/hooks.json)
    python install.py --cursor         Cursor only        (~/.cursor/hooks.json)
    python install.py --cursor-approvals
                                       also route Cursor shell commands through the widget
                                       (every command waits for you; see README)
    python install.py --remove         uninstall (combine with --claude / --codex / --cursor)
    python install.py --startup        start the widget when you sign in to Windows
    python install.py --no-startup     stop doing that

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
    "cursor": {
        "file": Path.home() / ".cursor" / "hooks.json",
        # beforeShellExecution is only added with --cursor-approvals.
        "events": ["sessionStart", "beforeSubmitPrompt", "postToolUse", "afterAgentResponse", "stop",
                   "sessionEnd", "beforeShellExecution"],
        "timeouts": {"beforeShellExecution": 110},
    },
}


def is_ours(group):
    if MARK in group.get("command", ""):  # Cursor's flat entries
        return True
    return any(MARK in h.get("command", "") or MARK in h.get("commandWindows", "")
               for h in group.get("hooks", []))


def handler(name, event, timeouts):
    cmd = f'python "{HOOK}" {name}'
    timeout = timeouts.get(event, 10)
    if name == "cursor":
        return {"command": cmd, "timeout": timeout}
    h = {"type": "command", "command": cmd, "timeout": timeout}
    if name == "codex":
        h["commandWindows"] = cmd
    return {"hooks": [h]}


def configure(name, remove, approvals):
    cfg = TARGETS[name]
    path = cfg["file"]
    if not path.parent.exists() and name != "claude" and not remove:
        print(f"{name}: {path.parent} not found, skipping")
        return
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if name == "cursor":
        settings.setdefault("version", 1)
    hooks = settings.setdefault("hooks", {})
    for event in cfg["events"]:
        groups = [g for g in hooks.get(event, []) if not is_ours(g)]
        wanted = event != "beforeShellExecution" or approvals
        if not remove and wanted:
            groups.append(handler(name, event, cfg["timeouts"]))
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    extra = " (with shell approvals)" if name == "cursor" and approvals and not remove else ""
    print(f"{name}: {'removed' if remove else 'installed'}{extra} -> {path}")


def main():
    args = set(sys.argv[1:])
    if args & {"--startup", "--no-startup"}:
        import startup
        (startup.enable if "--startup" in args else startup.disable)()
        print("start with Windows:", "on" if startup.is_enabled() else "off")
        if args <= {"--startup", "--no-startup"}:
            return  # only the startup switch was asked for
    approvals = "--cursor-approvals" in args
    names = [n for n in TARGETS if f"--{n}" in args] or (["cursor"] if approvals else list(TARGETS))
    for name in names:
        configure(name, "--remove" in args, approvals)


if __name__ == "__main__":
    main()

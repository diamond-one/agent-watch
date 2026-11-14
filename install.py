"""Register the Agent Watch hooks in ~/.claude/settings.json (idempotent).

    python install.py            install
    python install.py --remove   uninstall
"""
import json
import sys
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK = Path(__file__).with_name("watch_hook.py").resolve()
MARK = "watch_hook.py"
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
          "Notification", "Stop", "SessionEnd", "PermissionRequest"]


def main():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    hooks = settings.setdefault("hooks", {})
    for event in EVENTS:
        groups = [g for g in hooks.get(event, [])
                  if not any(MARK in h.get("command", "") for h in g.get("hooks", []))]
        if "--remove" not in sys.argv:
            groups.append({"hooks": [{
                "type": "command",
                "command": f'python "{HOOK}"',
                "timeout": 110 if event == "PermissionRequest" else 10,
            }]})
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print("removed" if "--remove" in sys.argv else "installed", "->", SETTINGS)


if __name__ == "__main__":
    main()

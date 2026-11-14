"""Claude Code hook: records each session's state for the Agent Watch widget.

Registered for SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
Notification, Stop, SessionEnd and PermissionRequest (see install.py).
For PermissionRequest it blocks until the widget answers (or times out, in
which case Claude Code falls back to its normal permission dialog).
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

HOME = Path(os.environ.get("AGENT_WATCH_DIR", Path.home() / ".agent-watch"))
SESSIONS = HOME / "sessions"
REQUESTS = HOME / "requests"
WAIT_SECONDS = 100  # hook timeout in settings is 110


def summarize_tool(name, tool_input):
    tool_input = tool_input or {}
    detail = (
        tool_input.get("command")
        or tool_input.get("file_path")
        or tool_input.get("pattern")
        or tool_input.get("url")
        or tool_input.get("description")
        or ""
    )
    detail = " ".join(str(detail).split())
    return f"{name}: {detail}"[:140]


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def main():
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception:
        return
    SESSIONS.mkdir(parents=True, exist_ok=True)
    REQUESTS.mkdir(parents=True, exist_ok=True)

    sid = event.get("session_id")
    if not sid:
        return
    name = event.get("hook_event_name", "")
    path = SESSIONS / f"{sid}.json"

    if name == "SessionEnd":
        path.unlink(missing_ok=True)
        return

    s = load(path)
    s.setdefault("id", sid)
    s.setdefault("steps", [])
    s["cwd"] = event.get("cwd") or s.get("cwd", "")
    s["project"] = Path(s["cwd"]).name or s["cwd"] or "session"
    s["transcript"] = event.get("transcript_path", s.get("transcript", ""))
    s["updated"] = time.time()

    if name == "SessionStart":
        s["status"] = "idle"
    elif name == "UserPromptSubmit":
        s["status"] = "running"
        s["task"] = " ".join(str(event.get("prompt", "")).split())[:120]
        s["steps"] = []
    elif name == "PreToolUse":
        s["status"] = "running"
        s["steps"].append(summarize_tool(event.get("tool_name", ""), event.get("tool_input")))
        s["steps"] = s["steps"][-30:]
    elif name == "PostToolUse":
        s["status"] = "running"
    elif name == "Notification":
        s["status"] = "waiting"
        s["note"] = str(event.get("message", ""))[:160]
    elif name == "Stop":
        s["status"] = "done"
        s["finished"] = time.time()
    elif name == "PermissionRequest":
        s["status"] = "waiting"
        req_id = uuid.uuid4().hex
        s["pending"] = {
            "id": req_id,
            "tool": summarize_tool(event.get("tool_name", ""), event.get("tool_input")),
        }
        save(path, s)
        answer = wait_for_answer(req_id)
        s = load(path)
        s.pop("pending", None)
        s["status"] = "running" if answer else "waiting"
        s["updated"] = time.time()
        save(path, s)
        if answer == "allow":
            decision = {"behavior": "allow"}
        elif answer == "deny":
            decision = {"behavior": "deny", "message": "Denied from Agent Watch"}
        else:
            return  # no answer: fall through to Claude Code's own dialog
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PermissionRequest", "decision": decision}}))
        return

    save(path, s)


def wait_for_answer(req_id):
    resp = REQUESTS / f"{req_id}.json"
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        if resp.exists():
            try:
                answer = json.loads(resp.read_text(encoding="utf-8")).get("answer")
            except Exception:
                answer = None
            resp.unlink(missing_ok=True)
            return answer
        time.sleep(0.25)
    return None


if __name__ == "__main__":
    main()

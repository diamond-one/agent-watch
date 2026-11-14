"""Hook script: records each agent session's state for the Agent Watch widget.

Usage (registered by install.py):  python watch_hook.py <claude|codex>

Works for Claude Code and OpenAI Codex hooks (same event names and stdin fields).
For PermissionRequest it blocks until the widget answers (or times out, in which
case the app falls back to its normal permission dialog).
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
    if not isinstance(tool_input, dict):
        tool_input = {"command": tool_input}
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


CURSOR_EVENTS = {
    "sessionStart": "SessionStart",
    "beforeSubmitPrompt": "UserPromptSubmit",
    "postToolUse": "PostToolUse",
    "stop": "Stop",
    "afterAgentResponse": "AgentResponse",
    "sessionEnd": "SessionEnd",
    "beforeShellExecution": "PermissionRequest",  # only registered with --cursor-approvals
}


def normalize_cursor(e):
    """Map a Cursor hook payload onto the Claude Code / Codex shape used below."""
    name = CURSOR_EVENTS.get(e.get("hook_event_name"))
    if not name:
        return None
    roots = e.get("workspace_roots") or []
    has_cmd = "command" in e and "tool_input" not in e
    out = {
        "hook_event_name": name,
        "session_id": e.get("conversation_id") or e.get("session_id"),
        "cwd": roots[0] if roots else e.get("cwd", ""),
        "transcript_path": e.get("transcript_path"),
        "prompt": e.get("prompt", ""),
        "text": e.get("text", ""),
        "tool_name": e.get("tool_name") or ("Shell" if has_cmd else ""),
        "tool_input": {"command": e["command"]} if has_cmd else e.get("tool_input") or {},
    }
    if name == "Stop" and e.get("status") not in (None, "completed"):
        out["hook_event_name"] = "Interrupt"  # aborted / error: not a clean finish
    return out


def clean_text(text, limit=320):
    """Flatten an assistant message into a short plain-text blurb."""
    text = str(text or "")
    for mark in ("**", "__", "`", "```"):
        text = text.replace(mark, "")
    lines = [ln.strip().lstrip("#>-* ").strip() for ln in text.splitlines()]
    text = " ".join(ln for ln in lines if ln)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def last_assistant_text(transcript_path):
    """Final assistant message from a Claude Code transcript (JSONL), or ''."""
    return last_assistant_entry(transcript_path)[0]


def _epoch(stamp):
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def last_assistant_entry(transcript_path):
    """(text, epoch seconds) of the latest assistant message or wrap_up recap, or ('', 0)."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 400_000))
            lines = fh.read().decode("utf-8", "ignore").splitlines()
    except Exception:
        return "", 0.0
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        msg = entry.get("message") if isinstance(entry, dict) else None
        if entry.get("type") != "assistant" or not isinstance(msg, dict):
            continue
        when = _epoch(entry.get("timestamp"))
        content = msg.get("content")
        if isinstance(content, str):
            return content, when
        # Walk the blocks backwards so the latest wins. Besides plain text, the Claude desktop
        # app ends a turn with a wrap_up tool call whose recap is the real summary.
        for block in reversed(content or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text", "").strip():
                return block["text"], when
            if block.get("type") == "tool_use" and str(block.get("name", "")).endswith("wrap_up"):
                recap = (block.get("input") or {}).get("recap")
                if recap:
                    return recap, when
    return "", 0.0


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
    source = sys.argv[1] if len(sys.argv) > 1 else "claude"
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception:
        return
    if source == "cursor":
        if event.get("hook_event_name") == "beforeSubmitPrompt":
            print(json.dumps({"continue": True}))  # this hook must always answer
        event = normalize_cursor(event)
        if event is None:
            return
    SESSIONS.mkdir(parents=True, exist_ok=True)
    REQUESTS.mkdir(parents=True, exist_ok=True)

    raw_id = event.get("session_id")
    if not raw_id:
        return
    key = f"{source}-{raw_id}"
    name = event.get("hook_event_name", "")
    path = SESSIONS / f"{key}.json"

    if name == "SessionEnd":
        path.unlink(missing_ok=True)
        return

    s = load(path)
    s["id"] = key
    s["source"] = source
    s.setdefault("steps", [])
    s["cwd"] = event.get("cwd") or s.get("cwd", "")
    s["project"] = Path(s["cwd"]).name or s["cwd"] or "session"
    s["transcript"] = event.get("transcript_path") or s.get("transcript", "")
    s["updated"] = time.time()

    if name == "SessionStart":
        s["status"] = "idle"
    elif name == "UserPromptSubmit":
        s["status"] = "running"
        prompt = " ".join(str(event.get("prompt", "")).split())
        # Harness-injected turns (<task-notification>, <agent-message>, ...) are not user tasks.
        if prompt and not prompt.startswith("<"):
            s["task"] = prompt[:120]
            s["steps"] = []
            s["turn_started"] = time.time()  # lets the widget ignore narration from older turns
            s.pop("summary", None)
    elif name == "PreToolUse":
        s["status"] = "running"
        s["steps"].append(summarize_tool(event.get("tool_name", ""), event.get("tool_input")))
        s["steps"] = s["steps"][-30:]
    elif name == "PostToolUse":
        s["status"] = "running"
        if source == "cursor":  # Cursor has no pre-tool hook here, so log the step afterwards
            s["steps"].append(summarize_tool(event.get("tool_name", ""), event.get("tool_input")))
            s["steps"] = s["steps"][-30:]
    elif name == "Notification":
        s["status"] = "waiting"
        s["note"] = str(event.get("message", ""))[:160]
    elif name == "Interrupt":
        s["status"] = "idle"
    elif name == "AgentResponse":  # Cursor: the reply text arrives just before stop
        s["summary"] = clean_text(event.get("text"))
    elif name == "Stop":
        s["status"] = "done"
        s["finished"] = time.time()
        text = event.get("last_assistant_message")
        if not text:
            time.sleep(0.5)  # the transcript can lag a moment behind the Stop event
            text = last_assistant_text(event.get("transcript_path"))
        if text:  # Codex hands it over directly; Claude Code needs the transcript
            s["summary"] = clean_text(text)
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
        if source == "cursor":
            if answer == "allow":
                out = {"permission": "allow"}
            elif answer == "deny":
                out = {"permission": "deny", "user_message": "Denied from Agent Watch"}
            else:
                out = {"permission": "ask"}  # no answer: let Cursor decide
            print(json.dumps(out))
            return
        if answer == "allow":
            decision = {"behavior": "allow"}
        elif answer == "deny":
            decision = {"behavior": "deny", "message": "Denied from Agent Watch"}
        else:
            return  # no answer: fall through to the app's own dialog
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

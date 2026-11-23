#!/usr/bin/env python3
"""Claude Code Stop / Notification hook -> POST to local Passport Hub."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

HUB_URL = os.environ.get("PASSPORT_HUB_URL", "http://127.0.0.1:8765").rstrip("/")


def main() -> None:
    raw = sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    cwd = data.get("cwd") or os.getcwd()
    project = Path(cwd).name or "Claude"
    event = data.get("hook_event_name", "Stop")
    status = "waiting_input" if event == "Notification" else "done"
    summary = (
        data.get("message")
        or data.get("last_assistant_message")
        or f"Claude Code {event} in {project}"
    )

    payload = {
        "source": "claude",
        "project": project,
        "title": f"{project} ({event})",
        "summary": str(summary)[:300],
        "status": status,
        "session_id": data.get("session_id", ""),
        "tmux_target": os.environ.get("TMUX_PANE", ""),
        "cwd": cwd,
    }
    req = urllib.request.Request(
        f"{HUB_URL}/api/hooks/report",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception:
        # Never block Claude Code if Hub is not running.
        pass


if __name__ == "__main__":
    main()

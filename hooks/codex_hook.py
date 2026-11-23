#!/usr/bin/env python3
"""Codex CLI notify hook -> POST to local Passport Hub.

In ~/.codex/config.toml:
  notify = ["python3", "/abs/path/to/passport-hub/hooks/codex_hook.py"]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

HUB_URL = os.environ.get("PASSPORT_HUB_URL", "http://127.0.0.1:8765").rstrip("/")


def main() -> None:
    raw = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read().strip()
    data = json.loads(raw) if raw else {}
    cwd = data.get("cwd") or os.getcwd()
    project = Path(cwd).name or "Codex"
    summary = data.get("last-assistant-message") or data.get("message") or f"Codex turn complete in {project}"

    payload = {
        "source": "codex",
        "project": project,
        "title": f"{project} ready",
        "summary": str(summary)[:300],
        "status": "done",
        "session_id": data.get("thread-id", "") or data.get("session_id", ""),
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
        pass


if __name__ == "__main__":
    main()

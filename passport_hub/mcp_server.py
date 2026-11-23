#!/usr/bin/env python3
"""Minimal zero-dependency stdio MCP Server for Antigravity / Trae / Claude / Codex.

Provides bidirectional communication with the user's FoloToy AI Passport hardware:
1. report_task_status   - Push task progress/completion/decision card to AI Passport.
2. ask_passport_user    - Push a prompt/question (with optional decision buttons) to AI
                          Passport and wait for the user's voice command or button choice,
                          returning the exact reply back to THIS conversation (no focus stealing).
3. check_passport_reply - Non-blocking check for any queued voice/decision reply on a task.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any

HUB_URL = os.environ.get("PASSPORT_HUB_URL", "http://127.0.0.1:8765").rstrip("/")

TOOLS = [
    {
        "name": "report_task_status",
        "description": (
            "Report AI task completion, progress, or waiting-for-input status to the user's "
            "FoloToy AI Passport hardware badge so it chimes, updates the screen, and reads the summary aloud."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["antigravity", "codex", "trae", "claude"],
                    "default": "antigravity",
                    "description": "Which AI client is reporting.",
                },
                "project": {"type": "string", "description": "Short project or workspace name (ASCII preferred)."},
                "title": {"type": "string", "description": "Short task title (<40 chars, ASCII preferred)."},
                "summary": {
                    "type": "string",
                    "description": "Concise spoken summary (Chinese or English) to read aloud on AI Passport.",
                },
                "status": {
                    "type": "string",
                    "enum": ["done", "waiting_input", "running", "error"],
                    "default": "done",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                    "description": "Optional up to 3 short decision buttons for waiting_input.",
                },
                "task_id": {"type": "string", "description": "Optional stable ID to update an existing task."},
                "session_id": {"type": "string", "description": "Optional CLI session ID for resume."},
                "tmux_target": {"type": "string", "description": "Optional tmux pane target (e.g. main:0.1)."},
                "cwd": {"type": "string", "description": "Optional workspace path."},
            },
            "required": ["project", "title", "summary"],
        },
    },
    {
        "name": "ask_passport_user",
        "description": (
            "Send a question, decision prompt, or task handoff to the user's FoloToy AI Passport "
            "and WAIT for the user to reply by voice (Hold OK) or button decision (UP/DN + OK). "
            "Returns the exact transcribed voice command or selected option directly to this conversation "
            "without touching the mouse, keyboard, or window focus."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Short project name (e.g. AIPassport)."},
                "title": {"type": "string", "description": "Short prompt title (<32 chars, ASCII)."},
                "summary": {
                    "type": "string",
                    "description": "Spoken message read aloud on AI Passport asking the user what to do.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                    "description": "Optional up to 3 short button choices; omit to ask for a free-form voice reply.",
                },
                "task_id": {"type": "string", "description": "Optional stable task ID to reuse across turns."},
                "source": {
                    "type": "string",
                    "enum": ["antigravity", "codex", "trae", "claude"],
                    "default": "antigravity",
                },
                "timeout_sec": {
                    "type": "number",
                    "default": 180,
                    "description": "Max seconds to wait for the user's voice or button reply (default 180).",
                },
            },
            "required": ["project", "title", "summary"],
        },
    },
    {
        "name": "check_passport_reply",
        "description": (
            "Non-blocking check for any queued voice command or button decision sent by the user "
            "from FoloToy AI Passport for a given task_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task_id previously returned by report_task_status or ask_passport_user."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_passport_devices",
        "description": (
            "Scan connected USB serial ports on this Mac and return candidate FoloToy AI Passport "
            "(ESP32-C3) devices ready for firmware flashing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_firmware_releases",
        "description": (
            "List published FoloToy AI Passport firmware releases available on GitHub Releases "
            "(including version tags, release notes, and binary asset info)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Optional GitHub repo in 'owner/repo' format (defaults to TironFox/ai-passport-mcp).",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max number of releases to return (default 10).",
                },
            },
        },
    },
    {
        "name": "flash_passport_firmware",
        "description": (
            "Download a verified merged FoloToy AI Passport firmware image from GitHub Releases "
            "(or use a local .bin path) and flash it to an ESP32-C3 device at 0x0 via esptool. "
            "First call with confirm=false (default) to download/verify and inspect the target port; "
            "then ask the user to confirm and call again with confirm=true to write flash."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "string",
                    "description": "Serial port path from list_passport_devices (e.g. /dev/cu.usbmodem1101).",
                },
                "version": {
                    "type": "string",
                    "default": "latest",
                    "description": "GitHub Release tag to flash (e.g. v1.0.0) or 'latest'.",
                },
                "repo": {
                    "type": "string",
                    "description": "Optional GitHub repo in 'owner/repo' format.",
                },
                "baud": {
                    "type": "integer",
                    "default": 460800,
                    "description": "Flashing baud rate (default 460800).",
                },
                "confirm": {
                    "type": "boolean",
                    "default": False,
                    "description": "Set to true only after the user confirms the target serial port and firmware version.",
                },
                "local_bin_path": {
                    "type": "string",
                    "description": "Optional local path to a merged .bin firmware file instead of downloading from GitHub.",
                },
            },
            "required": ["port"],
        },
    },
]


def _post_hub(path: str, payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{HUB_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_hub(path: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(f"{HUB_URL}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "passport-hub-mcp", "version": "0.3.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = dict(params.get("arguments") or {})

        if name == "report_task_status":
            args.setdefault("source", "antigravity")
            try:
                res = _post_hub("/api/hooks/report", args)
                text = json.dumps(res, ensure_ascii=False)
                is_error = False
            except Exception as exc:
                text = f"Failed to reach Passport Hub at {HUB_URL}: {exc}"
                is_error = True
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
            }

        if name == "ask_passport_user":
            timeout_sec = float(args.pop("timeout_sec", 180) or 180)
            timeout_sec = min(600.0, max(5.0, timeout_sec))
            report_payload = {
                "source": args.get("source", "antigravity"),
                "project": args.get("project", "Workspace"),
                "title": args.get("title", "Need Input"),
                "summary": args.get("summary", ""),
                "status": "waiting_input",
            }
            if args.get("options"):
                report_payload["options"] = args["options"]
            if args.get("task_id"):
                report_payload["task_id"] = args["task_id"]
            try:
                created = _post_hub("/api/hooks/report", report_payload)
                tid = created["task"]["task_id"]
                quoted = urllib.parse.quote(tid, safe="")
                waited = _get_hub(
                    f"/api/tasks/{quoted}/wait?timeout={int(timeout_sec)}",
                    timeout=timeout_sec + 10.0,
                )
                waited["task_id"] = tid
                text = json.dumps(waited, ensure_ascii=False)
                is_error = False
            except Exception as exc:
                text = f"Failed to ask AI Passport user via {HUB_URL}: {exc}"
                is_error = True
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
            }

        if name == "check_passport_reply":
            tid = str(args.get("task_id") or "").strip()
            if not tid:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": "task_id is required"}],
                        "isError": True,
                    },
                }
            try:
                quoted = urllib.parse.quote(tid, safe="")
                res = _get_hub(f"/api/tasks/{quoted}/reply")
                text = json.dumps(res, ensure_ascii=False)
                is_error = False
            except Exception as exc:
                text = f"Failed to check reply from {HUB_URL}: {exc}"
                is_error = True
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
            }

        if name == "list_passport_devices":
            try:
                from passport_hub.firmware import list_serial_devices

                res = {"devices": list_serial_devices()}
                text = json.dumps(res, ensure_ascii=False)
                is_error = False
            except Exception as exc:
                text = f"Failed to scan serial devices: {exc}"
                is_error = True
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
            }

        if name == "list_firmware_releases":
            try:
                from passport_hub.firmware import list_firmware_releases

                res = list_firmware_releases(
                    repo=args.get("repo"),
                    limit=int(args.get("limit") or 10),
                )
                text = json.dumps(res, ensure_ascii=False)
                is_error = False
            except Exception as exc:
                text = f"Failed to list firmware releases: {exc}"
                is_error = True
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
            }

        if name == "flash_passport_firmware":
            try:
                from passport_hub.firmware import flash_firmware

                res = flash_firmware(
                    port=str(args.get("port") or ""),
                    version=str(args.get("version") or "latest"),
                    repo=args.get("repo"),
                    baud=int(args.get("baud") or 460800),
                    confirm=bool(args.get("confirm", False)),
                    local_bin_path=args.get("local_bin_path"),
                )
                text = json.dumps(res, ensure_ascii=False)
                is_error = res.get("status") == "error"
            except Exception as exc:
                text = f"Failed to flash firmware: {exc}"
                is_error = True
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {name}"},
        }
    if req_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unsupported method: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import queue
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .asr import OfflineASR
from .dispatcher import ClientDispatcher, DispatchResult
from .store import TaskStore
from .tts import OfflineTTS


class PassportHubServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        store: TaskStore | None = None,
        tts: OfflineTTS | None = None,
        asr: OfflineASR | None = None,
        dispatcher: ClientDispatcher | None = None,
    ) -> None:
        self.store = store or TaskStore()
        self.tts = tts or OfflineTTS()
        self.asr = asr or OfflineASR()
        self.dispatcher = dispatcher or ClientDispatcher()
        super().__init__(server_address, PassportHubHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        import sys

        _, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class PassportHubHandler(BaseHTTPRequestHandler):
    server: PassportHubServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> bytes:
        # Handle both Content-Length and Transfer-Encoding: chunked (used by ESP32 voice stream)
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            chunks = bytearray()
            while True:
                size_line = self.rfile.readline()
                if not size_line:
                    break
                size_str = size_line.split(b";", 1)[0].strip()
                if not size_str:
                    continue
                chunk_size = int(size_str, 16)
                if chunk_size == 0:
                    self.rfile.readline()
                    break
                chunks.extend(self.rfile.read(chunk_size))
                self.rfile.readline()  # trailing CRLF
            return bytes(chunks)
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length > 0 else b""

    def _read_json(self) -> dict[str, Any]:
        raw = self._read_body()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _handle_sse_stream(self) -> None:
        """Active push channel (Server-Sent Events) for ESP32 AI Passport."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        sub = self.server.store.subscribe()
        try:
            # Push initial snapshot immediately
            initial = json.dumps(self.server.store.device_snapshot(), ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"data: {initial}\n\n".encode("utf-8"))
            self.wfile.flush()

            while True:
                try:
                    event = sub.get(timeout=15.0)
                    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.store.unsubscribe(sub)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "seq": self.server.store.seq,
                    "unread": self.server.store.unread_count(),
                    "online": self.server.store.get_online_clients(),
                },
            )
            return

        if path == "/api/events":
            self._handle_sse_stream()
            return

        if path == "/api/state":
            self._send_json(HTTPStatus.OK, self.server.store.device_snapshot())
            return

        if path == "/api/badge":
            self._send_json(HTTPStatus.OK, self.server.store.get_badge().to_dict())
            return

        if path == "/api/tasks":
            compact = query.get("compact", ["0"])[0] == "1"
            limit = int(query.get("limit", ["8"])[0])
            if compact:
                self._send_json(HTTPStatus.OK, self.server.store.device_snapshot(limit=max(1, min(limit, 16))))
            else:
                items = self.server.store.list_tasks(limit=max(1, min(limit, 16)))
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "seq": self.server.store.seq,
                        "unread": self.server.store.unread_count(),
                        "online": self.server.store.get_online_clients(),
                        "tasks": [t.to_dict() for t in items],
                    },
                )
            return

        parts = [p for p in path.split("/") if p]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "tts.pcm":
            task_id = urllib.parse.unquote(parts[2])
            task = self.server.store.get_task(task_id)
            if task is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "task_not_found"})
                return
            self.server.store.mark_read(task_id)
            spoken = f"{task.source_tag}，{task.project}，{task.summary}"
            try:
                pcm = self.server.tts.synthesize_pcm16(spoken)
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/pcm; rate=16000; bits=16; channels=1")
            self.send_header("Content-Length", str(len(pcm)))
            self.end_headers()
            try:
                self.wfile.write(pcm)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "wait":
            task_id = urllib.parse.unquote(parts[2])
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                timeout_sec = min(600.0, max(1.0, float((qs.get("timeout") or ["180"])[0])))
            except ValueError:
                timeout_sec = 180.0
            reply = self.server.store.wait_for_reply(task_id, timeout_sec=timeout_sec)
            if reply is None:
                self._send_json(HTTPStatus.OK, {"ok": True, "received": False, "timeout": True})
            else:
                self._send_json(HTTPStatus.OK, {"ok": True, "received": True, "reply": reply})
            return

        if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "reply":
            task_id = urllib.parse.unquote(parts[2])
            reply = self.server.store.pop_reply(task_id)
            self._send_json(HTTPStatus.OK, {"ok": True, "received": reply is not None, "reply": reply})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/badge":
            data = self._read_json()
            badge = self.server.store.update_badge(data)
            self._send_json(HTTPStatus.OK, badge.to_dict())
            return

        if path in ("/api/hooks/report", "/api/tasks"):
            data = self._read_json()
            item = self.server.store.upsert_task(
                source=str(data.get("source", "system")),
                project=str(data.get("project", "Workspace")),
                title=str(data.get("title", "Task Completed")),
                summary=str(data.get("summary", "")),
                status=str(data.get("status", "done")),
                options=data.get("options") if isinstance(data.get("options"), list) else None,
                task_id=data.get("task_id"),
                session_id=str(data.get("session_id", "")),
                tmux_target=str(data.get("tmux_target", "")),
                app_name=str(data.get("app_name", "")),
                cwd=str(data.get("cwd", "")),
            )
            self._send_json(HTTPStatus.CREATED, {"ok": True, "task": item.to_dict()})
            return

        parts = [p for p in path.split("/") if p]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks":
            task_id = urllib.parse.unquote(parts[2])
            action = parts[3]
            task = self.server.store.get_task(task_id)
            if task is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "task_not_found"})
                return

            if action == "read":
                self.server.store.mark_read(task_id)
                self._send_json(HTTPStatus.OK, {"ok": True, "task_id": task_id})
                return

            if action == "voice":
                pcm_bytes = self._read_body()
                dur = len(pcm_bytes) / 32000.0
                transcript, engine = self.server.asr.transcribe_pcm16(pcm_bytes)
                delivered_to_mcp = self.server.store.submit_reply(
                    task_id, transcript, kind="voice", asr_engine=engine
                )
                if delivered_to_mcp:
                    dispatch_res = DispatchResult(
                        ok=True,
                        method="mcp_waiter",
                        detail="Delivered directly to waiting Antigravity MCP conversation",
                    )
                else:
                    dispatch_res = self.server.dispatcher.dispatch(task, transcript)
                print(
                    f"[passport-hub] VOICE task={task_id} ({dur:.1f}s) "
                    f"asr={engine} text={transcript!r} -> "
                    f"dispatch({dispatch_res.method}, ok={dispatch_res.ok}): {dispatch_res.detail}",
                    flush=True,
                )
                self.server.store.mark_running(task_id, transcript)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "task_id": task_id,
                        "transcript": transcript,
                        "asr_engine": engine,
                        "dispatched": dispatch_res.ok,
                        "dispatch_method": dispatch_res.method,
                        "dispatch_detail": dispatch_res.detail,
                    },
                )
                return

            if action in ("reply", "decision"):
                data = self._read_json()
                text = str(data.get("decision") or data.get("text") or "").strip()
                delivered_to_mcp = self.server.store.submit_reply(
                    task_id, text, kind=action
                )
                if delivered_to_mcp:
                    dispatch_res = DispatchResult(
                        ok=True,
                        method="mcp_waiter",
                        detail="Delivered directly to waiting Antigravity MCP conversation",
                    )
                else:
                    dispatch_res = self.server.dispatcher.dispatch(task, text)
                print(
                    f"[passport-hub] DECISION task={task_id} text={text!r} -> "
                    f"dispatch({dispatch_res.method}, ok={dispatch_res.ok}): {dispatch_res.detail}",
                    flush=True,
                )
                self.server.store.mark_running(task_id, text)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "task_id": task_id,
                        "text": text,
                        "dispatched": dispatch_res.ok,
                        "dispatch_method": dispatch_res.method,
                        "dispatch_detail": dispatch_res.detail,
                    },
                )
                return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

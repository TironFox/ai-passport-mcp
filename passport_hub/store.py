from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any

from .models import BadgeProfile, TaskItem


class TaskStore:
    """Thread-safe multi-task queue + SSE event broadcaster for AI Passport."""

    def __init__(self, max_tasks: int = 16) -> None:
        self._lock = threading.RLock()
        self._max_tasks = max_tasks
        self._tasks: dict[str, TaskItem] = {}
        self._order: list[str] = []
        self._seq: int = 0
        self._badge = BadgeProfile()
        self._online: dict[str, bool] = {
            "antigravity": True,
            "claude": False,
            "codex": False,
            "trae": False,
        }
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self._reply_queues: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._mcp_waiters: dict[str, int] = {}

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=16)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast_locked(self, event_type: str = "sync") -> None:
        payload = self.device_snapshot()
        payload["event"] = event_type
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except queue.Empty:
                    pass

    def get_online_clients(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._online)

    def update_online_clients(self, online: dict[str, bool]) -> bool:
        with self._lock:
            changed = False
            for k in ("antigravity", "claude", "codex", "trae"):
                if k in online and bool(online[k]) != self._online.get(k):
                    self._online[k] = bool(online[k])
                    changed = True
            if changed:
                self._seq += 1
                self._broadcast_locked("presence")
            return changed

    def device_snapshot(self, limit: int = 8) -> dict[str, Any]:
        with self._lock:
            items = [self._tasks[tid].to_device_dict() for tid in self._order[:limit] if tid in self._tasks]
            return {
                "seq": self._seq,
                "unread": self.unread_count(),
                "online": {
                    "AGY": self._online.get("antigravity", False),
                    "CLD": self._online.get("claude", False),
                    "CDX": self._online.get("codex", False),
                    "TRAE": self._online.get("trae", False),
                },
                "tasks": items,
            }

    def get_badge(self) -> BadgeProfile:
        with self._lock:
            return self._badge

    def update_badge(self, data: dict[str, Any]) -> BadgeProfile:
        with self._lock:
            for key in ("name", "role", "handle", "qr_text", "tagline"):
                if key in data and isinstance(data[key], str):
                    setattr(self._badge, key, data[key].strip()[:96])
            self._seq += 1
            self._broadcast_locked("badge")
            return self._badge

    def upsert_task(
        self,
        *,
        source: str,
        project: str,
        title: str,
        summary: str,
        status: str = "done",
        options: list[str] | None = None,
        task_id: str | None = None,
        session_id: str = "",
        tmux_target: str = "",
        app_name: str = "",
        cwd: str = "",
        mark_unread: bool = True,
    ) -> TaskItem:
        with self._lock:
            tid = (task_id or "").strip() or f"{source[:3]}-{uuid.uuid4().hex[:8]}"
            now = time.time()
            existing = self._tasks.get(tid)
            if existing is not None:
                existing.source = source
                existing.project = project
                existing.title = title
                existing.summary = summary
                existing.status = status
                if options is not None:
                    existing.options = list(options)
                existing.unread = mark_unread
                existing.updated_at = now
                if session_id:
                    existing.session_id = session_id
                if tmux_target:
                    existing.tmux_target = tmux_target
                if app_name:
                    existing.app_name = app_name
                if cwd:
                    existing.cwd = cwd
                item = existing.normalize()
                if tid in self._order:
                    self._order.remove(tid)
                self._order.insert(0, tid)
            else:
                item = TaskItem(
                    task_id=tid,
                    source=source,
                    project=project,
                    title=title,
                    summary=summary,
                    status=status,
                    options=list(options or []),
                    unread=mark_unread,
                    session_id=session_id,
                    tmux_target=tmux_target,
                    app_name=app_name,
                    cwd=cwd,
                    updated_at=now,
                ).normalize()
                self._tasks[tid] = item
                self._order.insert(0, tid)
                while len(self._order) > self._max_tasks:
                    dropped = self._order.pop()
                    self._tasks.pop(dropped, None)

            if item.source in self._online:
                self._online[item.source] = True
            self._seq += 1
            self._broadcast_locked("task")
            return item

    def get_task(self, task_id: str) -> TaskItem | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 8) -> list[TaskItem]:
        with self._lock:
            return [self._tasks[tid] for tid in self._order[:limit] if tid in self._tasks]

    def unread_count(self) -> int:
        with self._lock:
            return sum(1 for tid in self._order if self._tasks[tid].unread)

    def mark_read(self, task_id: str) -> TaskItem | None:
        with self._lock:
            item = self._tasks.get(task_id)
            if item is None:
                return None
            if item.unread:
                item.unread = False
                self._seq += 1
                self._broadcast_locked("read")
            return item

    def mark_running(self, task_id: str, reply_text: str) -> TaskItem | None:
        with self._lock:
            item = self._tasks.get(task_id)
            if item is None:
                return None
            item.status = "running"
            item.unread = False
            item.last_reply = reply_text
            item.summary = f"Running: {reply_text}"[:500]
            item.updated_at = time.time()
            self._seq += 1
            self._broadcast_locked("progress")
            return item

    def _get_reply_queue(self, task_id: str) -> queue.Queue[dict[str, Any]]:
        with self._lock:
            q = self._reply_queues.get(task_id)
            if q is None:
                q = queue.Queue(maxsize=16)
                self._reply_queues[task_id] = q
            return q

    def submit_reply(
        self,
        task_id: str,
        text: str,
        *,
        kind: str = "voice",
        asr_engine: str = "",
    ) -> bool:
        """Enqueue a user voice/decision reply for a task. Returns True if an MCP waiter is listening."""
        q = self._get_reply_queue(task_id)
        entry = {
            "task_id": task_id,
            "kind": kind,
            "text": text,
            "asr_engine": asr_engine,
            "timestamp": time.time(),
        }
        try:
            q.put_nowait(entry)
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(entry)
            except queue.Empty:
                pass
        with self._lock:
            return self._mcp_waiters.get(task_id, 0) > 0

    def wait_for_reply(self, task_id: str, timeout_sec: float = 180.0) -> dict[str, Any] | None:
        q = self._get_reply_queue(task_id)
        with self._lock:
            self._mcp_waiters[task_id] = self._mcp_waiters.get(task_id, 0) + 1
        try:
            return q.get(timeout=max(0.1, float(timeout_sec)))
        except queue.Empty:
            return None
        finally:
            with self._lock:
                cur = self._mcp_waiters.get(task_id, 1) - 1
                if cur <= 0:
                    self._mcp_waiters.pop(task_id, None)
                else:
                    self._mcp_waiters[task_id] = cur

    def pop_reply(self, task_id: str) -> dict[str, Any] | None:
        q = self._get_reply_queue(task_id)
        try:
            return q.get_nowait()
        except queue.Empty:
            return None

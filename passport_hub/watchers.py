from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .store import TaskStore


_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.DOTALL | re.IGNORECASE)
_META_BLOCK_RE = re.compile(
    r"<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_INSTRUCTIONS)>.*?</(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_INSTRUCTIONS)>",
    re.DOTALL | re.IGNORECASE,
)
_XML_TAG_RE = re.compile(r"</?[A-Za-z_][A-Za-z0-9_\s-]*>")


_WORKSPACE_CONTAINERS = {"trae-cn", "trae", "projects", "workspace", "workspaces", "code", "dev", "repos", "github"}


def _extract_project_from_text(text: str) -> str:
    """Extract the workspace/project directory name from a path in text, never a filename."""
    if "/Documents/" not in text:
        return ""
    for token in re.split(r"[\s'\"`,;()\[\]{}]+", text):
        if "/Documents/" not in token:
            continue
        after = token.split("/Documents/", 1)[1]
        parts = [p for p in after.split("/") if p]
        if not parts:
            continue
        if parts[0].lower() in _WORKSPACE_CONTAINERS and len(parts) >= 2:
            cand = parts[1]
        else:
            cand = parts[0]
        if "." in cand and len(cand.rsplit(".", 1)[-1]) <= 4:
            continue
        if cand:
            return cand[:18]
    return ""


def _clean_user_prompt(content: str) -> str:
    """Strip Antigravity XML wrapper tags like <USER_REQUEST> from USER_INPUT content."""
    if not content:
        return ""
    m = _USER_REQUEST_RE.search(content)
    text = m.group(1) if m else _META_BLOCK_RE.sub(" ", content)
    text = _XML_TAG_RE.sub(" ", text)
    return " ".join(text.split()).strip()


PROCESS_PATTERNS = {
    "antigravity": ["Antigravity", "agy"],
    "claude": ["claude"],
    "codex": ["codex"],
    "trae": ["Trae"],
}


def detect_running_clients() -> dict[str, bool]:
    """Check which of the 4 AI clients are currently running on macOS."""
    status = {k: False for k in PROCESS_PATTERNS}
    try:
        proc = subprocess.run(
            ["ps", "-axo", "comm="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        lines = proc.stdout.splitlines()
        joined = "\n".join(lines).lower()
        for client, patterns in PROCESS_PATTERNS.items():
            for pat in patterns:
                if pat.lower() in joined:
                    status[client] = True
                    break
    except Exception:
        pass
    return status


def _project_from_workspace_uris(raw_uris: str) -> str:
    """Extract the workspace folder name from Antigravity's workspace_uris JSON column."""
    if not raw_uris:
        return "Antigravity"
    try:
        uris = json.loads(raw_uris)
    except (json.JSONDecodeError, TypeError):
        return "Antigravity"
    if not isinstance(uris, list) or not uris:
        return "Antigravity"
    first = str(uris[0]).strip()
    if not first:
        return "Antigravity"
    parsed = urllib.parse.urlparse(first)
    path_str = urllib.parse.unquote(parsed.path if parsed.scheme == "file" else first).rstrip("/")
    name = Path(path_str).name
    return name[:23] if name else "Antigravity"


def _parse_iso_timestamp(ts_str: str) -> float:
    if not ts_str:
        return time.time()
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return time.time()


def _scan_antigravity_db(db_path: Path, limit: int = 5) -> list[dict] | None:
    """Read authoritative project name & conversation title from Antigravity's SQLite DB."""
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        try:
            rows = conn.execute(
                """
                SELECT conversation_id, title, preview, workspace_uris, status, not_fully_idle, last_modified_time
                FROM conversation_summaries
                WHERE parent_conversation_id = '' AND nesting_depth = 0 AND killed = 0
                ORDER BY last_modified_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    results: list[dict] = []
    for conv_id, title, preview, workspace_uris, raw_status, not_fully_idle, last_mod in rows:
        project = _project_from_workspace_uris(workspace_uris)
        clean_title = (title or "").strip() or f"AGY {str(conv_id)[:8]}"
        summary = " ".join((preview or clean_title).split())[:180]
        is_running = (raw_status == "CASCADE_RUN_STATUS_RUNNING") or bool(not_fully_idle)
        results.append(
            {
                "task_id": f"agy-{str(conv_id)[:8]}",
                "source": "antigravity",
                "project": project,
                "title": clean_title,
                "summary": summary,
                "status": "running" if is_running else "done",
                "updated_at": _parse_iso_timestamp(str(last_mod or "")),
            }
        )
    return results


def scan_antigravity_brain(brain_dir: Path | None = None, limit: int = 5) -> list[dict]:
    """Read recent active Antigravity conversations (preferring conversation_summaries.db)."""
    root = brain_dir or (Path.home() / ".gemini" / "antigravity" / "brain")
    db_results = _scan_antigravity_db(root.parent / "conversation_summaries.db", limit=limit)
    if db_results is not None:
        return db_results

    if not root.exists() or not root.is_dir():
        return []

    candidates: list[tuple[float, Path]] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        transcript = child / ".system_generated" / "logs" / "transcript.jsonl"
        target = transcript if transcript.exists() else child
        try:
            mtime = target.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, child))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    results: list[dict] = []

    for mtime, conv_dir in candidates[:limit]:
        conv_id = conv_dir.name
        project = "Antigravity"
        title = f"AGY {conv_id[:8]}"
        summary = "Active Antigravity session"
        status = "done"

        transcript = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript.exists():
            try:
                lines = transcript.read_text(encoding="utf-8", errors="ignore").splitlines()
                # 1. Extract topic title & workspace project name from the earliest user prompt
                first_user_title = ""
                for raw in lines[:30]:
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    content = (obj.get("content") or "").strip()
                    if not first_user_title and obj.get("type") == "USER_INPUT" and content:
                        clean = _clean_user_prompt(content)
                        if len(clean) >= 2:
                            first_user_title = clean[:28]
                    if project == "Antigravity":
                        found_proj = _extract_project_from_text(content)
                        if not found_proj and obj.get("tool_calls"):
                            found_proj = _extract_project_from_text(json.dumps(obj["tool_calls"], ensure_ascii=False))
                        if found_proj:
                            project = found_proj

                # 2. Extract latest status & summary from recent turns
                latest_user_title = ""
                for raw in reversed(lines[-40:]):
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    step_type = obj.get("type", "")
                    content = (obj.get("content") or "").strip()
                    if step_type == "USER_INPUT" and content and not latest_user_title:
                        clean = _clean_user_prompt(content)
                        if len(clean) >= 2:
                            latest_user_title = clean[:28]
                    elif step_type == "PLANNER_RESPONSE" and content and summary == "Active Antigravity session":
                        summary = " ".join(content.split())[:180]
                        if obj.get("status") != "DONE":
                            status = "running"

                chosen = first_user_title or latest_user_title
                if chosen:
                    prefix = f"{project}-"
                    title = chosen if chosen.startswith(prefix) else f"{prefix}{chosen}"
                    title = title[:39]
            except OSError:
                pass

        results.append(
            {
                "task_id": f"agy-{conv_id[:8]}",
                "source": "antigravity",
                "project": project,
                "title": title,
                "summary": summary,
                "status": status,
                "updated_at": mtime,
            }
        )
    return results


class MacClientWatcher:
    """Background daemon that monitors running AI clients and Antigravity sessions."""

    def __init__(
        self,
        store: TaskStore,
        on_change: Callable[[], None] | None = None,
        interval_sec: float = 3.0,
    ) -> None:
        self._store = store
        self._on_change = on_change
        self._interval = interval_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen_mtimes: dict[str, float] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def sync_once(self) -> bool:
        changed = False
        online = detect_running_clients()
        if self._store.update_online_clients(online):
            changed = True

        for item in scan_antigravity_brain(limit=5):
            tid = item["task_id"]
            mtime = item["updated_at"]
            prev = self._seen_mtimes.get(tid)
            if prev is None or mtime > prev + 0.5:
                is_update = prev is not None
                self._seen_mtimes[tid] = mtime
                self._store.upsert_task(
                    task_id=tid,
                    source="antigravity",
                    project=item["project"],
                    title=item["title"],
                    summary=item["summary"],
                    status=item["status"],
                    mark_unread=is_update,
                )
                changed = True

        if changed and self._on_change:
            self._on_change()
        return changed

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync_once()
            except Exception:
                pass
            self._stop.wait(self._interval)

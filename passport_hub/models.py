from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


VALID_SOURCES = ("antigravity", "codex", "trae", "claude", "system")
VALID_STATUSES = ("done", "waiting_input", "running", "error")

SOURCE_TAGS = {
    "antigravity": "AGY",
    "codex": "CDX",
    "trae": "TRAE",
    "claude": "CLD",
    "system": "SYS",
}


@dataclass
class TaskItem:
    task_id: str
    source: str
    project: str
    title: str
    summary: str
    status: str = "done"
    unread: bool = True
    options: list[str] = field(default_factory=list)
    session_id: str = ""
    tmux_target: str = ""
    app_name: str = ""
    cwd: str = ""
    last_reply: str = ""
    updated_at: float = field(default_factory=time.time)

    def normalize(self) -> "TaskItem":
        self.source = self.source.lower().strip()
        if self.source not in VALID_SOURCES:
            self.source = "system"
        self.status = self.status.lower().strip()
        if self.status not in VALID_STATUSES:
            self.status = "done"
        self.project = (self.project or "Workspace").strip()[:32]
        self.title = (self.title or "Task Update").strip()[:64]
        self.summary = (self.summary or self.title).strip()[:500]
        self.options = [str(o).strip()[:31] for o in (self.options or []) if str(o).strip()][:3]
        return self

    @property
    def source_tag(self) -> str:
        return SOURCE_TAGS.get(self.source, "SYS")

    def to_device_dict(self) -> dict[str, Any]:
        """Compact JSON representation tailored for ESP32-C3 (no PSRAM)."""
        d: dict[str, Any] = {
            "id": self.task_id[:31],
            "src": self.source_tag,
            "proj": self.project[:23],
            "title": self.title[:39],
            "sum": self.summary[:95],
            "status": self.status,
            "unread": self.unread,
            "ts": int(self.updated_at),
        }
        if self.options:
            d["opts"] = self.options[:3]
        return d

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_tag"] = self.source_tag
        return data


@dataclass
class BadgeProfile:
    name: str = "AI PASSPORT"
    role: str = "AI Builder"
    handle: str = "@github"
    qr_text: str = "https://github.com/folotoy"
    tagline: str = "Voice AI Desk Companion"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

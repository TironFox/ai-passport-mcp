from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from .models import TaskItem


@dataclass
class DispatchResult:
    ok: bool
    method: str
    detail: str


class ClientDispatcher:
    """Routes transcribed voice follow-ups back to Claude / Codex / Trae / Antigravity."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run or os.environ.get("PASSPORT_DISPATCH_DRY_RUN") == "1"

    def dispatch(self, task: TaskItem, prompt: str) -> DispatchResult:
        text = (prompt or "").strip()
        if not text:
            return DispatchResult(ok=False, method="none", detail="Empty prompt")

        if self.dry_run:
            return DispatchResult(
                ok=True,
                method=f"dry_run:{task.source}",
                detail=f"Would send to {task.source} ({task.task_id}): {text}",
            )

        # 1. Prefer tmux pane injection for CLI sessions (non-intrusive, no focus stealing)
        if task.tmux_target and shutil.which("tmux"):
            return self._send_tmux(task.tmux_target, text)

        # 2. Native CLI resume for Claude Code & Codex
        if task.source == "claude" and task.session_id and shutil.which("claude"):
            return self._run_bg(
                ["claude", "--resume", task.session_id, "-p", text],
                cwd=task.cwd,
                method="claude_resume",
            )
        if task.source == "codex" and task.session_id and shutil.which("codex"):
            return self._run_bg(
                ["codex", "exec", "resume", task.session_id, text],
                cwd=task.cwd,
                method="codex_resume",
            )

        # 3. GUI IDE injection on macOS (Trae / Antigravity / fallback)
        app_map = {
            "trae": task.app_name or "Trae",
            "antigravity": task.app_name or "Antigravity",
            "claude": task.app_name or "Terminal",
            "codex": task.app_name or "Terminal",
        }
        app_name = app_map.get(task.source, task.app_name)
        if app_name and shutil.which("osascript") and shutil.which("pbcopy"):
            return self._send_macos_gui(app_name, text)

        return DispatchResult(
            ok=False,
            method="unroutable",
            detail=f"No active session_id, tmux_target, or GUI target for {task.source}",
        )

    @staticmethod
    def _send_tmux(target: str, text: str) -> DispatchResult:
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "-l", text],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Enter"],
                check=True,
                capture_output=True,
            )
            return DispatchResult(ok=True, method="tmux", detail=f"Sent to tmux {target}")
        except subprocess.CalledProcessError as exc:
            return DispatchResult(ok=False, method="tmux", detail=str(exc))

    @staticmethod
    def _run_bg(cmd: list[str], cwd: str, method: str) -> DispatchResult:
        try:
            subprocess.Popen(
                cmd,
                cwd=cwd or None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return DispatchResult(ok=True, method=method, detail=f"Spawned {' '.join(cmd[:3])}")
        except OSError as exc:
            return DispatchResult(ok=False, method=method, detail=str(exc))

    @staticmethod
    def _send_macos_gui(app_name: str, text: str) -> DispatchResult:
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            script = (
                f'tell application "{app_name}" to activate\n'
                'delay 0.35\n'
                'tell application "System Events"\n'
                '  keystroke "v" using command down\n'
                '  delay 0.15\n'
                '  key code 36\n'
                'end tell\n'
            )
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
            return DispatchResult(
                ok=True,
                method="macos_gui",
                detail=f"Activated {app_name}, pasted prompt, and pressed Enter",
            )
        except subprocess.CalledProcessError as exc:
            err = (exc.stderr or str(exc)).strip()
            if "1002" in err or "not allowed" in err.lower() or "assistive" in err.lower():
                err = (
                    "macOS Accessibility permission required for Terminal! "
                    "Enable in System Settings -> Privacy & Security -> Accessibility. "
                    f"(Text copied to clipboard; raw: {err})"
                )
            return DispatchResult(ok=False, method="macos_gui", detail=err)

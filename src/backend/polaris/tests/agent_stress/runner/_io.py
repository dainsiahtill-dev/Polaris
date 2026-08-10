"""IO helpers for AgentStressRunner (mixin)."""

# mypy: ignore-errors

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..stress_path_policy import (
    default_stress_workspace_base,
)


class _AgentStressRunnerIOMixin:
    def _ensure_output_dir(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir

    def _write_text_atomic(self, path: Path, content: str) -> None:
        """Atomically write UTF-8 text to disk."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f"{target.name}.tmp")
        temp_path.write_text(str(content), encoding="utf-8")
        temp_path.replace(target)

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        """Atomically write JSON payload with UTF-8 encoding."""
        serialized = json.dumps(payload, indent=2, ensure_ascii=False)
        self._write_text_atomic(path, serialized)

    def _current_run_state(self) -> str:
        """Return current run lifecycle state."""
        if self.abort_reason:
            return "aborted"
        if self.end_time:
            return "completed"
        if self.start_time:
            return "running"
        return "initialized"

    def _record_audit_timeline_event(
        self,
        *,
        event: str,
        status: str = "info",
        detail: str = "",
        refs: dict[str, Any] | None = None,
    ) -> None:
        """Append timeline event and persist as JSONL for forensic replay."""
        normalized_event = str(event or "").strip() or "unknown"
        normalized_status = str(status or "").strip().lower() or "info"
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": normalized_event,
            "status": normalized_status,
            "detail": str(detail or "").strip(),
            "refs": refs or {},
        }
        self.audit_timeline.append(entry)
        try:
            timeline_path = self._ensure_output_dir() / "stress_audit_timeline.jsonl"
            with open(timeline_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False))
                handle.write("\n")
        except OSError as exc:
            print(f"⚠️ 写入审计时间线失败: {exc}")

    def _write_audit_checkpoint(
        self,
        *,
        phase: str,
        detail: str = "",
    ) -> None:
        """Persist checkpoint audit package during run for crash-safe forensics."""
        try:
            report = self._generate_json_report(run_state=self._current_run_state())
            self._audit_checkpoint_count += 1
            report["audit_checkpoint"] = {
                "index": self._audit_checkpoint_count,
                "phase": str(phase or "").strip() or "unknown",
                "detail": str(detail or "").strip(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "run_state": self._current_run_state(),
            }
            json_path = self._ensure_output_dir() / "stress_audit_package.json"
            self._write_json_atomic(json_path, report)
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"⚠️ 审计快照写入失败: {type(exc).__name__}: {exc}")

    @staticmethod
    def _safe_read_json_dict(path: Path) -> dict[str, Any]:
        """Read JSON object safely; return empty dict on failure."""
        try:
            if not path.exists() or not path.is_file():
                return {}
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _use_safe_policy_error_output_dir(self) -> None:
        if self._output_dir_explicit:
            return
        self.output_dir = (default_stress_workspace_base("tests-agent-stress-errors") / "stress_reports").resolve()

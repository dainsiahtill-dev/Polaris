"""stages methods for StressEngine (mixin)."""

# mypy: ignore-errors

import json
from datetime import datetime
from typing import Any

import httpx

from ..contracts import (
    normalize_status,
    resolve_factory_stage_index,
)
from ._constants import (
    COMPLETED_ROLE_STATUSES,
    FAILED_ROLE_STATUSES,
)
from ._models import RoundResult, StageExecution, StageResult


class _StressEngineStagesMixin:
    def _update_stage_executions(self, status: dict[str, Any], result: RoundResult):
        """从 Factory 状态更新各阶段执行记录"""
        lifecycle = normalize_status(status.get("status"))
        roles = status.get("roles", {}) if isinstance(status.get("roles"), dict) else {}
        gates = status.get("gates", []) if isinstance(status.get("gates"), list) else []
        created_at = str(status.get("created_at") or result.start_time).strip() or result.start_time
        completed_at = str(status.get("completed_at") or "").strip()
        observed_at = completed_at or datetime.now().isoformat()
        current_index = resolve_factory_stage_index(status)

        def calc_duration(start: str, end: str) -> int:
            try:
                start_dt = self._parse_iso_timestamp(start)
                end_dt = self._parse_iso_timestamp(end)
                if not start_dt or not end_dt:
                    return 0
                return int((end_dt - start_dt).total_seconds() * 1000)
            except (ValueError, TypeError):
                return 0

        def ensure_stage(
            attr_name: str,
            stage_name: str,
            default_start: str,
        ) -> StageExecution:
            stage = getattr(result, attr_name)
            if stage is None:
                stage = StageExecution(
                    stage_name=stage_name,
                    result=StageResult.PENDING,
                    start_time=default_start,
                    end_time=observed_at,
                    duration_ms=0,
                )
                setattr(result, attr_name, stage)
            return stage

        def stage_outcome(stage_index: int) -> StageResult:
            if stage_index < 0:
                return StageResult.PENDING
            if current_index is None:
                if lifecycle == "completed":
                    return StageResult.SUCCESS if stage_index == 0 else StageResult.PENDING
                if lifecycle in {"failed", "cancelled"}:
                    return StageResult.FAILURE if stage_index == 0 else StageResult.PENDING
                return StageResult.PENDING
            if lifecycle == "completed":
                if stage_index == 3 and any(normalize_status(g.get("status")) == "failed" for g in gates):
                    return StageResult.PARTIAL
                return StageResult.SUCCESS if stage_index <= current_index else StageResult.PENDING
            if lifecycle in {"failed", "cancelled"}:
                if stage_index < current_index:
                    return StageResult.SUCCESS
                if stage_index == current_index:
                    if stage_index == 3 and any(normalize_status(g.get("status")) == "failed" for g in gates):
                        return StageResult.FAILURE
                    return StageResult.FAILURE
                return StageResult.PENDING
            if stage_index < current_index:
                return StageResult.SUCCESS
            return StageResult.PENDING

        entry_stage = self._resolve_round_entry_stage(result)
        stage_specs = [
            ("architect_stage", "architect", 0, created_at, entry_stage == "architect"),
            ("pm_stage", "pm", 1, observed_at, entry_stage in {"architect", "pm"}),
            (
                "chief_engineer_stage",
                "chief_engineer",
                -1,
                observed_at,
                self.run_chief_engineer_stage and entry_stage in {"architect", "pm"},
            ),
            ("director_stage", "director", 2, observed_at, entry_stage in {"architect", "pm", "director"}),
            ("qa_stage", "qa", 3, observed_at, entry_stage in {"architect", "pm", "director"}),
        ]

        role_status_by_stage = {
            "architect": normalize_status((roles.get("architect") or {}).get("status")),
            "pm": normalize_status((roles.get("pm") or {}).get("status")),
            "chief_engineer": normalize_status((roles.get("chief_engineer") or {}).get("status")),
            "director": normalize_status((roles.get("director") or {}).get("status")),
        }
        for attr_name, stage_name, stage_index, default_start, stage_enabled in stage_specs:
            should_track = (current_index is not None and stage_index <= current_index) or getattr(
                result, attr_name
            ) is not None
            if not stage_enabled:
                should_track = bool(getattr(result, attr_name))

            role_status = role_status_by_stage.get(stage_name, "")
            if role_status:
                should_track = True
            if stage_name == "qa" and gates:
                should_track = True
            if lifecycle in {"completed", "failed", "cancelled"} and stage_name == "architect" and stage_enabled:
                should_track = True

            if not should_track:
                continue

            stage = ensure_stage(attr_name, stage_name, default_start)
            stage.end_time = observed_at
            outcome = stage_outcome(stage_index)

            if role_status in COMPLETED_ROLE_STATUSES:
                outcome = StageResult.SUCCESS
            elif role_status in FAILED_ROLE_STATUSES:
                outcome = StageResult.FAILURE
            elif stage_name == "qa" and gates and lifecycle == "completed":
                outcome = (
                    StageResult.PARTIAL
                    if any(normalize_status(g.get("status")) == "failed" for g in gates)
                    else StageResult.SUCCESS
                )

            stage.result = outcome
            stage.duration_ms = calc_duration(stage.start_time, stage.end_time)

    async def _backfill_stage_timings(self, result: RoundResult) -> None:
        if not self.require_full_chain_evidence:
            return
        if result.overall_result not in {"PASS", "PARTIAL"}:
            return
        run_id = str(result.factory_run_id or "").strip()
        if not run_id:
            return
        try:
            events = await self._fetch_factory_events(run_id)
        except (httpx.HTTPError, OSError, json.JSONDecodeError) as exc:
            print(f"[factory] 获取运行事件失败: {exc}")
            return
        timings = self._extract_stage_timings(events)
        if not timings:
            return
        self._apply_stage_timings(result, timings)

    def _extract_stage_timings(self, events: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        stage_map = {
            "docs_generation": "architect",
            "pm_planning": "pm",
            "director_dispatch": "director",
            "quality_gate": "qa",
        }
        timings: dict[str, dict[str, datetime]] = {}

        def update(role: str, key: str, raw_ts: Any, pick_earliest: bool) -> None:
            ts = str(raw_ts or "").strip()
            if not ts:
                return
            dt = self._parse_iso_timestamp(ts)
            if not dt:
                return
            existing = timings.get(role, {}).get(key)
            if existing is None:
                timings.setdefault(role, {})[key] = dt
                return
            if pick_earliest and dt < existing:
                timings[role][key] = dt
            if not pick_earliest and dt > existing:
                timings[role][key] = dt

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "").strip()
            stage_raw = str(event.get("stage") or "").strip()
            result_payload = event.get("result") if isinstance(event.get("result"), dict) else {}
            if not stage_raw:
                stage_raw = str(result_payload.get("stage") or "").strip()
            role = stage_map.get(stage_raw)
            if not role:
                continue
            if event_type == "stage_started":
                update(role, "start", event.get("timestamp"), True)
            elif event_type == "stage_completed":
                update(role, "start", result_payload.get("started_at") or event.get("timestamp"), True)
                update(role, "end", result_payload.get("completed_at") or event.get("timestamp"), False)

        finalized: dict[str, dict[str, str]] = {}
        for role, times in timings.items():
            start_dt = times.get("start")
            end_dt = times.get("end")
            if not start_dt and not end_dt:
                continue
            finalized[role] = {
                "start": start_dt.isoformat() if start_dt else "",
                "end": end_dt.isoformat() if end_dt else "",
            }
        return finalized

    def _apply_stage_timings(self, result: RoundResult, timings: dict[str, dict[str, str]]) -> None:
        stage_attr_map = {
            "architect": "architect_stage",
            "pm": "pm_stage",
            "chief_engineer": "chief_engineer_stage",
            "director": "director_stage",
            "qa": "qa_stage",
        }

        def calc_duration(start: str, end: str) -> int:
            try:
                start_dt = self._parse_iso_timestamp(start)
                end_dt = self._parse_iso_timestamp(end)
                if not start_dt or not end_dt:
                    return 0
                return int((end_dt - start_dt).total_seconds() * 1000)
            except (ValueError, TypeError):
                return 0

        for role_name, timing in timings.items():
            attr_name = stage_attr_map.get(role_name)
            if not attr_name:
                continue
            stage = getattr(result, attr_name)
            if stage is None:
                stage = StageExecution(
                    stage_name=role_name,
                    result=StageResult.SUCCESS,
                    start_time=timing.get("start") or result.start_time,
                    end_time=timing.get("end") or result.end_time or result.start_time,
                    duration_ms=0,
                )
                setattr(result, attr_name, stage)
            if timing.get("start"):
                stage.start_time = timing["start"]
            if timing.get("end"):
                stage.end_time = timing["end"]
            stage.duration_ms = calc_duration(stage.start_time, stage.end_time)
            if stage.duration_ms <= 0 and stage.start_time and stage.end_time:
                stage.duration_ms = 1

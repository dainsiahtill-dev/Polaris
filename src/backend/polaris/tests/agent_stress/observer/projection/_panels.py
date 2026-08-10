from __future__ import annotations

# Cross-mixin method calls resolve at RuntimeProjection composition time.
# mypy: disable-error-code="attr-defined,union-attr,arg-type,return,assignment,has-type,misc"
import json
import logging
from typing import Any

from ._base import RuntimeProjectionBase
from ._runtime_v2 import RuntimeProjectionRuntimeV2Mixin

logger = logging.getLogger("observer.projection")


class RuntimeProjectionPanelsMixin(RuntimeProjectionBase):
    """Domain mixin: panels methods for RuntimeProjection."""

    def _push_panel(self, panel_name: str, payload: dict[str, Any]) -> None:
        """向面板添加数据。"""
        rows = self.panels.setdefault(panel_name, [])
        rows.append(payload)
        if len(rows) > self._max_panel_items:
            del rows[: len(rows) - self._max_panel_items]

    def _push_llm_panel(self, payload: dict[str, Any]) -> None:
        """向 LLM 面板添加数据（合并连续的 chunk）。"""
        rows = self.panels.setdefault("llm_reasoning", [])
        event_type = str(payload.get("event_type") or "")
        stream_key = str(payload.get("stream_key") or "")

        if event_type in {"thinking_chunk", "content_chunk"} and rows:
            last = rows[-1]
            if str(last.get("event_type") or "") == event_type and str(last.get("stream_key") or "") == stream_key:
                merged_content = str(last.get("content") or "") + str(payload.get("content") or "")
                merged = dict(last)
                merged.update(payload)
                merged["content"] = merged_content[-self._max_llm_content_chars :]
                rows[-1] = merged
                return

        rows.append(payload)
        if len(rows) > self._max_panel_items:
            del rows[: len(rows) - self._max_panel_items]

    @staticmethod
    def _safe_json_compact(value: Any, max_chars: int = 220) -> str:
        """将值压缩为紧凑的 JSON 字符串。"""
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (ValueError, TypeError):
            # ValueError/TypeError: non-serializable object passed
            text = str(value)
        text = str(text).replace("\n", " ").strip()
        if len(text) > max_chars:
            return f"{text[:max_chars]}..."
        return text

    @staticmethod
    def _compact_text_preview(value: Any, max_chars: int = 240, preserve_lines: bool = False) -> str:
        """压缩文本预览。"""
        raw_text = str(value or "").strip()
        if not raw_text:
            return ""

        if preserve_lines:
            lines = raw_text.split("\n")
            result_lines = []
            total_chars = 0
            for line in lines:
                if total_chars + len(line) > max_chars - 3:
                    remaining = max_chars - total_chars - 3
                    if remaining > 0:
                        result_lines.append(line[:remaining] + "...")
                    else:
                        result_lines.append("...")
                    break
                result_lines.append(line)
                total_chars += len(line) + 1
            return "\n".join(result_lines)
        else:
            text = " ".join(raw_text.split()).strip()
            if len(text) > max_chars:
                return f"{text[:max_chars]}..."
            return text

    @staticmethod
    def _compact_patch_preview(value: Any, *, max_lines: int = 180, max_chars: int = 12000) -> str:
        """压缩 patch 文本，保留 diff 行结构。"""
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not text:
            return ""

        lines = text.split("\n")
        truncated_by_lines = False
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[:max_lines]
            truncated_by_lines = True
        compact = "\n".join(lines)

        truncated_by_chars = False
        if max_chars > 0 and len(compact) > max_chars:
            compact = compact[:max_chars]
            truncated_by_chars = True

        if truncated_by_lines or truncated_by_chars:
            compact = f"{compact}\n... [diff truncated]"
        return compact

    def _normalize_llm_stream_item(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """归一化 LLM 流事件。"""
        raw_event = msg.get("event")
        payload = raw_event if isinstance(raw_event, dict) else {}
        line = msg.get("line", "")

        if not payload and isinstance(line, str):
            text = line.strip()
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    # Malformed JSON, ignore and use empty payload
                    payload = {}

        event_raw = payload.get("raw")
        event_raw = event_raw if isinstance(event_raw, dict) else {}
        event_type = str(event_raw.get("stream_event") or event_raw.get("event") or payload.get("event") or "").strip()
        timestamp = str(payload.get("ts") or msg.get("timestamp") or "")
        channel = str(msg.get("channel") or payload.get("channel") or "llm")
        role = str(payload.get("actor") or event_raw.get("role") or "")
        stream_key = f"{channel}:{role or 'unknown'}"

        content = ""
        if event_type in {"thinking_chunk", "content_chunk"}:
            content = str(event_raw.get("content") or payload.get("message") or "")
        elif event_type == "tool_call":
            tool = str(event_raw.get("tool") or "unknown")
            args = event_raw.get("args") or {}
            args_text = self._safe_json_compact(args)
            content = f"{tool}({args_text})"

            # 保存完整的工具调用详情供后续展示
            return {
                "timestamp": timestamp,
                "channel": channel,
                "role": role,
                "event_type": event_type or "llm_stream",
                "stream_key": stream_key,
                "content": content,
                "tool_name": tool,
                "tool_args": args,
            }
        elif event_type == "tool_result":
            tool = str(event_raw.get("tool") or "unknown")
            success = event_raw.get("success")
            result_payload = event_raw.get("result") if isinstance(event_raw.get("result"), dict) else {}
            if success is None and isinstance(result_payload, dict):
                success = result_payload.get("success")
            if success is True:
                status = "ok"
            elif success is False:
                status = "failed"
            else:
                status = "ok" if not str(result_payload.get("error") or "").strip() else "failed"
            detail = ""
            if isinstance(result_payload, dict):
                detail = str(result_payload.get("error") or result_payload.get("message") or "")
                if not detail:
                    detail = self._safe_json_compact(result_payload, max_chars=160)
            if detail:
                detail_text = self._compact_text_preview(detail, max_chars=240)
                if status == "failed":
                    content = f"{tool} -> {status}\nreason: {detail_text}"
                else:
                    content = f"{tool} -> {status} ({detail_text})"
            else:
                content = f"{tool} -> {status}"

            # 保存完整的工具结果详情供后续展示
            full_result = event_raw.get("result")
            return {
                "timestamp": timestamp,
                "channel": channel,
                "role": role,
                "event_type": event_type or "llm_stream",
                "stream_key": stream_key,
                "content": content,
                "tool_name": tool,
                "tool_status": status,
                "tool_success": success,
                "tool_result_raw": full_result,
                "tool_args": event_raw.get("args"),
            }
        elif event_type == "error":
            content = str(event_raw.get("error") or payload.get("message") or "")
        else:
            content = str(payload.get("message") or payload.get("content") or line or "").strip()

        content = str(content or "").strip()
        if not content:
            return None

        if len(content) > self._max_llm_content_chars:
            content = content[-self._max_llm_content_chars :]

        return {
            "timestamp": timestamp,
            "channel": channel,
            "role": role,
            "event_type": event_type or "llm_stream",
            "stream_key": stream_key,
            "content": content,
        }

    def _normalize_dialogue_item(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """归一化对话事件。"""
        raw_event = msg.get("event")
        payload = raw_event if isinstance(raw_event, dict) else {}
        line = msg.get("line", "")

        if not payload and isinstance(line, str):
            text = line.strip()
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        payload = parsed
                except json.JSONDecodeError:
                    # Malformed JSON, ignore and use empty payload
                    payload = {}

        timestamp = str(payload.get("ts") or payload.get("timestamp") or msg.get("timestamp") or "")
        speaker = str(
            payload.get("speaker") or payload.get("actor") or payload.get("role") or payload.get("source") or "unknown"
        ).strip()
        dialogue_type = str(payload.get("type") or payload.get("kind") or "dialogue").strip().lower()
        text = str(
            payload.get("text")
            or payload.get("summary")
            or payload.get("content")
            or payload.get("message")
            or line
            or ""
        ).strip()
        if not text:
            if payload:
                text = self._safe_json_compact(payload, max_chars=self._max_dialogue_chars)
            else:
                return None
        if len(text) > self._max_dialogue_chars:
            text = text[: self._max_dialogue_chars] + "..."
        return {
            "timestamp": timestamp,
            "speaker": speaker,
            "dialogue_type": dialogue_type,
            "content": text,
        }

    @staticmethod
    def _infer_role_from_runtime_payload(payload: dict[str, Any], content: str) -> str:
        """Infer role from runtime payload and message text."""
        role = ""
        code = str(payload.get("code") or payload.get("event_code") or "").strip().lower()
        if code and "." in code:
            candidate = code.split(".", 1)[0]
            if candidate in {"architect", "pm", "director", "qa", "chief_engineer"}:
                role = candidate
        if role:
            return role
        content_lower = str(content or "").strip().lower()
        for candidate in ("architect", "pm", "director", "qa", "chief_engineer"):
            if candidate in content_lower:
                return candidate
        return "unknown"

    def _push_llm_lifecycle_hint(
        self,
        *,
        timestamp: str,
        role: str,
        event_type: str,
        content: str,
    ) -> None:
        """Push synthetic LLM lifecycle event to reasoning panel."""
        normalized_role = str(role or "unknown").strip().lower() or "unknown"
        normalized_type = str(event_type or "").strip().lower()
        if normalized_type not in {"llm_waiting", "llm_completed", "llm_failed"}:
            return
        payload = {
            "timestamp": str(timestamp or ""),
            "channel": "runtime_event",
            "role": normalized_role,
            "event_type": normalized_type,
            "stream_key": f"runtime_hint:{normalized_role}",
            "content": str(content or "")[: self._max_llm_content_chars],
        }
        self._push_llm_panel(payload)

    def _project_llm_lifecycle_from_runtime_payload(
        self,
        *,
        timestamp: str,
        payload: dict[str, Any],
        content: str,
    ) -> None:
        """Map runtime/task-trace events into LLM lifecycle visualization hints."""
        detail = " ".join(
            [
                str(payload.get("code") or ""),
                str(payload.get("step_title") or ""),
                str(payload.get("step_detail") or ""),
                str(payload.get("reason") or ""),
                str(content or ""),
            ]
        ).strip()
        detail_lower = detail.lower()
        if "llm" not in detail_lower and "first_call" not in detail_lower and "retry_call" not in detail_lower:
            return

        role = self._infer_role_from_runtime_payload(payload, detail)
        if any(
            marker in detail_lower
            for marker in (
                ".started",
                "call started",
                "waiting for first llm response",
                "retrying",
                "force-write retry started",
            )
        ):
            self._push_llm_lifecycle_hint(
                timestamp=timestamp,
                role=role,
                event_type="llm_waiting",
                content=detail,
            )
            return

        if any(
            marker in detail_lower
            for marker in (
                "timeout",
                "failed",
                "format_validation_failed",
                "llm_error",
                "no_writable_output_after_retry",
            )
        ):
            self._push_llm_lifecycle_hint(
                timestamp=timestamp,
                role=role,
                event_type="llm_failed",
                content=detail,
            )
            return

        if any(
            marker in detail_lower
            for marker in (
                "tools.first_round.summary",
                "tools.retry_round.summary",
                "tools.force_retry_round.summary",
                "response",
                "summary",
            )
        ):
            self._push_llm_lifecycle_hint(
                timestamp=timestamp,
                role=role,
                event_type="llm_completed",
                content=detail,
            )

    @staticmethod
    def _normalize_role_token(value: Any) -> str:
        """归一化角色标识。"""
        token = str(value or "").strip().lower()
        mapping = {
            "architect": "architect",
            "pm": "pm",
            "director": "director",
            "qa": "qa",
            "chief engineer": "chief_engineer",
            "chief_engineer": "chief_engineer",
        }
        return mapping.get(token, token or "unknown")

    @staticmethod
    def _infer_llm_event_type_from_runtime_v2(kind: str, content: str, tags: Any = None) -> str:
        """根据 runtime.v2 kind/content 推断 LLM 事件类型。"""
        projection_event = RuntimeProjectionRuntimeV2Mixin._extract_projection_event_type(tags)
        if projection_event:
            return projection_event

        kind_token = str(kind or "").strip().lower()
        content_token = str(content or "").strip().lower()

        if "llm_waiting" in kind_token:
            return "llm_waiting"
        if "llm_completed" in kind_token:
            return "llm_completed"
        if "llm_failed" in kind_token:
            return "llm_failed"
        if "thinking" in kind_token:
            return "thinking_chunk"
        if "tool.call" in kind_token or "tool_call" in kind_token:
            return "tool_call"
        if "tool.result" in kind_token or "tool_result" in kind_token:
            return "tool_result"
        if "error" in kind_token or "failed" in kind_token:
            return "llm_failed" if "llm" in kind_token else "error"
        if "content" in kind_token or "response" in kind_token:
            return "content_chunk"

        if "llm_waiting" in content_token or "thinking" in content_token:
            return "llm_waiting"
        if "llm_completed" in content_token:
            return "llm_completed"
        if "llm_failed" in content_token:
            return "llm_failed"
        if "tool_call" in content_token:
            return "tool_call"
        if "tool_result" in content_token:
            return "tool_result"
        if "error" in content_token or "failed" in content_token:
            return "error"
        return "content_preview"

    def get_panels(self) -> dict[str, list]:
        """获取所有面板数据。"""
        return {
            "chain_status": list(self.panels.get("chain_status", [])),
            "llm_reasoning": list(self.panels.get("llm_reasoning", [])),
            "dialogue_stream": list(self.panels.get("dialogue_stream", [])),
            "tool_activity": list(self.panels.get("tool_activity", [])),
            "taskboard_status": list(self.panels.get("taskboard_status", [])),
            "code_diff": list(self.panels.get("code_diff", [])),
            "realtime_events": list(self.panels.get("realtime_events", [])),
        }

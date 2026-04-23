"""QA role adapter.

Runs deterministic workspace quality checks and persists an auditable QA report.
LLM output is treated as optional enrichment and never as single point of truth.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from polaris.bootstrap.config import get_settings
from polaris.cells.llm.dialogue.public.service import generate_role_response
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.storage import resolve_runtime_path

from .base import BaseRoleAdapter

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rs", ".json", ".yaml", ".yml"}
_IGNORE_ROOTS = {".polaris", ".git", "node_modules", "__pycache__", ".venv", "venv"}
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
    re.compile(r"\bNotImplemented(?:Error|Exception)?\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bstub\b", re.IGNORECASE),
)
_DOMAIN_STOPWORDS = {"project", "quality", "gate", "feature", "module", "system", "task", "tasks"}
_DEFAULT_DIRECTOR_TASK_REWORK_MAX_RETRIES = 3


class QAAdapter(BaseRoleAdapter):
    """QA adapter."""

    @property
    def role_id(self) -> str:
        return "qa"

    def get_capabilities(self) -> list[str]:
        return [
            "code_review",
            "quality_check",
            "report_defect",
            "acceptance_test",
            "test_execution_verification",
            "semantic_equivalence_checking",
            "regression_detection",
        ]

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute QA task."""
        review_type = str(input_data.get("review_type", "quality_gate")).strip() or "quality_gate"
        target = str(input_data.get("review_target") or input_data.get("input") or "Project quality gate").strip()

        self._update_task_progress(task_id, "analyzing")

        raw_content = ""
        try:
            review_result = self._run_static_review(target)
            message = self._build_qa_message(review_type, target, review_result=review_result)
            settings = get_settings()
            response = await generate_role_response(
                workspace=self.workspace,
                settings=settings,
                role=self.role_id,
                message=message,
                context=None,
                validate_output=False,
                max_retries=1,
            )
            raw_content = str(response.get("response") or "") if isinstance(response, dict) else str(response or "")
            llm_review = self._parse_review_result(raw_content)
            review_result = self._merge_review_result(review_result, llm_review)

            review_result = self._finalize_review_result(review_result)
            report_path = self._write_qa_report(
                review_type=review_type,
                target=target,
                review_result=review_result,
                raw_output=raw_content,
            )
            taskboard_update = self._apply_taskboard_qa_verdict(
                review_result=review_result,
                context=context,
            )

            self._update_task_progress(task_id, "completed")
            return {
                "success": bool(review_result.get("passed")),
                "stage": "qa",
                "review_type": review_type,
                "target": target,
                "passed": bool(review_result.get("passed")),
                "score": int(review_result.get("score") or 0),
                "critical_issues": review_result.get("critical_issues", []),
                "major_issues": review_result.get("major_issues", []),
                "warnings": review_result.get("warnings", []),
                "suggestions": review_result.get("suggestions", []),
                "artifacts": [str(report_path)],
                "taskboard_qa_update": taskboard_update,
                "content_length": len(raw_content),
            }
        except (RuntimeError, ValueError) as exc:
            fallback_review = self._run_static_review(target)
            fallback_review["critical_issues"] = self._dedupe_list(
                [*list(fallback_review.get("critical_issues") or []), "qa_runtime_exception"]
            )
            fallback_review["evidence"] = self._dedupe_list(
                [*list(fallback_review.get("evidence") or []), f"qa_runtime_exception={exc}"]
            )
            finalized = self._finalize_review_result(fallback_review)
            report_path = self._write_qa_report(
                review_type=review_type,
                target=target,
                review_result=finalized,
                raw_output=raw_content,
            )
            taskboard_update = self._apply_taskboard_qa_verdict(
                review_result=finalized,
                context=context,
            )
            self._update_task_progress(task_id, "completed")
            return {
                "success": bool(finalized.get("passed")),
                "stage": "qa",
                "review_type": review_type,
                "target": target,
                "passed": bool(finalized.get("passed")),
                "score": int(finalized.get("score") or 0),
                "critical_issues": finalized.get("critical_issues", []),
                "major_issues": finalized.get("major_issues", []),
                "warnings": finalized.get("warnings", []),
                "suggestions": finalized.get("suggestions", []),
                "artifacts": [str(report_path)],
                "taskboard_qa_update": taskboard_update,
                "error": str(exc),
            }

    def _apply_taskboard_qa_verdict(
        self,
        *,
        review_result: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply QA verdict to Director TaskBoard rows.

        Flow-level retry budget only:
        - This governs QA rework rounds for completed Director tasks.
        - It is intentionally independent from kernel/network retry settings.
        """
        passed = bool(review_result.get("passed"))
        score = int(review_result.get("score") or 0)
        _raw_critical = review_result.get("critical_issues") if isinstance(review_result, dict) else None
        critical_count = len(_raw_critical) if isinstance(_raw_critical, list) else 0
        default_max_retries = self._resolve_rework_retry_budget()
        run_id = str(context.get("run_id") or "").strip() if isinstance(context, dict) else ""
        now_iso = datetime.now(timezone.utc).isoformat()
        last_reason = (
            str((review_result.get("critical_issues") or ["qa_failed"])[0]).strip() if not passed else "qa_passed"
        )

        summary = {
            "evaluated": 0,
            "passed_marked": 0,
            "reopened": 0,
            "failed": 0,
            "skipped": 0,
            "max_retries_default": default_max_retries,
        }

        try:
            entries = self.task_board.list_all()
        except (RuntimeError, ValueError):
            return summary

        for entry in entries:
            record = self._coerce_task_record(entry)
            task_id = self._safe_int(record.get("id"), default=0)
            if task_id <= 0:
                summary["skipped"] += 1
                continue

            status = str(record.get("status") or "").strip().lower()
            if status not in {"completed", "done"}:
                continue

            _raw_meta = record.get("metadata")
            metadata: dict[str, Any] = _raw_meta if isinstance(_raw_meta, dict) else {}
            _raw_adapter_result = metadata.get("adapter_result") if isinstance(metadata, dict) else None
            adapter_result: dict[str, Any] = _raw_adapter_result if isinstance(_raw_adapter_result, dict) else {}
            qa_required = bool(adapter_result.get("qa_required_for_final_verdict"))
            if not qa_required:
                continue

            summary["evaluated"] += 1
            retry_count = self._safe_int(
                metadata.get("qa_rework_retry_count", adapter_result.get("qa_rework_retry_count")),
                default=0,
            )
            max_retries = self._safe_int(
                metadata.get("qa_rework_max_retries", adapter_result.get("qa_rework_max_retries")),
                default=default_max_retries,
            )
            max_retries = max(1, max_retries)
            next_retry_count = retry_count if passed else retry_count + 1
            exhausted = (not passed) and next_retry_count >= max_retries

            merged_adapter_result: dict[str, Any] = dict(adapter_result) if adapter_result else {}
            merged_adapter_result.update(
                {
                    "qa_required_for_final_verdict": True,
                    "qa_passed": passed,
                    "qa_score": score,
                    "qa_critical_issue_count": critical_count,
                    "qa_reviewed_at": now_iso,
                    "qa_rework_retry_count": next_retry_count,
                    "qa_rework_max_retries": max_retries,
                }
            )
            if run_id:
                merged_adapter_result["qa_review_run_id"] = run_id
            if not passed:
                merged_adapter_result["qa_rework_reason"] = last_reason
                merged_adapter_result["qa_rework_exhausted"] = exhausted

            metadata_update: dict[str, Any] = {
                "adapter_result": merged_adapter_result,
                "qa_rework_retry_count": next_retry_count,
                "qa_rework_max_retries": max_retries,
                "qa_rework_requested": False if passed else not exhausted,
                "qa_rework_reason": last_reason,
                "qa_rework_exhausted": exhausted,
                "qa_last_reviewed_at": now_iso,
                "qa_last_verdict": "PASS" if passed else "FAIL",
            }
            if run_id:
                metadata_update["qa_last_review_run_id"] = run_id

            try:
                if passed:
                    self.task_board.update(task_id, metadata=metadata_update)
                    summary["passed_marked"] += 1
                    continue

                if exhausted:
                    # completed -> failed is not a standard transition; reopen first
                    self.task_board.reopen(
                        task_id,
                        reason="qa_rework_retry_exhausted",
                        metadata=metadata_update,
                    )
                    self.task_board.update(task_id, status="failed", metadata=metadata_update)
                    summary["failed"] += 1
                else:
                    self.task_board.reopen(
                        task_id,
                        reason=last_reason,
                        metadata=metadata_update,
                    )
                    summary["reopened"] += 1
            except (RuntimeError, ValueError):
                summary["skipped"] += 1

        return summary

    @staticmethod
    def _coerce_task_record(entry: Any) -> dict[str, Any]:
        if isinstance(entry, dict):
            return dict(entry)
        to_dict = getattr(entry, "to_dict", None)
        if callable(to_dict):
            try:
                payload = to_dict()
                if isinstance(payload, dict):
                    return dict(payload)
            except (RuntimeError, ValueError):
                return {}
        record: dict[str, Any] = {}
        for key in ("id", "status", "subject", "title", "metadata"):
            if hasattr(entry, key):
                record[key] = getattr(entry, key)
        return record

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (RuntimeError, ValueError):
            return int(default)

    @staticmethod
    def _resolve_rework_retry_budget() -> int:
        raw = str(
            os.environ.get(
                "KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES",
                str(_DEFAULT_DIRECTOR_TASK_REWORK_MAX_RETRIES),
            )
            or ""
        ).strip()
        try:
            value = int(raw)
        except (RuntimeError, ValueError):
            value = _DEFAULT_DIRECTOR_TASK_REWORK_MAX_RETRIES
        return max(1, min(value, 20))

    def _run_static_review(self, target: str) -> dict[str, Any]:
        """Run deterministic workspace checks."""
        workspace = Path(self.workspace).resolve()
        code_file_count = 0
        total_lines = 0
        test_file_count = 0
        placeholder_hits: list[str] = []
        unreadable_files: list[str] = []
        domain_tokens = self._extract_domain_tokens(target)
        domain_hit = False

        for path in workspace.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(workspace)
            if any(part in _IGNORE_ROOTS for part in rel.parts):
                continue
            if path.suffix.lower() not in _CODE_EXTENSIONS:
                continue

            code_file_count += 1
            rel_path = rel.as_posix()
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content = ""
                unreadable_files.append(rel_path)

            total_lines += len(content.splitlines())
            if "test" in rel_path.lower() or rel_path.lower().startswith("tests/"):
                test_file_count += 1

            for pattern in _PLACEHOLDER_PATTERNS:
                if pattern.search(content):
                    placeholder_hits.append(f"{rel_path}:{pattern.pattern}")
                    break

            if domain_tokens and not domain_hit:
                lowered = content.lower()
                rel_lower = rel_path.lower()
                if any(token in rel_lower or token in lowered for token in domain_tokens):
                    domain_hit = True

        critical_issues: list[str] = []
        major_issues: list[str] = []
        warnings: list[str] = []
        evidence: list[str] = [
            f"code_file_count={code_file_count}",
            f"code_line_count={total_lines}",
            f"test_file_count={test_file_count}",
        ]

        if placeholder_hits:
            critical_issues.append("placeholder_content_detected")
            evidence.extend(placeholder_hits[:8])
        if test_file_count < 1:
            warnings.append("tests_not_detected")
        if domain_tokens and not domain_hit:
            warnings.append("domain_signal_missing")
            evidence.append(f"expected_domain_tokens={domain_tokens[:8]}")
        if unreadable_files:
            warnings.append("unreadable_code_files")
            evidence.extend([f"unreadable={item}" for item in unreadable_files[:8]])

        runtime_signals = self._load_runtime_stage_signals()
        for signal in runtime_signals[:40]:
            code = str(signal.get("code") or "").strip() or "unknown_signal"
            severity = str(signal.get("severity") or "").strip().lower() or "unknown"
            detail = str(signal.get("detail") or "").strip()
            evidence.append(f"stage_signal:{severity}:{code}:{detail[:160]}")
            if severity == "error":
                if code.endswith("run_status_non_success") or "runtime.exception" in code:
                    critical_issues.append(f"upstream_stage_error:{code}")
                else:
                    major_issues.append(f"upstream_stage_signal:{code}")
            elif severity == "warning":
                warnings.append(f"upstream_stage_warning:{code}")

        return {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": critical_issues,
            "major_issues": major_issues,
            "warnings": warnings,
            "evidence": evidence,
            "suggestions": [],
            "parsed_json": True,
        }

    def _build_qa_message(
        self,
        review_type: str,
        target: str,
        *,
        review_result: dict[str, Any] | None = None,
    ) -> str:
        """Build optional LLM review prompt."""
        evidence_lines: list[str] = []
        if isinstance(review_result, dict):
            for item in list(review_result.get("evidence") or [])[:16]:
                token = str(item).strip()
                if token:
                    evidence_lines.append(f"- {token}")
        evidence_block = "\n".join(evidence_lines) if evidence_lines else "- no deterministic evidence"
        return "\n".join(
            [
                "You are Polaris QA. Return JSON only.",
                "Judge semantic task completion quality; do not rely only on file count or line count.",
                "Small but complete utility classes are acceptable if requirements are met.",
                f"Review type: {review_type}",
                f"Target: {target}",
                "",
                "Deterministic evidence collected from upstream stages:",
                evidence_block,
                "",
                "{",
                '  "verdict": "PASS|FAIL",',
                '  "score": 0,',
                '  "critical_issues": ["..."],',
                '  "major_issues": ["..."],',
                '  "warnings": ["..."],',
                '  "evidence": ["..."],',
                '  "suggestions": ["..."]',
                "}",
            ]
        )

    def _parse_review_result(self, content: str) -> dict[str, Any]:
        """Parse LLM review payload."""
        payload = self._extract_json_payload(content)
        if isinstance(payload, dict):
            return self._normalize_review_payload(payload)
        fallback_verdict = re.search(
            r'"verdict"\s*:\s*"?(PASS|CONDITIONAL|FAIL|BLOCKED)"?',
            str(content or ""),
            flags=re.IGNORECASE,
        )
        fallback_score = re.search(r'"score"\s*:\s*(\d+)', str(content or ""), flags=re.IGNORECASE)
        if fallback_verdict:
            return {
                "verdict": str(fallback_verdict.group(1) or "").strip().upper(),
                "score": self._coerce_int(fallback_score.group(1) if fallback_score else 0),
                "critical_issues": [],
                "major_issues": [],
                "warnings": ["qa_llm_partial_parse_recovered"],
                "evidence": [],
                "suggestions": [],
                "parsed_json": True,
            }
        return {
            "parsed_json": False,
            "parse_error": "llm_output_not_valid_json_object",
            "raw_excerpt": str(content or "")[:300],
        }

    def _merge_review_result(self, base: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
        """Merge deterministic review with LLM review."""
        merged = {
            "verdict": str(base.get("verdict") or "PASS"),
            "score": int(base.get("score") or 100),
            "critical_issues": self._dedupe_list(base.get("critical_issues")),
            "major_issues": self._dedupe_list(base.get("major_issues")),
            "warnings": self._dedupe_list(base.get("warnings")),
            "evidence": self._dedupe_list(base.get("evidence")),
            "suggestions": self._dedupe_list(base.get("suggestions")),
        }

        if bool(llm.get("parsed_json")):
            llm_verdict = str(llm.get("verdict") or "").strip().upper()
            if llm_verdict in {"PASS", "CONDITIONAL", "FAIL", "BLOCKED"}:
                merged["verdict"] = llm_verdict
            merged["critical_issues"] = self._dedupe_list(
                list(cast("list", merged["critical_issues"])) + self._dedupe_list(llm.get("critical_issues"))
            )
            merged["major_issues"] = self._dedupe_list(
                list(cast("list", merged["major_issues"])) + self._dedupe_list(llm.get("major_issues"))
            )
            merged["warnings"] = self._dedupe_list(
                list(cast("list", merged["warnings"])) + self._dedupe_list(llm.get("warnings"))
            )
            merged["evidence"] = self._dedupe_list(
                list(cast("list", merged["evidence"])) + self._dedupe_list(llm.get("evidence"))
            )
            merged["suggestions"] = self._dedupe_list(
                list(cast("list", merged["suggestions"])) + self._dedupe_list(llm.get("suggestions"))
            )
            llm_score = int(llm.get("score") or 0) if isinstance(llm.get("score"), (int, float, str)) else 0
            if llm_score > 0:
                _current_score = merged.get("score")
                merged["score"] = min(
                    int(_current_score) if isinstance(_current_score, (int, float, str)) else 100, llm_score
                )
        else:
            # LLM 审查输出是增强信息，不应成为单点致死条件。
            # 当确定性静态门禁已通过时，仅将其记录为 warning，
            # 避免因为模型格式漂移导致 quality_gate 假阴性失败。
            merged["warnings"] = self._dedupe_list(
                [*list(cast("list", merged["warnings"])), "qa_llm_judgement_unavailable"]
            )
            excerpt = str(llm.get("raw_excerpt") or "").strip()
            if excerpt:
                merged["evidence"] = self._dedupe_list(
                    [*list(cast("list", merged["evidence"])), f"llm_excerpt={excerpt}"]
                )
        return merged

    def _finalize_review_result(self, review: dict[str, Any]) -> dict[str, Any]:
        """Finalize score and verdict after merge."""
        merged_verdict = str(review.get("verdict") or "").strip().upper()
        critical = self._dedupe_list(review.get("critical_issues"))
        major = self._dedupe_list(review.get("major_issues"))
        warnings = self._dedupe_list(review.get("warnings"))
        evidence = self._dedupe_list(review.get("evidence"))
        suggestions = self._dedupe_list(review.get("suggestions"))

        computed_score = max(0, 100 - len(critical) * 30 - len(major) * 10 - len(warnings) * 4)
        raw_score = int(review.get("score") or 100)
        score = min(raw_score, computed_score)
        if critical or merged_verdict in {"FAIL", "BLOCKED", "CONDITIONAL"}:
            passed = False
        elif merged_verdict == "PASS":
            passed = True
        else:
            # 无显式 FAIL 且无 critical 时，交给 QA 语义判定通过
            passed = True

        return {
            "verdict": "PASS" if passed else "FAIL",
            "passed": passed,
            "score": score,
            "critical_issues": critical,
            "major_issues": major,
            "warnings": warnings,
            "evidence": evidence,
            "suggestions": suggestions,
        }

    def _write_qa_report(
        self,
        *,
        review_type: str,
        target: str,
        review_result: dict[str, Any],
        raw_output: str,
    ) -> Path:
        report_path = Path(resolve_runtime_path(self.workspace, "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "qa_adapter_v3",
            "review_type": review_type,
            "target": target,
            "verdict": review_result.get("verdict"),
            "passed": bool(review_result.get("passed")),
            "score": int(review_result.get("score") or 0),
            "critical_issue_count": (
                len(cast("list", review_result.get("critical_issues")))
                if isinstance(review_result, dict) and isinstance(review_result.get("critical_issues"), list)
                else 0
            ),
            "major_issues": review_result.get("major_issues", []),
            "warnings": review_result.get("warnings", []),
            "evidence": review_result.get("evidence", []),
            "suggestions": review_result.get("suggestions", []),
            "raw_excerpt": str(raw_output or "")[:2000],
        }
        write_text_atomic(
            str(report_path),
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report_path

    @staticmethod
    def _extract_json_payload(content: str) -> dict[str, Any] | None:
        text = str(content or "").strip()
        if not text:
            return None
        candidates = [text]
        candidates.extend(
            item.strip()
            for item in re.findall(r"```json\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
            if item.strip()
        )
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if 0 <= first_brace < last_brace:
            snippet = text[first_brace : last_brace + 1].strip()
            if snippet:
                candidates.append(snippet)
        for candidate in candidates:
            for normalized in (
                candidate,
                QAAdapter._strip_json_line_comments(candidate),
            ):
                try:
                    data = json.loads(normalized)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    return data
        return None

    def _normalize_review_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        critical_issues = self._coerce_list(payload.get("critical_issues"))
        major_issues = self._coerce_list(payload.get("major_issues"))
        warnings = self._coerce_list(payload.get("warnings"))
        evidence = self._coerce_list(payload.get("evidence"))
        suggestions = self._coerce_list(payload.get("suggestions"))

        findings = payload.get("findings")
        if isinstance(findings, list):
            for item in findings:
                if not isinstance(item, dict):
                    continue
                severity = str(item.get("severity") or "").strip().lower()
                description = str(item.get("description") or item.get("category") or "").strip()
                if description:
                    if severity == "critical":
                        critical_issues.append(description)
                    elif severity in {"high", "major"}:
                        major_issues.append(description)
                    else:
                        warnings.append(description)
                evidence_token = str(item.get("evidence") or "").strip()
                if evidence_token:
                    evidence.append(evidence_token)
                recommendation = str(item.get("recommendation") or "").strip()
                if recommendation:
                    suggestions.append(recommendation)

        summary = str(payload.get("summary") or "").strip()
        if summary:
            evidence.append(f"llm_summary={summary}")

        return {
            "verdict": str(payload.get("verdict") or "").strip().upper(),
            "score": self._coerce_int(payload.get("score")),
            "critical_issues": self._dedupe_list(critical_issues),
            "major_issues": self._dedupe_list(major_issues),
            "warnings": self._dedupe_list(warnings),
            "evidence": self._dedupe_list(evidence),
            "suggestions": self._dedupe_list(suggestions),
            "parsed_json": True,
        }

    @staticmethod
    def _strip_json_line_comments(content: str) -> str:
        text = str(content or "")
        if "//" not in text:
            return text
        output: list[str] = []
        in_string = False
        escaped = False
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            next_char = text[index + 1] if index + 1 < length else ""
            if in_string:
                output.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                in_string = True
                output.append(char)
                index += 1
                continue
            if char == "/" and next_char == "/":
                index += 2
                while index < length and text[index] not in {"\r", "\n"}:
                    index += 1
                continue
            output.append(char)
            index += 1
        return "".join(output)

    @staticmethod
    def _extract_domain_tokens(target: str) -> list[str]:
        tokens = re.findall(r"[a-z][a-z0-9_-]{3,}", str(target or "").lower())
        unique: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in _DOMAIN_STOPWORDS:
                continue
            if token in seen:
                continue
            seen.add(token)
            unique.append(token)
        return unique[:12]

    def _load_runtime_stage_signals(self) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        signal_dir = Path(resolve_runtime_path(self.workspace, "runtime/signals"))
        if not signal_dir.exists() or not signal_dir.is_dir():
            return signals
        for file_path in sorted(signal_dir.glob("*.json")):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            rows = payload.get("signals")
            if not isinstance(rows, list):
                continue
            payload_run_id = str(payload.get("run_id") or payload.get("factory_run_id") or "").strip()
            for item in rows:
                if not isinstance(item, dict):
                    continue
                if payload_run_id:
                    row_run_id = str(item.get("run_id") or item.get("factory_run_id") or "").strip()
                    if row_run_id and row_run_id != payload_run_id:
                        continue
                signals.append(item)
        return signals

    @staticmethod
    def _coerce_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _coerce_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _dedupe_list(value: Any) -> list[str]:
        raw = value if isinstance(value, list) else []
        output: list[str] = []
        seen: set[str] = set()
        for item in raw:
            token = str(item).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            output.append(token)
        return output

    # -------------------------------------------------------------------------
    # Phase 2.5: Proactive Verification
    # -------------------------------------------------------------------------

    def _verify_test_execution(
        self,
        target: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Phase 2.5: Execute tests and verify they pass.

        Args:
            target: Target path or spec to verify
            context: Execution context with test configuration

        Returns:
            Test execution result with pass/fail and details
        """
        import subprocess

        workspace = Path(self.workspace).resolve()
        test_results: list[dict[str, Any]] = []
        passed_count = 0
        failed_count = 0
        skipped_count = 0
        errors: list[str] = []

        ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
        test_commands = []
        if isinstance(ctx_metadata, dict):
            test_commands = ctx_metadata.get("test_commands", [])

        if not test_commands:
            test_commands = ["pytest", "python -m pytest"]

        for cmd in test_commands:
            cmd_parts = cmd.split()
            try:
                result = subprocess.run(
                    cmd_parts,
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                exit_code = result.returncode
                output = result.stdout + result.stderr

                if exit_code == 0:
                    passed_count += 1
                    test_results.append(
                        {
                            "command": cmd,
                            "status": "passed",
                            "exit_code": exit_code,
                            "output_length": len(output),
                        }
                    )
                elif exit_code == 5:
                    skipped_count += 1
                    test_results.append(
                        {
                            "command": cmd,
                            "status": "skipped",
                            "exit_code": exit_code,
                            "output_length": len(output),
                        }
                    )
                else:
                    failed_count += 1
                    test_results.append(
                        {
                            "command": cmd,
                            "status": "failed",
                            "exit_code": exit_code,
                            "output": output[-500:] if len(output) > 500 else output,
                        }
                    )
            except subprocess.TimeoutExpired:
                errors.append(f"test_timeout:{cmd}")
                failed_count += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"test_error:{cmd}:{exc}")
                failed_count += 1

        all_passed = passed_count > 0 and failed_count == 0 and len(errors) == 0

        return {
            "test_execution_verified": True,
            "passed": all_passed,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "test_results": test_results,
            "errors": errors,
        }

    def _check_semantic_equivalence(
        self,
        new_code: str,
        spec: str,
        language: str = "python",
    ) -> dict[str, Any]:
        """Phase 2.5: Check if new code is semantically equivalent to specification.

        Args:
            new_code: The new implementation code
            spec: The specification to check against
            language: Programming language hint

        Returns:
            Equivalence check result with confidence score
        """
        if not new_code or not spec:
            return {
                "semantic_equivalence_checked": True,
                "equivalent": False,
                "confidence": 0.0,
                "issues": ["missing_code_or_spec"],
            }

        equivalence_indicators: list[str] = []
        mismatch_indicators: list[str] = []

        spec_lower = spec.lower()
        new_lower = new_code.lower()

        spec_keywords = set(re.findall(r"\b\w{4,}\b", spec_lower))
        new_keywords = set(re.findall(r"\b\w{4,}\b", new_lower))

        spec_funcs = {
            w
            for w in spec_keywords
            if w
            not in {
                "function",
                "class",
                "method",
                "return",
                "input",
                "output",
                "should",
                "must",
                "shall",
                "have",
                "with",
                "from",
                "import",
                "the",
                "and",
                "for",
                "this",
                "that",
                "these",
                "those",
            }
        }
        new_funcs = {
            w
            for w in new_keywords
            if w
            not in {
                "function",
                "class",
                "method",
                "return",
                "input",
                "output",
                "should",
                "must",
                "shall",
                "have",
                "with",
                "from",
                "import",
                "the",
                "and",
                "for",
                "this",
                "that",
                "these",
                "those",
            }
        }

        matched_spec = spec_funcs & new_funcs
        missing_in_code = spec_funcs - new_funcs
        extra_in_code = new_funcs - spec_funcs

        if len(matched_spec) >= len(spec_funcs) * 0.6:
            equivalence_indicators.append("keyword_coverage_sufficient")
        else:
            mismatch_indicators.append(f"keyword_coverage_low:{len(matched_spec)}/{len(spec_funcs)}")

        if missing_in_code:
            mismatch_indicators.append(f"missing_keywords:{list(missing_in_code)[:5]}")
        if len(extra_in_code) > len(matched_spec) * 0.5:
            mismatch_indicators.append("excessive_extra_keywords")

        has_return = "return" in new_lower
        spec_implies_return = any(k in spec_lower for k in ["return", "output", "result", "value"])
        if spec_implies_return and not has_return:
            mismatch_indicators.append("missing_return_statement")
        elif not spec_implies_return or has_return:
            equivalence_indicators.append("return_statement_present")

        confidence = len(equivalence_indicators) / max(len(equivalence_indicators) + len(mismatch_indicators), 1)

        return {
            "semantic_equivalence_checked": True,
            "equivalent": len(mismatch_indicators) == 0 and confidence >= 0.6,
            "confidence": round(confidence, 2),
            "equivalence_indicators": equivalence_indicators,
            "mismatch_indicators": mismatch_indicators,
            "matched_keywords": list(matched_spec)[:10],
            "missing_keywords": list(missing_in_code)[:5],
        }

    def _detect_regressions(
        self,
        current_code: str,
        baseline_snapshot: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Phase 2.5: Detect regressions by comparing to known good baseline.

        Args:
            current_code: Current code state
            baseline_snapshot: Known good baseline snapshot
            context: Execution context with baseline data

        Returns:
            Regression detection result
        """
        regressions: list[str] = []
        warnings: list[str] = []
        improvements: list[str] = []

        if baseline_snapshot is None:
            ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
            if isinstance(ctx_metadata, dict):
                baseline_snapshot = ctx_metadata.get("baseline_snapshot")

        if baseline_snapshot is None:
            return {
                "regression_detection_performed": True,
                "regressions_found": 0,
                "regressions": [],
                "warnings": ["no_baseline_available"],
                "improvements": [],
                "status": "unknown",
            }

        baseline_lines = len(baseline_snapshot.get("code", "").splitlines())
        current_lines = len(current_code.splitlines())

        if current_lines < baseline_lines * 0.7:
            regressions.append(f"significant_code_reduction:{current_lines}/{baseline_lines}")
        elif current_lines > baseline_lines * 1.5:
            warnings.append(f"significant_code_inflation:{current_lines}/{baseline_lines}")

        baseline_keywords = set(re.findall(r"\b\w{4,}\b", baseline_snapshot.get("code", "").lower()))
        current_keywords = set(re.findall(r"\b\w{4,}\b", current_code.lower()))

        lost_keywords = baseline_keywords - current_keywords
        if len(lost_keywords) > len(baseline_keywords) * 0.3:
            regressions.append(f"keyword_loss:{len(lost_keywords)}/{len(baseline_keywords)}")

        baseline_api_count = len(re.findall(r"\b(?:def|class|async\s+def)\s+\w+", baseline_snapshot.get("code", "")))
        current_api_count = len(re.findall(r"\b(?:def|class|async\s+def)\s+\w+", current_code))

        if current_api_count < baseline_api_count * 0.7:
            regressions.append(f"api_reduction:{current_api_count}/{baseline_api_count}")

        baseline_test_count = len(re.findall(r"\b(?:test_|should_|expect_)\w+", baseline_snapshot.get("code", "")))
        current_test_count = len(re.findall(r"\b(?:test_|should_|expect_)\w+", current_code))

        if current_test_count < baseline_test_count and current_test_count == 0:
            warnings.append("test_coverage_decreased")

        if current_lines > baseline_lines * 1.2 and current_api_count >= baseline_api_count:
            improvements.append("code_expansion_with_maintained_api")

        return {
            "regression_detection_performed": True,
            "regressions_found": len(regressions),
            "regressions": regressions,
            "warnings": warnings,
            "improvements": improvements,
            "status": "regression" if regressions else ("improved" if improvements else "stable"),
            "metrics": {
                "baseline_lines": baseline_lines,
                "current_lines": current_lines,
                "baseline_apis": baseline_api_count,
                "current_apis": current_api_count,
                "baseline_tests": baseline_test_count,
                "current_tests": current_test_count,
            },
        }

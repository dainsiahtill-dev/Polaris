"""Unit tests for QAAdapter pure logic (no I/O, no LLM, no filesystem).

Covers:
- _coerce_task_record / _safe_int / _resolve_rework_retry_budget
- _build_qa_message
- _parse_review_result / _merge_review_result / _finalize_review_result
- _extract_json_payload / _normalize_review_payload / _strip_json_line_comments
- _extract_domain_tokens
- _coerce_int / _coerce_list / _dedupe_list
- _check_semantic_equivalence / _detect_regressions
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, cast

from polaris.cells.roles.adapters.internal.qa_adapter import (
    QAAdapter,
    _extract_qa_rework_evidence,
    _has_unfinished_placeholder_match,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Any) -> QAAdapter:
    return QAAdapter(workspace=str(tmp_path))


class _QaRowProjectionOnlyTaskBoard:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = [dict(row) for row in rows]
        self.reopened: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.failed_after_rework_exhausted: list[dict[str, Any]] = []

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def list_task_rows(self) -> list[dict[str, Any]]:
        raise AssertionError("QA read-model consumers must use list_observable_task_rows()")

    def list_all(self) -> list[Any]:
        raise AssertionError("QA read-model consumers must use list_observable_task_rows()")

    def reopen_task_row(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self.reopened.append(
            {
                "task_id": task_id,
                "reason": reason,
                "metadata": dict(metadata or {}),
            }
        )
        return self._update_row(task_id, status="pending", metadata=metadata)

    def update_task_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self.updated.append(
            {
                "task_id": task_id,
                "status": status,
                "metadata": dict(metadata or {}),
            }
        )
        return self._update_row(task_id, status=status, metadata=metadata)

    def fail_task_row_after_rework_exhausted(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
    ) -> dict[str, Any] | None:
        self.failed_after_rework_exhausted.append(
            {
                "task_id": task_id,
                "reason": reason,
                "metadata": dict(metadata or {}),
                "source": source,
            }
        )
        return self._update_row(task_id, status="failed", metadata=metadata)

    def _update_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        for row in self.rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            if status is not None:
                row["status"] = status
            if metadata is not None:
                current_metadata = row.get("metadata")
                merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
                merged_metadata.update(metadata)
                row["metadata"] = merged_metadata
            return dict(row)
        return None

    def reopen(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("QA verdict routing must use reopen_task_row()")

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("QA verdict routing must use update_task_row()")


class _QaExecutionEventFailureTaskBoard(_QaRowProjectionOnlyTaskBoard):
    def __init__(self, rows: list[dict[str, Any]], *, fail_action: str) -> None:
        super().__init__(rows)
        self.fail_action = fail_action

    def reopen_task_row(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._with_execution_event_failure(
            "reopen", super().reopen_task_row(task_id, reason=reason, metadata=metadata)
        )

    def update_task_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._with_execution_event_failure(
            "update",
            super().update_task_row(task_id, status=status, metadata=metadata),
        )

    def fail_task_row_after_rework_exhausted(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
    ) -> dict[str, Any] | None:
        return self._with_execution_event_failure(
            "fail_after_rework_exhausted",
            super().fail_task_row_after_rework_exhausted(
                task_id,
                reason=reason,
                metadata=metadata,
                source=source,
            ),
        )

    def _with_execution_event_failure(
        self,
        action: str,
        result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if action != self.fail_action or result is None:
            return result
        failed_result = dict(result)
        failed_result["execution_event"] = {
            "ok": False,
            "event_type": "task_runtime.execution",
            "error_code": "append_failed",
        }
        return failed_result


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


class TestCoerceTaskRecord:
    def test_dict_passthrough(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_task_record({"id": 1}) == {"id": 1}

    def test_object_with_to_dict(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class Obj:
            def to_dict(self):
                return {"id": 2}

        assert adapter._coerce_task_record(Obj()) == {"id": 2}

    def test_object_with_attributes(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class Obj:
            id = 3
            status = "pending"
            unknown = "x"

        result = adapter._coerce_task_record(Obj())
        assert result["id"] == 3
        assert result["status"] == "pending"
        assert "unknown" not in result

    def test_to_dict_exception_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class Obj:
            def to_dict(self):
                raise RuntimeError("fail")

        assert adapter._coerce_task_record(Obj()) == {}


class TestTaskboardQaVerdict:
    def test_missing_task_row_projection_returns_empty_verdict_scan(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class LegacyBoard:
            def list_all(self) -> list[object]:
                raise AssertionError("QA read-model consumers must not use legacy list_all")

        adapter._task_runtime = cast(Any, LegacyBoard())

        summary = adapter._apply_taskboard_qa_verdict(
            review_result={"passed": True, "score": 100},
            context={},
        )

        assert summary["evaluated"] == 0
        assert summary["passed_marked"] == 0

    def test_uses_runtime_task_row_projection_before_raw_task_entities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task_runtime = _QaRowProjectionOnlyTaskBoard(
            [
                {
                    "id": 7,
                    "status": "completed",
                    "metadata": {
                        "adapter_result": {
                            "qa_required_for_final_verdict": True,
                            "qa_passed": None,
                        }
                    },
                }
            ]
        )
        adapter._task_runtime = cast(Any, task_runtime)

        summary = adapter._apply_taskboard_qa_verdict(
            review_result={
                "passed": False,
                "score": 50,
                "critical_issues": ["integration failed"],
                "evidence": ["src/main.py:integration failed"],
            },
            context={"run_id": "qa-run"},
        )

        assert summary["evaluated"] == 1
        assert summary["reopened"] == 1
        assert task_runtime.reopened[0]["task_id"] == 7
        assert task_runtime.reopened[0]["reason"] == "integration failed"
        metadata = task_runtime.reopened[0]["metadata"]
        assert metadata["qa_rework_requested"] is True
        assert metadata["adapter_result"]["qa_review_run_id"] == "qa-run"

    def test_exhausted_rework_uses_task_runtime_owner_failure_transition(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task_runtime = _QaRowProjectionOnlyTaskBoard(
            [
                {
                    "id": 8,
                    "status": "completed",
                    "metadata": {
                        "adapter_result": {
                            "qa_required_for_final_verdict": True,
                            "qa_rework_retry_count": 2,
                            "qa_rework_max_retries": 3,
                        }
                    },
                }
            ]
        )
        adapter._task_runtime = cast(Any, task_runtime)

        summary = adapter._apply_taskboard_qa_verdict(
            review_result={
                "passed": False,
                "score": 20,
                "critical_issues": ["integration failed"],
            },
            context={"run_id": "qa-run"},
        )

        assert summary["evaluated"] == 1
        assert summary["failed"] == 1
        assert task_runtime.failed_after_rework_exhausted == [
            {
                "task_id": 8,
                "reason": "qa_rework_retry_exhausted",
                "metadata": task_runtime.rows[0]["metadata"],
                "source": "qa_verdict",
            }
        ]
        assert not any(item["status"] == "failed" for item in task_runtime.updated)

    def test_failed_task_runtime_transition_is_not_counted_as_qa_success(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class MissingRowTransitionBoard(_QaRowProjectionOnlyTaskBoard):
            def update_task_row(
                self,
                task_id: Any,
                *,
                status: str | None = None,
                metadata: dict[str, Any] | None = None,
            ) -> dict[str, Any] | None:
                self.updated.append(
                    {
                        "task_id": task_id,
                        "status": status,
                        "metadata": dict(metadata or {}),
                    }
                )
                return None

        task_runtime = MissingRowTransitionBoard(
            [
                {
                    "id": 9,
                    "status": "completed",
                    "metadata": {
                        "adapter_result": {
                            "qa_required_for_final_verdict": True,
                            "qa_passed": None,
                        }
                    },
                }
            ]
        )
        adapter._task_runtime = cast(Any, task_runtime)

        summary = adapter._apply_taskboard_qa_verdict(
            review_result={
                "passed": True,
                "score": 95,
                "critical_issues": [],
            },
            context={"run_id": "qa-run"},
        )

        assert summary["evaluated"] == 1
        assert summary["passed_marked"] == 0
        assert summary["skipped"] == 1
        assert summary["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 9,
                "action": "mark_passed",
                "reason": "task_runtime_update_missing_row",
                "transition_result": {},
            }
        ]

    def test_execution_event_append_failure_is_not_counted_as_qa_pass(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task_runtime = _QaExecutionEventFailureTaskBoard(
            [
                {
                    "id": 10,
                    "status": "completed",
                    "metadata": {
                        "adapter_result": {
                            "qa_required_for_final_verdict": True,
                            "qa_passed": None,
                        }
                    },
                }
            ],
            fail_action="update",
        )
        adapter._task_runtime = cast(Any, task_runtime)

        summary = adapter._apply_taskboard_qa_verdict(
            review_result={
                "passed": True,
                "score": 95,
                "critical_issues": [],
            },
            context={"run_id": "qa-run"},
        )

        assert summary["evaluated"] == 1
        assert summary["passed_marked"] == 0
        assert summary["skipped"] == 1
        assert summary["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 10,
                "action": "mark_passed",
                "reason": "task_runtime_execution_event_append_failed",
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]

    def test_execution_event_append_failure_is_not_counted_as_qa_reopen(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task_runtime = _QaExecutionEventFailureTaskBoard(
            [
                {
                    "id": 11,
                    "status": "completed",
                    "metadata": {
                        "adapter_result": {
                            "qa_required_for_final_verdict": True,
                            "qa_rework_retry_count": 0,
                            "qa_rework_max_retries": 3,
                        }
                    },
                }
            ],
            fail_action="reopen",
        )
        adapter._task_runtime = cast(Any, task_runtime)

        summary = adapter._apply_taskboard_qa_verdict(
            review_result={
                "passed": False,
                "score": 40,
                "critical_issues": ["integration failed"],
            },
            context={"run_id": "qa-run"},
        )

        assert summary["evaluated"] == 1
        assert summary["reopened"] == 0
        assert summary["skipped"] == 1
        assert summary["task_runtime_transition_failures"][0]["action"] == "reopen_for_rework"
        assert summary["task_runtime_transition_failures"][0]["reason"] == (
            "task_runtime_execution_event_append_failed"
        )

    def test_execution_event_append_failure_is_not_counted_as_qa_exhausted_failure(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task_runtime = _QaExecutionEventFailureTaskBoard(
            [
                {
                    "id": 12,
                    "status": "completed",
                    "metadata": {
                        "adapter_result": {
                            "qa_required_for_final_verdict": True,
                            "qa_rework_retry_count": 2,
                            "qa_rework_max_retries": 3,
                        }
                    },
                }
            ],
            fail_action="fail_after_rework_exhausted",
        )
        adapter._task_runtime = cast(Any, task_runtime)

        summary = adapter._apply_taskboard_qa_verdict(
            review_result={
                "passed": False,
                "score": 20,
                "critical_issues": ["integration failed"],
            },
            context={"run_id": "qa-run"},
        )

        assert summary["evaluated"] == 1
        assert summary["failed"] == 0
        assert summary["skipped"] == 1
        assert summary["task_runtime_transition_failures"][0]["action"] == "fail_after_rework_exhausted"
        assert summary["task_runtime_transition_failures"][0]["reason"] == (
            "task_runtime_execution_event_append_failed"
        )


class TestSafeInt:
    def test_numeric(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._safe_int(5) == 5
        assert adapter._safe_int("7") == 7

    def test_invalid_returns_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._safe_int("abc") == 0
        assert adapter._safe_int("abc", default=3) == 3

    def test_none_returns_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._safe_int(None) == 0
        assert adapter._safe_int(None, default=4) == 4


def test_placeholder_in_html_class_attribute_is_not_unfinished_marker() -> None:
    pattern = re.compile(r"\bplaceholder\b", re.IGNORECASE)

    assert _has_unfinished_placeholder_match('<span class="avatar-placeholder">张</span>', pattern) is False
    assert _has_unfinished_placeholder_match("<p>placeholder</p>", pattern) is True


def test_notimplemented_stub_named_as_string_literal_is_not_unfinished_marker() -> None:
    """A forbidden-token list / "must not contain X" assertion NAMES the marker.

    Regression (factory-bench L1-02 r10): a correct anti-placeholder test defined
    FORBIDDEN_TOKENS = ("notimplemented", "stub", ...); the bare NotImplemented/
    stub scan matched those string literals, failed materialization quality, and
    trapped the Director in an unfixable rewrite loop. A token quoted as string
    content must not count as an unfinished-code marker.
    """
    ni = re.compile(r"\bNotImplemented(?:Error|Exception)?\b", re.IGNORECASE)
    stub = re.compile(r"\bstub\b", re.IGNORECASE)

    # String-literal naming -> NOT a marker.
    assert (
        _has_unfinished_placeholder_match(
            'FORBIDDEN_TOKENS = ("todo", "fixme", "notimplemented", "no test specified")\n',
            ni,
        )
        is False
    )
    assert _has_unfinished_placeholder_match('assert "stub" not in source\n', stub) is False
    assert _has_unfinished_placeholder_match("BAD = ['NotImplementedError', 'stub']\n", ni) is False

    # Genuine unfinished-code markers -> STILL flagged.
    assert _has_unfinished_placeholder_match("def f():\n    raise NotImplementedError\n", ni) is True
    assert _has_unfinished_placeholder_match("def f():\n    raise NotImplementedError('later')\n", ni) is True
    assert _has_unfinished_placeholder_match("# stub: fill in later\n", stub) is True
    assert _has_unfinished_placeholder_match("function bar() { /* stub */ }\n", stub) is True


class TestExtractQaReworkEvidence:
    def test_filters_metrics_and_keeps_actionable_paths(self) -> None:
        result = _extract_qa_rework_evidence(
            {
                "evidence": [
                    "code_file_count=71",
                    "src/backend/fashiongen_worker.py:\\bplaceholder\\b",
                    "llm_excerpt=blocked",
                    "src/main/providers.ts:\\bplaceholder\\b",
                ]
            }
        )

        assert result == [
            "src/backend/fashiongen_worker.py:\\bplaceholder\\b",
            "src/main/providers.ts:\\bplaceholder\\b",
        ]


class TestResolveReworkRetryBudget:
    def test_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._resolve_rework_retry_budget() == 3

    def test_env_override(self, tmp_path: Any, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES", "5")
        assert QAAdapter._resolve_rework_retry_budget() == 5

    def test_env_clamped(self, tmp_path: Any, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES", "99")
        assert QAAdapter._resolve_rework_retry_budget() == 20
        monkeypatch.setenv("KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES", "0")
        assert QAAdapter._resolve_rework_retry_budget() == 1


class TestRuntimeStageSignals:
    def test_filters_stale_run_signals(self, tmp_path: Any, monkeypatch: Any) -> None:
        adapter = _make_adapter(tmp_path)
        signal_dir = tmp_path / "runtime" / "signals"
        signal_dir.mkdir(parents=True)
        (signal_dir / "director_dispatch.signals.json").write_text(
            json.dumps(
                {
                    "factory_run_id": "old-run",
                    "signals": [
                        {
                            "factory_run_id": "old-run",
                            "severity": "error",
                            "code": "director.run_status_non_success",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (signal_dir / "quality_gate.signals.json").write_text(
            json.dumps(
                {
                    "factory_run_id": "current-run",
                    "signals": [
                        {
                            "factory_run_id": "current-run",
                            "severity": "info",
                            "code": "qa.ready",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        def _fake_runtime_path(_workspace: str, _relative: str) -> Any:
            return signal_dir

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.internal.qa_adapter.resolve_runtime_path",
            _fake_runtime_path,
        )

        signals = adapter._load_runtime_stage_signals(run_id="current-run")
        assert [signal["code"] for signal in signals] == ["qa.ready"]


class TestStaticReview:
    def test_workspace_quality_evidence_marks_factory_runtime_hard_gate(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "app.ts").write_text("export const value = 1;\n", encoding="utf-8")
        target = "\n".join(
            [
                "Workspace quality evidence collected before QA judgement:",
                "{",
                '  "schema_version": "factory.workspace_quality_checks.v1",',
                '  "source": "factory_stage_executor",',
                '  "passed": true,',
                '  "commands": [',
                '    {"command": ["npm", "install"], "phase": "prepare", "passed": true},',
                '    {"command": ["npm", "run", "build"], "phase": "check_after_repair", "passed": true}',
                "  ],",
                '  "repair": {"attempted": true, "success": true, "source_tools": ["deterministic_ts"]}',
                "}",
            ]
        )

        review = adapter._run_static_review(target)

        assert "factory_workspace_quality_passed=True" in review["evidence"]
        assert "factory_runtime_hard_gate_passed=True" in review["evidence"]
        assert "factory_workspace_repair_source_tools=deterministic_ts" in review["evidence"]

    def test_jsx_placeholder_attribute_is_not_unfinished_content(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "Library.tsx").write_text(
            'export function Library() { return <input placeholder="Search templates..." />; }\n',
            encoding="utf-8",
        )
        (src / "Library.test.tsx").write_text("test('renders', () => expect(true).toBe(true));\n", encoding="utf-8")

        review = adapter._run_static_review("Fashion Gen Studio")

        assert "placeholder_content_detected" not in review["critical_issues"]
        assert review["verdict"] == "PASS"

    def test_todo_still_counts_as_unfinished_content(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "App.tsx").write_text(
            "// TODO wire the real workbench\nexport function App() { return null; }\n", encoding="utf-8"
        )

        review = adapter._run_static_review("Fashion Gen Studio")

        assert "placeholder_content_detected" in review["critical_issues"]

    def test_todo_product_selectors_are_not_unfinished_content(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "index.html").write_text(
            '<form id="todo-form"><input id="todo-input"><ul id="todo-list"></ul></form>\n',
            encoding="utf-8",
        )
        (tmp_path / "style.css").write_text(
            ".todo-list { display: grid; }\n.todo-item.done { opacity: .7; }\n",
            encoding="utf-8",
        )
        (tmp_path / "app.js").write_text(
            "\n".join(
                [
                    'const STORAGE_KEY = "todo_app_items";',
                    'const form = document.getElementById("todo-form");',
                    "const items = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');",
                    "form.addEventListener('submit', (event) => { event.preventDefault(); });",
                    "localStorage.setItem(STORAGE_KEY, JSON.stringify(items));",
                ]
            ),
            encoding="utf-8",
        )

        review = adapter._run_static_review("原生本地待办事项")

        assert "placeholder_content_detected" not in review["critical_issues"]
        assert "code_file_count=3" in review["evidence"]
        assert any(str(item).startswith("feature:local_storage=") for item in review["evidence"])
        assert any(str(item).startswith("feature:dom_event_listener=") for item in review["evidence"])
        assert any(str(item).startswith("feature:dom_selector=") for item in review["evidence"])

    def test_todo_domain_comments_are_not_todo_markers(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        tests = tmp_path / "tests"
        tests.mkdir()
        (tmp_path / "app.js").write_text(
            "/**\n"
            " * Todo List Application - Pure Vanilla JavaScript\n"
            " */\n"
            "const form = document.getElementById('todo-form');\n"
            "form.addEventListener('submit', (event) => event.preventDefault());\n",
            encoding="utf-8",
        )
        (tests / "app.test.js").write_text(
            "/** Unit tests for the Todo App (app.js) */\ndescribe('Todo App - Data Layer', function () {});\n",
            encoding="utf-8",
        )

        review = adapter._run_static_review("原生本地待办事项")

        assert "placeholder_content_detected" not in review["critical_issues"]
        assert not any(":\\bTODO\\b" in str(item) for item in review["evidence"])

    def test_targeted_repair_does_not_fail_on_out_of_scope_placeholder(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        src = tmp_path / "src"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir(parents=True)
        (src / "legacy.ts").write_text("export const value = 'placeholder';\n", encoding="utf-8")
        (tests / "GenerationSpec.test.ts").write_text(
            "test('generation spec', () => expect(true).toBe(true));\n",
            encoding="utf-8",
        )

        review = adapter._run_static_review("Fix npm test failure in tests/GenerationSpec.test.ts")

        assert "placeholder_content_detected" not in review["critical_issues"]
        assert "out_of_scope_placeholder_content_detected" in review["warnings"]
        assert review["verdict"] == "PASS"

    def test_project_quality_gate_does_not_scope_limit_placeholder_scan(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        src = tmp_path / "src"
        tests = tmp_path / "tests"
        src.mkdir(parents=True)
        tests.mkdir(parents=True)
        (src / "legacy.ts").write_text("export const value = 'placeholder';\n", encoding="utf-8")
        (src / "assets.ts").write_text("export const assetPath = 'src/assets/catalog.ts';\n", encoding="utf-8")
        (tests / "GenerationSpec.test.ts").write_text(
            "test('generation spec', () => expect(true).toBe(true));\n",
            encoding="utf-8",
        )

        review = adapter._run_static_review(
            "FashionGenStudio final project quality gate. Reference paths include src/assets/catalog.ts. "
            "The product requirement includes batch failure retry and 失败重试 features."
        )

        assert "placeholder_content_detected" in review["critical_issues"]
        assert "out_of_scope_placeholder_content_detected" not in review["warnings"]

    def test_generated_runtime_outputs_are_ignored_for_placeholder_scan(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        src = tmp_path / "src"
        dist = tmp_path / "dist"
        runtime = tmp_path / "runtime"
        src.mkdir(parents=True)
        dist.mkdir(parents=True)
        runtime.mkdir(parents=True)
        (src / "App.test.ts").write_text("test('ok', () => expect(true).toBe(true));\n", encoding="utf-8")
        (dist / "bundle.js").write_text("const label = 'placeholder';\n", encoding="utf-8")
        (runtime / "request.json").write_text('{"prompt":"placeholder"}\n', encoding="utf-8")

        review = adapter._run_static_review("Project quality gate")

        assert "placeholder_content_detected" not in review["critical_issues"]
        assert "out_of_scope_placeholder_content_detected" not in review["warnings"]


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


class TestBuildQaMessage:
    def test_includes_review_type_and_target(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_qa_message("quality_gate", "Project X")
        assert "quality_gate" in msg
        assert "Project X" in msg
        assert "Return exactly one JSON object" not in msg
        assert "Do not call tools" not in msg

    def test_includes_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {"evidence": ["code_file_count=5"]}
        msg = adapter._build_qa_message("quality_gate", "Project X", review_result=review)
        assert "code_file_count=5" in msg

    def test_no_evidence_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_qa_message("quality_gate", "Project X", review_result={})
        assert "no deterministic evidence" in msg

    def test_sanitizes_factory_run_id_without_dropping_ce_context(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = (
            'PM task contract: target_files ["src/main.ts"].\n'
            "Chief Engineer blueprint evidence: blueprint_id=bp-1.\n"
            'factory_workspace_quality: npm run build failed; "factory_run_id": "run-1"'
        )

        msg = adapter._build_qa_message("quality_gate", target)

        assert "Chief Engineer blueprint evidence" in msg
        assert "factory_workspace_quality" in msg
        assert '"factory_run_id"' not in msg
        assert '"factory_run_ref"' in msg

    def test_prompt_appendix_contains_json_contract(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        appendix = adapter._build_qa_prompt_appendix()
        assert "Return exactly one JSON object" in appendix
        assert "Do not call tools" in appendix
        assert '"verdict": "PASS|FAIL"' in appendix

    def test_json_repair_message_preserves_previous_output(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_qa_json_repair_message(
            "quality_gate",
            "Project X",
            review_result={"evidence": ["workspace_checks_passed=True"]},
            previous_output="我先对工作区进行侦察，然后执行审查。",
        )

        assert "Return exactly one JSON object" not in msg
        assert "workspace_checks_passed=True" in msg
        assert "我先对工作区进行侦察" in msg

        appendix = adapter._build_qa_json_repair_prompt_appendix()
        assert "Return exactly one JSON object" in appendix
        assert "Do not call tools" in appendix


# ---------------------------------------------------------------------------
# Review result parsing
# ---------------------------------------------------------------------------


class TestParseReviewResult:
    def test_json_payload(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._parse_review_result('{"verdict": "PASS", "score": 90}')
        assert result["verdict"] == "PASS"
        assert result["score"] == 90

    def test_fallback_regex(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._parse_review_result('Some text "verdict": "FAIL" "score": 42')
        assert result["verdict"] == "FAIL"
        assert result["score"] == 42
        assert "qa_llm_partial_parse_recovered" in result["warnings"]

    def test_unparseable(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._parse_review_result("random text")
        assert result["parsed_json"] is False


class TestQaExecute:
    def test_execute_sanitizes_target_before_llm_context_projection(self, tmp_path: Any, monkeypatch: Any) -> None:
        adapter = _make_adapter(tmp_path)
        calls: list[dict[str, Any]] = []

        async def fake_invoke_role_runtime_first(**kwargs: Any) -> dict[str, str]:
            calls.append(dict(kwargs))
            return {
                "response": json.dumps(
                    {
                        "verdict": "PASS",
                        "score": 96,
                        "critical_issues": [],
                        "major_issues": [],
                        "warnings": [],
                        "evidence": ["workspace_checks_passed=True"],
                        "suggestions": [],
                    },
                    ensure_ascii=False,
                )
            }

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.internal.qa_adapter.invoke_role_runtime_first",
            fake_invoke_role_runtime_first,
        )
        monkeypatch.setattr(
            adapter,
            "_run_static_review",
            lambda target, *, run_id="": {
                "verdict": "PASS",
                "score": 100,
                "critical_issues": [],
                "major_issues": [],
                "warnings": [],
                "evidence": ["static_passed=True"],
                "suggestions": [],
            },
        )
        target = (
            'Workspace quality evidence: {"factory_run_id": "factory-1"}\n'
            "Chief Engineer blueprint evidence collected before QA judgement: blueprint_id=bp-1"
        )

        result = asyncio.run(
            adapter.execute(
                "qa-task",
                {"review_type": "quality_gate", "review_target": target},
                {"run_id": "factory-1"},
            )
        )

        assert result["success"] is True
        assert len(calls) == 1
        message = str(calls[0].get("message") or "")
        context = calls[0].get("context")
        assert isinstance(context, dict)
        assert '"factory_run_id"' not in message
        assert '"factory_run_id"' not in str(context.get("target") or "")
        assert '"factory_run_ref"' in message
        assert '"factory_run_ref"' in str(context.get("target") or "")
        assert "Chief Engineer blueprint evidence" in message
        # R183/M02: QA LLM context must carry a first-class execution identity.
        assert str(context.get("turn_request_id") or "").startswith("qa-")
        metadata = context.get("metadata")
        assert isinstance(metadata, dict)
        assert str(metadata.get("turn_request_id") or "").startswith("qa-")
        assert metadata.get("turn_request_id") == context.get("turn_request_id")

    def test_bind_qa_transaction_execution_identity_mints_stable_turn_request_id(self) -> None:
        from polaris.cells.roles.adapters.internal.qa_adapter import (
            _bind_qa_transaction_execution_identity,
        )

        first = _bind_qa_transaction_execution_identity(
            task_id="task-0-qa",
            run_id="qa-run-1",
            review_type="quality_gate",
            parent_context={"run_id": "qa-run-1"},
            stage_label="judgement",
        )
        second = _bind_qa_transaction_execution_identity(
            task_id="task-0-qa",
            run_id="qa-run-1",
            review_type="quality_gate",
            parent_context={"run_id": "qa-run-1"},
            stage_label="judgement",
        )
        repair = _bind_qa_transaction_execution_identity(
            task_id="task-0-qa",
            run_id="qa-run-1",
            review_type="quality_gate",
            parent_context={"run_id": "qa-run-1"},
            stage_label="json_repair",
        )
        assert first["turn_request_id"] == second["turn_request_id"]
        assert first["turn_request_id"].startswith("qa-quality_gate-")
        assert repair["turn_request_id"] == f"{first['turn_request_id']}-json_repair"
        assert first["metadata"]["turn_request_id"] == first["turn_request_id"]
        parented = _bind_qa_transaction_execution_identity(
            task_id="task-0-qa",
            run_id="qa-run-1",
            review_type="quality_gate",
            parent_context={"execution_attempt_id": "attempt-parent-9", "run_id": "qa-run-1"},
            stage_label="judgement",
        )
        assert parented["execution_attempt_id"] == "attempt-parent-9"
        assert parented["metadata"]["execution_attempt_id"] == "attempt-parent-9"
        assert parented["turn_request_id"].startswith("qa-judgement-")
        assert parented["turn_request_id"] != "attempt-parent-9"

        evidence_parent = {
            "run_id": "qa-run-1",
            "metadata": {
                "pm_task_contract": {"schema_version": "pm.task_contract.v1", "task_id": "TASK-1"},
                "chief_engineer_blueprint": {
                    "schema_version": "chief_engineer.blueprint.v1",
                    "blueprint_id": "ce-1",
                },
                "target_files": ["src/main.py"],
                "verifier_receipts": [{"command": ["pytest"], "exit_code": 0}],
                "workspace_quality_evidence": {
                    "schema_version": "factory.workspace_quality_checks.v1",
                    "passed": True,
                },
                "request_timeout_seconds": 595,
                "timeout_seconds": 595,
            },
        }
        evidence_bound = _bind_qa_transaction_execution_identity(
            task_id="task-0-qa",
            run_id="qa-run-1",
            review_type="quality_gate",
            parent_context=evidence_parent,
            stage_label="json_repair",
        )
        for key in (
            "pm_task_contract",
            "chief_engineer_blueprint",
            "target_files",
            "verifier_receipts",
            "workspace_quality_evidence",
        ):
            assert evidence_bound[key] == evidence_parent["metadata"][key]
            assert evidence_bound["metadata"][key] == evidence_parent["metadata"][key]
            assert evidence_bound[key] is not evidence_parent["metadata"][key]
        assert evidence_bound["request_timeout_seconds"] == 595
        assert evidence_bound["timeout_seconds"] == 595
        assert evidence_bound["metadata"]["request_timeout_seconds"] == 595
        assert evidence_bound["metadata"]["timeout_seconds"] == 595

    def test_cognitive_runtime_blocked_is_nonfatal_when_static_gate_passes(
        self, tmp_path: Any, monkeypatch: Any
    ) -> None:
        adapter = _make_adapter(tmp_path)

        async def fake_invoke_role_runtime_first(**kwargs: Any) -> dict[str, str]:
            del kwargs
            raise RuntimeError("cognitive_runtime_blocked:Blockers: ('Low probability - insufficient confidence',)")

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.internal.qa_adapter.invoke_role_runtime_first",
            fake_invoke_role_runtime_first,
        )
        monkeypatch.setattr(
            adapter,
            "_run_static_review",
            lambda target, *, run_id="": {
                "verdict": "PASS",
                "score": 100,
                "critical_issues": [],
                "major_issues": [],
                "warnings": [],
                "evidence": ["static_passed=True"],
                "suggestions": [],
            },
        )

        result = asyncio.run(
            adapter.execute(
                "qa-task",
                {"review_type": "quality_gate", "review_target": "Project quality gate"},
                {
                    "run_id": "run-1",
                    "metadata": {
                        "pm_task_contract": {
                            "schema_version": "pm.task_contract.v1",
                            "task_id": "TASK-1",
                            "target_files": ["src/main.py"],
                        },
                        "chief_engineer_blueprint": {
                            "schema_version": "chief_engineer.blueprint.v1",
                            "blueprint_id": "ce-1",
                            "target_files": ["src/main.py"],
                        },
                        "target_files": ["src/main.py"],
                        "verifier_receipts": [{"command": ["pytest"], "exit_code": 0}],
                        "workspace_quality_evidence": {
                            "schema_version": "factory.workspace_quality_checks.v1",
                            "passed": True,
                        },
                    },
                },
            )
        )

        assert result["success"] is True
        assert result["critical_issues"] == []
        assert "qa_llm_judgement_unavailable" in result["warnings"]

    def test_retries_strict_json_when_llm_output_lacks_verdict(self, tmp_path: Any, monkeypatch: Any) -> None:
        adapter = _make_adapter(tmp_path)
        calls: list[str] = []
        appendices: list[str] = []
        metadata_calls: list[dict[str, Any]] = []

        async def fake_invoke_role_runtime_first(**kwargs: Any) -> dict[str, str]:
            calls.append(str(kwargs.get("message") or ""))
            appendices.append(str(kwargs.get("prompt_appendix") or ""))
            raw_context = kwargs.get("context")
            context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
            raw_metadata = context.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            metadata_calls.append(dict(metadata))
            if len(calls) == 1:
                return {"response": "我先对工作区进行侦察，然后按 QA 工作流执行审查 → 测试 → 报告。"}
            return {
                "response": json.dumps(
                    {
                        "verdict": "PASS",
                        "score": 92,
                        "critical_issues": [],
                        "major_issues": [],
                        "warnings": [],
                        "evidence": ["workspace_checks_passed=True"],
                        "suggestions": [],
                    },
                    ensure_ascii=False,
                )
            }

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.internal.qa_adapter.invoke_role_runtime_first",
            fake_invoke_role_runtime_first,
        )
        monkeypatch.setattr(
            adapter,
            "_run_static_review",
            lambda target, *, run_id="": {
                "verdict": "PASS",
                "score": 100,
                "critical_issues": [],
                "major_issues": [],
                "warnings": [],
                "evidence": ["static_passed=True"],
                "suggestions": [],
            },
        )

        result = asyncio.run(
            adapter.execute(
                "qa-task",
                {"review_type": "quality_gate", "review_target": "Project quality gate"},
                {
                    "run_id": "run-1",
                    "metadata": {
                        "pm_task_contract": {
                            "schema_version": "pm.task_contract.v1",
                            "task_id": "TASK-1",
                            "target_files": ["src/main.py"],
                        },
                        "chief_engineer_blueprint": {
                            "schema_version": "chief_engineer.blueprint.v1",
                            "blueprint_id": "ce-1",
                            "target_files": ["src/main.py"],
                        },
                        "target_files": ["src/main.py"],
                        "verifier_receipts": [{"command": ["pytest"], "exit_code": 0}],
                        "workspace_quality_evidence": {
                            "schema_version": "factory.workspace_quality_checks.v1",
                            "passed": True,
                        },
                    },
                },
            )
        )

        assert len(calls) == 2
        assert all("Return exactly one JSON object" not in item for item in calls)
        assert "Return exactly one JSON object" in appendices[0]
        assert "Return exactly one JSON object" in appendices[1]
        assert all(item.get("native_tool_mode") == "disabled" for item in metadata_calls)
        assert all(item.get("response_format_mode") == "json" for item in metadata_calls)
        assert all(item.get("qa_output_contract") == "json_only_verdict" for item in metadata_calls)
        for metadata in metadata_calls:
            assert metadata["pm_task_contract"]["task_id"] == "TASK-1"
            assert metadata["chief_engineer_blueprint"]["blueprint_id"] == "ce-1"
            assert metadata["target_files"] == ["src/main.py"]
            assert metadata["verifier_receipts"][0]["exit_code"] == 0
            assert metadata["workspace_quality_evidence"]["passed"] is True
        assert result["success"] is True
        assert "qa_llm_judgement_unavailable" not in result["warnings"]


# ---------------------------------------------------------------------------
# Review result merge
# ---------------------------------------------------------------------------


class TestMergeReviewResult:
    def test_llm_passed_inherits(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "evidence": [],
            "suggestions": [],
        }
        llm = {"parsed_json": True, "verdict": "FAIL", "score": 50, "critical_issues": ["bug"], "warnings": ["slow"]}
        merged = adapter._merge_review_result(base, llm)
        assert merged["verdict"] == "FAIL"
        assert merged["score"] == 50
        assert "bug" in merged["critical_issues"]
        assert "slow" in merged["warnings"]

    def test_llm_unparsed_adds_warning(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "evidence": [],
            "suggestions": [],
        }
        llm = {"parsed_json": False, "raw_excerpt": "bad json"}
        merged = adapter._merge_review_result(base, llm)
        assert "qa_llm_judgement_unavailable" in merged["warnings"]
        assert "llm_excerpt=bad json" in merged["evidence"]

    def test_llm_unparsed_does_not_mask_deterministic_gate_failure(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {
            "verdict": "FAIL",
            "score": 70,
            "critical_issues": ["workspace_quality_gate_failed"],
            "major_issues": [],
            "warnings": [],
            "evidence": ["workspace_quality_passed=False"],
            "suggestions": [],
        }
        llm = {"parsed_json": False, "raw_excerpt": "not json"}
        merged = adapter._merge_review_result(base, llm)
        assert "qa_llm_judgement_unavailable" not in merged["warnings"]
        assert "qa_llm_judgement_unavailable_suppressed=deterministic_gate_blocked" in merged["evidence"]
        assert "llm_excerpt=not json" in merged["evidence"]

    def test_dedupe(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": ["a"],
            "major_issues": [],
            "warnings": [],
            "evidence": [],
            "suggestions": [],
        }
        llm = {"parsed_json": True, "verdict": "PASS", "critical_issues": ["a", "b"]}
        merged = adapter._merge_review_result(base, llm)
        assert merged["critical_issues"] == ["a", "b"]

    def test_factory_runtime_hard_gate_demotes_quality_only_llm_criticals(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "evidence": ["factory_runtime_hard_gate_passed=True"],
            "suggestions": [],
        }
        llm = {
            "parsed_json": True,
            "verdict": "FAIL",
            "score": 55,
            "critical_issues": [
                "No evidence of behavioral test execution: npm test invokes tsc --noEmit",
                "The deterministic repair added a missing export but no before/after diff is included",
            ],
            "major_issues": ["No coverage gate is evidenced"],
            "warnings": [],
            "evidence": ["After repair: npm run build exit_code=0"],
            "suggestions": [],
        }

        merged = adapter._merge_review_result(base, llm)
        finalized = adapter._finalize_review_result(merged)

        assert merged["verdict"] == "PASS"
        assert merged["critical_issues"] == []
        assert "No evidence of behavioral test execution: npm test invokes tsc --noEmit" in merged["major_issues"]
        assert "qa_llm_quality_risk_not_runtime_blocker" in merged["warnings"]
        assert "qa_llm_verdict_downgraded=FAIL:factory_runtime_hard_gate_passed" in merged["evidence"]
        assert finalized["passed"] is False
        assert finalized["verdict"] == "FAIL"
        assert "qa_score_below_pass_threshold=55<70" in finalized["warnings"]

    def test_factory_runtime_hard_gate_keeps_placeholder_test_critical(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "evidence": ["factory_runtime_hard_gate_passed=True"],
            "suggestions": [],
        }
        llm = {
            "parsed_json": True,
            "verdict": "FAIL",
            "score": 42,
            "critical_issues": [
                "测试脚本为占位符: npm test 实际执行 tsc --noEmit && echo TypeScript compilation passed",
                "测试文件计数为 0 (test_file_count=0): 完全没有行为级验证",
            ],
            "major_issues": [],
            "warnings": [],
            "evidence": ["factory_runtime_hard_gate_passed=True"],
            "suggestions": [],
        }

        merged = adapter._merge_review_result(base, llm)
        finalized = adapter._finalize_review_result(merged)

        assert merged["verdict"] == "FAIL"
        assert (
            "测试脚本为占位符: npm test 实际执行 tsc --noEmit && echo TypeScript compilation passed"
            in merged["critical_issues"]
        )
        assert "测试文件计数为 0 (test_file_count=0): 完全没有行为级验证" in merged["critical_issues"]
        assert "qa_llm_quality_risk_not_runtime_blocker" not in merged["warnings"]
        assert "qa_llm_verdict_downgraded=FAIL:factory_runtime_hard_gate_passed" not in merged["evidence"]
        assert finalized["passed"] is False

    def test_factory_runtime_hard_gate_keeps_llm_runtime_blocker_critical(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        base = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "evidence": ["factory_runtime_hard_gate_passed=True"],
            "suggestions": [],
        }
        llm = {
            "parsed_json": True,
            "verdict": "FAIL",
            "score": 40,
            "critical_issues": ["Runtime failed: CLI entry cannot start"],
            "major_issues": [],
            "warnings": [],
            "evidence": [],
            "suggestions": [],
        }

        finalized = adapter._finalize_review_result(adapter._merge_review_result(base, llm))

        assert finalized["passed"] is False
        assert "Runtime failed: CLI entry cannot start" in finalized["critical_issues"]


# ---------------------------------------------------------------------------
# Finalize review result
# ---------------------------------------------------------------------------


class TestFinalizeReviewResult:
    def test_pass_when_no_issues(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "evidence": [],
            "suggestions": [],
        }
        result = adapter._finalize_review_result(review)
        assert result["passed"] is True
        assert result["score"] == 100

    def test_fail_on_critical(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": ["bug"],
            "major_issues": [],
            "warnings": [],
            "evidence": [],
            "suggestions": [],
        }
        result = adapter._finalize_review_result(review)
        assert result["passed"] is False
        assert result["score"] == 70

    def test_fail_on_verdict_fail(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {
            "verdict": "FAIL",
            "score": 100,
            "critical_issues": [],
            "major_issues": [],
            "warnings": [],
            "evidence": [],
            "suggestions": [],
        }
        result = adapter._finalize_review_result(review)
        assert result["passed"] is False

    def test_score_computed_correctly(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        review = {
            "verdict": "PASS",
            "score": 100,
            "critical_issues": ["a", "b"],
            "major_issues": ["c"],
            "warnings": ["d"],
            "evidence": [],
            "suggestions": [],
        }
        result = adapter._finalize_review_result(review)
        # 100 - 2*30 - 1*10 - 1*4 = 26
        assert result["score"] == 26


# ---------------------------------------------------------------------------
# JSON payload extraction
# ---------------------------------------------------------------------------


class TestExtractJsonPayload:
    def test_plain_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('{"verdict": "PASS"}')
        assert result == {"verdict": "PASS"}

    def test_fenced_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('```json\n{"verdict": "PASS"}\n```')
        assert result == {"verdict": "PASS"}

    def test_with_comments(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('{"verdict": "PASS" // comment\n}')
        assert result == {"verdict": "PASS"}

    def test_empty_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_json_payload("") is None


# ---------------------------------------------------------------------------
# Strip JSON comments
# ---------------------------------------------------------------------------


class TestStripJsonLineComments:
    def test_removes_comments(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = '{"a": 1 // comment\n}'
        assert adapter._strip_json_line_comments(text) == '{"a": 1 \n}'

    def test_preserves_url_in_string(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = '{"url": "http://example.com"}'
        assert adapter._strip_json_line_comments(text) == '{"url": "http://example.com"}'

    def test_no_comment_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = '{"a": 1}'
        assert adapter._strip_json_line_comments(text) == '{"a": 1}'


# ---------------------------------------------------------------------------
# Normalize review payload
# ---------------------------------------------------------------------------


class TestNormalizeReviewPayload:
    def test_basic(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        payload = {
            "verdict": "PASS",
            "score": 90,
            "critical_issues": ["a"],
            "findings": [{"severity": "high", "description": "bug"}],
        }
        result = adapter._normalize_review_payload(payload)
        assert result["verdict"] == "PASS"
        assert "bug" in result["major_issues"]

    def test_findings_critical(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        payload = {"findings": [{"severity": "critical", "description": "crash"}]}
        result = adapter._normalize_review_payload(payload)
        assert "crash" in result["critical_issues"]

    def test_summary_in_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        payload = {"summary": "overall good"}
        result = adapter._normalize_review_payload(payload)
        assert "llm_summary=overall good" in result["evidence"]


# ---------------------------------------------------------------------------
# Domain tokens
# ---------------------------------------------------------------------------


class TestExtractDomainTokens:
    def test_filters_stopwords(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_domain_tokens("project quality module")
        assert "project" not in result
        assert "quality" not in result

    def test_extracts_unique(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_domain_tokens("payment gateway payment")
        assert result == ["payment", "gateway"]


# ---------------------------------------------------------------------------
# Coerce / dedupe helpers
# ---------------------------------------------------------------------------


class TestCoerceInt:
    def test_numeric(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_int(5) == 5
        assert adapter._coerce_int("7") == 7

    def test_invalid_returns_zero(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_int("abc") == 0
        assert adapter._coerce_int(None) == 0


class TestCoerceList:
    def test_list_passthrough(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_list(["a", "b"]) == ["a", "b"]

    def test_string_wrap(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_list("hello") == ["hello"]

    def test_empty_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._coerce_list(None) == []
        assert adapter._coerce_list("") == []


class TestDedupeList:
    def test_removes_duplicates(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._dedupe_list(["a", "b", "a"]) == ["a", "b"]

    def test_non_list_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._dedupe_list("not a list") == []

    def test_strips_and_skips_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._dedupe_list([" a ", "", "a"]) == ["a"]


# ---------------------------------------------------------------------------
# Semantic equivalence
# ---------------------------------------------------------------------------


class TestCheckSemanticEquivalence:
    def test_empty_returns_false(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._check_semantic_equivalence("", "spec")
        assert result["equivalent"] is False
        assert "missing_code_or_spec" in result["issues"]

    def test_sufficient_keyword_coverage(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        code = "def process_payment(amount): return amount * 2"
        spec = "The payment processing function should take an amount and return double"
        result = adapter._check_semantic_equivalence(code, spec)
        assert result["semantic_equivalence_checked"] is True
        assert result["confidence"] > 0

    def test_missing_return_detected(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        code = "def foo(): pass"
        spec = "Return the computed value"
        result = adapter._check_semantic_equivalence(code, spec)
        assert "missing_return_statement" in result["mismatch_indicators"]


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


class TestDetectRegressions:
    def test_no_baseline(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._detect_regressions("code", baseline_snapshot=None, context=None)
        assert result["regressions_found"] == 0
        assert "no_baseline_available" in result["warnings"]

    def test_significant_reduction(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        baseline = {"code": "line\n" * 100}
        current = "line\n" * 30
        result = adapter._detect_regressions(current, baseline_snapshot=baseline)
        assert result["regressions_found"] == 1
        assert "significant_code_reduction" in result["regressions"][0]

    def test_api_reduction(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        baseline = {"code": "def a(): pass\ndef b(): pass\n"}
        current = "def a(): pass\n"
        result = adapter._detect_regressions(current, baseline_snapshot=baseline)
        # Both significant_code_reduction and api_reduction may be triggered;
        # assert api_reduction is present somewhere in regressions.
        assert any("api_reduction" in r for r in result["regressions"])

    def test_stable(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        baseline = {"code": "def a(): pass\n"}
        current = "def a(): pass\n"
        result = adapter._detect_regressions(current, baseline_snapshot=baseline)
        assert result["status"] == "stable"

    def test_improvement(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        baseline = {"code": "def a(): pass\n"}
        current = "def a(): pass\ndef b(): pass\n"
        result = adapter._detect_regressions(current, baseline_snapshot=baseline)
        assert result["status"] == "improved"


# ---------------------------------------------------------------------------
# Adapter identity
# ---------------------------------------------------------------------------


class TestQaAdapterIdentity:
    def test_role_id(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter.role_id == "qa"

    def test_capabilities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "code_review" in caps
        assert "semantic_equivalence_checking" in caps

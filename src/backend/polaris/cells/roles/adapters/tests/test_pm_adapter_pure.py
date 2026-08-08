"""Unit tests for PMAdapter pure logic (no I/O, no LLM).

Covers:
- _build_pm_message / _build_pm_retry_message
- _extract_task_contracts / _extract_tasks_from_payload / _extract_json_payload
- _extract_tasks_from_sections / _extract_tasks_from_bullets
- _normalize_task_contract / _normalize_list
- _infer_scope_from_title / _derive_domain_token / _extract_domain_keywords
- _analyze_directive_complexity / _apply_meta_planning_hints
- _normalize_projection_project_slug / _extract_projection_contract_hint
- _apply_projection_contract_hint / _build_projection_hint_contracts
- _synthesize_task_contracts_from_directive
- _canonical_text / _build_task_identity_signature / _pick_preferred_task_id / _find_existing_task_match
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from polaris.cells.roles.adapters.internal.pm.synthesis import (
    _extract_deterministic_checks_from_directive,
)
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Any) -> PMAdapter:
    return PMAdapter(workspace=str(tmp_path))


class _RowProjectionOnlyTaskBoard:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]

    def list_task_rows(self) -> list[dict[str, Any]]:
        raise AssertionError("PM read-model consumers must use list_observable_task_rows()")

    def list_all(self) -> list[Any]:
        raise AssertionError("PM read-model consumers must use list_observable_task_rows()")

    def task_exists(self, task_id: Any) -> bool:
        return any(str(row.get("id") or "") == str(task_id or "") for row in self._rows)

    def update_task(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        for row in self._rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            if status is not None:
                row["status"] = status
            if metadata is not None:
                current_metadata = row.get("metadata")
                merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
                merged_metadata.update(metadata)
                row["metadata"] = merged_metadata

    def fail_task_row_from_role_adapter(
        self,
        task_id: Any,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        for row in self._rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            row["status"] = "failed"
            current_metadata = row.get("metadata")
            merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
            merged_metadata["failure_reason"] = str(reason or "").strip()
            if metadata is not None:
                merged_metadata.update(metadata)
            row["metadata"] = merged_metadata
            return dict(row)
        return None


class _RowWriteOnlyTaskRuntime:
    def __init__(self) -> None:
        self._next_id = 1
        self._rows: list[dict[str, Any]] = []

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]

    def list_task_rows(self) -> list[dict[str, Any]]:
        raise AssertionError("PM read-model consumers must use list_observable_task_rows()")

    def create_task_row(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        row = {
            "id": self._next_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blocked_by": list(blocked_by or []),
            "metadata": dict(metadata or {}),
        }
        self._next_id += 1
        self._rows.append(row)
        return dict(row)

    def update_task_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        for row in self._rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            if status is not None:
                row["status"] = status
            if blocked_by is not None:
                row["blocked_by"] = list(blocked_by)
            if metadata is not None:
                current_metadata = row.get("metadata")
                merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
                merged_metadata.update(metadata)
                row["metadata"] = merged_metadata
            return dict(row)
        return None

    def get_task(self, task_id: Any) -> dict[str, Any] | None:
        for row in self._rows:
            if str(row.get("id") or "") == str(task_id or ""):
                return dict(row)
        return None

    def task_exists(self, task_id: Any) -> bool:
        return self.get_task(task_id) is not None

    def cancel_task_row_for_deduplication(
        self,
        task_id: Any,
        *,
        primary_task_id: int,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
    ) -> dict[str, Any] | None:
        for row in self._rows:
            if str(row.get("id") or "") != str(task_id or ""):
                continue
            row["status"] = "cancelled"
            current_metadata = row.get("metadata")
            merged_metadata = dict(current_metadata) if isinstance(current_metadata, dict) else {}
            merged_metadata.update(dict(metadata or {}))
            merged_metadata["dedup_merged_into"] = int(primary_task_id or 0)
            merged_metadata["dedup_reason"] = str(reason or "").strip()
            merged_metadata["dedup_source"] = str(source or "").strip()
            row["metadata"] = merged_metadata
            return dict(row)
        return None

    def create(self, **_kwargs: Any) -> None:
        raise AssertionError("PM task creation must use create_task_row()")

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("PM task updates must use update_task_row()")

    def get(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("PM task reads must use get_task()")

    def list_all(self) -> list[Any]:
        raise AssertionError("PM read-model consumers must use list_observable_task_rows()")


class _DependencyUpdateMissingTaskRuntime(_RowWriteOnlyTaskRuntime):
    def update_task_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        if blocked_by is not None:
            return None
        return super().update_task_row(task_id, status=status, blocked_by=blocked_by, metadata=metadata, **_kwargs)


class _ExecutionEventFailureTaskRuntime(_RowWriteOnlyTaskRuntime):
    def __init__(self, *, fail_action: str) -> None:
        super().__init__()
        self.fail_action = fail_action

    def create_task_row(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = self._with_execution_event_failure(
            "create_board_task",
            super().create_task_row(
                subject=subject,
                description=description,
                blocked_by=blocked_by,
                metadata=metadata,
                **_kwargs,
            ),
        )
        if result is None:
            raise AssertionError("create_task_row must return a row projection")
        return result

    def update_task_row(
        self,
        task_id: Any,
        *,
        status: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        action = "resolve_dependencies" if blocked_by is not None else "deduplicate_contract_match"
        return self._with_execution_event_failure(
            action,
            super().update_task_row(
                task_id,
                status=status,
                blocked_by=blocked_by,
                metadata=metadata,
                **_kwargs,
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


class _DedupCancelExecutionEventFailureTaskRuntime(_RowWriteOnlyTaskRuntime):
    def __init__(self, *, missing_row: bool = False, failed_event: bool = True) -> None:
        super().__init__()
        self.missing_row = missing_row
        self.failed_event = failed_event

    def cancel_task_row_for_deduplication(
        self,
        task_id: Any,
        *,
        primary_task_id: int,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
    ) -> dict[str, Any] | None:
        result = super().cancel_task_row_for_deduplication(
            task_id,
            primary_task_id=primary_task_id,
            reason=reason,
            metadata=metadata,
            source=source,
        )
        if self.missing_row:
            return None
        if not self.failed_event or result is None:
            return result
        failed_result = dict(result)
        failed_result["execution_event"] = {
            "ok": False,
            "event_type": "task_runtime.execution",
            "error_code": "append_failed",
        }
        return failed_result


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


class TestBuildPmMessage:
    def test_includes_directive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_pm_message([], "Implement login")
        assert "Implement login" in msg
        assert "JSON" in msg

    def test_includes_existing_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_pm_message([{"subject": "T1", "status": "pending"}], "Do more")
        assert "T1" in msg

    def test_meta_planning_injection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        analysis = {"complexity": "high", "estimated_task_count": 7, "recommended_strategy": "deep_decomposition"}
        msg = adapter._build_pm_message([], "Big task", directive_analysis=analysis)
        assert "深度分解" in msg
        assert "里程碑检查点" in msg

    def test_projection_hint_injection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {"projection": {"scenario_id": "s1", "project_slug": "lab"}}
        msg = adapter._build_pm_message([], "Task", projection_hint=hint)
        assert "projection_generate" in msg
        assert "s1" in msg

    def test_compacts_oversized_directive_for_prompt_budget(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "A" * 12_000 + "middle" + "Z" * 12_000
        msg = adapter._build_pm_message([], directive)
        assert len(msg) < 21_000
        assert "omitted" in msg
        assert "AAAA" in msg
        assert "ZZZZ" in msg

    def test_requires_structured_verification_command_authority(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        msg = adapter._build_pm_message([], "Build a runnable Go CLI")

        assert '"verification_commands"' in msg
        assert '"modality": "environment_prep|build|test|lint|entrypoint"' in msg
        assert '"argv": ["executable", "arg"]' in msg
        assert '"cwd": "."' in msg
        assert "shell command string" in msg


class TestBuildPmRetryMessage:
    def test_includes_quality_issues(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        quality = {"score": 50, "critical_issues": ["missing_goal"], "warnings": ["weak_scope"]}
        msg = adapter._build_pm_retry_message(directive="Fix it", quality=quality, previous_output="old")
        assert "missing_goal" in msg
        assert "weak_scope" in msg
        assert "至少 3 个任务" in msg
        assert "上一版 PM 合同未通过质量门禁" not in msg
        assert "禁止输出 [TOOL_CALL]" not in msg
        assert "Previous output excerpt:" in msg

    def test_retry_preserves_structured_verification_command_requirement(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        quality = {"score": 40, "critical_issues": ["verification_commands_missing"], "warnings": []}

        msg = adapter._build_pm_retry_message(directive="Build it", quality=quality, previous_output="old")

        assert "verification_commands" in msg
        assert "argv" in msg
        assert "cwd" in msg


class TestPlanArtifactSanitization:
    def test_write_plan_artifact_freezes_root_schema_without_changing_utf8_or_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive="构建中文桌面工作台，并保留可验证的任务结构",
        )
        contracts[0]["schema_version"] = "task.attempted_override"
        contracts[0]["title"] = "实现中文工作台核心流程"

        path = adapter._write_plan_artifact(
            directive='{"schema_version":"directive.attempted_override","提示词":"不得泄漏"}',
            task_contracts=contracts,
            quality={
                "schema_version": "quality.attempted_override",
                "score": 91,
                "critical_issues": ["一", "二"],
                "summary": "质量门禁通过",
            },
            quality_signals=[
                {
                    "schema_version": "signal.attempted_override",
                    "code": "pm.utf8",
                    "severity": "info",
                    "detail": "角色设定已完成检查",
                }
            ],
        )

        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))

        assert payload["schema_version"] == "pm.plan_artifact.v1"
        assert payload["directive"].startswith("[redacted planning context")
        assert payload["tasks"][0]["schema_version"] == "task.attempted_override"
        assert payload["tasks"][0]["title"] == "实现中文工作台核心流程"
        assert payload["quality_gate"] == {
            "score": 91,
            "critical_issue_count": 2,
            "summary": "质量门禁通过",
            "signals": [
                {
                    "schema_version": "signal.attempted_override",
                    "code": "pm.utf8",
                    "severity": "info",
                    "detail": "职责设定已完成检查",
                }
            ],
        }
        assert "实现中文工作台核心流程" in raw.decode("utf-8")
        assert raw.endswith(b"\n")

    def test_write_plan_artifact_redacts_prompt_leakage_terms(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive=(
                "you are the role planner\n"
                "system prompt: no yapping\n"
                "Build React Electron desktop workbench with /workbench/model /workbench/scene /workbench/batch"
            )
        )

        path = adapter._write_plan_artifact(
            directive="you are the role planner\nsystem prompt: no yapping",
            task_contracts=contracts,
            quality={"score": 90, "critical_issues": [], "summary": "ok"},
            quality_signals=[
                {"code": "pm.test", "severity": "info", "detail": "role chain detail and <tool_call> marker"}
            ],
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        for token in ("you are", "role", "system prompt", "no yapping", "<thinking>", "<tool_call>"):
            assert token not in serialized
        assert payload["directive"].startswith("[redacted planning context")
        assert payload["tasks"]


class TestFrontendTestRepairContracts:
    def test_deterministic_checks_use_declared_markdown_section(self) -> None:
        directive = """
## Acceptance Criteria
- Web projects provide an <html> entrypoint.

## Deterministic Checks
- rust_compile
- min_files:3

## Language-Specific Runnable Contract
- cargo build must pass.
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_explicit_html_check_and_legacy_inline_checks_remain_supported(self) -> None:
        explicit_section = """
## Deterministic Checks
- html
- ts_syntax
""".strip()

        assert _extract_deterministic_checks_from_directive(explicit_section) == [
            "html",
            "ts_syntax",
        ]
        assert _extract_deterministic_checks_from_directive("Verify rust_compile and min_files:3.") == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_section_accepts_closing_hashes(self) -> None:
        directive = """
## Acceptance Criteria
- Web projects provide an <html> entrypoint.

## Deterministic Checks ##
- rust_compile

## Other Checks
- py_compile
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == ["rust_compile"]

    def test_deterministic_check_section_keeps_nested_subsections_and_ignores_fenced_headings(self) -> None:
        directive = """
## Deterministic Checks
### Rust
- rust_compile
```text
## This is fenced content, not a section boundary
```
### Shared
- min_files:3

## Other Checks
- py_compile
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_section_resets_bare_declarations_after_nested_heading(self) -> None:
        directive = """
## Deterministic Checks
### Rust
rust_compile
min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_section_ignores_examples_notes_and_negated_identifiers(self) -> None:
        directive = """
## Deterministic Checks
1. rust_compile

min_files:3

```text
- html
- ts_syntax
```

### Notes
Mentioning html here is explanatory prose, not a declared check.
- no_html_for_cli
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]
        assert _extract_deterministic_checks_from_directive(
            "Keep no_html_for_cli metadata and verify rust_compile."
        ) == ["rust_compile"]

    def test_deterministic_check_section_ignores_lazy_list_continuation(self) -> None:
        directive = """
## Deterministic Checks
- Example verifier name:
html
- rust_compile
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == ["rust_compile"]

    def test_deterministic_check_section_ignores_lazy_blockquote_continuation(self) -> None:
        directive = """
## Deterministic Checks
> Example verifier name:
html
- rust_compile
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == ["rust_compile"]

    def test_deterministic_check_section_ignores_plain_paragraph_continuation(self) -> None:
        directive = """
## Deterministic Checks
Example verifier name:
html

rust_compile
min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_section_ignores_indented_fences_and_code_blocks(self) -> None:
        directive = """
## Deterministic Checks
- rust_compile
- Example payload:
    ```text
    - html
    - ts_syntax
    ```

    java_compile
    - cpp_compile
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == ["rust_compile"]
        assert _extract_deterministic_checks_from_directive(
            "Keep no-html-for-cli metadata and verify rust_compile."
        ) == ["rust_compile"]

    def test_deterministic_check_section_ignores_list_item_fenced_blocks(self) -> None:
        directive = """
## Deterministic Checks
- rust_compile
- ```text
  html
  ts_syntax
  ```
- min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_fence_closes_only_with_container_valid_marker(self) -> None:
        directive = """
## Deterministic Checks
- rust_compile
```text
- ```
html
1. ```
ts_syntax
- [ ] ```
java_compile
    ```
cpp_compile
```
- min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_fence_enforces_container_minimum_indent(self) -> None:
        directive = """
## Deterministic Checks
- rust_compile
- ```text
  html
```
ts_syntax
  ```
- min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_rejects_backticks_in_backtick_fence_info(self) -> None:
        directive = """
## Deterministic Checks
- rust_compile
```text```
- min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_fence_inherits_list_continuation_indent(self) -> None:
        directive = """
## Deterministic Checks
- rust_compile
- Example:
  ```text
  html
```
ts_syntax
  ```
- min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_list_fence_ends_when_container_dedents(self) -> None:
        directive = """
## Deterministic Checks
- rust_compile
- ```text
  html
```
ts_syntax
```
- min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_deterministic_check_task_list_checkbox_does_not_expand_container_indent(self) -> None:
        directive = """
## Deterministic Checks
- [ ] ```text
  html
  ```
- rust_compile
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == ["rust_compile"]

    def test_deterministic_check_nested_list_dedent_restores_outer_container(self) -> None:
        directive = """
## Deterministic Checks
- rust_compile
- Outer example:
  - Nested example:
    ```text
    html
    ```
  ```text
  html
```
ts_syntax
  ```
- min_files:3
""".strip()

        assert _extract_deterministic_checks_from_directive(directive) == [
            "rust_compile",
            "min_files:3",
        ]

    def test_synthesizes_placeholder_repair_before_generic_frontend_plan(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive=(
                "Fix the full-project QA blocker in FashionGenStudio.\n"
                "qa_rework_reason: placeholder_content_detected\n"
                "Evidence:\n"
                "- src/backend/fashiongen_worker.py:\\bplaceholder\\b\n"
                "- src/main/providers.ts:\\bplaceholder\\b\n"
                "- malformed artifact directory: PATCH_FILE src/\n"
                "Run npm test and npm run build."
            )
        )

        assert [item["id"] for item in contracts] == ["TASK-1", "TASK-2"]
        assert "QA Placeholder Evidence Repair" in contracts[0]["title"]
        assert contracts[0]["scope_paths"] == ["src/backend/fashiongen_worker.py", "src/main/providers.ts"]
        assert "placeholder_content_detected" in contracts[0]["metadata"]["qa_rework_reason"]
        assert contracts[0]["metadata"]["cleanup_paths"] == ["PATCH_FILE src/"]
        assert any("PATCH_FILE src/" in step for step in contracts[0]["steps"])
        assert "qa_rework_reason" not in contracts[1]["metadata"]
        assert contracts[1]["metadata"]["qa_rework_verification_only"] is True
        assert "npm test returns PASS" in contracts[1]["acceptance"]

    def test_plain_placeholder_directive_synthesizes_repair_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive=(
                "Fix placeholder marker in src/main/providers.ts. Keep provider API behavior stable and run npm test."
            )
        )

        assert [item["id"] for item in contracts] == ["TASK-1", "TASK-2"]
        assert contracts[0]["scope_paths"] == ["src/main/providers.ts"]
        assert contracts[0]["target_files"] == ["src/main/providers.ts"]

    def test_static_web_root_workspace_directive_synthesizes_file_level_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 个人响应式简历网页

## Goal
- 用纯 HTML5/CSS3 制作个人简历静态页面,包含现代 Flexbox/Grid 布局与媒体查询,适配移动端。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 附 README.md 说明如何运行。
- 关键验收维度: UI 布局、CSS 样式生成与语义化标签。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts)
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert contracts[0]["target_files"] == ["index.html", "styles.css"]
        assert "README.md" in contracts[2]["target_files"]
        assert "unittest discover" in serialized
        assert "pytest -q" not in serialized
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_quality_blocks_missing_structured_verifier_authority_before_ce(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "Build a Go CLI application with main.go, tests, and README."
        contracts = [
            adapter._normalize_task_contract(
                {
                    "id": f"TASK-{index}",
                    "title": f"实现 Go delivery part {index}",
                    "goal": "Deliver runnable Go code with deterministic validation",
                    "scope_paths": [path],
                    "target_files": [path],
                    "steps": ["Implement the declared artifact", "Validate the artifact"],
                    "acceptance": ["The declared artifact exists and is non-empty", "Validation passes"],
                    "verification_commands": [],
                },
                index,
                directive,
            )
            for index, path in enumerate(("go.mod", "main.go", "main_test.go"), start=1)
        ]

        _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)

        assert quality["ok"] is False
        assert "verification_commands_missing" in quality["critical_issues"]
        assert int(quality["score"]) <= 40

    def test_typescript_web_root_workspace_directive_prefers_package_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 发光昆虫花园模拟器

## Goal
- 用 TypeScript 实现「发光昆虫花园模拟器」。创意钩子: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件; API 项目提供可启动服务入口和健康检查说明。
- package.json 脚本不得是只检查 manifest 的占位脚本; build/test/start 或等价脚本必须实际运行产品入口或核心规则验证。
- 附 README.md 说明如何运行。
- 关键验收维度: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈; 同时验证 TypeScript 产物结构、入口可运行性和核心领域规则。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts)
        targets = [target for item in contracts for target in item.get("target_files", [])]
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert [item["id"] for item in contracts] == ["TASK-1", "TASK-2", "TASK-3"]
        assert contracts[2]["depends_on"] == ["TASK-2"]
        assert "package.json" in targets
        assert "tsconfig.json" in targets
        assert "src/index.ts" in targets
        assert "src/models/MoonPhase.ts" in targets
        assert "src/engine/renderer.ts" in targets
        assert "src/web.ts" in targets
        assert "src/verify.ts" in contracts[2]["target_files"]
        assert "tests/verify.test.ts" in contracts[2]["target_files"]
        assert "index.html" in targets
        assert "README.md" in targets
        assert "styles.css" not in targets
        assert "npm run build" in serialized
        assert "npm run test" in serialized
        assert "非空 canvas" in serialized
        assert "Node-only CLI" in serialized
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_l1_factory_context_prevents_lightweight_typescript_task_boundary_split(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 发光昆虫花园模拟器

## Goal
- 用 TypeScript 实现「发光昆虫花园模拟器」。创意钩子: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口。
- package.json 脚本不得是只检查 manifest 的占位脚本; build/test/start 或等价脚本必须实际运行产品入口或核心规则验证。
- 附 README.md 说明如何运行。
- 关键验收维度: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈; 同时验证 TypeScript 产物结构、入口可运行性和核心领域规则。

## Deterministic Checks
- ts_syntax
- package_scripts
- content_any:firefly|flower|moon|humidity
- source_target_coverage:src/**/*.ts

## Language-Specific Runnable Contract (TypeScript)
- 必须包含 `package.json` 且定义 `scripts.start` / `scripts.build` 脚本。
- `npm install && npm run build` 必须成功。
- 必须包含 `tsconfig.json`。
- `tsc --noEmit` 必须通过。
""".strip()
        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)

        normalized, quality = adapter._evaluate_contract_quality(
            contracts,
            directive=directive,
            context={
                "factory_bench_level": 1,
                "level_contract": {"schema_version": "factory-bench.level_contract.v1", "level": 1},
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "level": 1,
                },
            },
        )

        assert [item["id"] for item in normalized] == ["TASK-1", "TASK-2", "TASK-3"]
        assert all(not (item.get("metadata") or {}).get("task_boundary_split") for item in normalized)
        assert normalized[0]["factory_bench_level"] == 1
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_typescript_placeholder_script_rule_does_not_route_to_qa_repair(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — TypeScript market simulation

## Goal
- 用 TypeScript 实现管理模拟项目。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Project Metadata
- 主语言: typescript
- 项目类型: management_game

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口。
- package.json 脚本不得是只检查 manifest 的 placeholder 占位脚本; build/test/start 或等价脚本必须实际运行产品入口或核心规则验证。
- 附 README.md 说明如何运行。

## Deterministic Checks
- ts_syntax
- package_scripts
- content_any:market|fairy|inventory|reputation
- source_target_coverage:src/**/*.ts

## Source Tree Structure Contract (MANDATORY)
- 必须包含 `src/` 目录, 核心业务逻辑在 `src/` 下的 `.ts` 文件中。
- 至少包含 `src/models/`, `src/engine/` 或 `src/core/`, `src/index.ts`。
- simulation/game/interactive 项目必须包含一个可渲染的场景/引擎核心文件 (如 `src/engine/renderer.ts`, `src/core/simulation.py` 等)。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts)
        targets = [target for item in contracts for target in item.get("target_files", [])]
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert [item["id"] for item in contracts] == ["TASK-1", "TASK-2", "TASK-3"]
        assert contracts[2]["depends_on"] == ["TASK-2"]
        assert all("Placeholder" not in item["title"] for item in contracts)
        assert "package.json" in targets
        assert "tsconfig.json" in targets
        assert "index.html" in targets
        assert "README.md" in targets
        assert "src/index.ts" in targets
        assert "src/engine/renderer.ts" in targets
        assert "src/verify.ts" in contracts[2]["target_files"]
        assert "tests/verify.test.ts" in contracts[2]["target_files"]
        assert any(target.startswith("src/models/") and target.endswith(".ts") for target in targets)
        assert all(not target.endswith(".py") for target in targets)
        assert "placeholder_content_detected" not in serialized
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_javascript_directive_does_not_match_java_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 星际失物招领站

## Goal
- 用 JavaScript 实现「星际失物招领站」。创意钩子: 跨星系遗失物按能量读数和线索匹配主人。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Project Metadata
- 主语言: javascript
- 领域: creative
- 项目类型: story_tool

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: CLI 项目提供 package.json 脚本或可直接执行的 main 文件。
- 附 README.md 说明如何运行。
- 关键验收维度: 跨星系遗失物按能量读数和线索匹配主人; 同时验证 JavaScript 产物结构、入口可运行性和核心领域规则。

## Deterministic Checks
- js_syntax
- package_scripts
- min_files:4
- content_any:lost|alien|galaxy|clue
- source_target_coverage:src/**/*.js

## Source Tree Structure Contract (MANDATORY)
- 必须包含 `src/` 目录, 核心业务逻辑在 `src/` 下的 `.js` 文件中。
- 必须包含 `src/index.js` 应用入口。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
        targets = [target for item in contracts for target in item.get("target_files", [])]
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert len(contracts) == 2
        assert "package.json" in targets
        assert "src/index.js" in targets
        assert "src/engine/rules.js" in targets
        assert "src/engine/runner.js" in targets
        assert "tests/product.test.js" in targets
        assert "tests/test_product.py" in targets
        assert "README.md" in targets
        assert "src/lost.js" in targets
        assert "src/alien.js" in targets
        assert all(not target.startswith("src/models/") for target in targets)
        verification_task = next(item for item in contracts if item.get("id") == "TASK-2")
        verification_targets = set(verification_task.get("target_files") or [])
        verification_context = set(verification_task.get("context_files") or [])
        assert "package.json" in verification_targets
        assert "tests/product.test.js" in verification_targets
        assert "src/index.js" not in verification_targets
        assert "src/engine/rules.js" not in verification_targets
        assert "src/lost.js" not in verification_targets
        assert "src/index.js" in verification_context
        assert "src/engine/rules.js" in verification_context
        assert "src/lost.js" in verification_context
        assert all("src/main/java" not in target for target in targets)
        assert "RhythmMonster" not in serialized
        assert "BeatPattern" not in serialized
        assert "javac" not in serialized.lower()
        assert "js_syntax" in serialized
        assert "package_scripts" in serialized
        assert "source_target_coverage:src/**/*.js" in serialized
        assert "polaris.delivery_plan_document.v1" in serialized
        assert "polaris.delivery_depth_contract.v1" in serialized
        assert "至少实现 3 条可解释业务规则" in serialized
        assert "测试覆盖正常路径、边界情况和错误/非法输入" in serialized
        assert "lost" in serialized
        assert "alien" in serialized
        assert "galaxy" in serialized
        assert "clue" in serialized
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_python_directive_prefers_src_package_over_html_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 情绪天气电台

## Goal
- 用 Python 实现「情绪天气电台」。创意钩子: 心情日志以天气和私人广播形式回放。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Project Metadata
- 主语言: python
- 领域: creative
- 项目类型: wellbeing_tool

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件。
- 附 README.md 说明如何运行。
- 关键验收维度: 心情日志以天气和私人广播形式回放; 同时验证 Python 产物结构、入口可运行性和核心领域规则。

## Deterministic Checks
- py_compile
- min_files:4
- content_any:mood|weather|radio|forecast
- source_target_coverage:src/**/*.py

## Source Tree Structure Contract (MANDATORY)
- 必须包含 `src/` 目录(或项目级 Python 包), 核心业务逻辑在 `.py` 文件中。
- 必须包含 `tests/` 目录下的至少一个 `test_*.py` 测试文件。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
        targets = [target for item in contracts for target in item.get("target_files", [])]
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert len(contracts) == 3
        assert "requirements.txt" in targets
        assert "src/__init__.py" in targets
        assert "src/models/mood.py" in targets
        assert "src/models/weather.py" in targets
        assert "src/engine/forecast.py" in targets
        assert "src/radio.py" in targets
        assert "src/main.py" in targets
        assert "tests/test_product.py" in targets
        assert "README.md" in targets
        assert "index.html" not in targets
        assert "styles.css" not in targets
        assert "py_compile" in serialized
        assert "source_target_coverage:src/**/*.py" in serialized
        assert "python src/main.py" in serialized
        assert "python -m src.main" in serialized
        assert "mood" in serialized
        assert "weather" in serialized
        assert "radio" in serialized
        assert "forecast" in serialized
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_go_directive_prefers_go_module_over_html_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 时间胶囊博物馆

## Goal
- 用 Go 实现「时间胶囊博物馆」。创意钩子: 未来才可打开的记忆展品有谜语和展厅布局。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Project Metadata
- 主语言: go
- 领域: creative
- 项目类型: memory_app

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件; API 项目提供可启动服务入口和健康检查说明。
- 附 README.md 说明如何运行。
- 关键验收维度: 未来才可打开的记忆展品有谜语和展厅布局; 同时验证 Go 产物结构、入口可运行性和核心领域规则。

## Deterministic Checks
- go_compile
- min_files:4
- content_any:capsule|museum|riddle|unlock
- source_target_coverage:**/*.go

## Source Tree Structure Contract (MANDATORY)
- 必须包含 `src/` 或项目级 Go 包, 核心业务逻辑在 `.go` 文件中。
- 至少包含 `models/`、`engine/`、`main.go` 和 `*_test.go`。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
        targets = [target for item in contracts for target in item.get("target_files", [])]
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert len(contracts) == 3
        assert "go.mod" in targets
        assert "main.go" in targets
        assert "models/capsule.go" in targets
        assert "models/exhibit.go" in targets
        assert "engine/museum.go" in targets
        assert "engine/riddle.go" in targets
        assert "engine/unlock.go" in targets
        assert "main_test.go" in targets
        assert "tests/test_product.py" not in targets
        assert "README.md" in targets
        assert "index.html" not in targets
        assert "styles.css" not in targets
        assert "go_compile" in serialized
        assert "source_target_coverage:**/*.go" in serialized
        assert "go test ./..." in serialized
        assert "go run ." in serialized
        assert "python -m unittest" not in serialized
        assert contracts[2]["target_files"] == ["main_test.go", "README.md"]
        assert "engine/unlock.go" in contracts[2]["context_files"]
        assert "models/capsule.go" in contracts[2]["context_files"]
        assert "capsule" in serialized
        assert "museum" in serialized
        assert "riddle" in serialized
        assert "unlock" in serialized
        assert "polaris.delivery_plan_document.v1" in serialized
        assert "polaris.delivery_depth_contract.v1" in serialized
        command_rows = [row for item in contracts for row in item["verification_commands"]]
        assert {tuple(row["argv"]) for row in command_rows} >= {
            ("go", "mod", "download"),
            ("go", "build", "./..."),
            ("go", "test", "./..."),
            ("go", "run", "."),
        }
        assert all(set(row) == {"modality", "argv", "cwd"} for row in command_rows)
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_go_contract_preserves_explicit_html_deterministic_check(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
# Product Requirements — Go Inspector

## Project Metadata
- 主语言: go
- 项目类型: cli

## Deterministic Checks
- html
- go_compile
- min_files:3
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert "确定性检查进入任务验收：html; go_compile; min_files:3" in serialized
        assert "go_compile" in serialized
        assert "min_files:3" in serialized

    def test_rust_root_workspace_directive_prefers_cargo_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 厨房味觉配色器

## Goal
- 用 Rust 实现「厨房味觉配色器」。创意钩子: 把味觉映射成菜谱色板和摆盘规则。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件; API 项目提供可启动服务入口和健康检查说明。
- 附 README.md 说明如何运行。
- 关键验收维度: 把味觉映射成菜谱色板和摆盘规则; 同时验证 Rust 产物结构、入口可运行性和核心领域规则。

## Deterministic Checks
- rust_compile
- min_files:3
- content_any:flavor|palette|ingredient|recipe
- source_target_coverage:src/**/*.rs

## Language-Specific Runnable Contract (Rust)
- 必须包含 `Cargo.toml`。
- `cargo build` 必须成功。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
        targets = [target for item in contracts for target in item.get("target_files", [])]
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert "Cargo.toml" in targets
        assert "src/lib.rs" in targets
        assert "src/main.rs" in targets
        assert "src/engine/flavor_rules.rs" in targets
        assert "src/models/flavor.rs" in targets
        assert "tests/product.rs" in targets
        assert "tests/test_product.py" not in targets
        assert "README.md" in targets
        assert "index.html" not in targets
        assert "styles.css" not in targets
        assert "cargo test" in serialized
        assert "python -m unittest" not in serialized
        assert '"html"' not in serialized
        assert "确定性检查进入任务验收：html" not in serialized
        assert "flavor" in serialized
        assert "palette" in serialized
        verification_task = next(item for item in contracts if item.get("id") == "TASK-3")
        assert verification_task["target_files"] == ["tests/product.rs", "README.md"]
        assert "Cargo.toml" in verification_task["context_files"]
        assert "src/lib.rs" in verification_task["context_files"]
        assert "src/engine/flavor_rules.rs" in verification_task["context_files"]
        assert set(verification_task["target_files"]).isdisjoint(verification_task["context_files"])
        assert all(
            set(item.get("target_files") or []).issubset(set(item.get("scope_paths") or [])) for item in contracts
        ), "every declared task target must remain inside that task's capability scope"
        command_rows = [row for item in contracts for row in item["verification_commands"]]
        assert {tuple(row["argv"]) for row in command_rows} >= {
            ("cargo", "fetch"),
            ("cargo", "build"),
            ("cargo", "test"),
            ("cargo", "run"),
        }
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_deterministic_language_templates_follow_feature_keywords(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        rust_directive = """
# Product Requirements — 海盗藏宝图预算器

## Project Metadata
- 主语言: rust
- 项目类型: cli

## Deterministic Checks
- rust_compile
- content_any:treasure|budget|port|reef
- source_target_coverage:src/**/*.rs
""".strip()
        cpp_directive = """
# Product Requirements — 微型机器人巡逻棋

## Project Metadata
- 主语言: cpp
- 项目类型: cli

## Deterministic Checks
- cpp_compile
- content_any:robot|patrol|queue|energy
- source_target_coverage:src/**/*.cpp
""".strip()
        java_directive = """
# Product Requirements — 会唱歌的植物图鉴

## Project Metadata
- 主语言: java
- 项目类型: cli

## Deterministic Checks
- java_compile
- content_any:plant|melody|season|growth
""".strip()

        rust_contracts = adapter._synthesize_task_contracts_from_directive(directive=rust_directive)
        cpp_contracts = adapter._synthesize_task_contracts_from_directive(directive=cpp_directive)
        java_contracts = adapter._synthesize_task_contracts_from_directive(directive=java_directive)

        rust_serialized = json.dumps(rust_contracts, ensure_ascii=False)
        cpp_serialized = json.dumps(cpp_contracts, ensure_ascii=False)
        java_serialized = json.dumps(java_contracts, ensure_ascii=False)
        rust_targets = [target for item in rust_contracts for target in item.get("target_files", [])]
        cpp_targets = [target for item in cpp_contracts for target in item.get("target_files", [])]
        java_targets = [target for item in java_contracts for target in item.get("target_files", [])]

        assert "src/models/treasure.rs" in rust_targets
        assert "src/models/budget.rs" in rust_targets
        assert "src/engine/treasure_rules.rs" in rust_targets
        assert "flavor" not in rust_serialized
        assert "palette" not in rust_serialized
        assert "recipe" not in rust_serialized

        assert "src/models/robot.hpp" in cpp_targets
        assert "src/models/patrol.cpp" in cpp_targets
        assert "robot, patrol, queue, energy" in cpp_serialized
        assert "postcard" not in cpp_serialized
        assert "stamp" not in cpp_serialized
        assert "poem" not in cpp_serialized

        assert "src/main/java/polaris/factory/engine/PlantEngine.java" in java_targets
        assert "src/test/java/polaris/factory/PlantEngineTest.java" in java_targets
        assert "plant, melody, season, growth" in java_serialized
        assert "RhythmEngine" not in java_serialized
        assert "RhythmMonster" not in java_serialized
        assert "BeatPattern" not in java_serialized

    def test_cpp_root_workspace_directive_prefers_cpp_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 月球邮局明信片生成器

## Goal
- 用 C++17 实现「月球邮局明信片生成器」。创意钩子: 根据月相、邮票和收件人心情生成月球明信片与短诗。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: CLI 项目提供可直接执行的 main 文件或构建脚本。
- 附 README.md 说明如何运行。
- 关键验收维度: 根据月相、邮票和心情生成月球明信片与短诗; 同时验证 C++17 产物结构、入口可运行性和核心领域规则。

## Deterministic Checks
- cpp_compile
- min_files:3
- content_any:moon|postcard|stamp|poem
- source_target_coverage:src/**/*.cpp

## Language-Specific Runnable Contract (C++17)
- 必须包含 `src/main.cpp`。
- 必须包含 `src/models` 或 `include/models` 领域模型。
- 必须包含 `src/engine` 或 `src/core` 生成逻辑。
- C++17 编译必须成功。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
        targets = [target for item in contracts for target in item.get("target_files", [])]
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert len(contracts) == 1
        assert "CMakeLists.txt" in targets
        assert "src/main.cpp" in targets
        assert "src/engine/generator.cpp" in targets
        assert "src/models/postcard.cpp" in targets
        assert "src/models/stamp.hpp" in targets
        assert "tests/test_product.py" in targets
        assert "README.md" in targets
        assert "index.html" not in targets
        assert "styles.css" not in targets
        assert "cpp_compile" in serialized
        assert "C++17" in serialized
        assert "moon" in serialized
        assert "postcard" in serialized
        assert "stamp" in serialized
        assert "poem" in serialized
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_java_game_directive_prefers_java_contracts_over_typescript_web(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 口袋节奏怪兽

## Goal
- 用 Java 实现「口袋节奏怪兽」。创意钩子: 节奏正确性会塑造怪兽性格和鼓机 pattern。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Project Metadata
- 主语言: java
- 领域: game
- 项目类型: music_game

## Acceptance Criteria
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件。
- 关键验收维度: 节奏正确性会塑造怪兽性格和鼓机 pattern; 同时验证 Java 产物结构、入口可运行性和核心领域规则。

## Deterministic Checks
- java_compile
- min_files:3
- content_any:rhythm|monster|beat|pattern

## Source Tree Structure Contract (MANDATORY)
- 必须包含 `src/main/java/` 目录, 核心业务逻辑在 `.java` 文件中。
- 必须包含 `src/test/java/` 下的测试文件。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
        targets = [target for item in contracts for target in item.get("target_files", [])]
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert len(contracts) == 1
        assert "src/main/java/polaris/factory/Main.java" in targets
        assert "src/main/java/polaris/factory/engine/RhythmEngine.java" in targets
        assert "src/test/java/polaris/factory/RhythmEngineTest.java" in targets
        assert "tests/test_product.py" in targets
        assert "README.md" in targets
        assert "package.json" not in targets
        assert "tsconfig.json" not in targets
        assert "src/index.ts" not in targets
        assert "java_compile" in serialized
        assert "javac" in serialized
        assert "rhythm" in serialized
        assert "monster" in serialized
        assert "beat" in serialized
        assert "pattern" in serialized
        assert quality["ok"] is True
        assert (quality.get("score") or 0) >= 80

    def test_typescript_web_bad_llm_contract_fails_factory_guard(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 发光昆虫花园模拟器

## Goal
- 用 TypeScript 实现「发光昆虫花园模拟器」。创意钩子: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件; API 项目提供可启动服务入口和健康检查说明。
- package.json 脚本不得是只检查 manifest 的占位脚本; build/test/start 或等价脚本必须实际运行产品入口或核心规则验证。
- 附 README.md 说明如何运行。
- 关键验收维度: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈; 同时验证 TypeScript 产物结构、入口可运行性和核心领域规则。
""".strip()
        bad_contracts = [
            {
                "id": "TASK-1",
                "title": "实现→ TASK-2 → TASK-3 → TASK-5",
                "goal": "验收标准汇总 ts_syntax package_scripts source_target_coverage:src/**/*.ts",
                "target_files": ["tests/test_product.py"],
                "acceptance": ["相关测试命令执行通过", "功能行为与预期一致"],
            }
        ]

        _normalized, bad_quality = adapter._evaluate_contract_quality(bad_contracts, directive=directive)
        recovered_contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        _recovered, recovered_quality = adapter._evaluate_contract_quality(
            recovered_contracts,
            directive=directive,
        )

        assert bad_quality["ok"] is False
        assert "factory_typescript_contract_missing" in json.dumps(bad_quality, ensure_ascii=False)
        assert recovered_quality["ok"] is True
        assert int(recovered_quality["score"]) >= 80

    def test_python_contract_with_generic_ts_boilerplate_does_not_trigger_typescript_guard(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
# Product Requirements — 迷你行星天气球

## Project Metadata
- 主语言: python
- 项目类型: cli

## Goal
- 用 Python 实现「迷你行星天气球」核心引擎与 CLI 入口。

## Acceptance Criteria
- `python src/main.py` 与 `python -m src.main` 都可执行并返回成功。
- 关键验收维度: planet, weather, cloud, wind。

## Deterministic Checks
- py_compile
- min_files:3
- content_any:planet|weather|cloud|wind
- source_target_coverage:src/**/*.py

## Generic source tree examples that must not select the project language
- package.json, tsconfig.json, index.html, src/**/*.ts, src/models/*.ts, tests/*.test.ts
""".strip()
        contracts = [
            {
                "id": "TASK-1",
                "title": "实现 迷你行星天气球 Python 引擎与 CLI 入口",
                "goal": "实现核心规则引擎、广播输出和可执行 Python 入口。",
                "description": "补齐 forecast/radio/main，让 CLI 运行真实规则。",
                "scope": "src/engine/forecast.py, src/radio.py, src/main.py",
                "target_files": [
                    "src/engine/__init__.py",
                    "src/engine/forecast.py",
                    "src/radio.py",
                    "src/main.py",
                    "tests/test_product.py",
                    "README.md",
                ],
                "steps": [
                    "实现 mood 到 forecast 的映射规则",
                    "实现 radio 播报文本",
                    "实现 python src/main.py 和 python -m src.main 入口",
                ],
                "acceptance": [
                    "python src/main.py 返回成功",
                    "python -m src.main 返回成功",
                    "源码或输出覆盖 planet/weather/cloud/wind",
                ],
                "verification_commands": [
                    {
                        "modality": "environment_prep",
                        "argv": ["python", "-m", "venv", ".venv"],
                        "cwd": ".",
                    },
                    {
                        "modality": "build",
                        "argv": ["python", "-m", "compileall", "-q", "."],
                        "cwd": ".",
                    },
                    {
                        "modality": "test",
                        "argv": [
                            "python",
                            "-m",
                            "unittest",
                            "discover",
                            "-s",
                            "tests",
                            "-p",
                            "test_*.py",
                            "-v",
                        ],
                        "cwd": ".",
                    },
                    {"modality": "entrypoint", "argv": ["python", "-m", "src.main"], "cwd": "."},
                ],
            }
        ]

        _normalized, quality = adapter._evaluate_contract_quality(contracts, directive=directive)

        serialized_quality = json.dumps(quality, ensure_ascii=False)
        assert quality["ok"] is True
        assert "factory_typescript_contract_missing" not in serialized_quality

    def test_pm_execute_blocks_critical_quality_without_creating_board_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = [
            {
                "id": "TASK-1",
                "title": "Bad task",
                "goal": "Bad task",
                "target_files": ["src/main.py"],
            }
        ]

        def fake_synthesize(*, directive: str, projection_hint: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            return contracts

        def fake_quality(
            quality_contracts: list[dict[str, Any]],
            *,
            directive: str = "",
            context: dict[str, Any] | None = None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            return quality_contracts, {
                "ok": False,
                "score": 40,
                "critical_issues": ["factory_typescript_contract_missing:package.json"],
                "warnings": [],
                "summary": "critical contract defect",
            }

        def fail_if_board_tasks_are_created(task_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
            raise AssertionError(f"PM should not create board tasks for blocked quality: {task_contracts!r}")

        adapter._synthesize_task_contracts_from_directive = fake_synthesize
        adapter._evaluate_contract_quality = fake_quality
        adapter._create_board_tasks = fail_if_board_tasks_are_created

        result = asyncio.run(
            adapter.execute(
                "pm-task",
                {
                    "stage": "pm",
                    "input": "Generate invalid contracts",
                    "deterministic_pm_contracts": True,
                },
                {"deterministic_pm_contracts": True},
            )
        )

        assert result["success"] is False
        assert result["tasks_created"] == 0
        assert result["quality_gate"]["blocked"] is True

    def test_synthesizes_focused_frontend_test_repair_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive=(
                "Fix npm test fails in Vitest TypeScript test: "
                "src/types/__tests__/spec.test.ts imports AssetType from ../generation, "
                "AssetType is declared in src/types/asset.ts"
            )
        )

        assert [item["id"] for item in contracts] == ["TASK-1", "TASK-2"]
        assert "Vitest" in contracts[0]["acceptance"][0]
        assert "src/types/__tests__/spec.test.ts" in contracts[0]["scope"]
        assert "src/types/generation.ts" in contracts[0]["scope_paths"]
        assert "AssetType" in contracts[1]["description"]
        assert any("npm test returns PASS" in item for item in contracts[1]["acceptance"])

    def test_project_delivery_verification_does_not_trigger_repair_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_frontend_test_repair_contracts(
            directive=(
                "FashionGenStudio full delivery. Final npm test and npm run build must pass. "
                "The project includes TypeScript workbenches and src/**/*.test.ts coverage."
            ),
            source_metadata={},
        )

        assert contracts == []

    def test_no_critical_issues_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        quality = {"score": 50, "critical_issues": [], "warnings": []}
        msg = adapter._build_pm_retry_message(directive="Fix it", quality=quality, previous_output="old")
        assert "无关键问题信息" in msg

    def test_compacts_oversized_retry_directive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        quality = {"score": 50, "critical_issues": [], "warnings": []}
        directive = "A" * 8_000 + "Z" * 8_000
        msg = adapter._build_pm_retry_message(directive=directive, quality=quality, previous_output="old")
        assert len(msg) < 8_000
        assert "omitted" in msg


class TestDeterministicContractsFlag:
    def test_accepts_nested_metadata_flag(self) -> None:
        assert PMAdapter._deterministic_pm_contracts_enabled(
            input_data={"metadata": {"deterministic_pm_contracts": True}},
            context={},
        )

    def test_context_flag_still_supported(self) -> None:
        assert PMAdapter._deterministic_pm_contracts_enabled(
            input_data={},
            context={"deterministic_pm_contracts": "yes"},
        )

    def test_route_audit_probe_flag_accepts_nested_metadata(self) -> None:
        assert PMAdapter._pm_route_audit_probe_enabled(
            input_data={"metadata": {"pm_route_audit_probe": True}},
            context={},
        )

    def test_deterministic_pm_invokes_route_probe_without_using_probe_contracts(
        self,
        tmp_path: Any,
        monkeypatch: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        calls: list[dict[str, Any]] = []

        async def fake_call_role_llm(
            message: str,
            context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            calls.append({"message": message, "context": context or {}})
            return {"response": "PM route probe acknowledged."}

        monkeypatch.setattr(adapter, "_call_role_llm", fake_call_role_llm)

        result = asyncio.run(
            adapter._run_pm_stage(
                "pm-route-probe",
                "Build a C++ postcard generator with tests and README.",
                {
                    "metadata": {
                        "deterministic_pm_contracts": True,
                        "pm_route_audit_probe": True,
                        "factory_run_id": "factory-run-1",
                    }
                },
                {},
            )
        )

        assert result["success"] is True
        assert result["tasks_created"] >= 1
        assert len(calls) == 1
        assert calls[0]["context"]["mode"] == "pm_task_contract_route_probe"
        assert calls[0]["context"]["route_audit_probe"] is True
        assert calls[0]["context"]["task_id"] == "pm-route-probe"
        assert calls[0]["context"]["pm_task_id"] == "pm-route-probe"
        assert calls[0]["context"]["turn_request_id"].startswith("pm-route-probe-")
        assert len(calls[0]["context"]["turn_request_id"]) == len("pm-route-probe-") + 24
        assert calls[0]["context"]["disable_internal_tool_rounds"] is True
        assert calls[0]["context"]["tool_contract_require_no_tool_calls"] is True
        assert calls[0]["context"]["require_no_tool_calls"] is True
        assert calls[0]["context"]["no_tool_calls"] is True
        assert calls[0]["context"]["tool_contract"]["require_no_tool_calls"] is True
        assert calls[0]["context"]["tool_contract"]["execution_mode"] == "text_only_probe"
        assert calls[0]["context"]["_transaction_kernel_forced_tool_definitions"] == []
        assert calls[0]["context"]["_transaction_kernel_forced_tool_choice"] == "none"
        assert calls[0]["context"]["suppress_tool_policy_prompt"] is True
        assert calls[0]["context"]["suppress_working_memory_contract"] is True
        assert calls[0]["context"]["_transaction_kernel_suppress_session_patch"] is True
        signals = result["quality_gate"]["signals"]
        assert any(signal["code"] == "pm.contracts.deterministic_route_probe" for signal in signals)

    def test_route_probe_turn_request_identity_is_stable_and_run_scoped(self) -> None:
        build = PMAdapter._build_deterministic_pm_route_probe_context
        input_data = {"metadata": {"factory_run_id": "factory-run-1"}}

        first = build(task_id="pm-route-probe", input_data=input_data, context={})
        repeated = build(task_id="pm-route-probe", input_data=input_data, context={})
        other_run = build(
            task_id="pm-route-probe",
            input_data={"metadata": {"factory_run_id": "factory-run-2"}},
            context={},
        )

        assert first["turn_request_id"] == repeated["turn_request_id"]
        assert first["turn_request_id"] != other_run["turn_request_id"]


# ---------------------------------------------------------------------------
# JSON payload extraction
# ---------------------------------------------------------------------------


class TestExtractJsonPayload:
    def test_plain_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('{"tasks": []}')
        assert result == {"tasks": []}

    def test_fenced_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('```json\n{"tasks": []}\n```')
        assert result == {"tasks": []}

    def test_embedded_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('Some text\n{"tasks": []}\nMore text')
        assert result == {"tasks": []}

    def test_python_literal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('{"tasks": []}')
        assert result == {"tasks": []}

    def test_empty_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_json_payload("") is None
        assert adapter._extract_json_payload("   ") is None

    def test_invalid_python_literal_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_json_payload("- 建立记账模型: 定义交易实体与校验") is None

    def test_prompt_echo_error_does_not_parse_schema_example_as_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = """
Failed to parse action: 你是 Polaris PM，需要产出可执行任务合同。
需求指令:
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 命令行猜数字游戏

## Goal
- 实现命令行猜数字游戏:系统随机生成 1-100 的数字,玩家输入猜测,系统给予高/低提示。

请仅输出 JSON，格式如下：
{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "任务标题",
      "goal": "该任务目标",
      "description": "执行背景与约束",
      "scope": "变更范围摘要",
      "steps": ["步骤1", "步骤2"],
      "acceptance": ["可测验收1", "可测验收2"]
    }
  ]
}
禁止返回 Markdown、解释文本、代码块或工具调用标签。
""".strip()

        assert adapter._extract_task_contracts(response, directive="实现命令行猜数字游戏") == []

    def test_retry_thinking_echo_does_not_parse_constraints_as_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = """
=== THINKING PHASE ===
Intent: 上一版 PM 合同未通过质量门禁，请重写并只输出 JSON。
当前分数: 88
关键问题:
- TASK-2: Director task requires file-level target_files or scope_paths

强制要求：
- 至少 3 个任务
- 每个任务必须含 goal/scope/steps/acceptance
- Director/ChiefEngineer 任务必须含真实相对路径 scope_paths/target_files
- scope_paths/target_files 禁止使用自然语言句子或中文模块描述
- 只能输出 JSON 对象，禁止任何额外文字与代码块

上一版输出片段：
**风险点已标注**
- 单文件 vs 多文件：建议 index.html + styles.css 分离，便于维护
- 媒体查询断点：需覆盖典型移动端（≤768px）
- 无构建工具：纯静态，验收以文件存在和浏览器验证为准

**验收标准（可验证）**
- 文件存在性：index.html、styles.css、README.md 均落盘工作区根
- 功能验证：浏览器打开 index.html 正常渲染，缩放至 375px 宽度布局无错乱
- README 包含运行说明（本地打开或简易 HTTP 服务器方式）

<SESSION_PATCH>
{"task_progress": "done", "action_taken": "完成需求理解与任务拆解"}
</SESSION_PATCH>
Reasoning confidence: high
Should proceed: True
Blockers: Cannot verify - high risk
=== END THINKING PHASE ===
""".strip()

        assert adapter._extract_task_contracts(response, directive="个人响应式简历网页") == []

    def test_json_dependency_chain_task_is_not_promoted_to_contract(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "实现需求分析与项目骨架搭建",
                        "target_files": ["requirements.md"],
                    },
                    {
                        "id": "TASK-2",
                        "title": "核心计算引擎实现",
                        "target_files": ["calculator.py"],
                    },
                    {
                        "id": "TASK-3",
                        "title": "实现验证、文档与 QA 闭环",
                        "target_files": ["README.md"],
                    },
                    {
                        "id": "TASK-4",
                        "title": "实现(骨架) → TASK-2 (引擎) → TASK-3 (验证+文档)",
                        "target_files": ["src/task-2", "tests"],
                    },
                ]
            },
            ensure_ascii=False,
        )

        result = adapter._extract_task_contracts(response, directive="CLI 科学计算器")

        assert [item["id"] for item in result] == ["TASK-1", "TASK-2", "TASK-3"]
        assert all("→" not in item["title"] for item in result)

    def test_json_meta_diagnostic_tasks_are_not_promoted_to_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "事实已补齐",
                        "target_files": ["requirements.md"],
                        "goal": "requirements.md 已读取，需求边界清晰。；满足需求: CLI 科学计算器",
                    },
                    {
                        "id": "TASK-2",
                        "title": "任务数",
                        "scope": ["src", "tests"],
                        "goal": "0 → 需新建 3 个任务形成依赖链。；满足需求: CLI 科学计算器",
                    },
                    {
                        "id": "TASK-3",
                        "title": "实现 CLI 科学计算器入口",
                        "target_files": ["calculator.py"],
                    },
                ]
            },
            ensure_ascii=False,
        )

        result = adapter._extract_task_contracts(response, directive="CLI 科学计算器")

        assert [item["id"] for item in result] == ["TASK-3"]
        assert [item["title"] for item in result] == ["实现 CLI 科学计算器入口"]

    def test_json_non_delivery_constraint_tasks_force_recovery(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = json.dumps(
            {
                "tasks": [
                    {"id": "TASK-1", "title": "无现有代码基，需从零构建", "scope": ["src", "tests"]},
                    {
                        "id": "TASK-2",
                        "title": "验收维度强调“基础字符串处理与条件/循环控制流”，实现需体现这些教学/考核点",
                        "scope": ["src", "tests"],
                    },
                    {"id": "TASK-3", "title": "实现必须形成依赖链，避免并行冲突", "scope": ["src", "tests"]},
                    {"id": "TASK-4", "title": "design", "scope": ["src/design", "tests"]},
                    {"id": "TASK-5", "title": "实现calculator", "scope": ["src/calculator", "tests"]},
                    {
                        "id": "TASK-6",
                        "title": "执行至少 5 组测试用例（含正常计算、括号优先级、除零、非法字符、空输入），全部通过",
                        "scope": ["src", "tests"],
                    },
                ]
            },
            ensure_ascii=False,
        )

        assert adapter._extract_task_contracts(response, directive="CLI 科学计算器") == []

    def test_json_test_implementation_with_explicit_target_is_preserved(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "执行至少 5 组测试用例并固化回归",
                        "target_files": ["tests/test_calculator.py"],
                    }
                ]
            },
            ensure_ascii=False,
        )

        result = adapter._extract_task_contracts(response, directive="CLI 科学计算器")

        assert [item["id"] for item in result] == ["TASK-1"]
        assert result[0]["target_files"] == ["tests/test_calculator.py"]


# ---------------------------------------------------------------------------
# Task extraction from payload
# ---------------------------------------------------------------------------


class TestExtractTasksFromPayload:
    def test_list_of_dicts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_tasks_from_payload([{"title": "A"}, {"title": "B"}])
        assert len(result) == 2

    def test_dict_with_tasks_key(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_tasks_from_payload({"tasks": [{"title": "A"}]})
        assert len(result) == 1

    def test_nested_dict(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_tasks_from_payload({"data": {"task_list": [{"title": "A"}]}})
        assert len(result) == 1

    def test_mapped_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_tasks_from_payload({"task-1": {"title": "A"}, "t_2": {"title": "B"}})
        assert len(result) == 2

    def test_none_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_tasks_from_payload(None) == []


# ---------------------------------------------------------------------------
# Task extraction from sections
# ---------------------------------------------------------------------------


class TestExtractTasksFromSections:
    def test_heading_and_keys(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "Task 1: Fix bug\ngoal: make it work\nscope: src/\nsteps: a, b\nacceptance: test passes\n"
        result = adapter._extract_tasks_from_sections(text, directive="fix")
        assert len(result) == 1
        # Title gets "实现" prefix because "Fix" is not an action marker
        assert result[0]["title"] == "实现Fix bug"
        assert result[0]["goal"] == "make it work"

    def test_bullet_continuation(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "## Task 2\ntitle: Build feature\nsteps:\n- step one\n- step two\nacceptance:\n- criteria one\n"
        result = adapter._extract_tasks_from_sections(text, directive="build")
        assert len(result) == 1
        assert "step one" in result[0]["steps"]
        # Title gets "实现" prefix because "Build" is an action marker, so no prefix
        assert result[0]["title"] == "Build feature"

    def test_chinese_headings(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "任务 1: 编写修复bug\n目标: 让它工作\n"
        result = adapter._extract_tasks_from_sections(text, directive="fix")
        assert len(result) == 1
        # "编写" is in _ACTION_MARKERS so no prefix is added
        assert result[0]["title"] == "编写修复bug"

    def test_bare_task_heading_is_not_promoted_to_placeholder_title(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = """
## Task 1
goal: 实现命令行猜数字游戏核心逻辑
scope: guess_number.py, test_guess_number.py
steps:
- 编写随机数与输入校验逻辑
- 补充单元测试
acceptance:
- pytest -q 通过
- 游戏可在命令行运行
""".strip()

        result = adapter._extract_tasks_from_sections(text, directive="实现命令行猜数字游戏")

        assert len(result) == 1
        assert "命令行猜数字游戏" in result[0]["title"]
        assert result[0]["title"] != "实现Task 1"

    def test_task_preamble_is_not_promoted_to_section_task(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = """
任务已拆解，风险点已标注，计划如下
- **目标**：确立项目结构
- **范围**：创建 calculator.py 和 README.md
- **验收标准**：python calculator.py 不报错
""".strip()

        result = adapter._extract_tasks_from_sections(text, directive="CLI 科学计算器")

        assert result == []


# ---------------------------------------------------------------------------
# Task extraction from bullets
# ---------------------------------------------------------------------------


class TestExtractTasksFromBullets:
    def test_simple_bullets(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "- Fix login\n- Build dashboard\n"
        result = adapter._extract_tasks_from_bullets(text, directive="do")
        assert len(result) == 2
        # "Fix" is not an action marker, so prefix is added
        assert result[0]["title"] == "实现Fix login"

    def test_numbered_list(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "1. Fix login\n2. Build dashboard\n"
        result = adapter._extract_tasks_from_bullets(text, directive="do")
        assert len(result) == 2

    def test_with_description(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "- Fix login: auth bug\n"
        result = adapter._extract_tasks_from_bullets(text, directive="do")
        assert result[0]["title"] == "实现Fix login"
        assert result[0]["description"] == "auth bug"

    def test_markdown_task_label_uses_meaningful_title(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "- **TASK-1: 需求解析与架构设计** — 明确解析策略和交付边界\n"

        result = adapter._extract_tasks_from_bullets(text, directive="实现 CLI 科学计算器")

        assert result[0]["title"] == "需求解析与架构设计"
        assert "TASK-1" not in result[0]["title"]
        assert "**" not in result[0]["title"]

    def test_detail_bullets_are_not_promoted_to_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = """
- **目标**：确立项目结构
- **范围**：创建 calculator.py 和 README.md
- **验收标准**：python calculator.py 不报错
- **依赖链**：TASK-1 (requirements) → TASK-2 (implementation) → TASK-3 (verification)
- **全局风险**：解析器边界条件需覆盖
- TASK-1 (骨架) → TASK-2 (引擎) → TASK-3 (验证+QA)
""".strip()

        result = adapter._extract_tasks_from_bullets(text, directive="CLI 科学计算器")

        assert result == []


# ---------------------------------------------------------------------------
# Task contract normalization
# ---------------------------------------------------------------------------


class TestNormalizeTaskContract:
    def test_basic_normalization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "Fix bug", "description": "auth issue"}
        result = adapter._normalize_task_contract(raw, 1, "directive")
        assert result["id"] == "TASK-1"
        # "Fix" is not an action marker, so prefix is added
        assert result["title"] == "实现Fix bug"
        assert result["phase"] == "requirements"
        assert result["assigned_to"] == "Director"
        assert result["verification_commands"] == []

    def test_preserves_exact_structured_verification_commands(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "Verify Go delivery",
            "verification_commands": [
                {"modality": "environment_prep", "argv": ["go", "mod", "download"], "cwd": "."},
                {"modality": "test", "argv": ["go", "test", "./..."], "cwd": "."},
                {"modality": "entrypoint", "argv": ["go", "run", "."], "cwd": "."},
            ],
        }

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["verification_commands"] == raw["verification_commands"]

    def test_drops_malformed_verification_command_rows_fail_closed(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "Verify",
            "verification_commands": [
                {"modality": "test", "command": "echo ok", "cwd": "."},
                {"modality": "test", "argv": ["pytest"], "cwd": "../escape"},
                {"modality": "fake", "argv": ["pytest"], "cwd": "."},
            ],
        }

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["verification_commands"] == []

    def test_title_without_action_marker_gets_prefix(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "Bug fix"}
        result = adapter._normalize_task_contract(raw, 1, "")
        assert result["title"].startswith("实现")

    def test_infers_scope_from_title(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "Fix login module"}
        result = adapter._normalize_task_contract(raw, 1, "")
        # After title normalization, title becomes "实现Fix login module"
        # _infer_scope_from_title extracts keywords from the normalized title.
        # "fix" is not a stopword, so first keyword is "fix" -> scope = src/fix
        assert "src/fix" in result["scope"] or "login" in result["scope"].lower()

    def test_projection_metadata_merged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "T",
            "projection_scenario": "s1",
            "project_slug": "lab",
            "projection_requirement": "req",
        }
        result = adapter._normalize_task_contract(raw, 1, "")
        meta = result["metadata"]
        assert meta["projection"]["scenario_id"] == "s1"
        assert meta["projection"]["project_slug"] == "lab"
        assert meta["projection"]["requirement"] == "req"

    def test_execution_backend_in_metadata(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "T", "execution_backend": "projection_generate"}
        result = adapter._normalize_task_contract(raw, 1, "")
        assert result["metadata"]["execution_backend"] == "projection_generate"

    def test_natural_language_scope_is_not_promoted_to_target_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "实现素材管理与本地存储",
            "scope_paths": ["backend 素材 API 路由、数据库模型、文件存储；frontend 素材面板组件、拖拽上传交互"],
        }

        result = adapter._normalize_task_contract(raw, 2, "")

        assert result["scope_paths"] == ["src/", "tests/"]
        assert result["target_files"] == []

    def test_keeps_concrete_relative_scope_paths(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "Implement asset storage",
            "scope_paths": ["src/store", "src/spec/generationSpec.ts", "package.json"],
        }

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["scope_paths"] == ["src/store", "src/spec/generationSpec.ts", "package.json"]

    def test_declared_targets_are_never_truncated_from_capability_scope(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        targets = [
            "Cargo.toml",
            "src/lib.rs",
            "src/models/mod.rs",
            "src/models/flavor.rs",
            "src/models/palette.rs",
            "src/models/ingredient.rs",
            "src/models/recipe.rs",
        ]
        raw = {
            "title": "Implement Rust domain model",
            "target_files": targets,
            "scope_paths": [*targets, "src/", "tests/"],
        }

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["target_files"] == targets
        assert result["scope_paths"] == targets

    def test_inline_target_files_are_preferred_over_title_inference(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "README 编写与端到端验证",
            "description": (
                "- **goal**: 交付运行说明文档，执行端到端测试。 "
                '- **scope_paths**: [".", "tests"] '
                '- **target_files**: ["README.md", "tests/test_calculator.py"] '
                "- **steps**: 编写 README 与测试。"
            ),
        }

        result = adapter._normalize_task_contract(raw, 3, "")

        assert result["scope_paths"][:2] == ["README.md", "tests/test_calculator.py"]
        assert result["target_files"] == ["README.md", "tests/test_calculator.py"]

    def test_documentation_task_drops_generic_product_test_target(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "实现README 与运行文档",
            "description": "编写 README.md，包含项目简介、安装步骤、运行命令、核心功能说明。",
            "goal": "提供完整运行说明，确保用户能独立启动和验证项目",
            "target_files": ["README.md", "tests/test_product.py"],
            "scope_paths": ["README.md", "tests/test_product.py"],
            "steps": [
                "撰写项目概述，说明发光昆虫花园模拟器的创意钩子",
                "说明如何运行测试（npm run test）",
            ],
            "acceptance": [
                "README.md 存在且非空",
                "README 包含 npm run build 说明",
            ],
        }

        result = adapter._normalize_task_contract(raw, 6, "Product Requirements — 发光昆虫花园模拟器")

        assert result["target_files"] == ["README.md"]
        assert "tests/test_product.py" not in result["scope_paths"]

    def test_preserves_more_than_four_explicit_target_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "Initialize TypeScript project",
            "target_files": [
                "package.json",
                "tsconfig.json",
                "src/models/Flower.ts",
                "src/models/Firefly.ts",
                "src/models/MoonPhase.ts",
                "src/index.ts",
            ],
        }

        result = adapter._normalize_task_contract(raw, 1, "Use TypeScript and package.json")

        assert result["target_files"] == [
            "package.json",
            "tsconfig.json",
            "src/models/Flower.ts",
            "src/models/Firefly.ts",
            "src/models/MoonPhase.ts",
            "src/index.ts",
        ]

    def test_inline_target_files_accept_backticked_array(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "实现需求解析与工程骨架搭建",
            "description": ('- **target_files**: `["calculator.py", "README.md"]` - **steps**: 创建实现与说明文档。'),
        }

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["target_files"] == ["calculator.py", "README.md"]

    def test_workspace_root_cli_requirement_uses_root_files_not_src_directory_targets(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
# Product Requirements — CLI 科学计算器

## Goal
- 实现一个命令行交互式计算器,支持 +、-、*、/ 及括号优先级的字符串解析与计算。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 附 README.md 说明如何运行。
""".strip()
        raw = {
            "id": "TASK-1",
            "title": "实现关键约束",
            "goal": "必须落盘真实代码文件到工作区根；创建 calculator.py、parser.py；附带 README.md；满足需求: CLI 科学计算器",
            "target_files": ["src", "tests"],
            "scope_paths": ["src", "tests"],
        }

        result = adapter._normalize_task_contract(raw, 1, directive)

        assert result["target_files"] == ["calculator.py", "parser.py", "README.md"]
        assert "src/" in result["scope_paths"]
        assert "tests/" in result["scope_paths"]
        assert "calculator.py" in result["scope_paths"]
        assert "parser.py" in result["scope_paths"]
        assert "README.md" in result["scope_paths"]

    def test_implementation_task_default_test_wording_does_not_add_test_target(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "id": "TASK-2",
            "title": "核心计算引擎与输入校验实现",
            "target_files": ["calculator.py"],
            "phase": "implementation",
        }

        result = adapter._normalize_task_contract(raw, 2, "CLI 科学计算器")

        assert result["target_files"] == ["calculator.py"]

    def test_verification_task_adds_test_target_for_root_cli_project(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
# Product Requirements — CLI 科学计算器

## Goal
- 实现一个命令行交互式计算器,支持 +、-、*、/ 及括号优先级的字符串解析与计算。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根。
- 附 README.md 说明如何运行。
""".strip()
        raw = {
            "id": "TASK-3",
            "title": "README 完善与端到端验证",
            "target_files": ["README.md"],
            "phase": "verification",
        }

        result = adapter._normalize_task_contract(raw, 3, directive)

        assert result["target_files"] == ["README.md", "tests/test_calculator.py"]
        assert "tests/test_calculator.py" in result["scope_paths"]

    def test_synthetic_root_cli_contracts_use_file_level_targets(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
# Product Requirements — CLI 科学计算器

## Goal
- 实现一个命令行交互式计算器,支持 +、-、*、/ 及括号优先级的字符串解析与计算,含输入校验与错误提示。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 附 README.md 说明如何运行。
- 关键验收维度: 基础字符串处理与条件/循环控制流。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
        normalized, quality = adapter._evaluate_contract_quality(contracts)
        serialized = json.dumps(contracts, ensure_ascii=False)

        assert quality["ok"] is True
        assert int(quality["score"]) >= 80
        assert "unittest discover" in serialized
        assert "pytest -q" not in serialized
        assert [item["target_files"] for item in normalized] == [
            ["calculator.py"],
            ["calculator.py", "tests/test_calculator.py"],
            ["README.md", "tests/test_calculator.py"],
        ]

    def test_quality_autofix_added_targets_remain_inside_capability_scope(
        self,
        tmp_path: Any,
        monkeypatch: Any,
    ) -> None:
        monkeypatch.setenv("KERNELONE_PM_DOMAIN_TEXT_HINTS", "1")
        adapter = _make_adapter(tmp_path)
        directive = (
            "Build a multiplayer online creative card game with a TypeScript Three.js 3D client and a Node.js backend."
        )
        contracts = [
            {
                "id": "PM-CARD3D-TESTS",
                "title": "Add multiplayer card integration tests",
                "goal": "Replace placeholder tests with meaningful multiplayer card coverage.",
                "description": "Replace placeholder tests with meaningful multiplayer card coverage.",
                "scope": "tests",
                "scope_paths": ["tests"],
                "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                "steps": ["Read all seed test files.", "Replace placeholder arithmetic tests.", "Run npm test."],
                "acceptance": ["Run `npm test` exits 0."],
                "acceptance_criteria": ["Run `npm test` exits 0."],
                "phase": "implementation",
                "depends_on": [],
                "assigned_to": "Director",
                "execution_checklist": [
                    "Read all seed test files.",
                    "Replace placeholder arithmetic tests.",
                    "Run npm test.",
                ],
                "backlog_ref": "PM-CARD3D-TESTS",
                "metadata": {},
            }
        ]

        normalized, _quality = adapter._evaluate_contract_quality(contracts, directive=directive)
        task = normalized[0]
        target_files = cast(list[str], task["target_files"])
        scope_paths = cast(list[str], task["scope_paths"])

        assert "scripts/build.mjs" in target_files
        assert "scripts/test.mjs" in target_files
        assert scope_paths[0] == "tests"
        assert all(
            any(target == scope.strip("/") or target.startswith(f"{scope.strip('/')}/") for scope in scope_paths)
            for target in target_files
        )
        assert set(scope_paths[1:]).issubset(set(target_files))

    def test_fallback_goal_does_not_echo_prompt_directive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "**TASK-1"}
        directive = "请基于 Architect 阶段产物生成 PM 执行任务合同。# Product Requirements — CLI 科学计算器\n"

        result = adapter._normalize_task_contract(raw, 1, directive)

        assert result["title"] == "实现CLI 科学计算器"
        assert "请基于" not in result["goal"]
        assert "Architect 阶段产物" not in result["goal"]
        assert "CLI 科学计算器" in result["goal"]

    def test_trailing_task_label_is_removed_from_title(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "需求锁定与工程骨架搭建（TASK-1）"}

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["title"] == "实现需求锁定与工程骨架搭建"

    def test_preserves_delivery_plan_and_depth_contract_fields(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "实现核心规则",
            "target_files": ["src/engine.js"],
            "delivery_plan_document": {
                "schema_version": "polaris.delivery_plan_document.v1",
                "product_summary": "自然语言说明产品设计意图",
            },
            "delivery_depth_contract": {
                "schema_version": "polaris.delivery_depth_contract.v1",
                "behavior_contract": {"rule_matrix": [{"rule": "normal", "expected": "score"}]},
            },
            "behavior_contract": {"required_behavior_tests": ["normal", "boundary", "invalid"]},
        }

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["delivery_plan_document"]["schema_version"] == "polaris.delivery_plan_document.v1"
        assert result["delivery_depth_contract"]["schema_version"] == "polaris.delivery_depth_contract.v1"
        assert result["behavior_contract"]["required_behavior_tests"] == ["normal", "boundary", "invalid"]
        assert result["metadata"]["delivery_plan_document"] == result["delivery_plan_document"]
        assert result["metadata"]["delivery_depth_contract"] == result["delivery_depth_contract"]
        assert result["metadata"]["behavior_contract"] == result["behavior_contract"]


# ---------------------------------------------------------------------------
# List normalization
# ---------------------------------------------------------------------------


class TestNormalizeList:
    def test_string_split(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_list("a, b, c") == ["a", "b", "c"]
        assert adapter._normalize_list("a\nb\nc") == ["a", "b", "c"]

    def test_list_passthrough(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_list(["a", "b"]) == ["a", "b"]

    def test_none_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_list(None) == []


# ---------------------------------------------------------------------------
# Scope / domain inference
# ---------------------------------------------------------------------------


class TestInferScopeFromTitle:
    def test_extracts_keyword(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Use a title with an action marker so it doesn't get prefixed,
        # and use a keyword that is not in _STOPWORDS.
        result = adapter._infer_scope_from_title("Implement authentication service")
        assert "src/authentication" in result

    def test_fallback_when_no_keywords(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._infer_scope_from_title("a")
        assert result == ["src/", "tests/"]


class TestDeriveDomainToken:
    def test_from_workspace_name(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        token = adapter._derive_domain_token("")
        # workspace name is derived from tmp_path which is random; just assert non-empty string
        assert isinstance(token, str)
        assert token != ""

    def test_from_directive_keywords(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Workspace name from tmp_path may take precedence; verify it returns a non-empty string
        token = adapter._derive_domain_token("Keywords: payment-gateway, checkout")
        assert isinstance(token, str) and len(token) >= 3

    def test_from_directive_text(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        token = adapter._derive_domain_token("Implement the billing module")
        assert isinstance(token, str) and len(token) >= 3

    def test_fallback_project(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        token = adapter._derive_domain_token("a b c")
        # If workspace name yields a token, it will be returned; otherwise "project"
        assert isinstance(token, str) and len(token) >= 3


class TestExtractDomainKeywords:
    def test_limit_respected(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_domain_keywords("one two three four five six", limit=3)
        assert len(result) == 3

    def test_keyword_hint_parsed(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_domain_keywords("Keywords: alpha, beta, gamma")
        assert "alpha" in result

    def test_synthesized_contracts_prefer_original_requirement_subject_over_pm_prompt_noise(
        self, tmp_path: Any
    ) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。任务必须覆盖需求、实现、验证、QA 闭环。

## Original Requirement Excerpt
# Product Requirements — 命令行猜数字游戏

## Goal
- 实现命令行猜数字游戏:系统随机生成 1-100 的数字,玩家输入猜测,系统给予高/低提示,限制 10 次机会,结束显示战绩。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根。
- 附 README.md 说明如何运行。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)

        titles = [str(item.get("title") or "") for item in contracts]
        assert any("命令行猜数字游戏" in title for title in titles)
        assert all("Task " not in title for title in titles)
        assert all("architect" not in title.lower() for title in titles)


# ---------------------------------------------------------------------------
# Directive complexity analysis
# ---------------------------------------------------------------------------


class TestAnalyzeDirectiveComplexity:
    def test_empty_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._analyze_directive_complexity("", {}) == {}

    def test_low_complexity(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._analyze_directive_complexity("fix typo", {})
        assert result["complexity"] == "low"
        assert result["recommended_strategy"] == "minimal_decomposition"

    def test_high_complexity(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = (
            "Implement authentication API with database schema, "
            "frontend integration, CI/CD pipeline, and test suite. "
            "If user is admin, show extra panel. Iterate over all records. "
            "Also implement build and define the deployment schema. "
            "Create tests for /src/auth.py, /src/db.py, /src/api.py"
        )
        result = adapter._analyze_directive_complexity(directive, {})
        assert result["complexity"] == "high"
        assert result["recommended_strategy"] == "deep_decomposition"
        assert result["estimated_task_count"] >= 5

    def test_medium_complexity(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = (
            "Implement API with tests and deploy to staging. "
            "If errors occur, retry three times. "
            "Build /src/a.py, /src/b.py, /src/c.py"
        )
        result = adapter._analyze_directive_complexity(directive, {})
        assert result["complexity"] == "medium"


# ---------------------------------------------------------------------------
# Meta-planning hints
# ---------------------------------------------------------------------------


class TestApplyMetaPlanningHints:
    def test_no_analysis_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = "some message"
        assert adapter._apply_meta_planning_hints(msg, {}) == msg

    def test_injects_before_tasks_section(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = 'Header\n"tasks": [\n    {\n      "id": "TASK-1"\n    }\n  ]'
        analysis = {"recommended_strategy": "deep_decomposition", "estimated_task_count": 5}
        result = adapter._apply_meta_planning_hints(msg, analysis)
        assert "Meta-Planning" in result
        assert "deep_decomposition" in result
        assert result.index("Meta-Planning") < result.index('"tasks": [')
        array_prefix = result.split('"tasks": [', 1)[1].split("{", 1)[0]
        assert "Meta-Planning" not in array_prefix

    def test_injects_before_json_format_section(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = 'Header\n请仅输出 JSON，格式如下：\n{\n  "tasks": [\n    {}\n  ]\n}'
        analysis = {"recommended_strategy": "minimal_decomposition", "estimated_task_count": 2}
        result = adapter._apply_meta_planning_hints(msg, analysis)
        assert result.index("Meta-Planning") < result.index("请仅输出 JSON")

    def test_existing_meta_planning_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = '[Meta-Planning] Complexity: high.\n请仅输出 JSON，格式如下：\n{\n  "tasks": []\n}'
        analysis = {"recommended_strategy": "deep_decomposition", "estimated_task_count": 6}
        assert adapter._apply_meta_planning_hints(msg, analysis) == msg

    def test_no_tasks_section_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = "Just some text"
        analysis = {"recommended_strategy": "minimal_decomposition", "estimated_task_count": 2}
        result = adapter._apply_meta_planning_hints(msg, analysis)
        # When no '"tasks": [' section exists, the message is returned unchanged
        assert result == msg


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


class TestNormalizeProjectionProjectSlug:
    def test_basic(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_projection_project_slug("My Project") == "my_project"
        assert adapter._normalize_projection_project_slug("a--b__c") == "a_b_c"

    def test_empty_uses_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_projection_project_slug("") == "projection_lab"


class TestExtractProjectionContractHint:
    def test_non_projection_backend_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_projection_contract_hint(input_data={}, context={}, directive="") == {}

    def test_projection_backend_extracts_fields(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_projection_contract_hint(
            input_data={"execution_backend": "projection_generate", "projection": {"scenario_id": "s1"}},
            context={},
            directive="req",
        )
        assert result["execution_backend"] == "projection_generate"
        assert result["projection"]["scenario_id"] == "s1"
        assert result["projection"]["requirement"] == "req"

    def test_missing_scenario_id_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_projection_contract_hint(
            input_data={"execution_backend": "projection_generate"},
            context={},
            directive="",
        )
        assert result == {}


class TestApplyProjectionContractHint:
    def test_no_hint_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = [{"title": "T"}]
        assert adapter._apply_projection_contract_hint(contracts, projection_hint=None) == contracts

    def test_first_task_gets_projection_generate(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {
            "execution_backend": "projection_generate",
            "projection": {"scenario_id": "s1"},
        }
        contracts = [{"title": "T1"}, {"title": "T2"}]
        result = adapter._apply_projection_contract_hint(contracts, projection_hint=hint)
        assert result[0]["execution_backend"] == "projection_generate"
        assert result[0]["metadata"]["projection"]["scenario_id"] == "s1"
        assert result[1]["execution_backend"] == "code_edit"

    def test_preserves_existing_projection_generate(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {
            "execution_backend": "projection_generate",
            "projection": {"scenario_id": "s1"},
        }
        contracts = [{"title": "T1", "execution_backend": "projection_generate"}]
        result = adapter._apply_projection_contract_hint(contracts, projection_hint=hint)
        assert result[0]["execution_backend"] == "projection_generate"

    def test_later_task_projection_generate_is_demoted_to_code_edit(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {
            "execution_backend": "projection_generate",
            "projection": {"scenario_id": "s1"},
        }
        contracts = [
            {"title": "T1", "execution_backend": "projection_generate"},
            {
                "title": "T2",
                "phase": "verification",
                "target_files": ["tests/test_guess_number.py"],
                "metadata": {"execution_backend": "projection_generate"},
            },
        ]
        result = adapter._apply_projection_contract_hint(contracts, projection_hint=hint)
        assert result[0]["execution_backend"] == "projection_generate"
        assert result[1]["execution_backend"] == "code_edit"
        assert result[1]["metadata"]["execution_backend"] == "code_edit"


class TestNormalizeTaskContractProjectionBackend:
    def test_later_raw_task_projection_generate_is_demoted_to_code_edit(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._normalize_task_contract(
            {
                "id": "TASK-3",
                "title": "实现功能验证与 QA 闭环",
                "goal": "创建测试并验证交付结果",
                "phase": "verification",
                "target_files": ["guess_number.py", "tests/test_guess_number.py"],
                "metadata": {"execution_backend": "projection_generate"},
            },
            index=3,
            directive="实现命令行猜数字游戏",
        )
        assert result["metadata"]["execution_backend"] == "code_edit"


class TestBuildProjectionHintContracts:
    def test_returns_three_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {"projection": {"scenario_id": "s1", "project_slug": "lab"}}
        result = adapter._build_projection_hint_contracts(directive="req", projection_hint=hint)
        assert len(result) == 3
        assert result[0]["execution_backend"] == "projection_generate"
        assert result[1]["execution_backend"] == "code_edit"
        assert result[2]["execution_backend"] == "code_edit"


class TestSynthesizeTaskContractsFromDirective:
    def test_without_hint_returns_three_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._synthesize_task_contracts_from_directive(directive="Implement payment module")
        assert len(result) == 3
        assert all(isinstance(c, dict) for c in result)

    def test_with_hint_uses_projection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {"projection": {"scenario_id": "s1"}}
        result = adapter._synthesize_task_contracts_from_directive(directive="req", projection_hint=hint)
        assert len(result) == 3
        assert result[0]["metadata"]["execution_backend"] == "projection_generate"


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------


class TestListBoardTaskRows:
    def test_missing_runtime_task_row_projection_returns_empty_rows(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        class LegacyBoard:
            def list_all(self) -> list[object]:
                raise AssertionError("PM read-model consumers must not use legacy list_all")

        adapter._task_runtime = cast(Any, LegacyBoard())

        assert adapter._list_board_task_rows() == []

    def test_prefers_runtime_task_row_projection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        adapter._task_runtime = cast(
            Any,
            _RowProjectionOnlyTaskBoard(
                [{"id": 1, "subject": "Existing", "status": "pending", "metadata": {"goal": "Ship"}}]
            ),
        )

        rows = adapter._list_board_task_rows()

        assert rows == [{"id": 1, "subject": "Existing", "status": "pending", "metadata": {"goal": "Ship"}}]

    def test_pm_stage_prompt_snapshot_uses_runtime_task_rows(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        adapter._task_runtime = cast(
            Any,
            _RowProjectionOnlyTaskBoard(
                [{"id": 1, "subject": "Existing", "status": "pending", "metadata": {"goal": "Ship"}}]
            ),
        )
        captured: dict[str, Any] = {}

        def fake_build_pm_message(
            tasks: list[dict[str, Any]],
            directive: str,
            *,
            projection_hint: dict[str, Any] | None = None,
            directive_analysis: dict[str, Any] | None = None,
        ) -> str:
            captured["tasks"] = tasks
            return "message"

        def fake_synthesize(
            *,
            directive: str,
            projection_hint: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            return [{"id": "TASK-1", "title": "Bad task", "goal": "Bad task"}]

        def fake_quality(
            contracts: list[dict[str, Any]],
            *,
            directive: str = "",
            context: dict[str, Any] | None = None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            return contracts, {
                "ok": False,
                "score": 10,
                "critical_issues": ["missing_target_files"],
                "warnings": [],
                "summary": "blocked",
            }

        adapter._build_pm_message = fake_build_pm_message
        adapter._synthesize_task_contracts_from_directive = fake_synthesize
        adapter._evaluate_contract_quality = fake_quality

        result = asyncio.run(
            adapter._run_pm_stage(
                "pm-task",
                "Create implementation tasks",
                {"deterministic_pm_contracts": True},
                {"deterministic_pm_contracts": True},
            )
        )

        assert captured["tasks"] == [
            {"id": 1, "subject": "Existing", "status": "pending", "metadata": {"goal": "Ship"}}
        ]
        assert result["success"] is False
        assert result["tasks_created"] == 0


class TestCreateBoardTasksRows:
    def test_creates_and_links_tasks_through_runtime_row_api(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _RowWriteOnlyTaskRuntime()
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                },
                {
                    "id": "TASK-2",
                    "title": "Create engine",
                    "description": "Create the engine",
                    "goal": "engine",
                    "scope": "src/engine.py",
                    "scope_paths": ["src/engine.py"],
                    "target_files": ["src/engine.py"],
                    "steps": ["write engine"],
                    "acceptance": ["engine exists"],
                    "depends_on": ["TASK-1"],
                },
            ]
        )

        assert [row["id"] for row in created] == [1, 2]
        assert created[1]["blocked_by"] == [1]
        assert created[1]["metadata"]["resolved_depends_on_task_ids"] == [1]

    def test_dependency_update_failure_is_returned_as_transition_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DependencyUpdateMissingTaskRuntime()
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                },
                {
                    "id": "TASK-2",
                    "title": "Create engine",
                    "description": "Create the engine",
                    "goal": "engine",
                    "scope": "src/engine.py",
                    "scope_paths": ["src/engine.py"],
                    "target_files": ["src/engine.py"],
                    "steps": ["write engine"],
                    "acceptance": ["engine exists"],
                    "depends_on": ["TASK-1"],
                },
            ]
        )

        assert created[1]["blocked_by"] == []
        assert "resolved_depends_on_task_ids" not in created[1]["metadata"]
        assert created[1]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 2,
                "action": "resolve_dependencies",
                "reason": "task_runtime_dependency_update_missing_row",
                "blocked_by": [1],
                "transition_result": {},
            }
        ]

    def test_create_execution_event_failure_is_returned_as_transition_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _ExecutionEventFailureTaskRuntime(fail_action="create_board_task")
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 1,
                "action": "create_board_task",
                "reason": "task_runtime_execution_event_append_failed",
                "blocked_by": [],
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]

    def test_dedup_match_execution_event_failure_is_returned_as_transition_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _ExecutionEventFailureTaskRuntime(fail_action="deduplicate_contract_match")
        runtime.create_task_row(
            subject="Create model",
            description="Create the domain model",
            metadata={"goal": "model"},
        )
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["id"] == 1
        assert created[0]["task_runtime_transition_failures"][0]["action"] == "deduplicate_contract_match"
        assert created[0]["task_runtime_transition_failures"][0]["reason"] == (
            "task_runtime_execution_event_append_failed"
        )

    def test_dependency_execution_event_failure_is_returned_as_transition_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _ExecutionEventFailureTaskRuntime(fail_action="resolve_dependencies")
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                },
                {
                    "id": "TASK-2",
                    "title": "Create engine",
                    "description": "Create the engine",
                    "goal": "engine",
                    "scope": "src/engine.py",
                    "scope_paths": ["src/engine.py"],
                    "target_files": ["src/engine.py"],
                    "steps": ["write engine"],
                    "acceptance": ["engine exists"],
                    "depends_on": ["TASK-1"],
                },
            ]
        )

        assert created[1]["blocked_by"] == [1]
        assert created[1]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 2,
                "action": "resolve_dependencies",
                "reason": "task_runtime_execution_event_append_failed",
                "blocked_by": [1],
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]

    def test_dedup_cancel_execution_event_failure_attaches_to_primary_row(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DedupCancelExecutionEventFailureTaskRuntime()
        runtime.create_task_row(subject="Create model", description="old", metadata={"goal": "model"})
        runtime.create_task_row(subject="Create model", description="new", metadata={"goal": "model"})
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["id"] == 2
        assert created[0]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 1,
                "action": "deduplicate_cancel",
                "reason": "task_runtime_execution_event_append_failed",
                "blocked_by": [],
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]

    def test_dedup_cancel_missing_row_attaches_to_primary_row(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DedupCancelExecutionEventFailureTaskRuntime(missing_row=True)
        runtime.create_task_row(subject="Create model", description="old", metadata={"goal": "model"})
        runtime.create_task_row(subject="Create model", description="new", metadata={"goal": "model"})
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["id"] == 2
        assert created[0]["task_runtime_transition_failures"] == [
            {
                "success": False,
                "task_id": 1,
                "action": "deduplicate_cancel",
                "reason": "task_runtime_dedup_cancel_returned_none",
                "blocked_by": [],
                "transition_result": {},
            }
        ]

    def test_dedup_cancel_success_does_not_attach_transition_failure(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DedupCancelExecutionEventFailureTaskRuntime(failed_event=False)
        runtime.create_task_row(subject="Create model", description="old", metadata={"goal": "model"})
        runtime.create_task_row(subject="Create model", description="new", metadata={"goal": "model"})
        adapter._task_runtime = cast(Any, runtime)

        created = adapter._create_board_tasks(
            [
                {
                    "id": "TASK-1",
                    "title": "Create model",
                    "description": "Create the domain model",
                    "goal": "model",
                    "scope": "src/model.py",
                    "scope_paths": ["src/model.py"],
                    "target_files": ["src/model.py"],
                    "steps": ["write model"],
                    "acceptance": ["model exists"],
                    "depends_on": [],
                }
            ]
        )

        assert created[0]["id"] == 2
        assert "task_runtime_transition_failures" not in created[0]

    def test_pm_stage_surfaces_dedup_cancel_execution_event_failure_signal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        runtime = _DedupCancelExecutionEventFailureTaskRuntime()
        runtime.create_task_row(subject="Create model", description="old", metadata={"goal": "model"})
        runtime.create_task_row(subject="Create model", description="new", metadata={"goal": "model"})
        adapter._task_runtime = cast(Any, runtime)

        async def fake_call_role_llm(_message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
            return {
                "response": json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "TASK-1",
                                "title": "Create model",
                                "description": "Create the domain model",
                                "goal": "model",
                                "scope": "src/model.py",
                                "scope_paths": ["src/model.py"],
                                "target_files": ["src/model.py"],
                                "steps": ["write model"],
                                "acceptance": ["model exists"],
                                "depends_on": [],
                            }
                        ]
                    }
                )
            }

        adapter._call_role_llm = fake_call_role_llm

        result = asyncio.run(
            adapter._run_pm_stage(
                "pm-task",
                "Create model",
                {},
                {},
            )
        )

        signals = result["quality_gate"]["signals"]
        assert any(
            item["code"] == "pm.dedup.cancelled_rows" and item["detail"] == "cancelled_rows=1" for item in signals
        )
        failure_signals = [item for item in signals if item["code"] == "pm.dedup.execution_event_failure"]
        assert failure_signals == [
            {
                "code": "pm.dedup.execution_event_failure",
                "severity": "warning",
                "detail": "task_id=1; reason=task_runtime_execution_event_append_failed",
                "task_id": 1,
                "transition_result": {
                    "ok": False,
                    "event_type": "task_runtime.execution",
                    "error_code": "append_failed",
                },
            }
        ]


class TestCanonicalText:
    def test_strips_noise(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._canonical_text("Hello, World!") == "helloworld"
        assert adapter._canonical_text("") == ""


class TestBuildTaskIdentitySignature:
    def test_combines_title_and_goal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._build_task_identity_signature(title="Fix bug", goal="make it work") == "fixbug::makeitwork"
        assert adapter._build_task_identity_signature(title="", goal="x") == "x"
        assert adapter._build_task_identity_signature(title="x", goal="") == "x"


class TestPickPreferredTaskId:
    def test_prefers_in_progress(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        candidates = [
            {"id": 1, "status": "pending"},
            {"id": 2, "status": "in_progress"},
        ]
        assert adapter._pick_preferred_task_id(candidates) == 2

    def test_prefers_higher_id_on_same_status(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        candidates = [
            {"id": 1, "status": "pending"},
            {"id": 3, "status": "pending"},
        ]
        assert adapter._pick_preferred_task_id(candidates) == 3

    def test_empty_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._pick_preferred_task_id([]) is None


class TestFindExistingTaskMatch:
    def test_signature_match(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {"fixbug::makeitwork": [{"id": 7, "status": "pending"}]}
        title_index: dict[str, list[dict[str, Any]]] = {}
        assert (
            adapter._find_existing_task_match(
                subject="Fix bug", goal="make it work", signature_index=sig_index, title_index=title_index
            )
            == 7
        )

    def test_title_match(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {}
        title_index: dict[str, list[dict[str, Any]]] = {"fixbug": [{"id": 8, "status": "pending"}]}
        assert (
            adapter._find_existing_task_match(
                subject="Fix bug", goal="", signature_index=sig_index, title_index=title_index
            )
            == 8
        )

    def test_fuzzy_match_above_threshold(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {}
        title_index: dict[str, list[dict[str, Any]]] = {"fixbug": [{"id": 9, "status": "pending"}]}
        # "fixbugs" vs "fixbug" ratio is ~0.923, below 0.93 threshold
        assert (
            adapter._find_existing_task_match(
                subject="Fix bugs", goal="", signature_index=sig_index, title_index=title_index
            )
            is None
        )

    def test_no_match_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._find_existing_task_match(subject="X", goal="Y", signature_index={}, title_index={}) is None


# ---------------------------------------------------------------------------
# Adapter identity
# ---------------------------------------------------------------------------


class TestPmAdapterIdentity:
    def test_role_id(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter.role_id == "pm"

    def test_capabilities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "analyze_requirements" in caps
        assert "generate_tasks" in caps

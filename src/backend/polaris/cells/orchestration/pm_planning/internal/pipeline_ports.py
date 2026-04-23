"""Pure port interfaces and default implementations for pm_planning.pipeline.

This module provides:
- Protocol interfaces (PmInvokeBackendPort, PmStatePort) so that
  ``pm_planning.pipeline`` can stay cell-local and avoid importing delivery
  modules directly.
- Pure-function copies of logic previously in ``polaris.delivery.cli.pm.tasks``
  and ``polaris.delivery.cli.pm.utils`` so the Cell can run without delivery.
- Cell-local prompt and backend helpers so the Cell module can always be
  imported in isolation.

Design invariant: this file MUST NOT contain any import of
``polaris.delivery.*``.  Validated by
``tests/test_pm_planning_no_delivery_import.py``.

Architecture note: prompt building and backend invocation are implemented
here as cell-local ports backed by KernelOne/infrastructure primitives.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import (
    Any,
    Protocol,
    runtime_checkable,
)

__all__ = [
    # Noop implementations
    "CellPmInvokePort",
    "NoopPmInvokePort",
    "NoopPmStatePort",
    "PmBackendInvokeResult",
    # Protocols
    "PmInvokeBackendPort",
    "PmStatePort",
    # Pure functions (previously from delivery.cli.pm.backend)
    "_extract_json_from_llm_output",
    "_looks_like_tool_call_output",
    "_migrate_tasks_in_place",
    "collect_schema_warnings",
    "format_json_for_prompt",
    # Lazy loaders (for pipeline.py)
    "get_pm_invoke_port",
    "get_pm_state_port",
    # Pure functions (previously from delivery.cli.pm.tasks)
    "normalize_engine_config",
    # Pure functions (previously from delivery.cli.pm.utils)
    "normalize_path_list",
    "normalize_pm_payload",
    "normalize_priority",
]

# ---------------------------------------------------------------------------
# Constants (copied verbatim from delivery.cli.pm.tasks to keep canonical set)
# ---------------------------------------------------------------------------

_ACTIVE_TASK_STATUSES = {"todo", "in_progress", "review", "needs_continue"}
_TERMINAL_TASK_STATUSES = {"done", "failed", "blocked"}
_DEFAULT_PM_SCHEMA_REQUIRED_FIELDS = [
    "id",
    "priority",
    "dependencies",
    "spec",
    "acceptance_criteria",
    "assigned_to",
]
_PRIORITY_ALIASES = {
    "urgent": 0,
    "highest": 0,
    "high": 1,
    "normal": 5,
    "medium": 5,
    "low": 9,
}

# ---------------------------------------------------------------------------
# PmStatePort – abstract boundary for PM state container
# ---------------------------------------------------------------------------


@runtime_checkable
class PmStatePort(Protocol):
    """Port for PM role state container.

    Abstracts the ``polaris.delivery.cli.pm.config.PmRoleState`` dataclass
    so that the Cell only accesses structured attributes, never the concrete
    delivery class.
    """

    @property
    def workspace_full(self) -> str: ...

    @property
    def cache_root_full(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def show_output(self) -> bool: ...

    @property
    def timeout(self) -> int: ...

    @property
    def prompt_profile(self) -> str: ...

    @property
    def ollama_full(self) -> str: ...

    @property
    def events_full(self) -> str: ...

    @property
    def log_full(self) -> str: ...

    @property
    def llm_events_full(self) -> str: ...


class NoopPmStatePort:
    """Null-object implementation of ``PmStatePort``."""

    @property
    def workspace_full(self) -> str:
        return ""

    @property
    def cache_root_full(self) -> str:
        return ""

    @property
    def model(self) -> str:
        return ""

    @property
    def show_output(self) -> bool:
        return False

    @property
    def timeout(self) -> int:
        return 0

    @property
    def prompt_profile(self) -> str:
        return ""

    @property
    def ollama_full(self) -> str:
        return ""

    @property
    def events_full(self) -> str:
        return ""

    @property
    def log_full(self) -> str:
        return ""

    @property
    def llm_events_full(self) -> str:
        return ""


def format_json_for_prompt(payload: Any, max_chars: int = 2000) -> str:
    """Format a payload as prompt-friendly JSON text."""
    if payload is None:
        return "none"
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(payload)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


# ---------------------------------------------------------------------------
# PmBackendInvokeResult – return type for PmInvokeBackendPort
# ---------------------------------------------------------------------------


@dataclass
class PmBackendInvokeResult:
    """Result of a PM backend invocation."""

    output: str
    ok: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# PmInvokeBackendPort – abstract boundary for PM LLM backend
# ---------------------------------------------------------------------------


@runtime_checkable
class PmInvokeBackendPort(Protocol):
    """Port for PM LLM backend invocation.

    Abstracts ``polaris.delivery.cli.pm.backend.invoke_pm_backend`` and
    ``polaris.delivery.cli.pm.backend.build_pm_prompt`` so that the Cell
    never imports delivery directly.
    """

    def invoke(
        self,
        state: PmStatePort,
        prompt: str,
        backend_kind: str,
        args: Any,
        usage_ctx: Any,
    ) -> str:
        """Invoke the PM LLM backend.

        Args:
            state: PM state container (PmStatePort).
            prompt: The prompt string to send.
            backend_kind: Backend kind (e.g. "ollama", "codex", "auto").
            args: argparse.Namespace with runtime arguments.
            usage_ctx: Optional UsageContext for metrics.

        Returns:
            LLM output as a string.

        Raises:
            RuntimeError: Raised when backend invocation fails or the
                configured runtime provider cannot be resolved.
        """
        ...

    def build_prompt(
        self,
        requirements: str,
        plan_text: str,
        gap_report: str,
        last_qa: str,
        last_tasks: Any,
        director_result: Any,
        pm_state: Any,
        iteration: int = 0,
        run_id: str = "",
        events_path: str = "",
        workspace_root: str = "",
    ) -> str:
        """Build the PM planning prompt.

        Args:
            requirements: User requirements text.
            plan_text: Existing plan text.
            gap_report: Gap analysis report.
            last_qa: Previous QA output.
            last_tasks: Previous tasks JSON.
            director_result: Previous Director result.
            pm_state: PM state dict.
            iteration: Current iteration number.
            run_id: Run identifier.
            events_path: Path to events log.
            workspace_root: Workspace root path.

        Returns:
            Rendered prompt string.
        """
        ...

    def extract_json(self, raw_output: str) -> dict[str, Any] | None:
        """Extract JSON object from LLM raw output.

        Args:
            raw_output: Raw LLM output string.

        Returns:
            Parsed JSON dict, or None if extraction failed.
        """
        ...


class NoopPmInvokePort:
    """Null-object implementation of ``PmInvokeBackendPort``."""

    def invoke(
        self,
        state: PmStatePort,
        prompt: str,
        backend_kind: str,
        args: Any,
        usage_ctx: Any,
    ) -> str:
        raise RuntimeError(
            "NoopPmInvokePort.invoke called: pm_planning pipeline ran without a backend port implementation."
        )

    def build_prompt(
        self,
        requirements: str,
        plan_text: str,
        gap_report: str,
        last_qa: str,
        last_tasks: Any,
        director_result: Any,
        pm_state: Any,
        iteration: int = 0,
        run_id: str = "",
        events_path: str = "",
        workspace_root: str = "",
    ) -> str:
        return requirements

    def extract_json(self, raw_output: str) -> dict[str, Any] | None:
        try:
            return json.loads(raw_output)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None


def _use_context_engine_v2() -> bool:
    value = str(os.environ.get("KERNELONE_CONTEXT_ENGINE", "")).strip().lower()
    return value in ("v2", "context_v2", "engine_v2", "context-engine-v2")


def _build_pm_prompt_impl(
    requirements: str,
    plan_text: str,
    gap_report: str,
    last_qa: str,
    last_tasks: Any,
    director_result: Any,
    pm_state: Any,
    iteration: int = 0,
    run_id: str = "",
    events_path: str = "",
    workspace_root: str = "",
) -> str:
    from polaris.cells.context.engine.public.service import get_anthropomorphic_context_v2
    from polaris.kernelone.events import emit_event
    from polaris.kernelone.memory.integration import get_anthropomorphic_context
    from polaris.kernelone.prompts.loader import current_profile, get_template, render_template
    from polaris.kernelone.prompts.meta_prompting import build_meta_prompting_appendix

    profile = current_profile().strip().lower()
    is_zh = profile.endswith("_zh") or profile.startswith("zh") or profile in ("zh", "chinese")
    if "armada" in profile:
        intro = "你是这个海战 MMO 仓库的项目经理。" if is_zh else "You are the project manager for a naval MMO repo."
    else:
        intro = (
            "你是这个软件项目仓库的项目经理。" if is_zh else "You are the project manager for a software project repo."
        )

    query = f"{requirements}\n{plan_text}"
    context_root = str(workspace_root or os.environ.get("KERNELONE_CONTEXT_ROOT") or os.getcwd()).strip()
    if not context_root or not os.path.isdir(context_root):
        context_root = os.getcwd()
    context_root = os.path.abspath(context_root)

    if _use_context_engine_v2():
        anthro = get_anthropomorphic_context_v2(
            context_root,
            "pm",
            query,
            iteration,
            run_id,
            "pm.planning",
            events_path=events_path or "",
        )
    else:
        anthro = get_anthropomorphic_context(
            context_root,
            "pm",
            query,
            iteration,
            run_id,
            "pm.planning",
        )

    if events_path:
        prompt_context_obj = anthro.get("prompt_context_obj")
        output = (
            prompt_context_obj.model_dump()
            if prompt_context_obj is not None and hasattr(prompt_context_obj, "model_dump")
            else {}
        )
        context_pack = anthro.get("context_pack")
        if context_pack is not None:
            output["context_hash"] = getattr(context_pack, "request_hash", "")
            output["context_snapshot"] = getattr(context_pack, "snapshot_path", "")
        emit_event(
            events_path,
            kind="observation",
            actor="PM",
            name="prompt_context",
            refs={"run_id": run_id, "step": iteration},
            summary="Prompt Context Injection",
            output=output,
        )

    template = get_template("pm_prompt")
    rendered = render_template(
        template,
        {
            "pm_intro": intro,
            "requirements": requirements,
            "plan_text": plan_text,
            "gap_report": gap_report,
            "last_qa": last_qa,
            "last_tasks": format_json_for_prompt(last_tasks),
            "director_result": format_json_for_prompt(director_result),
            "pm_state": format_json_for_prompt(pm_state),
            "persona_instruction": anthro["persona_instruction"],
            "anthropomorphic_context": anthro["anthropomorphic_context"],
        },
    )
    if is_zh:
        role_boundary_rules = (
            "\n角色权限边界（必遵守）：\n"
            "- 天子/用户仅通过 UI 指令入口（廷议/快驿）下达意图，不生成直接写文件任务。\n"
            "- 中书令任务仅允许读写 `docs/`。\n"
            "- PM 只负责规划与契约编排，不直接执行代码实现。\n"
            "- 工部尚书（ChiefEngineer）负责生成代码施工图与方法级蓝图，不直接提交业务代码。\n"
            '- Director 任务必须声明代码作用域，默认 `scope_mode: "module"` + `scope_paths`。\n'
            "- QA/Auditor 为读多写少，仅可写审计结论与缺陷票据等验收产物。\n"
            "- 禁止自动回滚；回滚只允许人工确认触发。\n"
            "- 严格遵守已绑定角色->模型路由，不得跨模型越权调用。\n"
        )
    else:
        role_boundary_rules = (
            "\nRole boundary contract (mandatory):\n"
            "- Emperor/Human interacts via UI directives only (Dialogue/Express mode), not direct file-write tasks.\n"
            "- Architect tasks are docs-only and may read/write `docs/` only.\n"
            "- PM is planning-only and must not perform implementation edits.\n"
            "- ChiefEngineer tasks generate code construction blueprints (module/file/method level) and do not implement business code.\n"
            '- Director tasks must declare code scope, defaulting to `scope_mode: "module"` with `scope_paths`.\n'
            "- QA/Auditor is read-mostly and may write audit artifacts only.\n"
            "- Auto rollback is forbidden; rollback is manual-only.\n"
            "- Respect bound role->model routing; no cross-model override.\n"
        )
    meta_prompt_appendix = build_meta_prompting_appendix(
        workspace_root or context_root,
        "pm",
        limit=4,
    )
    return (
        rendered
        + "\n\nBacklog mapping rule (required):\n"
        + "- Each generated task SHOULD include `backlog_ref` with the original plan backlog text it derives from.\n"
        + "- If source backlog item is uncertain, set `backlog_ref` to an empty string.\n"
        + "- Never hallucinate a backlog source.\n"
        + "\nrequired_evidence contract rule (required):\n"
        + "- `required_evidence` is validation metadata only, not an instruction channel.\n"
        + "- Do NOT output `required_evidence.must_read` or `required_evidence.must_find_calls`.\n"
        + "- For Director tasks, use `required_evidence.validation_paths` only when post-run validation artifacts are needed.\n"
        + "- Return exactly one JSON object and no extra text.\n"
        + "- Do NOT emit TOOL_CALL/function-call markup.\n"
        + "\nBootstrap-first planning rule (required):\n"
        + "- If key project files are missing (e.g. package.json, pyproject.toml, src entry files), create bootstrap tasks first.\n"
        + "- Do NOT schedule verification-only tasks (build/test/lint/read-only inspection) before scaffold files exist.\n"
        + '- For Director tasks, default to `scope_mode: "module"` with `scope_paths` (module/directory prefixes).\n'
        + '- Only use `scope_mode: "exact_files"` with `target_files` when high-risk or when user explicitly requests precise file locking.\n'
        + "- In `module` mode, `target_files` is optional and acts as hints, not the primary scope boundary.\n"
        + "- Avoid exploration-only/read-only Director tasks unless explicitly requested by the user.\n"
        + "\nDocs-stage dispatch rule (required):\n"
        + "- If requirements contain `[PM_DOC_STAGE]`, tasks MUST stay within the active document scope only.\n"
        + "- Never generate synthetic bootstrap paths outside the active stage document.\n"
        + "- If file targets are unclear, emit a docs-stage convergence task bound to `active_document` instead of cross-stage code tasks.\n"
        + "\nDefect-loop and rollback rule (required):\n"
        + "- Auto rollback is forbidden. Director must use fix-forward multi-round repair.\n"
        + "- Keep retrying code repair up to 5 rounds before escalating to Tri-Council (Director -> ChiefEngineer -> PM).\n"
        + "- Request human intervention only after Tri-Council rounds are exhausted and issue remains unresolved.\n"
        + "\nVerification cadence rule (required):\n"
        + "- Do not require heavy syntax/test verification on every repair round.\n"
        + "- Prioritize final-round verification for a task and integration-level verification after task completion.\n"
        + "\nTask dependency rule (required):\n"
        + "- Emit `depends_on` when a task logically requires an earlier task output.\n"
        + "- Use `phase` hints to express execution order: bootstrap/scaffold -> core/implementation -> integration -> verification/qa -> polish.\n"
        + "- Never place test-only tasks before required implementation/bootstrap tasks.\n"
        + "- For complex coding tasks, you MAY assign `ChiefEngineer` before `Director` to produce a detailed construction blueprint.\n"
        + "\nTask detail quality rule (required):\n"
        + "- When tasks count >= 2, include at least one dependency chain via `depends_on`.\n"
        + "- Every task MUST include `phase` and `execution_checklist` (3-6 concrete steps).\n"
        + "- Every task acceptance criteria MUST include measurable signals (commands/thresholds/evidence paths), not generic prose.\n"
        + "- Every task SHOULD include `backlog_ref` mapped to the originating backlog/blueprint item text.\n"
        + "- In docs-stage, every task MUST include `metadata.doc_sections` (array) and `metadata.change_intent`.\n"
        + "- If active docs-stage is enabled, keep all task paths strictly within `active_document` (or its direct doc directory scope only when explicitly needed).\n"
        + role_boundary_rules
        + meta_prompt_appendix
    )


class CellPmInvokePort:
    """Cell-local PM invoke port that never imports delivery."""

    def invoke(
        self,
        state: PmStatePort,
        prompt: str,
        backend_kind: str,
        args: Any,
        usage_ctx: Any,
    ) -> str:
        import time

        from polaris.infrastructure.llm.provider_runtime_adapter import (
            AppLLMRuntimeAdapter,
        )
        from polaris.kernelone.events import emit_llm_event
        from polaris.kernelone.fs.text_ops import write_text_atomic
        from polaris.kernelone.llm.runtime import invoke_role_runtime_provider
        from polaris.kernelone.process.codex_adapter import invoke_codex
        from polaris.kernelone.process.ollama_utils import invoke_ollama

        started_at = time.time()
        resolved_backend = str(backend_kind or "").strip().lower() or "generic"
        state_events_full = str(getattr(state, "events_full", "") or "").strip()
        state_output_full = str(getattr(state, "ollama_full", "") or "").strip()
        state_workspace_full = str(getattr(state, "workspace_full", "") or "").strip()
        state_model = str(getattr(state, "model", "") or "").strip()
        state_show_output = bool(getattr(state, "show_output", False))
        state_timeout = int(getattr(state, "timeout", 0) or 0)

        run_id = str(getattr(usage_ctx, "run_id", "") or "").strip()
        iteration = 0
        if run_id.startswith("pm-") and run_id[3:].isdigit():
            iteration = int(run_id[3:])

        if state_events_full:
            emit_llm_event(
                state_events_full,
                event="invoke_start",
                role="pm",
                run_id=run_id,
                iteration=iteration,
                source="runtime",
                data={
                    "backend": resolved_backend,
                    "prompt_chars": len(str(prompt or "")),
                },
            )

        try:
            if backend_kind == "codex":
                output = invoke_codex(
                    prompt,
                    state_output_full,
                    state_workspace_full,
                    state_show_output,
                    getattr(args, "codex_full_auto", False),
                    getattr(args, "codex_dangerous", False),
                    getattr(args, "codex_profile", ""),
                    state_timeout,
                    None,
                    usage_ctx=usage_ctx,
                    events_path=state_events_full,
                )
                resolved_backend = "codex"
            elif backend_kind == "ollama":
                output = invoke_ollama(  # type: ignore[assignment]
                    prompt,
                    state_model,
                    state_workspace_full,
                    state_show_output,
                    state_timeout,
                    usage_ctx=usage_ctx,
                    events_path=state_events_full,
                )
                resolved_backend = "ollama"
            else:
                provider_result = invoke_role_runtime_provider(
                    role="pm",
                    workspace=state_workspace_full,
                    prompt=prompt,
                    fallback_model=state_model,
                    timeout=state_timeout,
                    adapter=AppLLMRuntimeAdapter(),
                    blocked_provider_types={
                        "",
                        "ollama",
                        "codex",
                        "codex_cli",
                        "codex_sdk",
                    },
                )
                if not provider_result.attempted or not provider_result.ok:
                    error_message = str(provider_result.error or "").strip() or "runtime_provider_unavailable"
                    raise RuntimeError(f"PM backend requires an explicit runtime provider binding: {error_message}")
                output = str(provider_result.output or "")
                resolved_backend = "runtime_provider"
                if state_output_full:
                    write_text_atomic(state_output_full, output or "")

            if state_events_full:
                emit_llm_event(
                    state_events_full,
                    event="invoke_done",
                    role="pm",
                    run_id=run_id,
                    iteration=iteration,
                    source="runtime",
                    data={
                        "backend": resolved_backend,
                        "duration_ms": int((time.time() - started_at) * 1000),
                        "output_chars": len(str(output or "")),
                        "preview": str(output or "")[:160],
                    },
                )
            return output
        except (OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            if state_events_full:
                emit_llm_event(
                    state_events_full,
                    event="invoke_error",
                    role="pm",
                    run_id=run_id,
                    iteration=iteration,
                    source="error",
                    data={
                        "backend": resolved_backend,
                        "duration_ms": int((time.time() - started_at) * 1000),
                        "error": str(exc or "").strip() or "invoke_failed",
                    },
                )
            raise

    def build_prompt(
        self,
        requirements: str,
        plan_text: str,
        gap_report: str,
        last_qa: str,
        last_tasks: Any,
        director_result: Any,
        pm_state: Any,
        iteration: int = 0,
        run_id: str = "",
        events_path: str = "",
        workspace_root: str = "",
    ) -> str:
        return _build_pm_prompt_impl(
            requirements,
            plan_text,
            gap_report,
            last_qa,
            last_tasks,
            director_result,
            pm_state,
            iteration=iteration,
            run_id=run_id,
            events_path=events_path,
            workspace_root=workspace_root,
        )

    def extract_json(self, raw_output: str) -> dict[str, Any] | None:
        return _extract_json_from_llm_output(raw_output)


# ---------------------------------------------------------------------------
# Lazy loaders (mirrors pm_dispatch dispatch_pipeline.py pattern)
# ---------------------------------------------------------------------------


def get_pm_state_port() -> PmStatePort:
    """Return a safe PM state port placeholder."""
    return NoopPmStatePort()


def get_pm_invoke_port() -> PmInvokeBackendPort:
    """Return the Cell-local PM invoke port."""
    return CellPmInvokePort()


# ---------------------------------------------------------------------------
# Pure functions (copied from polaris.delivery.cli.pm.tasks)
# ---------------------------------------------------------------------------


def normalize_priority(value: Any, fallback: int = 5) -> int:
    """Normalise a priority value to an integer.

    Mirrors ``polaris.delivery.cli.pm.task_helpers.normalize_priority``.
    """
    if isinstance(value, int):
        return max(0, min(9, value))
    token = str(value or "").strip().lower()
    if token in _PRIORITY_ALIASES:
        return _PRIORITY_ALIASES[token]
    try:
        return max(0, min(9, int(float(token))))
    except (OverflowError, TypeError, ValueError):
        return max(0, min(9, fallback))


def _normalize_scope_mode(value: Any) -> str:
    """Normalise scope mode to canonical token."""
    token = str(value or "").strip().lower()
    if token in ("exact", "exact_files"):
        return "exact_files"
    if token in ("module", "directory", "dir"):
        return "module"
    if token in ("package", "repo", "repository"):
        return "repo"
    return "module"


def _normalize_phase_hint(value: Any) -> str:
    """Normalise phase hint to canonical token."""
    token = str(value or "").strip().lower()
    phase_map = {
        "bootstrap": "bootstrap",
        "scaffold": "scaffold",
        "core": "core",
        "implementation": "implementation",
        "implement": "implementation",
        "integration": "integration",
        "verify": "verify",
        "verification": "verify",
        "qa": "qa",
        "build": "build",
        "polish": "polish",
        "design": "design",
        "planning": "design",
    }
    return phase_map.get(token, "implementation")


def _normalize_acceptance_items(items: list[str]) -> list[str]:
    """Normalise acceptance criteria items."""
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token:
            continue
        norm = token.lower()
        if norm in seen:
            continue
        seen.add(norm)
        result.append(token)
    return result


def _normalize_scope_list(value: Any) -> list[str]:
    """Normalise scope list value."""
    return normalize_path_list(value)


def _derive_scope_paths_from_target_files(target_files: list[str]) -> list[str]:
    """Derive scope paths from target file list."""
    if not target_files:
        return []
    scopes: list[str] = []
    seen: set[str] = set()
    for f in target_files:
        if not f:
            continue
        parts = str(f).replace("\\", "/").split("/")
        scope = parts[0] if len(parts) >= 2 else parts[0]
        if scope and scope not in seen:
            seen.add(scope)
            scopes.append(scope)
    return scopes


def _generate_task_id(task: dict[str, Any], iteration: int, index: int) -> str:
    """Generate a stable task ID."""
    title = str(task.get("title") or task.get("goal") or "").strip()
    if title:
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower())
        slug = re.sub(r"^_+|_+$", "", slug)[:20]
        if slug:
            return f"T{iteration:02d}-{slug}"
    return f"T{iteration:02d}-{index:03d}"


def normalize_engine_config(raw_config: Any) -> dict[str, Any]:
    """Normalize top-level engine execution config from PM payload.

    Mirrors ``polaris.delivery.cli.pm.tasks.normalize_engine_config``.
    """
    if not isinstance(raw_config, dict):
        return {}

    normalized: dict[str, Any] = {}

    mode = str(raw_config.get("director_execution_mode") or "").strip().lower()
    if mode in ("single", "multi"):
        normalized["director_execution_mode"] = mode

    policy = str(raw_config.get("scheduling_policy") or "").strip().lower()
    if policy in ("fifo", "priority", "dag"):
        normalized["scheduling_policy"] = policy

    max_directors_raw = raw_config.get("max_directors")
    if max_directors_raw is not None:
        try:
            max_directors = int(max_directors_raw)
        except (TypeError, ValueError):
            max_directors = 0
        if max_directors > 0:
            normalized["max_directors"] = max_directors

    return normalized


def _migrate_tasks_in_place(payload: dict[str, Any]) -> None:
    """Migrate tasks in place for backward compatibility.

    Mirrors ``polaris.delivery.cli.pm.tasks._migrate_tasks_in_place``.
    """
    if not isinstance(payload, dict):
        return
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return
    for task in tasks:
        if not isinstance(task, dict):
            continue
        backlog_ref = str(task.get("backlog_ref") or "").strip()
        task["backlog_ref"] = backlog_ref if backlog_ref else ""

        status_token = str(task.get("status") or "").strip().lower()
        if status_token in ("failed", "blocked"):
            task.setdefault("error_code", "")
            task.setdefault("failure_detail", "")
            task.setdefault("failed_at", "")
        else:
            task.pop("error_code", None)
            task.pop("failure_detail", None)
            task.pop("failed_at", None)


def _looks_like_tool_call_output(text: str) -> bool:
    """Check if output looks like a tool call.

    Mirrors ``polaris.delivery.cli.pm.tasks_utils._looks_like_tool_call_output``.
    """
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    markers = (
        "[tool_call]",
        "</tool_call>",
        "[TOOL_CALL]",
        "[function_call]",
        "</function_call>",
        "<tool_call>",
        "<function_call>",
        "tool_calls",
        "function_calls",
    )
    return any(m.lower() in lowered for m in markers)


def normalize_pm_payload(
    raw_payload: Any,
    iteration: int,
    start_timestamp: str,
) -> dict[str, Any]:
    """Normalize raw PM payload to canonical contract format.

    Mirrors ``polaris.delivery.cli.pm.tasks.normalize_pm_payload``.
    Returns a dict with schema_version, run_id, pm_iteration, timestamp,
    overall_goal, focus, tasks, notes fields.
    """
    if not isinstance(raw_payload, dict):
        return {
            "schema_version": 2,
            "run_id": f"pm-{iteration:05d}",
            "pm_iteration": iteration,
            "timestamp": start_timestamp,
            "overall_goal": "",
            "focus": "",
            "tasks": [],
            "notes": "Invalid PM payload: not a dict",
        }

    # Extract tasks
    raw_tasks = raw_payload.get("tasks")
    tasks: list[dict[str, Any]] = []
    if isinstance(raw_tasks, list):
        for idx, item in enumerate(raw_tasks, start=1):
            if not isinstance(item, dict):
                continue
            task_id = _generate_task_id(item, iteration, idx)
            priority = normalize_priority(item.get("priority"), fallback=idx)
            context_files = normalize_path_list(item.get("context_files") or item.get("files"))
            target_files = normalize_path_list(item.get("target_files") or item.get("files"))
            scope_paths = _normalize_scope_list(
                item.get("scope_paths") or item.get("scope") or item.get("module_scope") or item.get("write_scope")
            )
            if not scope_paths and target_files:
                scope_paths = _derive_scope_paths_from_target_files(target_files)
            scope_mode = _normalize_scope_mode(item.get("scope_mode"))
            if scope_mode == "exact_files" and not target_files:
                scope_mode = "module"

            from polaris.kernelone.runtime.shared_types import normalize_str_list

            acceptance = normalize_str_list(item.get("acceptance_criteria") or item.get("acceptance"))
            acceptance = _normalize_acceptance_items(acceptance)
            dependencies = normalize_str_list(item.get("dependencies") or item.get("deps") or item.get("depends_on"))
            phase_hint = _normalize_phase_hint(item.get("phase"))

            task = {
                "id": task_id,
                "title": str(item.get("title") or item.get("goal") or "").strip(),
                "goal": str(item.get("goal") or item.get("title") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "priority": priority,
                "assigned_to": str(item.get("assigned_to") or "director").strip().lower(),
                "context_files": context_files,
                "target_files": target_files,
                "scope_paths": scope_paths,
                "scope_mode": scope_mode,
                "acceptance_criteria": acceptance,
                "dependencies": dependencies,
                "phase": phase_hint,
                "status": "todo",
                "spec": str(item.get("spec") or item.get("specification") or "").strip(),
                "execution_checklist": normalize_path_list(item.get("execution_checklist")),
                "backlog_ref": str(item.get("backlog_ref") or "").strip(),
                "metadata": item.get("metadata", {}),
            }
            tasks.append(task)

    overall_goal = str(raw_payload.get("overall_goal") or raw_payload.get("goal") or "").strip()
    focus = str(raw_payload.get("focus") or "").strip()
    notes = str(raw_payload.get("notes") or "").strip()

    return {
        "schema_version": 2,
        "run_id": f"pm-{iteration:05d}",
        "pm_iteration": iteration,
        "timestamp": start_timestamp,
        "overall_goal": overall_goal,
        "focus": focus,
        "tasks": tasks,
        "notes": notes,
    }


def _load_pm_schema_required_fields(workspace_full: str) -> list[str]:
    """Load required fields from schema file.

    Mirrors ``polaris.delivery.cli.pm.tasks._load_pm_schema_required_fields``.
    """
    candidates = [
        os.path.join(workspace_full, "schema", "pm_tasks.schema.json"),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                schema = json.load(handle)
            tasks_schema = schema.get("properties", {}).get("tasks", {}) if isinstance(schema, dict) else {}
            items_schema = tasks_schema.get("items", {}) if isinstance(tasks_schema, dict) else {}
            required = items_schema.get("required", []) if isinstance(items_schema, dict) else []
            parsed = [str(item).strip() for item in required if str(item).strip()]
            if parsed:
                return parsed
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return list(_DEFAULT_PM_SCHEMA_REQUIRED_FIELDS)


def collect_schema_warnings(
    normalized_payload: dict[str, Any],
    workspace_full: str,
) -> list[str]:
    """Collect schema warnings for tasks.

    Mirrors ``polaris.delivery.cli.pm.tasks.collect_schema_warnings``.
    """
    warnings: list[str] = []
    required_fields = _load_pm_schema_required_fields(workspace_full)
    tasks = normalized_payload.get("tasks") if isinstance(normalized_payload, dict) else []
    if not isinstance(tasks, list):
        return ["pm_tasks payload invalid: tasks is not a list"]
    for idx, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            warnings.append(f"task[{idx}] invalid: not an object")
            continue
        task_id = str(task.get("id") or f"TASK-{idx}")
        allow_empty_fields = {"dependencies", "depends_on", "spec"}
        for field in required_fields:
            value = task.get(field)
            missing = value is None
            if isinstance(value, str):
                if field not in allow_empty_fields:
                    missing = missing or (not value.strip())
            elif (isinstance(value, (list, dict))) and field not in allow_empty_fields:
                missing = missing or (len(value) == 0)
            if missing:
                warnings.append(f"{task_id}: missing required field '{field}'")
    return warnings


# ---------------------------------------------------------------------------
# Pure functions (copied from polaris.delivery.cli.pm.utils)
# ---------------------------------------------------------------------------


def normalize_path_list(value: Any) -> list[str]:
    """Normalize value to canonical relative path list.

    Mirrors ``polaris.delivery.cli.pm.utils.normalize_path_list``.
    Delegated to KernelOne shared_types for consistency.
    """
    from polaris.kernelone.runtime.shared_types import normalize_path_list as _impl

    return _impl(value)


# ---------------------------------------------------------------------------
# Pure functions (copied from polaris.delivery.cli.pm.backend)
# ---------------------------------------------------------------------------


_LLM_STRIP_PATTERNS = [
    re.compile(r"<minimax:tool_call>.*?</minimax:tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<function_calls?>.*?</function_calls?>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<invoke\b[^>]*>.*?</invoke>", re.DOTALL | re.IGNORECASE),
    re.compile(r"\[tool_call\].*?\[/tool_call\]", re.DOTALL | re.IGNORECASE),
    re.compile(r"\[function_call\].*?\[/function_call\]", re.DOTALL | re.IGNORECASE),
]


def _strip_llm_xml_tags(text: str) -> str:
    """Strip XML-like tags from LLM output."""
    result = text
    for pat in _LLM_STRIP_PATTERNS:
        result = pat.sub("", result)
    return result.strip()


def _extract_json_from_llm_output(raw_output: str) -> dict[str, Any] | None:
    """Try to extract a JSON object from LLM output.

    Mirrors ``polaris.delivery.cli.pm.backend._extract_json_from_llm_output``.
    """
    if not raw_output:
        return None
    try:
        from polaris.kernelone.runtime.shared_types import strip_ansi
    except ImportError:

        def strip_ansi(s):
            return s  # type: ignore[assignment]

    cleaned = _strip_llm_xml_tags(strip_ansi(raw_output))
    if not cleaned:
        return None
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", cleaned, re.IGNORECASE)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1))
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    for m in re.finditer(r"\{", cleaned):
        start = m.start()
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        obj = json.loads(candidate)
                        if isinstance(obj, dict) and ("tasks" in obj or "overall_goal" in obj or "focus" in obj):
                            return obj
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                    break
    return None

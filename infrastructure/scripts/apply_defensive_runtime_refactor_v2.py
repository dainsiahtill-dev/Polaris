"""Apply the reviewed defensive runtime integration edits with UTF-8 I/O.

This temporary migration helper is intentionally exact and idempotent. It
fails when an expected source boundary changed instead of silently applying a
partial patch.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def _replace_once_or_verify(path: str, old: str, new: str) -> None:
    content = _read(path)
    if new in content:
        if old in content:
            raise RuntimeError(f"both old and new forms exist in {path}")
        return
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"expected one exact match in {path}, found {count}")
    _write(path, content.replace(old, new, 1))


def _replace_region_or_verify(
    path: str,
    *,
    start_marker: str,
    end_marker: str,
    replacement: str,
    verification_marker: str,
) -> None:
    content = _read(path)
    if verification_marker in content:
        return
    start = content.find(start_marker)
    if start < 0:
        raise RuntimeError(f"start marker not found in {path}: {start_marker!r}")
    end = content.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"end marker not found in {path}: {end_marker!r}")
    _write(path, content[:start] + replacement + content[end:])


def _patch_internal_bench_gate() -> None:
    path = "src/backend/polaris/delivery/http/routers/_shared.py"
    _replace_once_or_verify(
        path,
        "from polaris.kernelone.llm import config_store as llm_config\n",
        "from polaris.kernelone.env_flags import EnvFlagDecision, resolve_env_flag\n"
        "from polaris.kernelone.llm import config_store as llm_config\n",
    )
    replacement = '''_INTERNAL_BENCH_ENV_FLAGS = (
    "POLARIS_INTERNAL_BENCH_ENABLED",
    "POLARIS_FACTORY_BENCH_INTERNAL_ENABLED",
    "VITE_POLARIS_INTERNAL_BENCH",
)


def internal_bench_surface_decision() -> EnvFlagDecision:
    """Resolve the internal Bench gate without trusting malformed aliases."""

    return resolve_env_flag(_INTERNAL_BENCH_ENV_FLAGS, default=False)


def internal_bench_surface_enabled() -> bool:
    """Return whether the internal Bench HTTP surface is explicitly enabled."""

    return internal_bench_surface_decision().enabled


'''
    _replace_region_or_verify(
        path,
        start_marker="def _env_flag_enabled(name: str) -> bool:\n",
        end_marker="def require_internal_bench_surface() -> None:\n",
        replacement=replacement,
        verification_marker="def internal_bench_surface_decision() -> EnvFlagDecision:\n",
    )
    _replace_once_or_verify(
        path,
        "    if internal_bench_surface_enabled():\n        return\n",
        "    decision = internal_bench_surface_decision()\n"
        "    if decision.enabled:\n"
        "        return\n",
    )
    _replace_once_or_verify(
        path,
        '            "formal_projection": "/v2/control-plane/ledger/projection",\n',
        '            "formal_projection": "/v2/control-plane/ledger/projection",\n'
        '            "gate_reason": decision.reason,\n'
        '            "configured_envs": list(decision.configured_names),\n',
    )
    content = _read(path)
    if "os." not in content and "import os\n" in content:
        _write(path, content.replace("import os\n", "", 1))


def _patch_context_admin_gate() -> None:
    path = "src/backend/polaris/delivery/http/v2/context.py"
    _replace_once_or_verify(
        path,
        "from polaris.kernelone.llm.engine.context_store_retention import (\n",
        "from polaris.kernelone.env_flags import resolve_env_flag\n"
        "from polaris.kernelone.llm.engine.context_store_retention import (\n",
    )
    replacement = '''def _admin_enabled() -> bool:
    """Resolve the Context Admin gate as an explicit fail-closed opt-in."""

    return resolve_env_flag((ADMIN_ENV_FLAG,), default=False).enabled


'''
    _replace_region_or_verify(
        path,
        start_marker="def _admin_enabled() -> bool:\n",
        end_marker="def _resolve_workspace(request: Request) -> str:\n",
        replacement=replacement,
        verification_marker="Resolve the Context Admin gate as an explicit fail-closed opt-in.",
    )


def _patch_context_admin_tests() -> None:
    path = "src/backend/polaris/delivery/tests/test_context_admin_endpoints.py"
    replacement = '''    def test_stats_disabled_when_env_unset(
        self,
        disabled_client: TestClient,
    ) -> None:
        """Unset configuration keeps the privileged surface invisible."""

        response = disabled_client.get("/v2/context/admin/stats")

        assert response.status_code == 404
        assert response.json().get("detail", {}).get("code") == "ADMIN_DISABLED"

    def test_sweep_disabled_when_env_unset(
        self,
        disabled_client: TestClient,
    ) -> None:
        response = disabled_client.post("/v2/context/admin/sweep")

        assert response.status_code == 404
        assert response.json().get("detail", {}).get("code") == "ADMIN_DISABLED"

    def test_stats_disabled_when_env_invalid(
        self,
        monkeypatch: pytest.MonkeyPatch,
        client: TestClient,
    ) -> None:
        monkeypatch.setenv("KERNELONE_CONTEXT_ADMIN_ENABLED", "development")

        response = client.get("/v2/context/admin/stats")

        assert response.status_code == 404
        assert response.json().get("detail", {}).get("code") == "ADMIN_DISABLED"

    def test_stats_disabled_when_env_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        client: TestClient,
    ) -> None:
        monkeypatch.setenv("KERNELONE_CONTEXT_ADMIN_ENABLED", "false")

        response = client.get("/v2/context/admin/stats")

        assert response.status_code == 404
        assert response.json().get("detail", {}).get("code") == "ADMIN_DISABLED"


'''
    _replace_region_or_verify(
        path,
        start_marker="    def test_stats_enabled_when_env_unset(\n",
        end_marker="class TestContextAdminStats:\n",
        replacement=replacement,
        verification_marker="    def test_stats_disabled_when_env_unset(\n",
    )


def _patch_factory_bench_tests() -> None:
    path = "src/backend/polaris/tests/integration/delivery/routers/test_factory_bench_router.py"
    replacement = '''def test_factory_bench_sessions_disabled_without_internal_flag() -> None:
    with patch.dict(os.environ, {}, clear=False):
        for name in (
            "POLARIS_INTERNAL_BENCH_ENABLED",
            "POLARIS_FACTORY_BENCH_INTERNAL_ENABLED",
            "VITE_POLARIS_INTERNAL_BENCH",
        ):
            os.environ.pop(name, None)
        client = _build_client()
        response = client.get("/v2/factory/bench/sessions")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INTERNAL_BENCH_SURFACE_DISABLED"


def test_factory_bench_sessions_fail_closed_on_invalid_flag() -> None:
    with patch.dict(
        os.environ,
        {"POLARIS_INTERNAL_BENCH_ENABLED": "development"},
        clear=False,
    ):
        os.environ.pop("POLARIS_FACTORY_BENCH_INTERNAL_ENABLED", None)
        os.environ.pop("VITE_POLARIS_INTERNAL_BENCH", None)
        client = _build_client()
        response = client.get("/v2/factory/bench/sessions")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INTERNAL_BENCH_SURFACE_DISABLED"


def test_factory_bench_sessions_fail_closed_on_conflicting_flags() -> None:
    with patch.dict(
        os.environ,
        {
            "POLARIS_INTERNAL_BENCH_ENABLED": "1",
            "POLARIS_FACTORY_BENCH_INTERNAL_ENABLED": "0",
        },
        clear=False,
    ):
        os.environ.pop("VITE_POLARIS_INTERNAL_BENCH", None)
        client = _build_client()
        response = client.get("/v2/factory/bench/sessions")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INTERNAL_BENCH_SURFACE_DISABLED"


'''
    _replace_region_or_verify(
        path,
        start_marker="def test_factory_bench_sessions_disabled_without_internal_flag() -> None:\n",
        end_marker="class TestFactoryBenchRouter:\n",
        replacement=replacement,
        verification_marker="def test_factory_bench_sessions_fail_closed_on_invalid_flag() -> None:\n",
    )


def _patch_dev_launcher() -> None:
    path = "infrastructure/scripts/run-dev.js"
    _replace_once_or_verify(
        path,
        "  const env = {\n    ...process.env,\n",
        "  const internalBenchConfigured = [\n"
        '    "POLARIS_INTERNAL_BENCH_ENABLED",\n'
        '    "POLARIS_FACTORY_BENCH_INTERNAL_ENABLED",\n'
        '    "VITE_POLARIS_INTERNAL_BENCH",\n'
        "  ].some((name) => Object.prototype.hasOwnProperty.call(process.env, name));\n"
        "  const env = {\n"
        "    ...process.env,\n"
        '    ...(internalBenchConfigured ? {} : { POLARIS_INTERNAL_BENCH_ENABLED: "1" }),\n',
    )


def _patch_go_execution_boundary() -> None:
    path = (
        "src/backend/polaris/cells/roles/adapters/internal/director/"
        "deterministic_repairs/generic_repairs.py"
    )
    _replace_once_or_verify(
        path,
        "from .go_repairs import repair_go_duplicate_declarations, "
        "repair_go_import_subpaths, repair_go_module_imports\n",
        "from .go_repairs import GoRepairBlocker, plan_go_repairs\n",
    )
    replacement = '''def _go_repair_blocker_result(blocker: GoRepairBlocker) -> dict[str, Any]:
    result = {
        "ok": False,
        "blocked": True,
        "error_type": blocker.code,
        "error": blocker.message,
        "source_tool": "deterministic_go_repair_planner",
        "evidence": list(blocker.evidence),
        "files": list(blocker.files),
    }
    return {
        "tool": "deterministic_go_repair",
        "tool_name": "deterministic_go_repair",
        "success": False,
        "error": blocker.message,
        "result": result,
    }


def _apply_deterministic_go_repairs(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    """Plan Go repairs, then execute every mutation through Director tools."""

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.is_dir():
        return []
    plan = plan_go_repairs(
        workspace_path,
        artifact_quality_errors=artifact_quality_errors,
    )
    if not plan.writes and not plan.blockers:
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    results: list[dict[str, Any]] = []
    for write in plan.writes:
        write_result = executor.execute_tool(
            "write_file",
            {"file": write.file, "content": write.content},
            task_id=task_id,
        )
        success = bool(write_result.get("ok"))
        evidence: dict[str, Any] = {
            **write_result,
            "ok": success,
            "source_tool": "deterministic_go_repair_executor",
            "repair_kinds": list(write.repair_kinds),
            "planner_evidence": list(write.evidence),
        }
        item: dict[str, Any] = {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": success,
            "result": evidence,
        }
        if not success:
            item["error"] = str(
                write_result.get("error") or "Deterministic Go repair write failed"
            )
        results.append(item)
        if success:
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                adapter._update_task_progress(
                    task_id,
                    "executing",
                    current_file=write.file,
                )

    results.extend(_go_repair_blocker_result(blocker) for blocker in plan.blockers)
    return results


'''
    _replace_region_or_verify(
        path,
        start_marker="def _apply_deterministic_go_module_import_repair(\n",
        end_marker="def _apply_deterministic_materialization_quality_repairs(\n",
        replacement=replacement,
        verification_marker="def _apply_deterministic_go_repairs(\n",
    )
    _replace_once_or_verify(
        path,
        "    go_import_repairs = _apply_deterministic_go_module_import_repair("
        "adapter, task_id=task_id)\n"
        "    results.extend(go_import_repairs)\n",
        "    results.extend(\n"
        "        _apply_deterministic_go_repairs(\n"
        "            adapter,\n"
        "            task_id=task_id,\n"
        "            artifact_quality_errors=artifact_quality_errors,\n"
        "        )\n"
        "    )\n",
    )


def main() -> None:
    _patch_internal_bench_gate()
    _patch_context_admin_gate()
    _patch_context_admin_tests()
    _patch_factory_bench_tests()
    _patch_dev_launcher()
    _patch_go_execution_boundary()


if __name__ == "__main__":
    main()

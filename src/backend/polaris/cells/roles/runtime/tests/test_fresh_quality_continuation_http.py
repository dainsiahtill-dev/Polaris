"""Fresh quality projection through real Engine, Factory cutoff, native HTTP and DEO."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from polaris.cells.roles.kernel.tests import test_director_persistent_execution_http as harness
from polaris.cells.roles.runtime.tests.test_fresh_quality_continuation import GOAL, _workspace
from polaris.cells.roles.runtime.tests.test_static_quality_observation_reconciliation import (
    _capture_producer_observation_context,
)
from polaris.kernelone.quality import artifact_quality_issues_from_errors


@pytest.mark.asyncio
async def test_fresh_quality_full_engine_http_replaces_residuals_after_admitted_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reuse the established engineering harness at execution boundaries.
    # Only private fixture inputs/authority and final assertions differ.
    source = inspect.getsource(harness.test_canonical_director_session_reads_writes_repairs_and_verifies_over_http)
    source = source[source.index("async def test_") :]
    source = source.replace(
        "test_canonical_director_session_reads_writes_repairs_and_verifies_over_http", "_run_fresh_quality_http", 1
    )
    source = source[: source.index("    if forged_wire:\n", source.index("    finally:"))]
    source = source.replace(
        "    attempt = _setup_attempt(str(tmp_path))",
        "    paths, old_errors = _workspace(tmp_path)\n    attempt = _setup_attempt(str(tmp_path))",
        1,
    )
    source = source.replace(
        "            payload = json.dumps(",
        '            if mixed_batch and index == 1:\n                content.append({"type": "tool_use", "id": "fresh-static-write", "name": "create_file", "input": {"path": "src/barrel.ts", "text": "export type {\\n First, Second, Third\\n} from \'./types\';\\nexport { makeFirst } from \'./types\';\\n"}})\n            payload = json.dumps(',
        1,
    )
    source = source.replace(
        "    paths, old_errors = _workspace(tmp_path)",
        "    paths, old_errors = _workspace(tmp_path)\n    (tmp_path / 'notes/input4.md').unlink()\n    paths = ['README.md' if path == 'notes/input4.md' else path for path in paths]\n    old_errors.append(\"Artifact quality scan failed: declared target file missing 'README.md'\")",
        1,
    )
    source = source.replace(
        "            payload = json.dumps(",
        '            if mixed_batch and index == 1:\n                content.append({"type": "tool_use", "id": "fresh-owned-readme", "name": "create_file", "input": {"path": "README.md", "text": "# Owned artifact created through an admitted tool.\\n"}})\n            payload = json.dumps(',
        1,
    )
    source = source.replace(
        '"allowed_read_paths": ["answer.py", "verify.py"],', '"allowed_read_paths": ["answer.py", "verify.py", *paths],'
    )
    source = source.replace(
        '"allowed_write_paths": [] if read_only else ["answer.py", "optional.md"],',
        '"allowed_write_paths": [] if read_only else ["answer.py", "optional.md", "src/barrel.ts", "README.md"],',
    )
    source = source.replace(
        '"allowed_paths": ["answer.py", "verify.py"] if read_only else ["answer.py", "verify.py", "optional.md"],',
        '"allowed_paths": ["answer.py", "verify.py", "optional.md", *paths],',
    )
    insertion = '    context["task_objective"] = GOAL\n    context["director_quality_repair"] = {"artifact_quality_errors": old_errors, "artifact_quality_issues": list(artifact_quality_issues_from_errors(old_errors)), "scan_relative_paths": [*paths, "answer.py", "verify.py"], "repair_target_files": ["src/barrel.ts", "answer.py", "README.md"], "declared_target_files": ["answer.py", "src/barrel.ts", "README.md"]}\n'
    insertion += '    context["failed_gate_evidence"] = {"schema_version": "polaris.failure_evidence.v1", "quality_errors": ["TypeScript project typecheck failed: src/use.ts(7,1): TS7777 actual compiler failure"], "issues": [{"code": "unrecognized_compiler_diagnostic", "message": "actual structured verifier failure", "path": "src/use.ts", "severity": "error", "source": "compiler_verifier"}], "checks_complete": False, "deferred_checks": [{"check_id": "compiler_verification", "command": "npm run build", "required": True, "status": "deferred"}]}\n'
    source = source.replace(
        "    try:\n        result = await service.execute_role_session(",
        insertion + "    try:\n        result = await service.execute_role_session(",
        1,
    )
    source = source.replace(
        '"target_files": [] if read_only else ["answer.py", "optional.md"],',
        '"target_files": [] if read_only else ["answer.py", "optional.md", "README.md"],',
    )
    source = source.replace(
        "    try:\n        result = await service.execute_role_session(",
        "    context['context_files'] = token['allowed_read_paths']\n    context = _capture_producer_observation_context(tmp_path, {'target_files': ['answer.py', 'src/barrel.ts', 'README.md']}, old_errors, context)\n    try:\n        result = await service.execute_role_session(",
        1,
    )
    source += "\n    return result, bodies, qualified_requests, old_errors, token, original_token\n"
    namespace = dict(harness.__dict__)
    namespace.update(
        _workspace=_workspace,
        GOAL=GOAL,
        artifact_quality_issues_from_errors=artifact_quality_issues_from_errors,
        _capture_producer_observation_context=_capture_producer_observation_context,
    )
    exec(compile(source, __file__, "exec"), namespace)
    result, bodies, qualified, old_errors, token, original = await namespace["_run_fresh_quality_http"](
        tmp_path, monkeypatch, "mixed_alias_superseded_engine"
    )
    assert result.ok, (result.error_code, result.error_message)
    assert token == original
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    assert "export type" in (tmp_path / "src/barrel.ts").read_text(encoding="utf-8")
    assert "Owned artifact" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert len(qualified) == len(bodies) >= 5
    assert all(row["audit"]["audit_scope"] == "provider_native_wire" for row in qualified)
    assert all(row["audit"]["context_os_audit"]["ok"] for row in qualified)

    def current_text(body):
        content = body["messages"][-1].get("content", "")
        if isinstance(content, str):
            return content
        return "\n".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )

    assert all(error in current_text(bodies[0]) for error in old_errors)
    assert all(error not in current_text(bodies[2]) for error in old_errors)
    assert all(GOAL in current_text(body) for body in bodies)
    assert "typescript_project_typecheck" in current_text(bodies[2])
    assert "checks_complete" in current_text(bodies[2])
    assert "TS7777 actual compiler failure" in current_text(bodies[2])
    assert "actual structured verifier failure" in current_text(bodies[2])
    assert "compiler_verification" in current_text(bodies[2])
    assert '"checks_complete": false' in current_text(bodies[2])
    assert "expected 42, got 1" in json.dumps(bodies[3], ensure_ascii=False)
    assert result.metadata["task_boundary_verdict"]["ok"] is True
    assert result.metadata["task_boundary_verdict"]["failed_required_verifiers"] == []
    assert result.metadata["task_boundary_verdict"]["missing_required_verifiers"] == []
    rows = result.metadata["batch_receipt"]["results"]
    assert any(
        row["call_id"] == "fresh-static-write" and row["status"] == "success" and row["effect_receipt"] for row in rows
    )

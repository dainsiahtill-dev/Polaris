"""Current quality evidence must replace mutable repair feedback in one session."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.internal.kernel_session_turn import KernelSessionTurnBridge
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.kernelone.quality import artifact_quality_issues_from_errors, scan_workspace_artifact_quality_evidence

GOAL = "Implement the authorized module; preserve Unicode 空格  and all acceptance constraints."


def _workspace(root: Path) -> tuple[list[str], list[str]]:
    files = {
        "src/types.ts": "export type First = { value: number };\nexport type Second = { value: number };\nexport type Third = { value: number };\nexport function makeFirst(): First { return { value: 1 }; }\n",
        "src/barrel.ts": "export {};\n",
        "src/use.ts": "import { First, Second, Third } from './barrel';\nimport { makeFirst } from './barrel';\nexport const value = makeFirst();\n",
        "tests/use.ts": "import { makeFirst } from '../src/barrel';\nexport const fixture = makeFirst();\n",
        "package.json": '{"scripts":{"test":"node --test --require ts-node/register tests/use.ts"},"devDependencies":{"typescript":"^5.0.0"}}',
        "tsconfig.json": '{"compilerOptions":{"noEmit":true}}',
    }
    files.update({f"notes/input{index}.md": f"Read-only evidence {index}.\n" for index in range(5)})
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    paths = list(files)
    evidence = scan_workspace_artifact_quality_evidence(str(root), relative_paths=paths, scan_mode="pure_static")
    errors = [error for error in evidence.errors if "unresolved import symbol" in error]
    assert len(errors) == 5
    return paths, errors


def _bridge(root: Path, paths: list[str], errors: list[str]) -> KernelSessionTurnBridge:
    return KernelSessionTurnBridge(
        SimpleNamespace(),
        ExecuteRoleSessionCommandV1(
            role="director",
            session_id="same-session",
            task_id="same-task",
            run_id="same-run",
            workspace=str(root),
            user_message=GOAL + "\nQuality errors:\n" + "\n".join(errors),
            host_kind="director_adapter",
            timeout_seconds=60,
            context={
                "task_objective": GOAL,
                "workspace_quality_evidence": {"quality_errors": list(errors), "all_checks_passed": False},
                "failed_gate_evidence": {"quality_errors": list(errors)},
                "target_files": paths,
                "verification_commands": ["npm run build", "npm test", "npm run lint", "node dist/main.js"],
                "allowed_commands": ["npm install", "npm run build", "npm test", "npm run lint", "node dist/main.js"],
                "execution_envelope": {"allowed_write_paths": paths, "allowed_read_paths": paths},
                "director_quality_repair": {
                    "artifact_quality_errors": errors,
                    "artifact_quality_issues": list(artifact_quality_issues_from_errors(errors)),
                    "scan_relative_paths": paths,
                    "repair_target_files": ["src/barrel.ts"],
                },
            },
            metadata={"execution_envelope_hash": "PRIVATE_SCOPE_HASH"},
        ),
    )


def _commit(bridge: KernelSessionTurnBridge, status: str = "success", command: str | None = None) -> None:
    tool = "execute_command" if command else "write_file"
    arguments = {"command": command} if command else {"file": "src/barrel.ts", "content": "committed"}
    payload = (
        {"exit_code": 1 if status == "error" else 0, "stderr": "expected 42, got 1" if status == "error" else ""}
        if command
        else {"file": "src/barrel.ts"}
    )
    bridge.results.append(
        RoleTurnResult(
            content="",
            tool_calls=[{"tool": tool, "args": arguments, "call_id": f"call-{len(bridge.results)}"}],
            batch_receipt={
                "results": [
                    {
                        "tool_name": tool,
                        "call_id": f"call-{len(bridge.results)}",
                        "arguments": arguments,
                        "status": status,
                        "result": payload,
                    }
                ]
            },
            metadata={
                "transaction_kind": "continue_multi_turn",
                "needs_followup_workflow": True,
                "turn_commit_receipt": {"turn_id": f"turn-{len(bridge.results)}"},
            },
        )
    )


def _fix(root: Path) -> None:
    (root / "src/barrel.ts").write_text(
        "export type {\n First, Second, Third\n} from './types';\nexport { makeFirst } from './types';\n",
        encoding="utf-8",
    )


def test_fresh_quality_retires_five_static_errors_but_keeps_verifier_intents(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    original = deepcopy(bridge.command.context)
    _fix(tmp_path)
    _commit(bridge)
    command = bridge._turn_command("next", "STALE_TEXT " + "\n".join(errors))
    quality = command.context["director_quality_repair"]
    assert quality["artifact_quality_errors"] == []
    assert command.context["workspace_quality_evidence"]["quality_errors"] == []
    assert command.context["failed_gate_evidence"]["quality_errors"] == []
    assert quality["current_static_evidence"]["deferred_checks"]
    assert quality["current_static_evidence"]["checks_complete"] is False
    assert command.user_message.startswith(GOAL)
    assert all(error not in command.user_message for error in errors)
    assert "STALE_TEXT" not in command.user_message
    assert "npm run build" in command.user_message and "npm test" in command.user_message
    assert "PRIVATE_SCOPE_HASH" not in command.user_message
    assert command.context["execution_envelope"] == original["execution_envelope"]
    assert bridge.command.context == original
    assert command.task_id == bridge.command.task_id and command.session_id == bridge.command.session_id


def test_fresh_quality_refreshes_again_after_new_input_error(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    _fix(tmp_path)
    _commit(bridge)
    first = bridge._turn_command("next-1", GOAL)
    (tmp_path / "src/use.ts").write_text(
        "import { NewMissing } from './barrel';\nexport const value = NewMissing;\n", encoding="utf-8"
    )
    _commit(bridge)
    second = bridge._turn_command("next-2", GOAL)
    assert "NewMissing" in second.user_message
    assert errors[0] not in second.user_message
    assert (
        first.context["director_quality_repair"]["input_hash"]
        != second.context["director_quality_repair"]["input_hash"]
    )


def test_fresh_quality_keeps_nonstatic_failure_and_unknown_loader(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    failure = "TypeScript project typecheck failed: src/use.ts(9,1): TS2304 unknown runtime identifier"
    bridge = _bridge(tmp_path, paths, [*errors, failure])
    _fix(tmp_path)
    _commit(bridge)
    command = bridge._turn_command("next", GOAL)
    assert failure in command.context["director_quality_repair"]["artifact_quality_errors"]
    assert "node_test_loader_unknown" in str(command.context["director_quality_repair"]["current_static_evidence"])
    assert failure in command.user_message


def test_fresh_quality_failed_command_remains_actionable(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    _fix(tmp_path)
    _commit(bridge, "error", "npm test")
    command = bridge._turn_command("next", GOAL)
    assert "expected 42, got 1" in command.user_message
    assert "npm test" in command.user_message


def test_fresh_quality_unknown_or_missing_source_cannot_clear_prior_findings(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    (tmp_path / "src/barrel.ts").unlink()
    _commit(bridge)
    command = bridge._turn_command("next", GOAL)
    assert command.context["director_quality_repair"]["artifact_quality_errors"]
    assert "src/barrel.ts" in command.user_message


@pytest.mark.asyncio
async def test_fresh_quality_objective_survives_initial_session_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent
    from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator

    class Kernel:
        canonical_role_kernel = True

        async def execute_stream(self, **kwargs):
            yield CompletionEvent(
                turn_id=kwargs["turn_id"], status="success", turn_kind="final_answer", visible_content="done"
            )

    orchestrator = RoleSessionOrchestrator("goal-fixture", Kernel(), str(tmp_path), role="director")

    async def checkpoint():
        pass

    monkeypatch.setattr(orchestrator, "_checkpoint_session", checkpoint)
    prompt = '<SESSION_PATCH>{"delivery_mode":"materialize_changes"}</SESSION_PATCH>\n' + GOAL + "\nSTALE_DIAGNOSTIC"
    _ = [event async for event in orchestrator.execute_stream(prompt, context={"task_objective": GOAL})]
    assert orchestrator.state.original_goal == GOAL
    orchestrator.state.session_invariants.validate(
        current_delivery_mode=orchestrator.state.delivery_mode,
        current_original_goal=GOAL,
        current_phase=orchestrator.state.task_progress,
    )


def test_fresh_quality_rejects_foreign_read_scope(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    (tmp_path / "foreign.ts").write_text("export const PRIVATE_FOREIGN = 1;\n", encoding="utf-8")
    bridge.context["director_quality_repair"] = {
        **bridge.context["director_quality_repair"],
        "scan_relative_paths": [*paths, "foreign.ts"],
    }
    with pytest.raises(RuntimeError, match=r"quality_continuation.*scope"):
        bridge._turn_command("next", GOAL)


def test_fresh_quality_rejects_source_change_during_readonly_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.internal import kernel_session_turn as module

    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    actual = module.scan_workspace_artifact_quality_evidence

    def changed(*args, **kwargs):
        result = actual(*args, **kwargs)
        (tmp_path / "src/types.ts").write_text("export type Changed = number;\n", encoding="utf-8")
        return result

    monkeypatch.setattr(module, "scan_workspace_artifact_quality_evidence", changed)
    with pytest.raises(RuntimeError, match="quality_continuation_source_snapshot_changed"):
        bridge._turn_command("next", GOAL)


def test_fresh_quality_dependency_failure_is_current_and_actionable(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    _fix(tmp_path)
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node --test tests/use.ts"}}', encoding="utf-8")
    _commit(bridge)
    command = bridge._turn_command("next", GOAL)
    assert "typescript' devDependency" in command.user_message
    assert "package.json" in command.user_message
    assert all(error not in command.user_message for error in errors)


def test_fresh_quality_preserves_prior_nonstatic_deferred_checks_and_prep(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    prior = {"check_id": "compiler_verification", "command": "npm run build", "required": True, "status": "deferred"}
    bridge.context["workspace_quality_evidence"].update(
        deferred_checks=[prior], incomplete_reasons=["dependency_install_pending"], checks_complete=False
    )
    _fix(tmp_path)
    _commit(bridge)
    command = bridge._turn_command("next", GOAL)
    current = command.context["workspace_quality_evidence"]
    assert prior in current["deferred_checks"]
    assert "dependency_install_pending" in current["incomplete_reasons"]
    assert "dependency_install_pending" in command.user_message
    assert "compiler_verification" in command.user_message
    assert "npm install" in command.user_message
    assert current["checks_complete"] is False


def test_fresh_quality_unknown_read_preserves_import_findings(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    _fix(tmp_path)
    (tmp_path / "src/types.ts").unlink()
    (tmp_path / "src/types.ts").symlink_to(tmp_path / "src/barrel.ts")
    _commit(bridge)
    command = bridge._turn_command("next", GOAL)
    assert all(error in command.context["director_quality_repair"]["artifact_quality_errors"] for error in errors)
    assert command.context["director_quality_repair"]["current_static_evidence"]["checks_complete"] is False


def test_fresh_quality_latest_success_does_not_repeat_prior_command_failure(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    _fix(tmp_path)
    _commit(bridge, "error", "npm test")
    failed = bridge._turn_command("failed", GOAL)
    assert "expected 42, got 1" in failed.user_message
    _commit(bridge, "success", "npm test")
    current = bridge._turn_command("success", GOAL)
    assert "expected 42, got 1" not in current.user_message
    assert bridge.results[0].batch_receipt["results"][0]["result"]["exit_code"] == 1


def test_fresh_quality_preserves_false_coverage_without_other_pending_fields(tmp_path: Path) -> None:
    (tmp_path / "answer.py").write_text("VALUE = 42\n", encoding="utf-8")
    bridge = KernelSessionTurnBridge(
        SimpleNamespace(),
        ExecuteRoleSessionCommandV1(
            role="director",
            session_id="private",
            workspace=str(tmp_path),
            user_message="Verify answer.py",
            context={
                "task_objective": "Verify answer.py",
                "target_files": ["answer.py"],
                "workspace_quality_evidence": {"checks_complete": False},
                "execution_envelope": {"allowed_read_paths": ["answer.py"]},
                "director_quality_repair": {"scan_relative_paths": ["answer.py"], "artifact_quality_errors": []},
            },
        ),
    )
    command = bridge._turn_command("next", "Verify answer.py")
    assert command.context["workspace_quality_evidence"]["checks_complete"] is False
    assert '"checks_complete": false' in command.user_message
    assert bridge.command.context["workspace_quality_evidence"]["checks_complete"] is False


def test_fresh_quality_preserves_nonstatic_failures_only_in_outer_evidence(tmp_path: Path) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    failure = "TypeScript project typecheck failed: src/use.ts(7,1): TS7777 actual compiler failure"
    structured = {
        "code": "unrecognized_compiler_diagnostic",
        "message": "actual structured verifier failure",
        "path": "src/use.ts",
        "severity": "error",
        "source": "compiler_verifier",
    }
    bridge.context["failed_gate_evidence"] = {"quality_errors": [failure], "issues": [structured]}
    _fix(tmp_path)
    command = bridge._turn_command("next", GOAL)
    assert failure in command.context["failed_gate_evidence"]["quality_errors"]
    assert failure in command.user_message
    assert structured in command.context["failed_gate_evidence"]["issues"]
    assert "actual structured verifier failure" in command.user_message
    assert all(error not in command.user_message for error in errors)


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"allowed_read_paths": [], "allowed_write_paths": [], "allowed_paths": []},
        {"allowed_paths": None},
        {"allowed_read_paths": "*"},
        {"allowed_read_paths": {"paths": ["*"]}},
        {"allowed_write_paths": ["src/"]},
    ],
)
def test_fresh_quality_explicit_empty_or_malformed_authority_never_falls_back(
    tmp_path: Path, envelope: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, errors = _workspace(tmp_path)
    bridge = _bridge(tmp_path, paths, errors)
    bridge.context["execution_envelope"] = envelope
    from polaris.cells.roles.runtime.internal import kernel_session_turn as module

    def forbidden_read(*args, **kwargs):
        raise AssertionError("unauthorized read or scan invoked")

    monkeypatch.setattr(module, "WorkspaceReadSnapshot", forbidden_read)
    monkeypatch.setattr(module, "scan_workspace_artifact_quality_evidence", forbidden_read)
    with pytest.raises(RuntimeError, match="quality_continuation_read_scope"):
        bridge._turn_command("next", GOAL)

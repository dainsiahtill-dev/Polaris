"""PM authority comes from a complete envelope and exact verifier rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.tests.test_factory_role_evidence_authority_sa import (
    _authority,
    _resolved_cut,
    _Resolver,
)
from polaris.cells.roles.adapters.internal.pm.normalization import (
    _pm_typescript_factory_contract_missing,
    _pm_verification_command_contract_issues,
)
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import (
    _contracts,
    _isolated_external_delivery as _isolated_external_delivery,
    _local_runtime,
    _verifier_revision_response,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import bind_factory_role_evidence_authority
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog


def _pol77_input(name: str) -> str:
    directory = (
        Path(__file__).resolve().parents[7]
        / ".superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-artifact-action-contract/fixtures"
    )
    return (directory / name).read_text(encoding="utf-8")


def test_pol77_exact_native_plan_does_not_invent_test_ownership(tmp_path: Path) -> None:
    raw = _pol77_input("RAW_PM_RESPONSE.txt")
    directive = _pol77_input("DIRECTIVE.txt")
    assert len(raw) == 12073
    assert len(directive) == 3006
    contracts = PMAdapter(workspace=str(tmp_path))._extract_task_contracts(raw, directive=directive)
    original = json.loads(raw)["tasks"]
    assert len(contracts) == len(original) == 10
    assert [t["target_files"] for t in contracts] == [t["target_files"] for t in original]


def test_pol77_quality_hints_preserve_authored_and_readonly_fields(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = [
        {
            "id": "TASK-1",
            "title": "Create Go module",
            "target_files": ["go.mod"],
            "context_files": [],
            "depends_on": [],
            "phase": "implementation",
            "assigned_to": "Director",
        },
        {
            "id": "TASK-2",
            "title": "Implement Go tests and delivery README",
            "target_files": ["main_test.go", "README.md"],
            "context_files": ["go.mod"],
            "depends_on": ["TASK-1"],
            "phase": "verification",
            "assigned_to": "Director",
        },
    ]
    old = {t["id"]: (list(t["target_files"]), list(t["context_files"])) for t in contracts}
    normalized, _quality = adapter._evaluate_contract_quality(contracts, directive="Create a Go CLI module with tests")
    assert {t["id"]: (t["target_files"], t["context_files"]) for t in normalized} == old


def test_pol77_native_read_scopes_and_javascript_helpers_are_not_language_failures(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    directive = _pol77_input("DIRECTIVE.txt")
    contracts = adapter._extract_task_contracts(_pol77_input("RAW_PM_RESPONSE.txt"), directive=directive)
    _, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
    assert quality["ok"] is True
    diagnostics = quality.get("contract_diagnostics", [])
    assert all(d["code"] != "pm_plan_language_mismatch" for d in diagnostics)
    assert all(d["code"] != "generated_output_declaration_missing" for d in diagnostics)
    assert adapter.task_runtime.list_observable_task_rows() == []


def test_pol77_revision_retains_raw_base_but_grants_only_diagnosed_fields(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base

    captured = json.loads(_pol77_input("RAW_PM_RESPONSE.txt"))
    captured["tasks"][2]["target_files"].append("src/foreign.rs")
    raw = json.dumps(captured, ensure_ascii=False)
    directive = _pol77_input("DIRECTIVE.txt")
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(raw, directive=directive)
    _, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
    base = make_revision_base(
        raw, workspace=str(tmp_path), run_id="child", task_id="pm", directive=directive, quality=quality
    )
    assert base is not None
    assert base.response == raw and base.tasks == json.loads(raw)["tasks"]
    manifest = base.view["edit_manifest"]["tasks"]
    assert {identity for identity, permission in manifest.items() if permission["set"]} == {"TASK-3"}
    assert "target_files" in manifest["TASK-3"]["set"]
    assert "TASK-1" not in manifest
    producer = next(task for task in base.view["tasks"] if task["id"] == "TASK-3")
    assert "assigned_to" not in producer and "execution_backend" not in producer
    for field in manifest["TASK-3"]["set"]:
        if field in base.tasks[2]:
            assert producer[field] == base.tasks[2][field]
    assert base.tasks[2]["goal"] == json.loads(raw)["tasks"][2]["goal"]
    patch = {
        "schema_version": "pm.task_revision.v1",
        "base_hash": base.base_hash,
        "updates": [{"task_id": "TASK-1", "set": {"goal": "Unauthorized"}}],
        "add_tasks": [],
        "remove_task_ids": [],
    }
    with pytest.raises(ValueError):
        apply_revision(base, json.dumps(patch))


def _pol72_input(name: str) -> str:
    directory = (
        Path(__file__).resolve().parents[7]
        / ".superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-bounded-task-revision/fixtures"
    )
    text = (directory / name).read_text(encoding="utf-8")
    if name == "PM_INITIAL_RESPONSE.txt":
        # Preserve archived evidence. These budget/revision cases need an
        # ownership-valid local input, independent of the captured PM overlap.
        payload = json.loads(text)
        payload["tasks"][1]["target_files"] = ["src/garden/simulation/garden.ts"]
        return json.dumps(payload, ensure_ascii=False)
    return text


def _native_fake_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    original = ModelCatalog.resolve

    def resolve(self: Any, provider_id: str, model: str, provider_cfg: Any = None) -> Any:
        if provider_id == "local-provider" and model == "local-model":
            provider_cfg = {
                "type": "openai_compat",
                "model_specific": {model: {"max_context_tokens": 1000000, "max_output_tokens": 65536}},
            }
        return original(self, provider_id, model, provider_cfg)

    monkeypatch.setattr(ModelCatalog, "resolve", resolve)


def _revision_wire_view(wire: dict[str, Any]) -> dict[str, Any]:
    content = "\n".join(message.get("content", "") for message in wire["messages"])
    marker = "Revision base/view/manifest (server-owned):\n"
    assert marker in content, "Typed revision is missing from the actual second wire"
    value, _ = json.JSONDecoder().raw_decode(content.split(marker, 1)[1])
    return value


@pytest.mark.parametrize(
    "edit",
    [
        "config",
        "empty",
        "wrong_base",
        "cross_run",
        "unknown_task",
        "unknown_field",
        "hidden_description",
        "hidden_steps",
        "route",
        "backend",
        "projection",
        "metadata",
        "duplicate_update",
        "conflict",
        "remove_readd",
        "remove",
        "bad_add",
        "bad_command",
        "mixed_command",
        "dangling",
        "cycle",
        "duplicate_key",
        "truncated",
        "prefix",
        "full_plan",
        "nonfinite",
    ],
)
async def test_pol72_typed_config_revision_fits_original_budget_and_preserves_raw_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edit: str
) -> None:
    port, _, _, _ = _authority(tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm")))
    workspace = tmp_path / "workspace"
    original_text = _pol72_input("PM_INITIAL_RESPONSE.txt")
    original = json.loads(original_text)
    directive = _pol72_input("FACTORY_DIRECTIVE.txt")
    assert len(directive) == 3006
    captured: dict[str, Any] = {}

    def model_edit(wire: dict[str, Any]) -> dict[str, Any]:
        view = _revision_wire_view(wire)
        captured["view"] = view
        task = view["tasks"][4]
        updates: list[dict[str, Any]] = (
            []
            if edit == "empty"
            else [
                {
                    "task_id": "TASK-5",
                    "set": {
                        "target_files": [*task["target_files"], "tsconfig.json"],
                        "scope_paths": [*task["scope_paths"], "tsconfig.json"],
                    },
                    "append": {},
                }
            ]
        )
        patch = {
            "schema_version": "pm.task_revision.v1",
            "base_hash": view["base_hash"] if edit != "wrong_base" else "0" * 64,
            "updates": updates,
            "add_tasks": [],
            "remove_task_ids": [],
        }
        captured["patch"] = patch
        update: dict[str, Any] = updates[0] if updates else {}
        if edit == "cross_run":
            from polaris.cells.roles.adapters.internal.pm.revision import make_revision_base

            foreign = make_revision_base(
                original_text,
                workspace=str(workspace),
                run_id="foreign-child",
                task_id="pm-task",
                directive=directive,
                quality={},
            )
            assert foreign is not None
            patch["base_hash"] = foreign.base_hash
        elif edit == "unknown_task":
            update["task_id"] = "TASK-999"
        elif edit in {
            "unknown_field",
            "hidden_description",
            "hidden_steps",
            "route",
            "backend",
            "projection",
            "metadata",
        }:
            field = {
                "unknown_field": "unknown",
                "hidden_description": "description",
                "hidden_steps": "steps",
                "route": "assigned_to",
                "backend": "execution_backend",
                "projection": "projection",
                "metadata": "metadata",
            }[edit]
            update["set"][field] = "forged"
        elif edit == "duplicate_update":
            updates.append(json.loads(json.dumps(update)))
        elif edit == "conflict":
            update["append"] = {"target_files": ["src/other.ts"]}
        elif edit in {"remove", "remove_readd"}:
            patch["remove_task_ids"] = ["TASK-1"]
            if edit == "remove_readd":
                patch["add_tasks"] = [original["tasks"][0]]
        elif edit == "bad_add":
            patch["add_tasks"] = [{"id": "TASK-6", "title": "Incomplete new task"}]
        elif edit in {"bad_command", "mixed_command"}:
            rows = [] if edit == "bad_command" else task["verification_commands"]
            update["set"]["verification_commands"] = [
                *rows,
                {"modality": "exec", "argv": ["bash", "-c", "true"], "cwd": "."},
            ]
        elif edit in {"dangling", "cycle"}:
            update["set"]["depends_on"] = ["TASK-999" if edit == "dangling" else "TASK-5"]
        text = json.dumps(patch, ensure_ascii=False)
        if edit == "duplicate_key":
            text = text.replace('"updates":', '"updates": [], "updates":', 1)
        elif edit == "truncated":
            text = text[:-1]
        elif edit == "prefix":
            text += ',"metadata":{}'
        elif edit == "full_plan":
            text = _pol71_config_revision()
        elif edit == "nonfinite":
            text = text.replace('"tsconfig.json"', "NaN", 1)
        captured["response"] = text
        return {"role": "assistant", "content": text}

    runtime, freezes, wires = _local_runtime(
        workspace, monkeypatch, [{"role": "assistant", "content": original_text}, model_edit]
    )
    _native_fake_capacity(monkeypatch)
    profile = runtime._kernels[str(workspace)].registry.get_profile_or_raise("pm")
    assert profile.context_policy.max_context_tokens == 8000
    authority = port.mint_authority_binding("pm")
    adapter = PMAdapter(workspace=str(workspace))
    with bind_factory_role_evidence_authority(authority):
        result = await adapter._run_pm_stage(
            "pm-task", directive, {}, {"run_id": "pm-pol72-child", "timeout_seconds": 43}
        )
    assert len(wires) == len(freezes) == len(runtime._test_commands) == 2
    assert all(wire["max_tokens"] == 60000 for wire in wires)
    assert {f.identity.run_id for f in freezes} == {"pm-pol72-child"}
    assert len({f.identity.call_id for f in freezes}) == len({f.identity.request_freeze_id for f in freezes}) == 2
    assert len({c.turn_request_id for c in runtime._test_commands}) == 2
    assert (
        port._test_physical_attempt_coordinator.budget_state(authority.execution_authority_hash).consumed_attempts == 2
    )
    retry = "\n".join(message.get("content", "") for message in wires[1]["messages"])
    assert directive.strip() in retry
    assert captured["view"]["edit_manifest"]["remove_task_ids"] == []
    assert result["success"] is (edit == "config"), (result, runtime._test_results)
    derivation = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.revision_derivation")
    evidence = json.loads(Path(derivation["artifact_path"]).read_text(encoding="utf-8"))
    assert evidence["base"]["tasks"] == original["tasks"]
    assert evidence["revision_response"] == captured["response"]
    if edit not in {"duplicate_key", "truncated", "prefix", "full_plan", "nonfinite"}:
        assert evidence["revision"] == captured["patch"]
    assert evidence["identity"]["run_id"] == "pm-pol72-child"
    assert evidence["identity"]["task_id"] == "pm-task"
    if edit == "config":
        expected = json.loads(original_text)
        expected["tasks"][4]["target_files"].append("tsconfig.json")
        expected["tasks"][4]["scope_paths"].append("tsconfig.json")
        assert evidence["raw_merged_candidate"] == expected
        assert evidence["normalized_plan"] != expected
        assert result["tasks_created"] > 0
        assert result["real_planning_qualified"] is True
    else:
        assert result["tasks_created"] == 0
        assert adapter.task_runtime.list_observable_task_rows() == []
        assert result["real_planning_qualified"] is False
        assert evidence["raw_merged_candidate"] is None or evidence["raw_merged_candidate"] == original
    for frozen, wire in zip(freezes, wires, strict=True):
        requests = []
        for path in (workspace / ".polaris/runtime/contexts").glob("*/*"):
            if path.is_file() and len(path.name) == 24:
                request = json.loads(path.read_text(encoding="utf-8")).get("provider_request", {})
                if (
                    request.get("factory_final_request", {}).get("final_semantic_request_hash")
                    == frozen.final_semantic_request_hash
                ):
                    requests.append(request)
        request = next(r for r in requests if "final_physical_request" in r)
        assert request["final_physical_request"]["body"] == wire
        assert request["final_physical_request"]["transport"]["timeout"] == [10.0, 43.0]
        semantic = request["factory_final_request"]["canonical_final_payload"]
        assert semantic["role"] == "pm"
        assert "**PM**" in semantic["messages"][0]["content"]
        assert {tool["function"]["name"] for tool in wire["tools"]} == {"repo_tree", "file_exists"}
        assert wire["tool_choice"] == "auto"
        audit = request["final_request_context_audit"]
        assert audit["request_identity"]["call_id"] == frozen.identity.call_id
        assert audit["final_request_token_estimate"] >= audit["message_token_estimate"]
        assert audit["final_request_evidence_coverage"]["role_identity_ok"] is True
        assert audit["final_request_evidence_coverage"]["pass"] is True


def _task() -> dict[str, Any]:
    return json.loads(_contracts(valid=True))["tasks"][0]


@pytest.mark.parametrize("field", ["acceptance", "acceptance_criteria"])
@pytest.mark.parametrize("edit", ["replace", "noop", "append", "bad_set"])
async def test_semantic_acceptance_revision_through_real_budget_and_quality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, edit: str
) -> None:
    port, _, _, _ = _authority(tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm")))
    workspace = tmp_path / "workspace"
    directive = _pol72_input("FACTORY_DIRECTIVE.txt")
    original = json.loads(_pol72_input("PM_INITIAL_RESPONSE.txt"))
    task = original["tasks"][0]
    valid_acceptance = task.pop("acceptance")
    task[field] = [*valid_acceptance[:2], "占位输出即可"]
    first = json.dumps(original, ensure_ascii=False)
    captured: dict[str, Any] = {}
    adapter = PMAdapter(workspace=str(workspace))
    _, first_quality = adapter._evaluate_contract_quality(
        adapter._extract_task_contracts(first, directive=directive), directive=directive
    )
    assert first_quality["ok"] is False
    assert "TASK-1: acceptance permits placeholder or manifest-only execution" in first_quality["critical_issues"]
    assert not first_quality.get("contract_diagnostics")

    def model_edit(wire: dict[str, Any]) -> dict[str, Any]:
        assert adapter.task_runtime.list_observable_task_rows() == []
        view = _revision_wire_view(wire)
        assert view["tasks"][0][field] == task[field]
        permissions = view["edit_manifest"]["tasks"]["TASK-1"]["set"]
        assert permissions[field] == "nonempty_string_array"
        assert "description" not in permissions
        update: dict[str, Any] = {"task_id": "TASK-1", "set": {}, "append": {}}
        if edit == "replace":
            update["set"][field] = valid_acceptance
        elif edit == "append":
            update["append"]["acceptance" if field == "acceptance" else "steps"] = ["npm test passes"]
        elif edit == "bad_set":
            update["set"][field] = "npm test passes"
        config = view["tasks"][4]
        patch = {
            "schema_version": "pm.task_revision.v1",
            "base_hash": view["base_hash"],
            "updates": [
                *([update] if edit != "noop" else []),
                {
                    "task_id": "TASK-5",
                    "set": {
                        "target_files": [*config["target_files"], "tsconfig.json"],
                        "scope_paths": [*config["scope_paths"], "tsconfig.json"],
                    },
                    "append": {},
                },
            ],
            "add_tasks": [],
            "remove_task_ids": [],
        }
        captured["view"] = view
        captured["patch"] = patch
        return {"role": "assistant", "content": json.dumps(patch, ensure_ascii=False)}

    runtime, freezes, wires = _local_runtime(
        workspace, monkeypatch, [{"role": "assistant", "content": first}, model_edit]
    )
    _native_fake_capacity(monkeypatch)
    assert (
        runtime._kernels[str(workspace)].registry.get_profile_or_raise("pm").context_policy.max_context_tokens == 8000
    )
    authority = port.mint_authority_binding("pm")
    with bind_factory_role_evidence_authority(authority):
        result = await adapter._run_pm_stage(
            "pm-task", directive, {}, {"run_id": "semantic-child", "timeout_seconds": 43}
        )
    assert len(wires) == len(freezes) == len(runtime._test_commands) == 2
    assert all(wire["max_tokens"] == 60000 for wire in wires)
    assert {f.identity.run_id for f in freezes} == {"semantic-child"}
    assert len({f.identity.call_id for f in freezes}) == len({f.identity.request_freeze_id for f in freezes}) == 2
    assert (
        port._test_physical_attempt_coordinator.budget_state(authority.execution_authority_hash).consumed_attempts == 2
    )
    assert directive.strip() in "\n".join(m.get("content", "") for m in wires[1]["messages"])
    assert result["success"] is (edit == "replace"), (result, runtime._test_results)
    signal = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.revision_derivation")
    evidence = json.loads(Path(signal["artifact_path"]).read_text(encoding="utf-8"))
    assert evidence["base"]["tasks"] == original["tasks"]
    assert evidence["revision"] == captured["patch"]
    assert evidence["identity"]["run_id"] == "semantic-child"
    if edit == "replace":
        expected = json.loads(first)
        expected["tasks"][0][field] = valid_acceptance
        expected["tasks"][4]["target_files"].append("tsconfig.json")
        expected["tasks"][4]["scope_paths"].append("tsconfig.json")
        assert evidence["raw_merged_candidate"] == expected
        assert result["real_planning_qualified"] is True
        assert result["tasks_created"] > 0
    else:
        assert result["tasks_created"] == 0
        assert adapter.task_runtime.list_observable_task_rows() == []
        assert result["real_planning_qualified"] is False
        if edit in {"noop", "append"}:
            assert evidence["raw_merged_candidate"] is not None
            assert "占位输出即可" in evidence["raw_merged_candidate"]["tasks"][0][field]
            assert (
                "TASK-1: acceptance permits placeholder or manifest-only execution"
                in evidence["quality"]["critical_issues"]
            )


@pytest.mark.parametrize("context", [{}, {"run_id": None}, {"run_id": ""}, {"run_id": " \t"}])
async def test_standalone_without_trusted_child_uses_complete_reanswer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context: dict[str, Any]
) -> None:
    workspace = tmp_path / "workspace"
    runtime, _, wires = _local_runtime(
        workspace,
        monkeypatch,
        [
            {"role": "assistant", "content": _contracts(valid=False)},
            {"role": "assistant", "content": _contracts(valid=True)},
        ],
    )
    _native_fake_capacity(monkeypatch)
    result = await PMAdapter(workspace=str(workspace))._run_pm_stage("pm-task", "Create model", {}, context)
    assert len(wires) == len(runtime._test_commands) == 2
    assert result["success"] is True, (result, runtime._test_results)
    retry = "\n".join(m.get("content", "") for m in wires[1]["messages"])
    assert "Revision base/view/manifest" not in retry
    assert not list((workspace / ".polaris/runtime/signals/pm_revision").glob("*.json"))
    assert not any(
        s["code"] in {"pm.contracts.revision_base", "pm.contracts.revision_derivation"}
        for s in result["quality_gate"]["signals"]
    )


@pytest.mark.parametrize(
    "response",
    [
        '{"tasks":[' + json.dumps(_task()) + ', {"title": "broken"',
        _contracts(valid=True) + ',"metadata":{}',
        _contracts(valid=True) + _contracts(valid=True),
        json.dumps({"notes": {"verification_commands": _task()["verification_commands"]}}),
        json.dumps({"tasks": [_task(), {"modality": "test", "argv": ["pytest"], "cwd": "."}]}),
        json.dumps({"tasks": [_task(), "bad"]}),
        json.dumps({"tasks": [{"title": []}]}),
        json.dumps({"tasks": [{"title": "Create model", "steps": 42}]}),
        json.dumps({"tasks": [{"title": "Create model", "metadata": []}]}),
        json.dumps({"tasks": [{"title": "Create model", "scope": [{}]}]}),
        json.dumps({"tasks": [{"title": "Create model", "projection": "fake"}]}),
        json.dumps({"tasks": {"tasks": [_task()]}}),
        json.dumps({"data": [_task()]}),
        json.dumps({"tasks": [_task()], "task_list": [_task()]}),
        json.dumps({"tasks": [{"title": "Create model", "modality": "test", "argv": ["pytest"], "cwd": "."}]}),
        '{"tasks": [], "tasks": ' + json.dumps([_task()]) + "}",
        "## TASK-1 Create model\ngoal: Implement model\nsteps: Write src/model.py\nacceptance: pytest passes",
        "- Create model: implement src/model.py\n- Test model: pytest passes",
        "Some prose\n" + _contracts(valid=True),
        "```json\n" + _contracts(valid=True) + "\n```\n```json\n" + _contracts(valid=True) + "\n```",
    ],
)
def test_incomplete_or_ambiguous_response_never_creates_contracts(tmp_path: Path, response: str) -> None:
    assert PMAdapter(workspace=str(tmp_path))._extract_task_contracts(response, directive="Create model") == []


@pytest.mark.parametrize(
    "wrapper", ["plain", "fenced", "data", "mapped", "list", "yaml", "yml", "markdown", "md", "unlabelled"]
)
def test_complete_known_envelope_preserves_verifier_authority(tmp_path: Path, wrapper: str) -> None:
    task = _task()
    payload: Any = {"tasks": [task]}
    if wrapper == "data":
        payload = {"data": {"task_list": [task]}}
    elif wrapper == "mapped":
        payload = {"task-1": task}
    elif wrapper == "list":
        payload = [task]
    response = json.dumps(payload)
    if wrapper == "fenced":
        response = "```json\n" + response + "\n```"
    elif wrapper in {"yaml", "yml", "markdown", "md", "unlabelled"}:
        # Existing fence labels remain compatible only with a complete JSON body.
        label = "" if wrapper == "unlabelled" else wrapper
        response = "```" + label + "\n" + response + "\n```"
    contracts = PMAdapter(workspace=str(tmp_path))._extract_task_contracts(response, directive="Create model")
    assert len(contracts) == 1
    assert contracts[0]["verification_commands"] == task["verification_commands"]


@pytest.mark.parametrize(
    "bad_row",
    [
        {"modality": "read", "argv": ["node", "check.js"], "cwd": "."},
        {"modality": "exec", "argv": ["npm", "test"], "cwd": "."},
        {"modality": "test", "argv": "pytest", "cwd": "."},
        {"modality": "test", "argv": ["pytest"], "cwd": "../foreign"},
        {"modality": "test", "argv": ["bash", "-c", "pytest"], "cwd": "."},
        {"modality": "test", "argv": ["pytest"], "cwd": ".", "extra": True},
        None,
    ],
)
def test_present_invalid_rows_block_even_with_valid_project_modalities(tmp_path: Path, bad_row: Any) -> None:
    task = _task()
    task["verification_commands"].append(bad_row)
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(json.dumps({"tasks": [task]}), directive="Create model")
    _, quality = adapter._evaluate_contract_quality(contracts, directive="Create model")
    assert quality["ok"] is False
    assert "verification_commands_invalid" in quality["critical_issues"]
    errors = quality["contract_diagnostics"]
    assert errors[0]["path"].startswith("$.tasks[0].verification_commands[2]")
    assert errors[0]["code"]
    assert "verification_commands_missing" not in quality["critical_issues"]


@pytest.mark.parametrize("first_kind", ["malformed", "invalid_modality"])
@pytest.mark.parametrize("revision_valid", [True, False])
async def test_boundary_failure_uses_one_revision_under_real_factory_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_kind: str, revision_valid: bool
) -> None:
    port, _, _, _ = _authority(tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm")))
    workspace = tmp_path / "workspace"
    first = _contracts(valid=True)
    if first_kind == "malformed":
        first += ',"metadata":{}'
    else:
        payload = json.loads(first)
        payload["tasks"][0]["verification_commands"][0]["modality"] = "read"
        first = json.dumps(payload)
    second: Any = {"role": "assistant", "content": _contracts(valid=True) if revision_valid else first}
    if first_kind == "invalid_modality" and revision_valid:
        second = _verifier_revision_response
    outputs = [
        {"role": "assistant", "content": first},
        second,
    ]
    runtime, freezes, wires = _local_runtime(workspace, monkeypatch, outputs)
    _native_fake_capacity(monkeypatch)
    authority = port.mint_authority_binding("pm")
    with bind_factory_role_evidence_authority(authority):
        result = await PMAdapter(workspace=str(workspace))._run_pm_stage(
            "pm-task", "Create model", {}, {"run_id": "pm-owned-child", "timeout_seconds": 43}
        )
    assert result["success"] is revision_valid, (result, runtime._test_results)
    assert len(wires) == len(freezes) == len(runtime._test_commands) == 2
    assert {f.identity.run_id for f in freezes} == {"pm-owned-child"}
    assert len({f.identity.call_id for f in freezes}) == len({f.identity.request_freeze_id for f in freezes}) == 2
    assert (
        port._test_physical_attempt_coordinator.budget_state(authority.execution_authority_hash).consumed_attempts == 2
    )
    assert all(command.context["timeout_seconds"] == 43 for command in runtime._test_commands)
    assert len({command.turn_request_id for command in runtime._test_commands}) == 2
    snapshots: dict[str, Any] = {}
    for path in (workspace / ".polaris/runtime/contexts").glob("*/*"):
        if path.is_file() and len(path.name) == 24:
            request = json.loads(path.read_text(encoding="utf-8")).get("provider_request", {})
            final = request.get("factory_final_request", {})
            if final and "final_physical_request" in request:
                snapshots[final["final_semantic_request_hash"]] = request
    for frozen, wire in zip(freezes, wires, strict=True):
        snapshot = snapshots[frozen.final_semantic_request_hash]
        assert snapshot["final_physical_request"]["body"] == wire
        assert snapshot["final_physical_request"]["transport"]["timeout"] == [10.0, 43.0]
        payload = snapshot["factory_final_request"]["canonical_final_payload"]
        assert payload["role"] == "pm"
        assert "**PM**" in payload["messages"][0]["content"]
        assert {tool["function"]["name"] for tool in wire["tools"]} == {"repo_tree", "file_exists"}
        assert wire["tool_choice"] == "auto"
        audit = snapshot["final_request_context_audit"]
        assert audit["request_identity"]["call_id"] == frozen.identity.call_id
        assert audit["final_request_token_estimate"] >= audit["message_token_estimate"]
        assert audit["context_window_tokens"] > 0
        assert audit["context_window_utilization"] > 0
        assert audit["tool_schema_count"] == len(payload["tools"])
        assert audit["final_request_evidence_coverage"]["role_identity_ok"] is True
        assert audit["final_request_evidence_coverage"]["pass"] is True
    revisions = [s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.quality_revision"]
    assert len(revisions) == 1
    assert revisions[0]["contract_diagnostics"]
    if first_kind == "malformed":
        diagnostic = revisions[0]["contract_diagnostics"][0]
        assert diagnostic["code"] == "pm_contract_json_invalid"
        assert diagnostic["position"] == len(_contracts(valid=True))
        assert revisions[0]["parsed_task_count"] == 0
    else:
        assert revisions[0]["critical_issues"] == ["verification_commands_invalid"]
        diagnostic = revisions[0]["contract_diagnostics"][0]
        assert diagnostic["code"] == "invalid_modality"
        assert diagnostic["allowed"] == ["environment_prep", "build", "test", "lint", "entrypoint"]
        retry = json.dumps(wires[1]["messages"], ensure_ascii=False)
        assert diagnostic["path"] in retry
        assert "environment_prep|build|test|lint|entrypoint" in retry
    if not revision_valid:
        assert result["tasks_created"] == 0
        assert PMAdapter(workspace=str(workspace)).task_runtime.list_observable_task_rows() == []


@pytest.mark.parametrize("value", [None, "npm test", {}, 42])
def test_present_invalid_command_container_is_distinct_from_missing(tmp_path: Path, value: Any) -> None:
    task = _task()
    task["verification_commands"] = value
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(json.dumps({"tasks": [task]}), directive="Create model")
    _, quality = adapter._evaluate_contract_quality(contracts, directive="Create model")
    assert quality["ok"] is False
    assert quality["contract_diagnostics"][0]["code"] == "invalid_type"
    assert "verification_commands_invalid" in quality["critical_issues"]
    assert "verification_commands_missing" not in quality["critical_issues"]


def test_known_envelope_verifier_diagnostic_retains_original_path(tmp_path: Path) -> None:
    task = _task()
    task["verification_commands"][0]["modality"] = "read"
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(json.dumps({"data": {"task_list": [task]}}), directive="Create model")
    _, quality = adapter._evaluate_contract_quality(contracts, directive="Create model")
    assert quality["contract_diagnostics"][0]["path"] == "$.data.task_list[0].verification_commands[0].modality"


def test_autofix_keeps_semantic_parent_and_original_input_diagnostic(tmp_path: Path) -> None:
    task = _task()
    task["target_files"] = [
        "package.json",
        "tsconfig.json",
        "index.html",
        "src/core.ts",
        "src/module.ts",
        "src/other.ts",
        "src/main.ts",
        "src/styles.css",
        "tests/core.test.ts",
        "README.md",
        "docs/blueprint.md",
    ]
    task["scope_paths"] = task["target_files"]
    task["verification_commands"][0]["modality"] = "read"
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(json.dumps({"tasks": [task]}), directive="Create model")
    normalized, quality = adapter._evaluate_contract_quality(contracts, directive="Create model")
    assert [item["id"] for item in normalized] == [contracts[0]["id"]]
    assert quality["ok"] is False
    assert "verification_commands_invalid" in quality["critical_issues"]
    assert len(quality["contract_diagnostics"]) == 1
    assert quality["contract_diagnostics"][0]["path"] == "$.tasks[0].verification_commands[0].modality"


def test_invalid_verifier_in_filtered_task_cannot_disappear(tmp_path: Path) -> None:
    invalid = _task()
    invalid["title"] = "事实已补齐"
    invalid["verification_commands"][0]["modality"] = "read"
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(json.dumps({"tasks": [invalid, _task()]}), directive="Create model")
    _, quality = adapter._evaluate_contract_quality(contracts, directive="Create model")
    assert quality["ok"] is False
    assert "verification_commands_invalid" in quality["critical_issues"]
    assert quality["contract_diagnostics"][0]["path"] == "$.tasks[0].verification_commands[0].modality"


def test_quality_validator_rejects_raw_mixed_rows_without_diagnostic_metadata(tmp_path: Path) -> None:
    task = _task()
    task["verification_commands"].append({"modality": "exec", "argv": ["npm", "test"], "cwd": "."})
    assert _pm_verification_command_contract_issues([task], "Create model") == ["verification_commands_invalid"]
    _, quality = PMAdapter(workspace=str(tmp_path))._evaluate_contract_quality([task], directive="Create model")
    assert quality["ok"] is False
    assert quality["contract_diagnostics"][0]["path"] == "$.tasks[0].verification_commands[2].modality"


def _pol71_input(name: str) -> str:
    """Use local ownership-valid candidates; retain the immutable Root inputs."""
    directory = (
        Path(__file__).resolve().parents[7]
        / ".superpowers/sdd/2026-10-06-paperclip-repair-frontier/pm-candidate-continuity-native-reason/fixtures"
    )
    text = (directory / name).read_text(encoding="utf-8").rstrip("\n")
    if name == "PM_INITIAL_RESPONSE.txt":
        payload = json.loads(text)
        payload["tasks"][1]["target_files"] = ["src/garden/simulation/garden.ts"]
        return json.dumps(payload, ensure_ascii=False)
    return text


def _pol71_config_revision() -> str:
    payload = json.loads(_pol71_input("PM_INITIAL_RESPONSE.txt"))
    # Synthetic declaration only, not a rewrite of historical candidate/target.
    payload["tasks"][4]["target_files"].append("tsconfig.json")
    payload["tasks"][4]["scope_paths"].append("tsconfig.json")
    return json.dumps(payload, ensure_ascii=False)


def _pol72_config_response(wire: dict[str, Any]) -> dict[str, Any]:
    view = _revision_wire_view(wire)
    task = view["tasks"][4]
    return {
        "role": "assistant",
        "content": json.dumps(
            {
                "schema_version": "pm.task_revision.v1",
                "base_hash": view["base_hash"],
                "updates": [
                    {
                        "task_id": "TASK-5",
                        "set": {
                            "target_files": [*task["target_files"], "tsconfig.json"],
                            "scope_paths": [*task["scope_paths"], "tsconfig.json"],
                        },
                        "append": {},
                    }
                ],
                "add_tasks": [],
                "remove_task_ids": [],
            },
            ensure_ascii=False,
        ),
    }


@pytest.mark.parametrize("first_good,revision_good", [(False, True), (False, False), (True, True)])
async def test_pol71_complete_candidate_survives_real_revision_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_good: bool, revision_good: bool
) -> None:
    port, _, _, _ = _authority(tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm")))
    workspace = tmp_path / "workspace"
    first = _pol71_config_revision() if first_good else _pol71_input("PM_INITIAL_RESPONSE.txt")
    assert len(first) > 1400
    original = json.loads(first)
    assert [task["id"] for task in original["tasks"]] == ["TASK-1", "TASK-2", "TASK-3", "TASK-4", "TASK-5"]
    outputs: list[Any] = [{"role": "assistant", "content": first}]
    if not first_good:
        outputs.append(
            _pol72_config_response
            if revision_good
            else {
                "role": "assistant",
                "content": _pol71_input("PM_REVISION_RESPONSE.txt"),
            }
        )
    runtime, freezes, wires = _local_runtime(workspace, monkeypatch, outputs)
    _native_fake_capacity(monkeypatch)
    assert (
        runtime._kernels[str(workspace)].registry.get_profile_or_raise("pm").context_policy.max_context_tokens == 8000
    )
    authority = port.mint_authority_binding("pm")
    adapter = PMAdapter(workspace=str(workspace))
    with bind_factory_role_evidence_authority(authority):
        result = await adapter._run_pm_stage(
            "pm-task", _pol72_input("FACTORY_DIRECTIVE.txt"), {}, {"run_id": "pm-pol71-child", "timeout_seconds": 43}
        )
    expected_calls = 1 if first_good else 2
    assert len(wires) == len(freezes) == len(runtime._test_commands) == expected_calls
    assert all(wire["max_tokens"] == 60000 for wire in wires)
    assert {f.identity.run_id for f in freezes} == {"pm-pol71-child"}
    assert len({f.identity.call_id for f in freezes}) == expected_calls
    assert len({f.identity.request_freeze_id for f in freezes}) == expected_calls
    assert len({c.turn_request_id for c in runtime._test_commands}) == expected_calls
    assert all(c.context["timeout_seconds"] == 43 for c in runtime._test_commands)
    state = port._test_physical_attempt_coordinator.budget_state(authority.execution_authority_hash)
    assert state.consumed_attempts == expected_calls
    assert state.attempt_budget == authority.attempt_budget
    snapshots = {}
    for path in (workspace / ".polaris/runtime/contexts").glob("*/*"):
        if path.is_file() and len(path.name) == 24:
            request = json.loads(path.read_text(encoding="utf-8")).get("provider_request", {})
            final = request.get("factory_final_request", {})
            if final and "final_physical_request" in request:
                snapshots[final["final_semantic_request_hash"]] = request
    for frozen, wire in zip(freezes, wires, strict=True):
        request = snapshots[frozen.final_semantic_request_hash]
        assert request["final_physical_request"]["body"] == wire
        assert request["final_physical_request"]["transport"]["timeout"] == [10.0, 43.0]
        semantic = request["factory_final_request"]["canonical_final_payload"]
        assert semantic["role"] == "pm"
        assert "**PM**" in semantic["messages"][0]["content"]
        assert wire["tool_choice"] == "auto"
        assert {tool["function"]["name"] for tool in wire["tools"]} == {"repo_tree", "file_exists"}
        audit = request["final_request_context_audit"]
        assert audit["request_identity"]["call_id"] == frozen.identity.call_id
        assert audit["final_request_token_estimate"] >= audit["message_token_estimate"]
        assert audit["context_window_tokens"] > 0
        assert audit["final_request_evidence_coverage"]["role_identity_ok"] is True
        assert audit["final_request_evidence_coverage"]["pass"] is True
    revisions = [s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.quality_revision"]
    assert len(revisions) == (0 if first_good else 1)
    if not first_good:
        retry_content = "\n".join(message.get("content", "") for message in wires[1]["messages"])
        view = _revision_wire_view(wires[1])
        assert [task["id"] for task in view["tasks"]] == [task["id"] for task in original["tasks"]]
        signal = next(s for s in result["quality_gate"]["signals"] if s["code"] == "pm.contracts.revision_derivation")
        retained = json.loads(Path(signal["artifact_path"]).read_text(encoding="utf-8"))
        assert retained["base"] == original
        if revision_good:
            assert retained["raw_merged_candidate"] == json.loads(_pol71_config_revision())
        assert _pol72_input("FACTORY_DIRECTIVE.txt").strip() in retry_content
        assert revisions[0]["parsed_task_count"] == 5
        assert revisions[0]["critical_issues"] == ["factory_typescript_contract_missing:tsconfig.json"]
        assert "factory_typescript_contract_missing:tsconfig.json" in retry_content
    assert result["success"] is (first_good or revision_good), (result, runtime._test_results)
    if first_good or revision_good:
        assert result["tasks_created"] > 0
        assert result["real_planning_qualified"] is True
    else:
        assert result["tasks_created"] == 0
        assert result["real_planning_qualified"] is False
        assert adapter.task_runtime.list_observable_task_rows() == []


def test_pol71_actual_nested_html_does_not_hide_real_missing_config(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    directive = _pol71_input("PM_INITIAL_PROMPT.txt")
    raw = json.loads(_pol71_input("PM_INITIAL_RESPONSE.txt"))["tasks"]
    assert raw[2]["target_files"] == [
        "src/garden/cli/runGarden.ts",
        "src/garden/web/index.html",
        "src/garden/web/gardenApp.ts",
    ]
    assert _pm_typescript_factory_contract_missing(raw, directive) == ["tsconfig.json"]
    contracts = adapter._extract_task_contracts(_pol71_input("PM_INITIAL_RESPONSE.txt"), directive=directive)
    _, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
    assert quality["critical_issues"] == ["factory_typescript_contract_missing:tsconfig.json"]
    repaired = adapter._extract_task_contracts(_pol71_config_revision(), directive=directive)
    _, quality = adapter._evaluate_contract_quality(repaired, directive=directive)
    assert quality["ok"] is True
    assert quality["critical_issues"] == []


async def test_pol71_full_candidate_over_small_window_is_explicitly_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.errors import BudgetExceededError

    port, _, _, _ = _authority(tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm")))
    workspace = tmp_path / "workspace"
    runtime, freezes, wires = _local_runtime(
        workspace, monkeypatch, [{"role": "assistant", "content": _pol71_input("PM_INITIAL_RESPONSE.txt")}]
    )
    adapter = PMAdapter(workspace=str(workspace))
    # Retain the old full-retransmission budget negative through the real compiler:
    # selecting the legacy private input mode changes no budget or identity guard.
    original_retry = adapter._build_pm_retry_message

    def full_retry(**kwargs: Any) -> str:
        kwargs["revision_base"] = None
        return original_retry(**kwargs)

    monkeypatch.setattr(adapter, "_build_pm_retry_message", full_retry)
    authority = port.mint_authority_binding("pm")
    with (
        bind_factory_role_evidence_authority(authority),
        pytest.raises(BudgetExceededError, match="current user instruction exceeds context enforcement budget"),
    ):
        await adapter._run_pm_stage(
            "pm-task",
            _pol71_input("PM_INITIAL_PROMPT.txt"),
            {},
            {"run_id": "pm-small-window-child", "llm_max_tokens": 4000},
        )
    assert len(wires) == len(freezes) == 1
    assert wires[0]["max_tokens"] == 4000
    assert len(runtime._test_commands) == 2
    assert adapter.task_runtime.list_observable_task_rows() == []
    assert (
        port._test_physical_attempt_coordinator.budget_state(authority.execution_authority_hash).consumed_attempts == 1
    )
    bases = list((workspace / ".polaris/runtime/signals/pm_revision").glob("*.base.json"))
    assert len(bases) == 1, "Original candidate must remain readable even when the revision request is refused"
    retained = json.loads(bases[0].read_text(encoding="utf-8"))
    assert retained["base"] == json.loads(_pol71_input("PM_INITIAL_RESPONSE.txt"))
    assert retained["identity"]["run_id"] == "pm-small-window-child"


async def test_pol71_malformed_complete_prefix_never_becomes_revision_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port, _, _, _ = _authority(tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm")))
    workspace = tmp_path / "workspace"
    first = _pol71_input("PM_INITIAL_RESPONSE.txt") + ',"metadata":{}'
    _runtime, freezes, wires = _local_runtime(
        workspace,
        monkeypatch,
        [{"role": "assistant", "content": first}, {"role": "assistant", "content": _pol71_config_revision()}],
    )
    _native_fake_capacity(monkeypatch)
    authority = port.mint_authority_binding("pm")
    with bind_factory_role_evidence_authority(authority):
        result = await PMAdapter(workspace=str(workspace))._run_pm_stage(
            "pm-task", _pol72_input("FACTORY_DIRECTIVE.txt"), {}, {"run_id": "pm-malformed-child"}
        )
    assert len(wires) == len(freezes) == 2
    assert all(wire["max_tokens"] == 60000 for wire in wires)
    retry = "\n".join(message.get("content", "") for message in wires[1]["messages"])
    assert "Previous output excerpt:" not in retry
    assert first[:1400] not in _runtime._test_commands[1].user_message
    assert "response_sha256" in _runtime._test_commands[1].user_message
    assert not list((workspace / ".polaris/runtime/signals/pm_revision").glob("*.base.json"))
    assert first not in retry
    assert "Complete validated candidate" not in retry
    revision = [
        signal for signal in result["quality_gate"]["signals"] if signal["code"] == "pm.contracts.quality_revision"
    ]
    assert revision[0]["parsed_task_count"] == 0
    assert "pm_contract_json_invalid" in revision[0]["critical_issues"]
    assert result["success"] is True
    assert result["tasks_created"] > 0


@pytest.mark.parametrize(
    "html",
    [
        "../web/index.html",
        "/web/index.html",
        "./web/index.html",
        "web//index.html",
        "web/../index.html",
        "web\\index.html",
        "web/index.html/",
    ],
)
def test_pol71_html_path_alone_is_not_equivalent_entrypoint(tmp_path: Path, html: str) -> None:
    payload = json.loads(_pol71_config_revision())
    task = payload["tasks"][2]
    task["target_files"] = [html]
    task["scope_paths"] = ["web"]
    task["description"] = "Provide HTML entrypoint web/index.html and web/app.ts"
    task["steps"] = ["Write web/index.html", "Write web/app.ts"]
    directive = _pol71_input("PM_INITIAL_PROMPT.txt")
    assert "index.html" in _pm_typescript_factory_contract_missing(payload["tasks"], directive)
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(json.dumps(payload), directive=directive)
    _, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
    assert any("index.html" in issue for issue in quality["critical_issues"])


@pytest.mark.parametrize(
    "case", ["prose", "directory", "documentation", "no_verifier", "invalid_verifier", "noncanonical"]
)
def test_pol71_equivalent_html_requires_structured_entrypoint_task(tmp_path: Path, case: str) -> None:
    payload = json.loads(_pol71_config_revision())
    task = payload["tasks"][2]
    if case == "prose":
        task["target_files"] = ["src/garden/cli/runGarden.ts"]
    elif case == "directory":
        task["target_files"] = ["src/garden/web/index.html/"]
    elif case == "documentation":
        task["target_files"] = ["docs/example.html", "README.md"]
        task["phase"] = "requirements"
    elif case == "no_verifier":
        task["verification_commands"] = []
    elif case == "invalid_verifier":
        task["verification_commands"][1]["cwd"] = "../foreign"
    else:
        task["target_files"][1] = "./src/garden/web/index.html"
    # Model cannot forge the normalizer's coverage evidence.
    task["metadata"] = {"pm_html_entrypoint_declared": True}
    directive = _pol71_input("PM_INITIAL_PROMPT.txt")
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(json.dumps(payload), directive=directive)
    _, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
    assert quality["ok"] is False
    assert any("index.html" in issue for issue in quality["critical_issues"])


def test_pol71_named_equivalent_html_with_script_and_entrypoint_is_accepted(tmp_path: Path) -> None:
    payload = json.loads(_pol71_config_revision())
    payload["tasks"][2]["target_files"][1] = "src/garden/web/garden.html"
    directive = _pol71_input("PM_INITIAL_PROMPT.txt")
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._extract_task_contracts(json.dumps(payload), directive=directive)
    _, quality = adapter._evaluate_contract_quality(contracts, directive=directive)
    assert quality["ok"] is True


_POL88_RAW_RESPONSE = '{"tasks":[{"id":"TASK-1","title":"Chief Engineer 蓝图：发光昆虫花园领域模块与入口拓扑","goal":"基于 PM 契约产出可无损执行的工程蓝图，命名具体 TypeScript 模块、公开实体类、核心行为规则（萤火虫 firefly 根据花朵 flower 情绪与月相 moon 编排灯光舞蹈，受 humidity 影响），并指定真实可执行入口与测试边界。保留 metadata：language=typescript、domain=science_creative、project_type=simulation_toy、hook=萤火虫实时灯光舞蹈。Blueprints must define ≥3 behavior rules、≥3 primary entities、≥2 edge cases、≥3 branch count、≥6 behavior symbols、sample/seed data。","scope_paths":["src","tests","scripts","index.html"],"target_files":["src/garden/types.ts","src/garden/entities.ts","src/garden/moonPhase.ts","src/garden/flowerMood.ts","src/garden/fireflyDance.ts","src/garden/seed.ts","src/garden/index.ts","src/entry/server.ts","src/entry/browser/main.ts","public/index.html","scripts/smoke.ts","tests/garden.test.ts","tsconfig.json","package.json","README.md"],"steps":["确定目录拓扑：src/garden/ 存放核心域模型、src/entry/ 存放 Node/浏览器入口、tests/ 放断言脚本、scripts/ 放 smoke 校验脚本、public/index.html 为浏览器入口页面","声明公开实体类与函数符号：Firefly、Flower、MoonPhase、Humidity、computeDancePattern、seedGarden、tickGarden，覆盖 ≥6 behavior symbols","声明 ≥3 条行为规则：(R1) Firefly.color 由 Flower.mood 决定；(R2) Firefly.beatDelay 受 MoonPhase 调制；(R3) Firefly.intensity 随 Humidity 衰减/放大，含 ≥3 分支与 ≥2 边界处理（湿度越界、月相缺失）","声明 sample seed 数据：3 朵花、5 只萤火虫、月相序列、湿度基线","声明 package.json scripts.start/build/test、tsconfig.json 严格模式、入口文件指向","输出 CE blueprint 文本作为 TASK-2 的可读契约"],"acceptance":["blueprint 命名了全部 src/garden/*.ts 与 src/entry/* 文件路径","blueprint 列出 ≥6 个 exported 符号、≥3 条规则、≥3 个 primary entities","blueprint 包含 sample seed 数据片段供 Director 复用","blueprint 显式声明 ≥3 build/test/entrypoint 验证命令的 argv 与 cwd"],"verification_commands":[],"phase":"blueprint","depends_on":[],"assigned_to":"ChiefEngineer","execution_backend":"code_edit"},{"id":"TASK-2","title":"Director 实现：发光昆虫花园核心域 + 入口 + 验证脚本","goal":"按 CE 蓝图无损落盘 TypeScript 业务源码、浏览器 HTML 入口、Node CLI smoke 入口与测试断言；萤火虫 firefly 必须真实依据 flower 情绪与 moon 月相生成灯光舞蹈并受 humidity 影响；产物覆盖 ≥3 prod files、≥120 prod lines、≥2 test assertions、≥1 test file；关键词 firefly|flower|moon|humidity 实际出现在源文件标识符或字符串中。必须区分核心行为（src/garden/*）与入口 I/O（src/entry/*、public/index.html、scripts/smoke.ts）。","scope_paths":["src","tests","scripts","public","index.html","package.json","tsconfig.json","README.md"],"target_files":["src/garden/types.ts","src/garden/entities.ts","src/garden/moonPhase.ts","src/garden/flowerMood.ts","src/garden/fireflyDance.ts","src/garden/seed.ts","src/garden/index.ts","src/entry/server.ts","src/entry/browser/main.ts","public/index.html","scripts/smoke.ts","tests/garden.test.ts","tsconfig.json","package.json","README.md"],"steps":["初始化 package.json（name、scripts.start=node scripts/smoke.ts、scripts.build=tsc -p .、scripts.test=node --test --import tsx tests/garden.test.ts 或 ts-node 等价命令、scripts.typecheck=tsc --noEmit）与 tsconfig.json（strict、target ES2022、module NodeNext/outDir dist、include src scripts tests）","实现 src/garden/types.ts：定义 FlowerMood、MoonPhase、Humidity 枚举/字面量类型与 FireflyDanceStep 接口","实现 src/garden/entities.ts：Firefly 类（id、position、color、beatDelay、intensity）、Flower 类（id、position、mood、resonance）、Garden 容器持有 fireflies/flowers/moon/humidity","实现 src/garden/moonPhase.ts：基于 tick 推进月相（新月至满月循环），输出 phase 0..1；边界处理 phase<0 抛错","实现 src/garden/flowerMood.ts：根据 seed 与时间计算 Flower.mood（emotional valence），湿度越界夹紧处理","实现 src/garden/fireflyDance.ts：computeDancePattern(garden, tick) 返回 FireflyDanceStep[]，颜色花心+月相节拍+湿度衰减三轴合成；至少 3 分支","实现 src/garden/seed.ts：导出 SEED_GARDEN 常量（≥3 朵花、≥5 只萤火虫、月相初值、湿度基线）","实现 src/garden/index.ts：聚合导出，tickGarden(garden, steps) 主循环","实现 src/entry/browser/main.ts：引用 core，驱动 requestAnimationFrame 渲染 FireflyDanceStep 到 Canvas2D","实现 public/index.html（含 ）作为浏览器入口","实现 src/entry/server.ts（可选 Node 导出）与 scripts/smoke.ts：加载 seed，运行 tickGarden 60 步，打印每只 fireflyDance 的颜色/节拍/强度摘要并断言关键词","实现 tests/garden.test.ts：使用 node:test + assert，含正常用例（满月+高湿度+快乐花 → 高强度暖色快节拍）、边界用例（湿度=0 → 强度下限、新月 → beatDelay 拉长）、非法用例（humidity 越界抛错）至少 2 条断言","撰写 README.md：how to run（npm install、npm run typecheck、npm run test、npm run build、npm run start、浏览器打开 public/index.html）","确认产物实际包含 firefly/flower/moon/humidity 关键词于标识符或字符串"],"acceptance":["工作区根存在真实代码文件 ts/tsx/ts、package.json、tsconfig.json、public/index.html、README.md，非纯描述","package.json scripts.start/build/test/typecheck 全部为可执行命令，argv 实际跑产品入口或核心规则","tsc --noEmit 退出码 0（typecheck 命令将通过）","src/garden/* 与 src/entry/*、public/index.html、scripts/smoke.ts、tests/garden.test.ts 全部落盘且 ≥3 prod files / ≥120 prod lines / ≥1 test file / ≥2 test assertions","FireflyDance 真实依赖 Flower.mood、MoonPhase、Humidity 三轴，非占位","firefly/flower/moon/humidity 至少一个关键词出现于 src/**/*.ts 的标识符或字符串","核心行为位于 src/garden/*，入口 I/O 位于 src/entry/* 与 public/index.html，互不混杂"],"verification_commands":[{"modality":"environment_prep","argv":["npm","install"],"cwd":"."},{"modality":"build","argv":["npx","tsc","-p","."],"cwd":"."},{"modality":"lint","argv":["npx","tsc","--noEmit"],"cwd":"."},{"modality":"test","argv":["npx","tsx","tests/garden.test.ts"],"cwd":"."},{"modality":"entrypoint","argv":["npx","tsx","scripts/smoke.ts"],"cwd":"."}],"phase":"implementation","depends_on":["TASK-1"],"assigned_to":"Director","execution_backend":"code_edit","generated_outputs":[{"path":"dist/garden/index.js","inputs":[{"task_id":"TASK-2","path":"src/garden/index.ts"}],"build":{"argv":["npx","tsc","-p","."],"cwd":"."}},{"path":"dist/entry/browser/main.js","inputs":[{"task_id":"TASK-2","path":"src/entry/browser/main.ts"}],"build":{"argv":["npx","tsc","-p","."],"cwd":"."}},{"path":"dist/entry/server.js","inputs":[{"task_id":"TASK-2","path":"src/entry/server.ts"}],"build":{"argv":["npx","tsc","-p","."],"cwd":"."}}]},{"id":"TASK-3","title":"QA 闭环：原生校验 deterministic checks + level-1 阈值","goal":"作为独立 QA 闭环，重跑 TASK-2 的 verification_commands 与 bench level-1 deterministic checks，确保 ts_syntax、package_scripts、min_files≥3、content_any(firefly|flower|moon|humidity)、source_target_coverage src/**/*.ts 全部通过；断言 firefly 灯光舞蹈由 flower 情绪与 moon 月相驱动且受 humidity 影响；输出 pass/fail 报告覆盖正常/边界/非法输入三类断言结果。","scope_paths":["src","tests","scripts","public","index.html","package.json","tsconfig.json","README.md"],"target_files":["reports/qa-report.json"],"steps":["复读 TASK-2 产生的源码静态确认 firefly/flower/moon/humidity 真实使用而非仅注释堆砌","执行 npx tsc --noEmit 收集 typecheck 退出码","执行 npx tsx tests/garden.test.ts 收集 node:test 输出并计算通过断言数 ≥2","执行 npx tsx scripts/smoke.ts 确认入口输出含 flower/mood/moon/phase/humidity/firefly 关键词摘要","执行 npm run build 等价 npx tsc -p . 确认 dist 产物","校验 source_target_coverage src/**/*.ts：实际存在 .ts 文件数 ≥3 且 min_prod_lines ≥120","撰写 reports/qa-report.json：每条 deterministic check 与 bench level-1 阈值给出 pass=true/false 与证据"],"acceptance":["ts_syntax pass：tsc --noEmit 退出码 0","package_scripts pass：start/build/test/typecheck 均可执行且非占位","min_files pass：prod .ts 文件数 ≥3","content_any pass：src/**/*.ts 真实使用 firefly 或 flower 或 moon 或 humidity","source_target_coverage pass：src/**/*.ts 至少 3 个文件被实际产物覆盖","bench level-1 全部 minimums（behavior_rules≥3、behavior_symbols≥6、branch_count≥3、edge_cases≥2、primary_entities≥3、prod_files≥3、prod_lines≥120、test_assertions≥2、test_files≥1）达标","reports/qa-report.json 落盘"],"verification_commands":[{"modality":"environment_prep","argv":["npm","install"],"cwd":"."},{"modality":"lint","argv":["npx","tsc","--noEmit"],"cwd":"."},{"modality":"test","argv":["npx","tsx","tests/garden.test.ts"],"cwd":"."},{"modality":"entrypoint","argv":["npx","tsx","scripts/smoke.ts"],"cwd":"."},{"modality":"build","argv":["npx","tsc","-p","."],"cwd":"."}],"phase":"qa","depends_on":["TASK-2"],"assigned_to":"QA","execution_backend":"code_edit"}]}'
_POL88_DIRECTIVE = "请基于 Architect 阶段产物生成 PM 执行任务合同。任务必须覆盖需求、实现、验证、QA 闭环；每个任务必须包含 goal、steps、acceptance、depends_on 与真实 scope_paths/target_files；保留独特约束，可省略重复 scope/description。先交 Chief Engineer 形成蓝图，再由 Director 执行。\n\n## Original Requirement (complete)\n# Product Requirements — 发光昆虫花园模拟器\n\n## Goal\n- 用 TypeScript 实现「发光昆虫花园模拟器」。创意钩子: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。\n\n\n## Project Metadata\n- 主语言: typescript\n- 领域: science_creative\n- 项目类型: simulation_toy\n- 创意钩子: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈\n- PM -> Chief Engineer -> Director -> QA 必须在任务合同中保留这些元数据字段, 确保目标语义不丢失。\n\n## Acceptance Criteria\n- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。\n- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件; API 项目提供可启动服务入口和健康检查说明。\n- package.json 脚本不得是只检查 manifest 的占位脚本; build/test/start 或等价脚本必须实际运行产品入口或核心规则验证。\n- 附 README.md 说明如何运行。\n- 关键验收维度: 萤火虫根据花朵情绪和月相组成实时灯光舞蹈; 同时验证 TypeScript 产物结构、入口可运行性和核心领域规则。。\n\n## Deterministic Checks\nPM -> Chief Engineer -> Director -> QA 必须把以下检查转成任务目标和验收标准, 缺失任一项应视为未完成:\n- ts_syntax\n- package_scripts\n- min_files:3\n- content_any:firefly|flower|moon|humidity\n- source_target_coverage:src/**/*.ts\n\n## Bench Level Contract (Mandatory)\n- schema_version: factory-bench.level_contract.v1\n- level: 1\n\nRequired minimums:\n- min_behavior_rules: 3\n- min_behavior_symbols: 6\n- min_branch_count: 3\n- min_edge_cases: 2\n- min_primary_entities: 3\n- min_prod_files: 3\n- min_prod_lines: 120\n- min_test_assertions: 2\n- min_test_files: 1\n\nRequired evidence:\n- PM task contract carries product-specific behavior rules and sample data\n- Chief Engineer blueprint names concrete modules, public contracts, and gate commands\n- Director implementation separates core behavior from entrypoint/I/O\n- Tests assert business results across normal, boundary, and invalid inputs\n- Factory audit implementation_depth passes the level-specific thresholds\n\nAnti-hollow delivery rules:\n- Do not satisfy content checks by keyword stuffing, comments, README text, or static placeholder output\n- Do not pass tests that only check files exist, scripts exist, or keywords appear\n- Do not let runtime hard gates override low QA score or major quality findings\n\n\n## Source Tree Structure Contract (MANDATORY)\nPolaris 不规定唯一目录范式。**目录拓扑由 Chief Engineer 决定**：\nCE 蓝图必须命名具体模块、公开文件与入口；Director 只能无损执行该拓扑。\n禁止 PM/Director/QA/bench 把 `src/models/` + `src/engine/` 写成唯一合法结构。\n仅生成 package.json / tsconfig.json / CMakeLists.txt 等脚手架不算完成。\n- 必须有 TypeScript 业务源码（`.ts`/`.tsx`），路径由 CE 决定。\n- 必须有可运行入口（Node CLI、浏览器 HTML+脚本，或 CE 声明的等价入口）。\n- 必须有至少一个可执行测试/检查文件。\n- `package.json` / `tsconfig.json` 必须指向 CE 选定的真实源码根。\n- simulation/game/interactive 必须有真实场景/渲染/交互源码；文件名与目录由 CE 决定，不强制 `src/engine/renderer.ts`。\n\n**重要**: Director `target_files` 必须覆盖 CE 蓝图声明的业务源码与入口，不能只包含配置/脚手架。CE 选定的路径即权威。\n\n\n## Feature Keywords Contract (MANDATORY)\n以下关键词必须出现在生成的源代码文件中(变量名、类名、注释或字符串均可): **firefly, flower, moon, humidity**\nPM -> Chief Engineer -> Director 的任务目标和验收标准必须包含这些关键词。\nDirector 的 target_files 中的源文件必须至少包含其中一个关键词的实际使用。\n\n## Language-Specific Runnable Contract (TypeScript)\n- 必须包含 `package.json` 且定义 `scripts.start` / `scripts.build` 脚本。\n- `npm install && npm run build` 必须成功。\n- 必须包含 `tsconfig.json`。\n- `tsc --noEmit` 必须通过。\n\n"
_POL88_REVISION_RESPONSE = '{"schema_version":"pm.task_revision.v1","base_hash":"0b51a2123f4383ccbaa549b92a18ab4e2834ca887894b6dddce9255185a63820","updates":[{"task_id":"TASK-2","set":{"generated_outputs":[{"path":"dist/garden/index.js","inputs":[{"task_id":"TASK-2","path":"src/garden/index.ts"}],"build":{"argv":["npx","tsc","-p","."],"cwd":"."}},{"path":"dist/entry/browser/main.js","inputs":[{"task_id":"TASK-2","path":"src/entry/browser/main.ts"}],"build":{"argv":["npx","tsc","-p","."],"cwd":"."}},{"path":"dist/entry/server.js","inputs":[{"task_id":"TASK-2","path":"src/entry/server.ts"}],"build":{"argv":["npx","tsc","-p","."],"cwd":"."}}]}},{"task_id":"TASK-3","append":{"steps":["bind generated_outputs of TASK-2 only","verify dist artifacts exist via build re-run","run scripts/smoke.ts and tests/garden.test.ts via npm scripts","validate tsc --noEmit, package_scripts, min_files>=3, content keywords, source_target_coverage","collect QA evidence for behavior rules and report failures"],"acceptance":["TASK-3 generated_outputs reference only TASK-2 artifacts","build argv equals its typed build verifier","inputs bind self/ancestor authored targets","no new write grants introduced","evidence recorded for verification"]}}],"add_tasks":[],"remove_task_ids":[]}'


def test_pol88_captured_parent_conserves_case_sensitive_sources_and_nested_html(tmp_path: Path) -> None:
    from copy import deepcopy

    from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_artifact_contracts

    adapter = PMAdapter(workspace=str(tmp_path))
    candidate = json.loads(_POL88_RAW_RESPONSE)
    # Semantic parent/output conservation is tested independently of the captured
    # CE task's duplicate write claims. The captured response stays immutable.
    candidate["tasks"][0]["target_files"] = ["docs/blueprint/architecture.md"]
    candidate["tasks"][0]["scope_paths"] = ["docs/blueprint"]
    candidate_text = json.dumps(candidate, ensure_ascii=False)
    contracts = adapter._extract_task_contracts(candidate_text, directive=_POL88_DIRECTIVE)
    before = deepcopy(contracts)
    assert len(contracts) == 3
    assert len(_POL88_DIRECTIVE) == 3235
    assert not validate_pm_artifact_contracts(contracts, primary_language="typescript").diagnostics
    normalized, quality = adapter._evaluate_contract_quality(contracts, directive=_POL88_DIRECTIVE)
    assert contracts == before
    assert [task["id"] for task in normalized] == ["TASK-1", "TASK-2", "TASK-3"]
    records = [(task, row) for task in normalized for row in task.get("generated_outputs", [])]
    assert [(task["id"], row["path"]) for task, row in records] == [
        ("TASK-2", "dist/garden/index.js"),
        ("TASK-2", "dist/entry/browser/main.js"),
        ("TASK-2", "dist/entry/server.js"),
    ]
    original_outputs = json.loads(_POL88_RAW_RESPONSE)["tasks"][1]["generated_outputs"]
    for task, row in records:
        original = next(value for value in original_outputs if value["path"] == row["path"])
        assert row["build"] == original["build"]
        assert [item["path"] for item in row["inputs"]] == [item["path"] for item in original["inputs"]]
        assert all(item["task_id"] == task["id"] for item in row["inputs"])
        assert {"modality": "build", **row["build"]} in task["verification_commands"]
    assert not validate_pm_artifact_contracts(normalized, primary_language="typescript").diagnostics
    assert "src/garden/moonPhase.ts" in {p for task in normalized for p in task["target_files"]}
    parent = normalized[1]
    for field in ("goal", "target_files", "verification_commands", "depends_on"):
        assert parent[field] == before[1][field]
    assert parent["acceptance_criteria"][:len(before[1]["acceptance_criteria"])] == before[1]["acceptance_criteria"]
    assert normalized[2]["depends_on"] == ["TASK-2"]
    assert "task_fission" not in quality
    assert quality["ok"] is True
    assert quality["critical_issues"] == []
    assert adapter.task_runtime.list_observable_task_rows() == []


def test_pol88_actual_noop_plus_task3_append_remains_atomic_rejection(tmp_path: Path) -> None:
    from copy import deepcopy

    from polaris.cells.roles.adapters.internal.pm.revision import apply_revision, make_revision_base

    # Replay the former localized output manifest separately from the new
    # mixed-HTML full-answer decision. TASK-3 never receives output edit authority.
    diagnostic = {
        "code": "generated_output_input_invalid",
        "path": "$.tasks[1].generated_outputs",
        "task_id": "TASK-2-entrypoints",
        "message": "Inputs must bind self/ancestor authored targets",
        "provenance": {
            "original_task_id": "TASK-2",
            "derived_task_id": "TASK-2-entrypoints",
            "stage": "quality_autofix",
        },
    }
    quality = {"critical_issues": ["generated_output_input_invalid"], "contract_diagnostics": [diagnostic]}
    base = make_revision_base(
        _POL88_RAW_RESPONSE,
        workspace=str(tmp_path),
        run_id="captured",
        task_id="pm",
        directive=_POL88_DIRECTIVE,
        quality=quality,
    )
    assert base is not None
    before = deepcopy(base.tasks)
    patch = json.loads(_POL88_REVISION_RESPONSE)
    assert patch["updates"][0]["set"]["generated_outputs"] == before[1]["generated_outputs"]
    assert patch["updates"][1]["task_id"] == "TASK-3"
    assert set(patch["updates"][1]["append"]) == {"steps", "acceptance"}
    patch["base_hash"] = base.base_hash
    with pytest.raises(Exception, match=r"TASK-3.*not server-authorized"):
        apply_revision(base, json.dumps(patch, ensure_ascii=False))
    assert base.tasks == before
    assert set(base.view["edit_manifest"]["tasks"]) == {"TASK-2"}


async def test_pol88_captured_html_admitted_without_unnecessary_full_reanswer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    port, _, _, _ = _authority(tmp_path=tmp_path, stage="pm_planning", resolver=_Resolver(_resolved_cut("pm")))
    workspace = tmp_path / "workspace"
    runtime, freezes, wires = _local_runtime(
        workspace,
        monkeypatch,
        [
            {"role": "assistant", "content": _POL88_RAW_RESPONSE},
        ],
    )
    # Match a full-request model window for this captured replay. Existing
    # small-window refusal and 8000-token revision tests remain unchanged.
    registry = runtime._kernels[str(workspace)].registry
    profile = registry.get_profile_or_raise("pm")
    registry.register(replace(profile, context_policy=replace(profile.context_policy, max_context_tokens=1000000)))
    _native_fake_capacity(monkeypatch)
    adapter = PMAdapter(workspace=str(workspace))
    with bind_factory_role_evidence_authority(port.mint_authority_binding("pm")):
        result = await adapter._run_pm_stage(
            "pm-task", _POL88_DIRECTIVE, {}, {"run_id": "pm-pol88-captured", "timeout_seconds": 43}
        )
    assert len(wires) == len(freezes) == len(runtime._test_commands) == 1
    assert all(wire["max_tokens"] == 60000 for wire in wires)
    assert {freeze.identity.run_id for freeze in freezes} == {"pm-pol88-captured"}
    for wire in wires:
        assert _POL88_DIRECTIVE in "\n".join(message.get("content", "") for message in wire["messages"])
        assert {tool["function"]["name"] for tool in wire["tools"]} == {"repo_tree", "file_exists"}
        assert wire["tool_choice"] == "auto"
    assert result["success"] is True
    assert result["tasks_created"] > 0
    assert adapter.task_runtime.list_observable_task_rows()
    snapshots = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (workspace / ".polaris/runtime/contexts").glob("*/*")
        if path.is_file() and len(path.name) == 24
    ]
    for freeze, wire in zip(freezes, wires, strict=True):
        request = next(
            snapshot["provider_request"]
            for snapshot in snapshots
            if "final_physical_request" in snapshot.get("provider_request", {})
            and snapshot.get("provider_request", {}).get("factory_final_request", {}).get("final_semantic_request_hash")
            == freeze.final_semantic_request_hash
        )
        assert request["final_physical_request"]["body"] == wire
        assert request["final_physical_request"]["transport"]["timeout"] == [10.0, 43.0]
        audit = request["final_request_context_audit"]
        assert audit["final_request_token_estimate"] >= audit["message_token_estimate"]
        assert audit["final_request_evidence_coverage"]["role_identity_ok"] is True
        assert audit["final_request_evidence_coverage"]["pass"] is True
        semantic = request["factory_final_request"]["canonical_final_payload"]
        assert semantic["role"] == "pm"
        assert "**PM**" in semantic["messages"][0]["content"]


def test_normalization_keeps_codegen_inputs_bound_to_authored_parent(tmp_path: Path) -> None:
    from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_artifact_contracts
    from polaris.cells.orchestration.pm_planning.tests.test_task_quality_gateb import _generated_fission_contract

    contracts = _generated_fission_contract()["tasks"]
    build = {"argv": ["npx", "tsc", "src/models/item.ts", "--outDir", "dist"], "cwd": "."}
    contracts.append(
        {
            "id": "CODEGEN",
            "title": "Compile typed source into external output",
            "goal": "Compile existing typed source",
            "phase": "implementation",
            "assigned_to": "Director",
            "depends_on": ["BUILD"],
            "target_files": ["scripts/compile.ts"],
            "verification_commands": [{"modality": "build", **build}],
            "generated_outputs": [
                {
                    "path": "dist/external.js",
                    "inputs": [{"task_id": "BUILD", "path": "src/models/item.ts"}],
                    "build": build,
                }
            ],
        }
    )
    normalized, quality = PMAdapter(workspace=str(tmp_path))._evaluate_contract_quality(
        contracts, directive="TypeScript modules"
    )
    consumer = next(task for task in normalized if task["id"] == "CODEGEN")
    assert consumer["generated_outputs"][0]["inputs"] == [
        {"task_id": "BUILD", "path": "src/models/item.ts"}
    ]
    assert consumer["generated_outputs"][0]["build"] == build
    assert contracts[-1]["generated_outputs"][0]["inputs"][0]["task_id"] == "BUILD"
    assert [task["id"] for task in normalized] == ["BUILD", "CODEGEN"]
    assert consumer["depends_on"] == ["BUILD"]
    assert "task_fission" not in quality
    assert not validate_pm_artifact_contracts(normalized, primary_language="typescript").diagnostics


class _MetadataCaptureRuntime:
    """Capture the real board-task assembly without a database or effects."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.creation_payloads: list[dict[str, Any]] = []

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        return self.rows

    def create_task_row(self, *, subject: str, description: str, metadata: dict[str, Any]) -> dict[str, Any]:
        from copy import deepcopy

        row = {"id": len(self.rows) + 1, "subject": subject, "description": description, "metadata": deepcopy(metadata)}
        self.creation_payloads.append(deepcopy(row))
        self.rows.append(row)
        return row

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        return next((row for row in self.rows if row["id"] == task_id), None)

    def task_exists(self, task_id: int) -> bool:
        return self.get_task(task_id) is not None

    def update_task_row(self, task_id: int, *, blocked_by: list[int], metadata: dict[str, Any]) -> dict[str, Any]:
        row = self.get_task(task_id)
        assert row is not None
        row["blocked_by"] = blocked_by
        row["metadata"].update(metadata)
        return row


def _metadata_capture_adapter(tmp_path: Path) -> tuple[PMAdapter, _MetadataCaptureRuntime]:
    # Keep every parsing, validation, dedup and metadata-assembly method real.
    # Only replace the TaskRuntime effect port; avoid adapter bootstrap writes.
    adapter = object.__new__(PMAdapter)
    adapter.workspace = str(tmp_path)
    runtime = _MetadataCaptureRuntime()
    adapter._task_runtime = runtime
    return adapter, runtime


@pytest.mark.parametrize("html_entrypoint", [False, True])
def test_pol88_actual_task_rows_conserve_validated_output_metadata(tmp_path: Path, html_entrypoint: bool) -> None:
    from copy import deepcopy

    adapter, runtime = _metadata_capture_adapter(tmp_path)
    proposal = json.loads(_POL88_RAW_RESPONSE)
    proposal["tasks"][0]["target_files"] = ["docs/blueprint/architecture.md"]
    proposal["tasks"][0]["scope_paths"] = ["docs/blueprint"]
    proposal["tasks"][1]["metadata"] = {"unrelated": {"note": "retain raw model metadata"}}
    if html_entrypoint:
        proposal["tasks"][1]["target_files"].append("index.html")
    before = deepcopy(proposal)
    contracts = adapter._extract_task_contracts(json.dumps(proposal), directive=_POL88_DIRECTIVE)
    normalized, quality = adapter._evaluate_contract_quality(contracts, directive=_POL88_DIRECTIVE)
    assert quality["ok"] is True
    assert quality["critical_issues"] == []
    # This captures assembly only, even for the independently blocked candidate;
    # it is neither an admission test nor permission to create real task rows.
    rows = adapter._create_board_tasks(normalized)
    records = [
        (row["metadata"]["task_id"], output)
        for row in runtime.creation_payloads
        for output in row["metadata"].get("generated_outputs", [])
    ]
    assert records == [
        (
            "TASK-2",
            {
                "path": "dist/garden/index.js",
                "inputs": [{"task_id": "TASK-2", "path": "src/garden/index.ts"}],
                "build": {"argv": ["npx", "tsc", "-p", "."], "cwd": "."},
            },
        ),
        (
            "TASK-2",
            {
                "path": "dist/entry/browser/main.js",
                "inputs": [{"task_id": "TASK-2", "path": "src/entry/browser/main.ts"}],
                "build": {"argv": ["npx", "tsc", "-p", "."], "cwd": "."},
            },
        ),
        (
            "TASK-2",
            {
                "path": "dist/entry/server.js",
                "inputs": [{"task_id": "TASK-2", "path": "src/entry/server.ts"}],
                "build": {"argv": ["npx", "tsc", "-p", "."], "cwd": "."},
            },
        ),
    ]
    assert len(rows) == len(normalized) == len(runtime.creation_payloads) == 3
    for task, row in zip(normalized, runtime.creation_payloads, strict=True):
        metadata = row["metadata"]
        if task["id"] == "TASK-2":
            assert metadata["generated_outputs"] == task["generated_outputs"]
            assert metadata["generated_outputs"] is not task["generated_outputs"]
        else:
            assert "generated_outputs" not in metadata
            assert "generated_outputs" not in task["metadata"]
        assert metadata["target_files"] == task["target_files"]
        assert metadata["scope_paths"] == task["scope_paths"]
        assert metadata["assigned_to"] == task["assigned_to"]
        assert not any(path.startswith("dist/") for path in metadata["scope_paths"])
        if task["id"] == "TASK-2":
            assert metadata["unrelated"] == {"note": "retain raw model metadata"}
    assert proposal == before


def test_actual_task_row_metadata_preserves_external_codegen_input(tmp_path: Path) -> None:
    from copy import deepcopy

    from polaris.cells.orchestration.pm_planning.tests.test_task_quality_gateb import _generated_fission_contract

    adapter, runtime = _metadata_capture_adapter(tmp_path)
    proposal = _generated_fission_contract()
    proposal["tasks"].append(
        {
            "id": "CODEGEN",
            "title": "Compile external input",
            "target_files": ["scripts/compile.ts"],
            "depends_on": ["BUILD"],
            "assigned_to": "Director",
            "phase": "implementation",
            "verification_commands": [{"modality": "build", "argv": ["npx", "tsc", "src/models/item.ts"], "cwd": "."}],
            "generated_outputs": [
                {
                    "path": "dist/external.js",
                    "inputs": [{"task_id": "BUILD", "path": "src/models/item.ts"}],
                    "build": {"argv": ["npx", "tsc", "src/models/item.ts"], "cwd": "."},
                }
            ],
            "metadata": {"unrelated": ["consumer"]},
        }
    )
    before = deepcopy(proposal)
    contracts = adapter._extract_task_contracts(json.dumps(proposal), directive="TypeScript modules")
    normalized, _quality = adapter._evaluate_contract_quality(contracts, directive="TypeScript modules")
    adapter._create_board_tasks(normalized)
    metadata = next(row["metadata"] for row in runtime.creation_payloads if row["metadata"]["task_id"] == "CODEGEN")
    assert metadata["generated_outputs"] == [
        {
            "path": "dist/external.js",
            "inputs": [{"task_id": "BUILD", "path": "src/models/item.ts"}],
            "build": {"argv": ["npx", "tsc", "src/models/item.ts"], "cwd": "."},
        }
    ]
    assert metadata["depends_on_external"] == ["BUILD"]
    assert metadata["unrelated"] == ["consumer"]
    assert proposal == before


@pytest.mark.parametrize("declaration", ["absent", "empty", "valid", "mixed", "missing_build", "missing_input"])
def test_output_metadata_is_derived_only_after_whole_graph_validation(tmp_path: Path, declaration: str) -> None:
    from copy import deepcopy

    adapter, runtime = _metadata_capture_adapter(tmp_path)
    raw: dict[str, Any] = {
        "id": "BUILD",
        "title": "Compile typed model",
        "target_files": ["src/model.ts"],
        "verification_commands": [{"modality": "build", "argv": ["npx", "tsc"], "cwd": "."}],
        "metadata": {
            "generated_outputs": [{"path": "dist/forged.js"}],
            "generated_outputs_trusted": True,
            "unrelated": {"opaque": [1, 2]},
        },
    }
    if declaration != "absent":
        raw["generated_outputs"] = (
            []
            if declaration == "empty"
            else [
                {
                    "path": "dist/model.js",
                    "inputs": [{"task_id": "BUILD", "path": "src/model.ts"}],
                    "build": {"argv": ["npx", "tsc"], "cwd": "."},
                }
            ]
        )
    if declaration == "mixed":
        raw["generated_outputs"].append({"path": "dist/broken.js"})
    elif declaration == "missing_build":
        raw["verification_commands"] = []
    elif declaration == "missing_input":
        raw["generated_outputs"][0]["inputs"][0]["path"] = "src/absent.ts"
    before = deepcopy(raw)
    contracts = adapter._extract_task_contracts(json.dumps({"tasks": [raw]}), directive="TypeScript modules")
    if declaration == "mixed":
        assert contracts == []
        assert runtime.creation_payloads == []
    else:
        assert "generated_outputs" not in contracts[0]["metadata"]
        normalized, quality = adapter._evaluate_contract_quality(contracts, directive="TypeScript modules")
        metadata = normalized[0]["metadata"]
        if declaration in {"valid", "empty"}:
            assert metadata["generated_outputs"] == raw["generated_outputs"]
            assert metadata["generated_outputs"] is not raw["generated_outputs"]
            assert not quality.get("contract_diagnostics")
            adapter._create_board_tasks(normalized)
            assert runtime.creation_payloads[0]["metadata"]["generated_outputs"] == raw["generated_outputs"]
        else:
            assert "generated_outputs" not in metadata
            if declaration != "absent":
                assert quality["ok"] is False
                assert quality["contract_diagnostics"]
            assert runtime.creation_payloads == []
        assert metadata["unrelated"] == {"opaque": [1, 2]}
        assert normalized[0]["target_files"] == ["src/model.ts"]
    assert raw == before

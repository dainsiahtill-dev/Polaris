"""Prepared physical requests retain full source without enlarging the goal."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import build_final_request_context_audit_for_request
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import (
    LLMRequestPreparer,
    _ensure_current_source_evidence_message_bound,
)
from polaris.cells.roles.kernel.tests.test_prompt_builder_cache import _make_profile
from polaris.kernelone.llm.engine.model_catalog import ModelCatalog
from polaris.kernelone.llm.engine.provider_native_request import project_factory_provider_native_request


@pytest.mark.asyncio
async def test_real_preparer_native_body_keeps_all_source_content_and_hashes(tmp_path, monkeypatch) -> None:
    profile = _make_profile(template_id="qa", role_id="qa")
    profile.provider_id = "private_qa"
    profile.provider_type = "anthropic_compat"
    profile.model = "private_model"
    catalog = ModelCatalog(workspace=str(tmp_path))
    monkeypatch.setattr(
        catalog,
        "_load_llm_config_payload",
        lambda: {
            "providers": {
                "private_qa": {"type": "anthropic_compat", "max_context_tokens": 32768, "max_output_tokens": 4096}
            }
        },
    )
    goal = "Independently verify the original required behavior."
    records = []
    for index in range(8):
        text = f"# 原始源码 {index}\n" + f"VALUE_{index} = 7\n" * 400
        records.append(
            {
                "path": f"module_{index}.py",
                "status": "readable",
                "content": text,
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "detail": "",
            }
        )
    override = {
        "context_evidence_slots": {"current_source": {"files": records, "source_drift": []}},
        "_transaction_kernel_prebuilt_messages": [
            {"role": "system", "content": "You are QA. Independently verify requirements."},
            {"role": "user", "content": goal},
        ],
    }
    preparer = LLMRequestPreparer(workspace=str(tmp_path))
    preparer._model_catalog = catalog
    prepared = await preparer._prepare_llm_request(
        profile=profile,
        system_prompt="You are QA. Independently verify requirements.",
        context=SimpleNamespace(message=goal, domain="code", context_override=override),
        temperature=0.0,
        max_tokens=1000,
        stream=False,
    )
    native = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload={
            "model": profile.model,
            "messages": prepared.messages,
            "tools": prepared.request_options.get("tools") or [],
            "tool_choice": prepared.request_options.get("tool_choice"),
            "response_format": prepared.request_options.get("response_format"),
            "temperature": prepared.request_options["temperature"],
            "max_tokens": prepared.request_options["max_tokens"],
            "stream": False,
        },
        provider_config={"base_url": "https://provider.test/anthropic"},
    )
    assert native is not None
    body = json.loads(native.expected_body_json)
    messages = body["messages"]
    assert goal in str(messages[-1]["content"])
    rendered = "\n".join(
        message["content"]
        if isinstance(message["content"], str)
        else "\n".join(block.get("text", "") for block in message["content"])
        for message in messages
    )
    for record in records:
        assert record["content_sha256"] in rendered
        assert json.dumps(record["content"], ensure_ascii=False)[1:-1] in rendered
    audit = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request, prepared=prepared, profile=profile
    )
    assert audit["final_request_token_estimate"] > 8000
    assert audit["context_window_tokens"] >= audit["final_request_token_estimate"]
    assert audit["context_window_tokens"] == catalog.resolve(profile.provider_id, profile.model).max_context_tokens


def _source_slot() -> dict:
    content = "# 当前源码\nVALUE = 7\n"
    return {
        "files": [
            {
                "path": "module.py",
                "status": "readable",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        ],
        "source_drift": [],
    }


@pytest.mark.parametrize("tamper", ["hash", "duplicate", "files"])
def test_source_observation_integrity_is_checked_before_binding(tamper: str) -> None:
    source = _source_slot()
    if tamper == "hash":
        source["files"][0]["content"] += "CHANGED = True\n"
    elif tamper == "duplicate":
        source["files"].append(dict(source["files"][0]))
    else:
        source["files"] = "not records"
    with pytest.raises(ValueError, match="current_source_evidence"):
        _ensure_current_source_evidence_message_bound(
            [{"role": "user", "content": "Review requirements"}],
            {"context_evidence_slots": {"current_source": source}},
        )


def test_current_source_replaces_stale_named_message_and_is_idempotent() -> None:
    source = _source_slot()
    override = {"context_evidence_slots": {"current_source": source}}
    messages = [
        {"role": "system", "content": "You are QA"},
        {"role": "assistant", "name": "current_source_evidence", "content": "STALE_SOURCE"},
        {"role": "user", "content": "Review requirements"},
    ]
    bound = _ensure_current_source_evidence_message_bound(messages, override)
    assert not any(message["content"] == "STALE_SOURCE" for message in bound)
    source_messages = [message for message in bound if message.get("name") == "current_source_evidence"]
    assert len(source_messages) == 1
    assert source_messages[0]["role"] == "user"
    assert bound[-1] == messages[-1]
    assert _ensure_current_source_evidence_message_bound(bound, override) == bound


def test_drift_remains_explicitly_incomplete_source_observation() -> None:
    source = _source_slot()
    source["source_drift"] = ["module.py changed after snapshot"]
    bound = _ensure_current_source_evidence_message_bound(
        [{"role": "user", "content": "Review requirements"}],
        {"context_evidence_slots": {"current_source": source}},
    )
    text = bound[0]["content"]
    assert "Source observations complete: false" in text
    assert source["source_drift"][0] in text
    assert source["files"][0]["content_sha256"] in text
    assert "execution authority" in text


def test_non_qa_request_without_source_slot_is_unchanged() -> None:
    messages = [
        {"role": "system", "content": "You are Director"},
        {"role": "user", "content": "Implement the Chief Engineer handoff"},
    ]
    assert _ensure_current_source_evidence_message_bound(messages, {"pm_contract": {"task": "TASK-1"}}) == messages


@pytest.mark.parametrize("tamper", ["none", "body_absent", "ce_hash", "receipts_empty", "wrong_argv"])
def test_final_body_is_required_even_when_old_metadata_and_messages_remain(tamper: str) -> None:
    blueprint = {
        "schema_version": "chief_engineer.blueprint.v1",
        "blueprint_hash": "e" * 64,
        "module_interface_contract": {"schema_version": "module.interface.v1", "modules": [{"path": "entry.js"}]},
        "execution_checklist": ["Check exact behavior"],
        "architecture_decisions": ["Separate input"],
    }
    ce = {
        "schema_version": "chief_engineer.blueprint_context.v1",
        "generated_blueprints": ["private-bp"],
        "blueprints": [blueprint],
        "source_observations": [
            {
                "model_payload_sha256": hashlib.sha256(
                    json.dumps(blueprint, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest(),
                "provenance": {"embedded_blueprint_hash": "e" * 64, "recomputed_blueprint_hash": "e" * 64},
            }
        ],
    }
    receipt = {
        "command": ["node", "entry.js"],
        "exit_code": 1,
        "verification_group": {
            "input_hash": "f" * 64,
            "commands": [
                {
                    "argv": ["/usr/bin/node", "entry.js"],
                    "exit_code": 1,
                    "input_hash": "f" * 64,
                    "output_hash": "c" * 64,
                    "process_owner_terminal": True,
                }
            ],
        },
    }
    slots = {
        "current_source": _source_slot(),
        "ce_blueprint": ce,
        "verifier_receipts": [receipt],
        "workspace_quality": {
            "schema_version": "factory.workspace_quality_checks.v1",
            "source": "factory_stage_executor",
            "commands": [receipt],
        },
    }
    old_messages = _ensure_current_source_evidence_message_bound(
        [{"role": "system", "content": "You are QA"}, {"role": "user", "content": "Review original scope"}],
        {"context_evidence_slots": slots},
    )
    actual = deepcopy(old_messages)
    current_slots = deepcopy(slots)
    if tamper == "body_absent":
        actual = [message for message in actual if message.get("name") != "current_source_evidence"]
    elif tamper != "none":
        if tamper == "ce_hash":
            current_slots["ce_blueprint"]["blueprints"][0]["execution_checklist"] = ["Changed requirement"]
        elif tamper == "receipts_empty":
            current_slots["verifier_receipts"] = []
        else:
            current_slots["verifier_receipts"][0]["command"] = ["node", "foreign.js"]
        actual = _ensure_current_source_evidence_message_bound(old_messages, {"context_evidence_slots": current_slots})
    # Provider-normalized Anthropic user content loses `name` and becomes blocks.
    actual = [
        {"role": "system", "content": actual[0]["content"]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "\n\n".join(item["content"] for item in actual if item["role"] == "user")}
            ],
        },
    ]
    context = {
        "chat_messages": old_messages,
        "context_evidence_slots": current_slots,
        "chief_engineer_blueprint": blueprint,
        "workspace_quality_evidence": slots["workspace_quality"],
    }
    original_context = deepcopy(context)
    request = SimpleNamespace(role="qa", context=context, options={})
    prepared = SimpleNamespace(
        messages=actual,
        request_options={},
        context_result=SimpleNamespace(metadata={}),
        capability_profile={"model_window_tokens": 32768},
        ai_request=request,
    )
    audit = build_final_request_context_audit_for_request(
        ai_request=request, prepared=prepared, profile=_make_profile("qa", role_id="qa")
    )
    missing = audit["final_request_evidence_coverage"]["missing_required_refs"]
    if tamper == "none":
        assert not set(missing) & {"ce_blueprint", "verifier_receipts", "workspace_quality"}
        assert audit["coverage"]["has_chief_engineer_blueprint"] is True
    elif tamper == "ce_hash":
        assert "ce_blueprint" in missing
        assert audit["coverage"]["has_chief_engineer_blueprint"] is False
    elif tamper == "body_absent":
        assert {"ce_blueprint", "verifier_receipts", "workspace_quality"}.issubset(missing)
        assert audit["coverage"]["has_chief_engineer_blueprint"] is False
    else:
        assert {"verifier_receipts", "workspace_quality"}.issubset(missing)
    assert request.context == original_context

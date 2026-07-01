from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

POLARIS_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = POLARIS_ROOT.parent

DEBT_REGISTER_PATH = BACKEND_ROOT / "docs" / "governance" / "debt.register.yaml"
DEBT_REGISTER_SCHEMA_PATH = BACKEND_ROOT / "docs" / "governance" / "schemas" / "debt-register.schema.yaml"
VERIFY_PACK_SCHEMA_PATH = BACKEND_ROOT / "docs" / "governance" / "schemas" / "verify-pack.schema.yaml"
VERIFY_PACK_PATH = POLARIS_ROOT / "cells" / "roles" / "kernel" / "generated" / "verify.pack.json"
VERIFY_CARD_SCHEMA_PATH = BACKEND_ROOT / "docs" / "governance" / "schemas" / "verification-card.schema.yaml"
VERIFY_CARD_PATH = (
    BACKEND_ROOT / "docs" / "governance" / "templates" / "verification-cards" / "vc-20260325-turn-engine-tool-loop.yaml"
)
ADR_0042_PATH = BACKEND_ROOT / "docs" / "governance" / "decisions" / "adr-0042-turn-engine-triple-responsibility.md"
ADR_0043_PATH = BACKEND_ROOT / "docs" / "governance" / "decisions" / "adr-0043-structural-bug-governance-loop.md"
FITNESS_RULES_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"
PIPELINE_TEMPLATE_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "pipeline.template.yaml"
ROLE_KERNEL_EXECUTION_AUTHORIZATION_FILES = (
    POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "turn_decision_decoder.py",
    POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "turn_transaction_controller.py",
    POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "transaction" / "decision_pipeline.py",
    POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "transaction" / "finalization.py",
    POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "transaction" / "stream_orchestrator.py",
)

EXPECTED_DEBT_IDS = {
    "DEBT-20260325-roles-kernel-turn-stage-contract",
    "DEBT-20260325-kernelone-llm-reexport-parity",
    "DEBT-20260620-runtime-realtime-single-rail",
}
ROLE_KERNEL_VERIFY_PACK_DEBT_IDS = {
    "DEBT-20260325-roles-kernel-turn-stage-contract",
    "DEBT-20260325-kernelone-llm-reexport-parity",
}
EXPECTED_ACTIVE_DEBT_IDS: set[str] = set()
EXPECTED_RULE_IDS = {
    "debt_register_schema_valid",
    "verify_pack_schema_valid",
    "structural_bug_governance_assets_complete",
    "kernelone_llm_contract_reexport_parity",
    "roles_kernel_turn_stage_boundaries_non_regressive",
}
DEBT_ID_RE = re.compile(r"^DEBT-\d{8}-[a-z0-9-]+$")
VERIFY_CARD_ID_RE = re.compile(r"^vc-\d{8}-[a-z0-9-]+$")


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must deserialize to a dict"
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} must deserialize to a dict"
    return payload


def _assert_repo_relative_file(path_text: str) -> None:
    relative_path = Path(path_text)
    candidates = (
        BACKEND_ROOT / relative_path,
        POLARIS_ROOT / relative_path,
    )
    assert any(candidate.exists() for candidate in candidates), f"missing linked asset: {path_text}"


def test_structural_bug_governance_files_exist() -> None:
    required_files = [
        DEBT_REGISTER_PATH,
        DEBT_REGISTER_SCHEMA_PATH,
        VERIFY_PACK_SCHEMA_PATH,
        VERIFY_PACK_PATH,
        VERIFY_CARD_SCHEMA_PATH,
        VERIFY_CARD_PATH,
        ADR_0042_PATH,
        ADR_0043_PATH,
        FITNESS_RULES_PATH,
        PIPELINE_TEMPLATE_PATH,
    ]
    for path in required_files:
        assert path.is_file(), f"missing governance asset: {path}"


def test_debt_register_shape_and_links() -> None:
    payload = _read_yaml(DEBT_REGISTER_PATH)

    assert payload.get("version") == 1
    assert isinstance(payload.get("generated_at"), str) and payload["generated_at"]
    assert isinstance(payload.get("scope"), str) and payload["scope"]

    debts = payload.get("debts")
    assert isinstance(debts, list) and debts, "debt register must contain debts"

    seen_ids: set[str] = set()
    for debt in debts:
        assert isinstance(debt, dict)
        debt_id = str(debt.get("id") or "")
        assert DEBT_ID_RE.match(debt_id), f"invalid debt id: {debt_id}"
        seen_ids.add(debt_id)
        assert debt.get("classification") in {"one_off", "pattern", "structural"}
        assert debt.get("status") in {"active", "monitoring", "retired"}
        assert debt.get("severity") in {"medium", "high", "blocker"}

        for key in (
            "scope_refs",
            "trigger_conditions",
            "mitigations",
            "residual_risks",
            "next_actions",
        ):
            value = debt.get(key)
            assert isinstance(value, list) and value, f"{debt_id} missing {key}"

        for key in ("summary", "root_cause", "title"):
            value = debt.get(key)
            assert isinstance(value, str) and value.strip(), f"{debt_id} missing {key}"

        linked_assets = debt.get("linked_assets")
        assert isinstance(linked_assets, dict), f"{debt_id} missing linked_assets"
        for key in ("adrs", "verification_cards", "verify_packs", "tests"):
            assets = linked_assets.get(key)
            assert isinstance(assets, list) and assets, f"{debt_id} missing linked asset group {key}"
            for asset in assets:
                assert isinstance(asset, str) and asset.strip()
                _assert_repo_relative_file(asset)

    assert seen_ids == EXPECTED_DEBT_IDS


def test_debt_register_has_no_unapproved_active_debt() -> None:
    """Debt register active items require an explicit architecture allowlist.

    Monitoring debt is allowed as a guarded long-term invariant. Active debt is
    treated as unfinished convergence work, so adding or re-opening an active
    item must update this test deliberately instead of silently increasing the
    residual gap count.
    """

    payload = _read_yaml(DEBT_REGISTER_PATH)
    active_debt_ids = {
        str(debt.get("id") or "")
        for debt in payload.get("debts", [])
        if isinstance(debt, dict) and debt.get("status") == "active"
    }

    assert active_debt_ids == EXPECTED_ACTIVE_DEBT_IDS


def test_roles_kernel_execution_paths_do_not_use_compat_tool_parser() -> None:
    """Execution authorization paths must stay native-tool-call driven.

    ``OutputParser.parse_tool_calls`` remains as a compatibility wrapper around
    the typed raw-content parser. It must not be reintroduced into transaction
    execution paths, where tool execution must flow through RawLLMResponse
    native_tool_calls and TurnDecisionDecoder.
    """

    forbidden_patterns = (
        ".parse_tool_calls(",
        "parse_tool_calls(",
        "from polaris.kernelone.llm.toolkit import parse_tool_calls",
    )
    findings: list[str] = []
    for path in ROLE_KERNEL_EXECUTION_AUTHORIZATION_FILES:
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            if pattern in text:
                findings.append(f"{path.relative_to(POLARIS_ROOT)} contains {pattern!r}")

    assert findings == []


def test_roles_kernel_verify_pack_shape_and_links() -> None:
    verify_pack = _read_json(VERIFY_PACK_PATH)
    debt_register = _read_yaml(DEBT_REGISTER_PATH)
    debt_ids = {str(item.get("id") or "") for item in debt_register.get("debts", []) if isinstance(item, dict)}

    assert verify_pack.get("version") == 1
    assert verify_pack.get("cell_id") == "roles.kernel"
    assert verify_pack.get("status") in {"green", "guarded", "blocked"}
    assert isinstance(verify_pack.get("summary"), str) and verify_pack["summary"]

    verify_targets = verify_pack.get("verify_targets")
    assert isinstance(verify_targets, dict)
    tests = verify_targets.get("tests")
    assert isinstance(tests, list) and tests
    for test_entry in tests:
        assert isinstance(test_entry, dict)
        test_path = str(test_entry.get("path") or "")
        purpose = str(test_entry.get("purpose") or "")
        assert test_path and purpose
        _assert_repo_relative_file(test_path)

    contracts = verify_targets.get("contracts")
    assert isinstance(contracts, list) and contracts
    joined_contracts = "\n".join(str(item) for item in contracts)
    assert "raw_content" in joined_contracts
    assert "clean_content" in joined_contracts
    assert "native_tool_calls" in joined_contracts

    governance_artifacts = verify_pack.get("governance_artifacts")
    assert isinstance(governance_artifacts, dict)
    for key in ("adrs", "verification_cards", "schemas"):
        items = governance_artifacts.get(key)
        assert isinstance(items, list) and items
        for item in items:
            _assert_repo_relative_file(str(item))
    debt_register_path = str(governance_artifacts.get("debt_register") or "")
    assert debt_register_path == "docs/governance/debt.register.yaml"
    _assert_repo_relative_file(debt_register_path)

    open_debt_ids = verify_pack.get("open_debt_ids")
    assert isinstance(open_debt_ids, list)
    assert ROLE_KERNEL_VERIFY_PACK_DEBT_IDS.issubset(set(open_debt_ids))
    for debt_id in open_debt_ids:
        assert debt_id in debt_ids, f"verify pack references unknown debt id {debt_id}"

    residual_risks = verify_pack.get("residual_risks")
    assert isinstance(residual_risks, list) and residual_risks


def test_structural_bug_governance_chain_is_complete() -> None:
    verify_card = _read_yaml(VERIFY_CARD_PATH)
    fitness_rules = _read_yaml(FITNESS_RULES_PATH)
    pipeline_template = _read_yaml(PIPELINE_TEMPLATE_PATH)
    adr_0043_text = ADR_0043_PATH.read_text(encoding="utf-8")

    card_id = str(verify_card.get("card_id") or "")
    assert VERIFY_CARD_ID_RE.match(card_id)
    assert verify_card.get("classification") == "structural"
    assert isinstance(verify_card.get("assumptions"), list) and len(verify_card["assumptions"]) >= 2
    related_adrs = verify_card.get("related_adrs")
    assert isinstance(related_adrs, list) and "ADR-0042" in related_adrs

    assert "verification card" in adr_0043_text
    assert "debt.register.yaml" in adr_0043_text
    assert "verify.pack.json" in adr_0043_text

    rule_ids = {str(item.get("id") or "") for item in fitness_rules.get("rules", []) if isinstance(item, dict)}
    assert EXPECTED_RULE_IDS.issubset(rule_ids)

    stages = pipeline_template.get("stages")
    assert isinstance(stages, list) and stages
    stage_ids = {str(item.get("id") or "") for item in stages if isinstance(item, dict)}
    assert "structural_bug_governance_gate" in stage_ids

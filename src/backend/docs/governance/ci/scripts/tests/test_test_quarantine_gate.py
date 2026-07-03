"""Unit tests for the test quarantine governance gate script."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from docs.governance.ci.scripts.run_test_quarantine_gate import (
    FileRunResult,
    classify_outcomes,
    load_manifest,
    main,
    parse_pytest_summary,
    run_quarantine_gate,
)

_FILE_A = "polaris/pkg/tests/test_alpha.py"
_FILE_B = "polaris/pkg/tests/test_beta.py"


def _entry(node_id: str, file_path: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "file": file_path,
        "reason": "known drift",
        "owner_hint": "cells.example",
        "registered_at": "2026-07-03",
        "expiry": None,
    }


def _write_manifest(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    manifest_path = tmp_path / "known_failures.json"
    manifest_path.write_text(
        json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


# ---------------------------------------------------------------------------
# classify_outcomes (pure classifier)
# ---------------------------------------------------------------------------


def test_classifier_registered_still_failing_is_known() -> None:
    node = f"{_FILE_A}::test_x"
    result = classify_outcomes([node], {node: "failed"})
    assert [item.node_id for item in result.known_failures] == [node]
    assert result.new_failures == ()
    assert result.unexpected_passes == ()
    assert result.gate_clean is True


def test_classifier_registered_error_outcome_is_known() -> None:
    node = f"{_FILE_A}::test_x"
    result = classify_outcomes([node], {node: "error"})
    assert [item.node_id for item in result.known_failures] == [node]
    assert result.gate_clean is True


def test_classifier_unregistered_failure_is_new_failure() -> None:
    registered = f"{_FILE_A}::test_known"
    rogue = f"{_FILE_A}::test_rogue"
    result = classify_outcomes([registered], {registered: "failed", rogue: "failed"})
    assert [item.node_id for item in result.new_failures] == [rogue]
    assert result.gate_clean is False


def test_classifier_registered_pass_is_unexpected_pass() -> None:
    node = f"{_FILE_A}::test_x"
    result = classify_outcomes([node], {node: "passed"})
    assert [item.node_id for item in result.unexpected_passes] == [node]
    assert result.unexpected_passes[0].outcome == "passed"
    assert result.gate_clean is False


def test_classifier_registered_missing_node_is_unexpected_pass_not_collected() -> None:
    node = f"{_FILE_A}::test_deleted"
    result = classify_outcomes([node], {})
    assert [item.node_id for item in result.unexpected_passes] == [node]
    assert result.unexpected_passes[0].outcome == "not_collected"
    assert result.gate_clean is False


def test_classifier_unregistered_pass_is_ignored() -> None:
    registered = f"{_FILE_A}::test_known"
    result = classify_outcomes(
        [registered],
        {registered: "failed", f"{_FILE_A}::test_green": "passed"},
    )
    assert result.new_failures == ()
    assert result.unexpected_passes == ()
    assert result.gate_clean is True


# ---------------------------------------------------------------------------
# parse_pytest_summary
# ---------------------------------------------------------------------------


def test_parse_pytest_summary_extracts_node_outcomes() -> None:
    output = "\n".join(
        [
            "=========================== short test summary info ===========================",
            f"PASSED {_FILE_A}::test_ok",
            f"FAILED {_FILE_A}::test_bad - AssertionError: assert 1 == 2",
            f"FAILED {_FILE_A}::test_param[case-1] - RuntimeError: boom",
            f"ERROR {_FILE_B}",
            f"SKIPPED [1] {_FILE_A}:12: requires network",
            f"XFAIL {_FILE_A}::test_xf",
            f"XPASS {_FILE_A}::test_xp",
            "1 failed, 1 passed in 0.10s",
        ]
    )
    outcomes = parse_pytest_summary(output)
    assert outcomes == {
        f"{_FILE_A}::test_ok": "passed",
        f"{_FILE_A}::test_bad": "failed",
        f"{_FILE_A}::test_param[case-1]": "failed",
        _FILE_B: "error",
        f"{_FILE_A}::test_xf": "xfailed",
        f"{_FILE_A}::test_xp": "xpassed",
    }


def test_parse_pytest_summary_ignores_non_summary_lines() -> None:
    assert parse_pytest_summary("all good\n2 passed in 0.01s\n") == {}


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------


def test_load_manifest_roundtrip(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, [_entry(f"{_FILE_A}::test_x", _FILE_A)])
    entries = load_manifest(manifest_path)
    assert len(entries) == 1
    assert entries[0].node_id == f"{_FILE_A}::test_x"
    assert entries[0].file == _FILE_A
    assert entries[0].expiry is None


def test_load_manifest_rejects_node_id_without_separator(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, [_entry(_FILE_A, _FILE_A)])
    with pytest.raises(ValueError, match="invalid node_id"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_file_not_prefixing_node_id(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, [_entry(f"{_FILE_A}::test_x", _FILE_B)])
    with pytest.raises(ValueError, match="does not prefix"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_duplicate_node_ids(tmp_path: Path) -> None:
    entry = _entry(f"{_FILE_A}::test_x", _FILE_A)
    manifest_path = _write_manifest(tmp_path, [entry, dict(entry)])
    with pytest.raises(ValueError, match="duplicate node_id"):
        load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# run_quarantine_gate with an injected fake pytest runner (no nested pytest)
# ---------------------------------------------------------------------------


def _fake_runner(outcomes_by_file: dict[str, dict[str, str]]):  # type: ignore[no-untyped-def]
    def runner(test_file: str, backend_root: Path) -> FileRunResult:
        return FileRunResult(
            file=test_file,
            exit_code=1 if any(v in {"failed", "error"} for v in outcomes_by_file.get(test_file, {}).values()) else 0,
            outcomes=dict(outcomes_by_file.get(test_file, {})),
        )

    return runner


def test_gate_clean_when_registered_failures_still_fail(tmp_path: Path) -> None:
    node = f"{_FILE_A}::test_known_red"
    manifest_path = _write_manifest(tmp_path, [_entry(node, _FILE_A)])
    report = run_quarantine_gate(
        manifest_path=manifest_path,
        backend_root=tmp_path,
        mode="enforce",
        pytest_runner=_fake_runner({_FILE_A: {node: "failed", f"{_FILE_A}::test_green": "passed"}}),
    )
    assert report["exit_code"] == 0
    assert report["gate_clean"] is True
    assert report["summary"] == {
        "known_failure_count": 1,
        "new_failure_count": 0,
        "unexpected_pass_count": 0,
    }


def test_gate_fails_on_injected_new_red(tmp_path: Path) -> None:
    """Blueprint verification: a fake NEW failure inside a quarantined file must FAIL."""
    known = f"{_FILE_A}::test_known_red"
    rogue = f"{_FILE_A}::test_new_red"
    manifest_path = _write_manifest(tmp_path, [_entry(known, _FILE_A)])
    report = run_quarantine_gate(
        manifest_path=manifest_path,
        backend_root=tmp_path,
        mode="enforce",
        pytest_runner=_fake_runner({_FILE_A: {known: "failed", rogue: "failed"}}),
    )
    assert report["exit_code"] == 1
    assert [item["node_id"] for item in report["new_failures"]] == [rogue]


def test_gate_fails_on_injected_unexpected_green(tmp_path: Path) -> None:
    """Blueprint verification: a registered test turning green must FAIL (zombie manifest)."""
    node = f"{_FILE_A}::test_known_red"
    manifest_path = _write_manifest(tmp_path, [_entry(node, _FILE_A)])
    report = run_quarantine_gate(
        manifest_path=manifest_path,
        backend_root=tmp_path,
        mode="enforce",
        pytest_runner=_fake_runner({_FILE_A: {node: "passed"}}),
    )
    assert report["exit_code"] == 1
    assert [item["node_id"] for item in report["unexpected_passes"]] == [node]


def test_gate_audit_only_reports_but_exits_zero(tmp_path: Path) -> None:
    node = f"{_FILE_A}::test_known_red"
    manifest_path = _write_manifest(tmp_path, [_entry(node, _FILE_A)])
    report = run_quarantine_gate(
        manifest_path=manifest_path,
        backend_root=tmp_path,
        mode="audit-only",
        pytest_runner=_fake_runner({_FILE_A: {node: "passed"}}),
    )
    assert report["exit_code"] == 0
    assert report["gate_clean"] is False
    assert [item["node_id"] for item in report["unexpected_passes"]] == [node]


def test_gate_runs_each_registered_file_once(tmp_path: Path) -> None:
    node_a = f"{_FILE_A}::test_a"
    node_a2 = f"{_FILE_A}::test_a2"
    node_b = f"{_FILE_B}::test_b"
    manifest_path = _write_manifest(
        tmp_path,
        [_entry(node_a, _FILE_A), _entry(node_a2, _FILE_A), _entry(node_b, _FILE_B)],
    )
    calls: list[str] = []

    def runner(test_file: str, backend_root: Path) -> FileRunResult:
        calls.append(test_file)
        return FileRunResult(
            file=test_file,
            exit_code=1,
            outcomes={node_a: "failed", node_a2: "failed"} if test_file == _FILE_A else {node_b: "failed"},
        )

    report = run_quarantine_gate(
        manifest_path=manifest_path,
        backend_root=tmp_path,
        mode="enforce",
        pytest_runner=runner,
    )
    assert calls == sorted([_FILE_A, _FILE_B])
    assert report["exit_code"] == 0
    assert report["summary"]["known_failure_count"] == 3


def test_gate_rejects_unknown_mode(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path, [_entry(f"{_FILE_A}::test_x", _FILE_A)])
    with pytest.raises(ValueError, match="unsupported mode"):
        run_quarantine_gate(
            manifest_path=manifest_path,
            backend_root=tmp_path,
            mode="hard-fail",
            pytest_runner=_fake_runner({}),
        )


# ---------------------------------------------------------------------------
# main (CLI wiring with monkeypatched module-level runner)
# ---------------------------------------------------------------------------


def test_main_manifest_override_and_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import docs.governance.ci.scripts.run_test_quarantine_gate as gate_module

    node = f"{_FILE_A}::test_known_red"
    manifest_path = _write_manifest(tmp_path, [_entry(node, _FILE_A)])
    monkeypatch.setattr(
        gate_module,
        "run_pytest_for_file",
        _fake_runner({_FILE_A: {node: "passed"}}),
    )
    argv_base = [
        "--manifest",
        str(manifest_path),
        "--backend-root",
        str(tmp_path),
    ]

    assert main([*argv_base, "--mode", "enforce"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["gate"] == "test_quarantine"
    assert payload["unexpected_passes"][0]["node_id"] == node

    report_path = tmp_path / "report.json"
    assert main([*argv_base, "--mode", "audit-only", "--report", str(report_path)]) == 0
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["mode"] == "audit-only"
    assert written["exit_code"] == 0

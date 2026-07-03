"""Test quarantine governance gate.

This script mechanizes the known-failing test baseline described in
``docs/blueprints/GOVERNANCE_MECHANIZATION_BLUEPRINT_20260703.md``.

It loads the machine-readable quarantine manifest
(``docs/governance/quarantine/known_failures.json``), runs pytest once per
quarantined test FILE, and classifies every observed outcome into three
buckets:

- ``known_failures``: registered node ids that still fail (gate PASS signal;
  the debt is acknowledged and tracked).
- ``new_failures``: failing node ids inside the quarantined files that are
  NOT registered (gate FAIL: a new regression is hiding behind known debt).
- ``unexpected_passes``: registered node ids that no longer fail — including
  nodes that were not collected at all (gate FAIL: the registration is stale
  and must be removed, preventing a zombie manifest).

The quarantine manifest is NOT a way to make tests pass: the test bodies keep
their failing assertions; this gate only enforces the increment, mirroring
the baseline + fail-on-new pattern of the catalog governance gate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

_MODE_ENFORCE = "enforce"
_MODE_AUDIT_ONLY = "audit-only"
_SUPPORTED_MODES = (_MODE_ENFORCE, _MODE_AUDIT_ONLY)

_GATE_NAME = "test_quarantine"

# Outcomes (from the pytest -rA short summary) that count as "failing".
_FAILING_OUTCOMES = frozenset({"failed", "error"})

# Registered nodes that were not observed in the pytest run at all (deleted,
# renamed, or not collected) are reported with this sentinel outcome and are
# classified as unexpected passes: either way the registration is stale.
_OUTCOME_NOT_COLLECTED = "not_collected"

_DEFAULT_MANIFEST_REL = Path("docs/governance/quarantine/known_failures.json")

# Matches pytest -rA short-summary lines, e.g.
#   FAILED polaris/pkg/tests/test_x.py::test_y - AssertionError: ...
#   PASSED polaris/pkg/tests/test_x.py::TestCls::test_z
#   ERROR polaris/pkg/tests/test_broken.py
_SUMMARY_LINE_RE = re.compile(r"^(PASSED|FAILED|ERROR|XPASS|XFAIL|SKIPPED)\s+(\S+)")

_SUMMARY_STATUS_TO_OUTCOME = {
    "PASSED": "passed",
    "FAILED": "failed",
    "ERROR": "error",
    "XPASS": "xpassed",
    "XFAIL": "xfailed",
    "SKIPPED": "skipped",
}


@dataclass(frozen=True)
class QuarantineEntry:
    """One registered known-failing test node."""

    node_id: str
    file: str
    reason: str
    owner_hint: str
    registered_at: str
    expiry: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "file": self.file,
            "reason": self.reason,
            "owner_hint": self.owner_hint,
            "registered_at": self.registered_at,
            "expiry": self.expiry,
        }


@dataclass(frozen=True)
class ObservedNode:
    """One classified node with its observed pytest outcome."""

    node_id: str
    outcome: str

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "outcome": self.outcome}


@dataclass(frozen=True)
class QuarantineClassification:
    """Pure classification result over registered ids and observed outcomes."""

    known_failures: tuple[ObservedNode, ...] = ()
    new_failures: tuple[ObservedNode, ...] = ()
    unexpected_passes: tuple[ObservedNode, ...] = ()

    @property
    def gate_clean(self) -> bool:
        return not self.new_failures and not self.unexpected_passes

    def to_dict(self) -> dict[str, Any]:
        return {
            "known_failures": [item.to_dict() for item in self.known_failures],
            "new_failures": [item.to_dict() for item in self.new_failures],
            "unexpected_passes": [item.to_dict() for item in self.unexpected_passes],
            "summary": {
                "known_failure_count": len(self.known_failures),
                "new_failure_count": len(self.new_failures),
                "unexpected_pass_count": len(self.unexpected_passes),
            },
        }


@dataclass(frozen=True)
class FileRunResult:
    """Raw pytest execution evidence for a single quarantined test file."""

    file: str
    exit_code: int
    outcomes: dict[str, str] = field(default_factory=dict)


def load_manifest(manifest_path: Path) -> tuple[QuarantineEntry, ...]:
    """Load and validate the quarantine manifest.

    Raises:
        ValueError: when the manifest shape is invalid.
    """
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"quarantine manifest must be a JSON object: {manifest_path}")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError(f"quarantine manifest must contain an 'entries' list: {manifest_path}")

    entries: list[QuarantineEntry] = []
    seen_node_ids: set[str] = set()
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise ValueError(f"entry #{index} is not an object in {manifest_path}")
        node_id = str(raw.get("node_id") or "").strip()
        file_path = str(raw.get("file") or "").strip()
        if not node_id or "::" not in node_id:
            raise ValueError(f"entry #{index} has invalid node_id {node_id!r} in {manifest_path}")
        if not file_path or not node_id.startswith(file_path):
            raise ValueError(f"entry #{index} file {file_path!r} does not prefix node_id {node_id!r}")
        if node_id in seen_node_ids:
            raise ValueError(f"duplicate node_id registered: {node_id}")
        seen_node_ids.add(node_id)
        expiry_raw = raw.get("expiry")
        entries.append(
            QuarantineEntry(
                node_id=node_id,
                file=file_path,
                reason=str(raw.get("reason") or "").strip(),
                owner_hint=str(raw.get("owner_hint") or "").strip(),
                registered_at=str(raw.get("registered_at") or "").strip(),
                expiry=None if expiry_raw is None else str(expiry_raw),
            )
        )
    return tuple(entries)


def parse_pytest_summary(output: str) -> dict[str, str]:
    """Parse a ``pytest -q -rA`` short summary into node_id -> outcome.

    Only tokens that look like pytest node ids (``file::node``) or file-level
    error tokens (``file.py``) are recorded; SKIPPED location lines such as
    ``SKIPPED [1] path/file.py:12: reason`` carry no node id and are ignored.
    """
    outcomes: dict[str, str] = {}
    for line in output.splitlines():
        match = _SUMMARY_LINE_RE.match(line.strip())
        if match is None:
            continue
        status, token = match.group(1), match.group(2)
        if "::" not in token and not token.endswith(".py"):
            continue
        outcomes[token] = _SUMMARY_STATUS_TO_OUTCOME[status]
    return outcomes


def classify_outcomes(
    registered_node_ids: Sequence[str],
    observed_outcomes: Mapping[str, str],
) -> QuarantineClassification:
    """Classify observed outcomes against the registered quarantine set.

    Pure function: ``observed_outcomes`` must only contain nodes from the
    quarantined FILES scope (the runner guarantees this by running pytest per
    registered file), so any unregistered failing node is a new failure.
    """
    registered = set(registered_node_ids)
    known: list[ObservedNode] = []
    new: list[ObservedNode] = []
    unexpected: list[ObservedNode] = []

    for node_id in sorted(registered):
        outcome = observed_outcomes.get(node_id, _OUTCOME_NOT_COLLECTED)
        if outcome in _FAILING_OUTCOMES:
            known.append(ObservedNode(node_id=node_id, outcome=outcome))
        else:
            unexpected.append(ObservedNode(node_id=node_id, outcome=outcome))

    for node_id in sorted(observed_outcomes):
        if node_id in registered:
            continue
        outcome = observed_outcomes[node_id]
        if outcome in _FAILING_OUTCOMES:
            new.append(ObservedNode(node_id=node_id, outcome=outcome))

    return QuarantineClassification(
        known_failures=tuple(known),
        new_failures=tuple(new),
        unexpected_passes=tuple(unexpected),
    )


def run_pytest_for_file(test_file: str, backend_root: Path) -> FileRunResult:
    """Run pytest for one quarantined test file and collect per-node outcomes."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        test_file,
        "-q",
        "--tb=no",
        "-rA",
        "-p",
        "no:cacheprovider",
    ]
    completed = subprocess.run(
        command,
        cwd=backend_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    outcomes = parse_pytest_summary(completed.stdout)
    if not outcomes and completed.returncode not in (0, 1):
        # Collection/usage breakage without any parsable node: fail closed by
        # recording a file-level error so it surfaces as a new failure.
        outcomes[test_file] = "error"
    return FileRunResult(file=test_file, exit_code=completed.returncode, outcomes=outcomes)


def run_quarantine_gate(
    manifest_path: Path,
    backend_root: Path,
    mode: str,
    pytest_runner: Callable[[str, Path], FileRunResult] | None = None,
) -> dict[str, Any]:
    """Execute the full gate and return the JSON-serializable report."""
    if mode not in _SUPPORTED_MODES:
        raise ValueError(f"unsupported mode {mode!r}; expected one of {_SUPPORTED_MODES}")

    # Resolved at call time so tests can monkeypatch the module-level runner.
    runner = pytest_runner if pytest_runner is not None else run_pytest_for_file

    entries = load_manifest(manifest_path)
    files = sorted({entry.file for entry in entries})

    observed: dict[str, str] = {}
    file_runs: list[dict[str, Any]] = []
    for test_file in files:
        result = runner(test_file, backend_root)
        observed.update(result.outcomes)
        file_runs.append(
            {
                "file": result.file,
                "pytest_exit_code": result.exit_code,
                "collected_outcomes": len(result.outcomes),
            }
        )

    classification = classify_outcomes([entry.node_id for entry in entries], observed)
    exit_code = 0 if (mode == _MODE_AUDIT_ONLY or classification.gate_clean) else 1

    report: dict[str, Any] = {
        "gate": _GATE_NAME,
        "mode": mode,
        "manifest": str(manifest_path),
        "backend_root": str(backend_root),
        "registered_count": len(entries),
        "quarantined_files": files,
        "file_runs": file_runs,
        "exit_code": exit_code,
        "gate_clean": classification.gate_clean,
    }
    report.update(classification.to_dict())
    return report


def _default_backend_root() -> Path:
    """Backend root derived from this script location (docs/governance/ci/scripts)."""
    return Path(__file__).resolve().parents[4]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the test quarantine governance gate.")
    parser.add_argument(
        "--manifest",
        default=None,
        help="Quarantine manifest JSON path (defaults to docs/governance/quarantine/known_failures.json)",
    )
    parser.add_argument(
        "--backend-root",
        default=None,
        help="Backend root used as pytest cwd (defaults to the repository backend root)",
    )
    parser.add_argument(
        "--mode",
        default=_MODE_ENFORCE,
        choices=_SUPPORTED_MODES,
        help="Gate mode: enforce fails on new failures / unexpected passes; audit-only always exits 0",
    )
    parser.add_argument("--report", default=None, help="Optional output report JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    backend_root = Path(str(args.backend_root)).resolve() if args.backend_root else _default_backend_root()
    manifest_path = Path(str(args.manifest)).resolve() if args.manifest else backend_root / _DEFAULT_MANIFEST_REL

    report = run_quarantine_gate(
        manifest_path=manifest_path,
        backend_root=backend_root,
        mode=str(args.mode),
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    print(serialized)

    if args.report:
        report_path = Path(str(args.report)).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(serialized, encoding="utf-8")

    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())

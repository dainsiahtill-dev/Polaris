"""Cross-artifact interface snapshot and consistency gate tests."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.artifact_quality import scan_workspace_artifact_quality
from polaris.kernelone.quality.cross_artifact_interfaces import (
    CrossArtifactInterfaceContract,
    CrossArtifactInterfaceRequirement,
    build_contract_amendment_request,
    build_symbol_index_snapshot,
    plan_cross_artifact_repairs,
    scan_cross_artifact_consistency,
)
from polaris.kernelone.quality.interface_ledger import record_declared_interfaces


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestPythonNamespaceExports:
    def test_python_package_reexport_resolves_imported_symbol(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "src/models/weather.py",
            "class WeatherReport:\n    def __init__(self, condition):\n        self.condition = condition\n",
        )
        _write(tmp_path / "src/models/__init__.py", "from .weather import WeatherReport\n")
        _write(
            tmp_path / "src/engine/forecast.py",
            "from src.models import WeatherReport\n\nreport = WeatherReport('cloud')\n",
        )

        issues = scan_cross_artifact_consistency(tmp_path)

        assert issues == []

    def test_python_dangling_reexport_is_reported(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/models/weather.py", "class WeatherSnapshot:\n    pass\n")
        _write(tmp_path / "src/models/__init__.py", "from .weather import WeatherReport\n")
        _write(tmp_path / "src/engine/forecast.py", "from src.models import WeatherReport\n")

        issues = scan_cross_artifact_consistency(tmp_path)

        messages = [issue.message for issue in issues]
        assert any("unresolved import symbol 'WeatherReport'" in message for message in messages)
        assert any("src/models/__init__.py" in message for message in messages)


class TestTypescriptNamespaceExports:
    def test_typescript_export_star_resolves_namespace_import(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherReport { condition: string }\n")
        _write(tmp_path / "src/index.ts", "export * from './weather';\n")
        _write(
            tmp_path / "src/forecast.ts",
            "import { WeatherReport } from './index';\nconst report: WeatherReport = { condition: 'wind' };\n",
        )

        issues = scan_cross_artifact_consistency(tmp_path)

        assert issues == []

    def test_typescript_dangling_barrel_export_is_reported_to_artifact_quality(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        _write(tmp_path / "src/index.ts", "export { WeatherReport } from './weather';\n")
        _write(tmp_path / "src/forecast.ts", "import { WeatherReport } from './index';\n")

        errors = scan_workspace_artifact_quality(str(tmp_path))

        assert any("unresolved import symbol 'WeatherReport' from './index'" in error for error in errors)

    def test_artifact_quality_consumes_ce_interface_ledger(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "CE-S1", "target_file": "src/weather.ts", "interface_names": ["WeatherReport"]}],
        )

        errors = scan_workspace_artifact_quality(str(tmp_path))

        assert "Artifact quality scan failed: declared interface 'WeatherReport' missing from src/weather.ts" in errors


class TestSnapshotSignatures:
    def test_python_signature_digest_is_stable_contract_evidence(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/engine/forecast.py", "def forecast_for(mood, *, wind=0):\n    return mood, wind\n")

        snapshot = build_symbol_index_snapshot(tmp_path)
        symbols = {symbol.name: symbol for symbol in snapshot.physical_exports["src/engine/forecast.py"]}

        assert symbols["forecast_for"].signature == "forecast_for(mood, *, wind)"
        assert symbols["forecast_for"].signature_digest
        assert snapshot.stable_hash()

    def test_contract_signature_mismatch_is_reported(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/engine/forecast.py", "def forecast_for(mood):\n    return mood\n")
        contract = CrossArtifactInterfaceContract(
            task_id="TASK-1",
            interfaces=(
                CrossArtifactInterfaceRequirement(
                    domain="code_symbol",
                    owner_path="src/engine/forecast.py",
                    name="forecast_for",
                    kind="function",
                    signature_digest="expected-different-signature",
                ),
            ),
        )

        issues = scan_cross_artifact_consistency(tmp_path, contract=contract)

        assert [issue.code for issue in issues] == ["contract_signature_mismatch"]

    def test_contract_mismatch_can_be_promoted_to_ce_amendment_request(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/engine/forecast.py", "def forecast_for(mood):\n    return mood\n")
        contract = CrossArtifactInterfaceContract(
            task_id="TASK-1",
            interfaces=(
                CrossArtifactInterfaceRequirement(
                    domain="code_symbol",
                    owner_path="src/engine/forecast.py",
                    name="missing_forecast",
                    kind="function",
                ),
            ),
        )
        issues = scan_cross_artifact_consistency(tmp_path, contract=contract)

        amendment = build_contract_amendment_request(task_id="TASK-1", issues=issues)

        assert amendment is not None
        assert amendment.to_dict()["schema_version"] == "cross_artifact.contract_amendment_request.v1"
        assert amendment.to_dict()["task_id"] == "TASK-1"
        assert "missing_forecast" in amendment.to_dict()["evidence"][0]


class TestTypedRepairPlans:
    def test_close_symbol_mismatch_plans_consumer_rename(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherReport { condition: string }\n")
        _write(tmp_path / "src/forecast.ts", "import { WeatherReprot } from './weather';\n")

        plans = plan_cross_artifact_repairs(scan_cross_artifact_consistency(tmp_path))

        assert [plan.strategy for plan in plans] == ["rename_consumer_to_existing_interface"]
        assert plans[0].authority == "director_repair_within_contract"
        assert plans[0].replacement_symbol == "WeatherReport"

    def test_missing_symbol_without_close_match_plans_real_owner_export(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        _write(tmp_path / "src/forecast.ts", "import { WeatherReport } from './weather';\n")

        plans = plan_cross_artifact_repairs(scan_cross_artifact_consistency(tmp_path))

        assert [plan.strategy for plan in plans] == ["add_real_interface_to_owner"]
        assert plans[0].owner_path == "src/weather.ts"
        assert any("Do not satisfy" in constraint for constraint in plans[0].constraints)

    def test_contract_issue_plans_ce_amendment_not_director_repair(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        contract = CrossArtifactInterfaceContract(
            task_id="TASK-1",
            interfaces=(
                CrossArtifactInterfaceRequirement(
                    domain="code_symbol",
                    owner_path="src/weather.ts",
                    name="WeatherReport",
                ),
            ),
        )

        plans = plan_cross_artifact_repairs(scan_cross_artifact_consistency(tmp_path, contract=contract))

        assert [plan.strategy for plan in plans] == ["contract_amendment_required"]
        assert plans[0].authority == "ce_amendment_required"


class TestGoExports:
    def test_go_exported_identifiers_are_in_snapshot(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "engine/museum.go",
            "package engine\n\ntype Capsule struct{}\nfunc UnlockCapsule() {}\nfunc hiddenHelper() {}\n",
        )

        snapshot = build_symbol_index_snapshot(tmp_path)
        exported = {symbol.name for symbol in snapshot.physical_exports["engine/museum.go"]}

        assert exported == {"Capsule", "UnlockCapsule"}

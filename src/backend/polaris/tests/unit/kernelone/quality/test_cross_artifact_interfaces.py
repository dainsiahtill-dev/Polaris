"""Cross-artifact interface snapshot and consistency gate tests."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.artifact_quality import (
    scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence,
)
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

    def test_python_import_alias_resolves_source_name_not_local_alias(self, tmp_path: Path) -> None:
        """Live L2-12 TASK-3-source-modules: ``compose_forecast as _compose_forecast``.

        The exporter owns ``compose_forecast``. The local alias is not a
        missing sibling export and must not fail the importer.
        """

        _write(
            tmp_path / "src/engine/forecast.py",
            "def compose_forecast(mood):\n    return mood\n\ndef known_rules():\n    return ()\n",
        )
        _write(
            tmp_path / "src/__init__.py",
            "from src.engine.forecast import compose_forecast as _compose_forecast, known_rules as _known_rules\n",
        )

        issues = scan_cross_artifact_consistency(tmp_path)

        assert issues == []

    def test_python_import_alias_still_reports_missing_source_name(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/engine/forecast.py", "def compose_forecast(mood):\n    return mood\n")
        _write(
            tmp_path / "src/__init__.py",
            "from src.engine.forecast import missing_forecast as _compose_forecast\n",
        )

        issues = scan_cross_artifact_consistency(tmp_path)

        messages = [issue.message for issue in issues]
        assert any("unresolved import symbol 'missing_forecast'" in message for message in messages)
        assert not any("_compose_forecast" in message for message in messages)


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

    def test_typescript_js_specifier_resolves_sibling_ts_for_contract_amendment(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/engine/renderer.ts", "export interface RenderContext { canvas: unknown }\n")
        _write(
            tmp_path / "src/web.ts",
            "import { drawMarket, updateHud, type HudRefs } from './engine/renderer.js';\n",
        )

        evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), task_id="TASK-HTML")

        assert [issue.code for issue in evidence.cross_artifact_issues] == [
            "unresolved_import_symbol",
            "unresolved_import_symbol",
        ]
        assert {issue.symbol for issue in evidence.cross_artifact_issues} == {"drawMarket", "updateHud"}
        assert [plan.strategy for plan in evidence.cross_artifact_repair_plans] == [
            "contract_amendment_required",
            "contract_amendment_required",
        ]
        assert evidence.contract_amendment_request is not None
        assert evidence.contract_amendment_request.task_id == "TASK-HTML"
        assert any("drawMarket" in item for item in evidence.contract_amendment_request.evidence)

    def test_artifact_quality_consumes_ce_interface_ledger(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "CE-S1", "target_file": "src/weather.ts", "interface_names": ["WeatherReport"]}],
        )

        errors = scan_workspace_artifact_quality(str(tmp_path))

        assert "Artifact quality scan failed: declared interface 'WeatherReport' missing from src/weather.ts" in errors

    def test_typescript_fixture_string_import_is_not_physical_interface_import(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "tests/verify.test.ts",
            """
const VALID_WEB = `import { render } from "./engine/renderer";
export function boot(): void { render(); }`;
""".lstrip(),
        )

        snapshot = build_symbol_index_snapshot(tmp_path)
        issues = scan_cross_artifact_consistency(tmp_path)

        assert snapshot.imports == ()
        assert issues == []

    def test_named_import_clause_comments_are_not_physical_symbols(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/engine/runner.js", "export const runPipeline = () => true;\n")
        _write(
            tmp_path / "src/index.js",
            """
import {
  runPipeline, // re-exported below
} from "./engine/runner.js";

export { runPipeline };
""".lstrip(),
        )

        snapshot = build_symbol_index_snapshot(tmp_path)
        issues = scan_cross_artifact_consistency(tmp_path)

        assert issues == []
        assert len(snapshot.imports) == 1
        assert snapshot.imports[0].symbols == ("runPipeline",)


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

    def test_uncontracted_symbol_gap_can_be_promoted_to_ce_amendment_request(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        _write(tmp_path / "src/forecast.ts", "import { WeatherReport } from './weather';\n")
        issues = scan_cross_artifact_consistency(tmp_path)

        amendment = build_contract_amendment_request(task_id="TASK-1", issues=issues)

        assert amendment is not None
        assert amendment.to_dict()["schema_version"] == "cross_artifact.contract_amendment_request.v1"
        assert amendment.to_dict()["task_id"] == "TASK-1"
        assert "WeatherReport" in amendment.to_dict()["evidence"][0]


class TestTypedRepairPlans:
    def test_close_symbol_mismatch_plans_consumer_rename(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherReport { condition: string }\n")
        _write(tmp_path / "src/forecast.ts", "import { WeatherReprot } from './weather';\n")

        plans = plan_cross_artifact_repairs(scan_cross_artifact_consistency(tmp_path))

        assert [plan.strategy for plan in plans] == ["rename_consumer_to_existing_interface"]
        assert plans[0].authority == "director_repair_within_contract"
        assert plans[0].replacement_symbol == "WeatherReport"

    def test_missing_symbol_without_contract_requires_ce_amendment(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        _write(tmp_path / "src/forecast.ts", "import { WeatherReport } from './weather';\n")

        plans = plan_cross_artifact_repairs(scan_cross_artifact_consistency(tmp_path))

        assert [plan.strategy for plan in plans] == ["contract_amendment_required"]
        assert plans[0].owner_path == "src/weather.ts"
        assert plans[0].authority == "ce_amendment_required"
        assert any("must not invent" in constraint for constraint in plans[0].constraints)

    def test_contract_declared_missing_export_plans_real_owner_export(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        _write(tmp_path / "src/forecast.ts", "import { WeatherReport } from './weather';\n")
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

        assert [plan.strategy for plan in plans] == ["add_real_interface_to_owner"]
        assert plans[0].authority == "director_repair_within_contract"
        assert any("declared by CE" in constraint for constraint in plans[0].constraints)

    def test_contract_signature_mismatch_plans_signature_alignment(self, tmp_path: Path) -> None:
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

        plans = plan_cross_artifact_repairs(scan_cross_artifact_consistency(tmp_path, contract=contract))

        assert [plan.strategy for plan in plans] == ["align_owner_signature_to_contract"]
        assert plans[0].authority == "director_repair_within_contract"

    def test_artifact_quality_evidence_exposes_cross_artifact_plan_and_amendment(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        _write(tmp_path / "src/forecast.ts", "import { WeatherReport } from './weather';\n")

        evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), task_id="TASK-1")

        assert any("WeatherReport" in error for error in evidence.errors)
        assert [issue.code for issue in evidence.cross_artifact_issues] == ["unresolved_import_symbol"]
        assert [plan.strategy for plan in evidence.cross_artifact_repair_plans] == ["contract_amendment_required"]
        assert evidence.contract_amendment_request is not None
        assert evidence.contract_amendment_request.task_id == "TASK-1"

    def test_artifact_quality_evidence_builds_contract_from_public_symbols(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/weather.ts", "export interface WeatherSnapshot { condition: string }\n")
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [
                {
                    "step_id": "S1",
                    "target_file": "src/weather.ts",
                    "interface_names": ["weather-panel"],
                    "public_symbols": ["WeatherReport"],
                }
            ],
        )

        evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), task_id="TASK-1")

        assert [issue.code for issue in evidence.cross_artifact_issues] == ["contract_export_missing"]
        assert [plan.strategy for plan in evidence.cross_artifact_repair_plans] == ["add_real_interface_to_owner"]

    def test_artifact_quality_evidence_does_not_treat_dom_identifiers_as_code_contract(self, tmp_path: Path) -> None:
        _write(tmp_path / "src/view.js", "export function render() { return document.getElementById('game'); }\n")
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "S1", "target_file": "src/view.js", "interface_names": ["#game"]}],
        )

        evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), task_id="TASK-1")

        assert evidence.cross_artifact_issues == ()
        assert evidence.cross_artifact_repair_plans == ()


class TestGoExports:
    def test_go_exported_identifiers_are_in_snapshot(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "engine/museum.go",
            "package engine\n\ntype Capsule struct{}\nfunc UnlockCapsule() {}\nfunc hiddenHelper() {}\n",
        )

        snapshot = build_symbol_index_snapshot(tmp_path)
        exported = {symbol.name for symbol in snapshot.physical_exports["engine/museum.go"]}

        assert exported == {"Capsule", "UnlockCapsule"}

"""Characterization tests for workspace-quality repair evidence + command execution."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import os
import shutil
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    derive_project_kind_authority_from_catalog_snapshot,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    TaskBlueprintResultV1,
    _issue_chief_engineer_portfolio_authority_carrier,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.service import (
    QueryFactEventsV1,
    query_fact_events,
)
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
    factory_workspace_quality as workspace_quality_module,
)
from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_settlement_consumer import _fencing_token
from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
    evaluate_canonical_factory_authority,
)
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection
from polaris.cells.roles.adapters.public import (
    build_director_materialization_quality_repair_message,
    extract_workspace_quality_summary,
    resolve_director_semantic_quality_repair_target_files,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    ObservableTaskRowsProjectionV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path


from polaris.cells.factory.pipeline.tests._characterization_helpers import (  # noqa: F401
    _executor,
)


class TestWorkspaceQualityRepairEvidence:
    @staticmethod
    def _assert_requires_canonical_attempt(
        *,
        results: list[dict[str, Any]],
        summary: dict[str, Any],
        source_tool: str,
        materialized_text: str,
        original_marker: str,
    ) -> None:
        """Factory may discover repairs, but it cannot execute outside roles.kernel."""

        assert results == []
        assert source_tool in summary["source_tools"]
        assert summary["write_tool_evidence"] is False
        assert original_marker in materialized_text
        non_effect_evidence = summary["non_effect_evidence_results"]
        assert summary["non_effect_evidence_result_count"] == len(non_effect_evidence)
        assert any(
            str((item.get("result") or {}).get("error_code") or "") == "deo_deferred_repair_attempt_required"
            for item in non_effect_evidence
        )

    def test_compacts_write_hash_and_diff_evidence(self) -> None:
        evidence = OrchestrationStageExecutor._workspace_quality_repair_evidence(
            [
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/simulation.ts",
                        "operation": "modify",
                        "before_sha256": "a" * 64,
                        "after_sha256": "b" * 64,
                        "diff_excerpt": "--- a/src/simulation.ts\n+++ b/src/simulation.ts\n+export type GardenConfig = any;",
                    },
                }
            ]
        )

        assert any(
            item.startswith("repair_write:tool=deterministic_typescript_missing_export_repair") for item in evidence
        )
        assert "repair_hash:file=src/simulation.ts;before=aaaaaaaaaaaaaaaa;after=bbbbbbbbbbbbbbbb" in evidence
        assert any("export type GardenConfig" in item for item in evidence)

    def test_interface_discrepancy_evidence_recognizes_cross_language_symbol_mismatches(self) -> None:
        cases = (
            "go test ./... :: src/main.go:17: undefined: NewCapsule",
            "cargo check :: error[E0432]: unresolved import `crate::engine::Forecast`",
            "cargo check :: error[E0583]: file not found for module `engine`",
            "g++ :: fatal error: engine/forecast.hpp: No such file or directory",
            "ld :: undefined reference to `ForecastEngine::run()`",
        )

        for diagnostic in cases:
            evidence = OrchestrationStageExecutor._workspace_quality_interface_discrepancy_evidence(
                {
                    "plan_probe_preaudit": {
                        "status": "coverage_matched_but_unplannable",
                        "plannable_source_tools": [],
                        "covered_unplannable_source_tools": ["deterministic_cross_language_symbol_repair"],
                        "covered_unplannable_diagnostic_count": 1,
                    }
                },
                [diagnostic],
            )

            assert evidence["recommended_owner"] == "chief_engineer"
            assert evidence["recommended_route"] == "pending_design_interface_contract"
            assert evidence["cross_artifact_route"] == "contract_amendment_request"
            assert evidence["schema_version"] == "director.interface_discrepancy_receipt.v1"
            assert evidence["source"] == "factory.pipeline.workspace_quality"
            assert evidence["reason"] == "coverage_matched_but_unplannable"
            assert evidence["director_retry_allowed"] is False
            assert evidence["llm_fallback_blocked"] is True

    def test_applies_javascript_esm_commonjs_entrypoint_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "dream-note-alchemy-furnace",
                    "type": "module",
                    "main": "src/index.js",
                    "scripts": {"start": "node src/index.js"},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "Note.js").write_text(
            "export class Note {}\nexport default Note;\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            '"use strict";\n'
            'const Note = require("./models/Note");\n'
            "function main() { return new Note(); }\n"
            "if (require.main === module) { main(); }\n"
            "module.exports = { main, Note };\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-esm-cjs",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:2\n"
                "ReferenceError: require is not defined in ES module scope. "
                'package.json contains "type": "module".'
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            materialized_text=repaired,
            original_marker="module.exports = { main, Note };",
        )

    def test_applies_javascript_esm_commonjs_default_imported_module_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "package.json").write_text(
            json.dumps({"type": "module", "main": "src/index.js", "scripts": {"start": "node src/index.js"}}),
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            'import AlchemyEngine from "./engine/AlchemyEngine.js";\n'
            'import { buildDefaultEngine } from "./engine/AlchemyEngine.js";\n'
            "export function main() {\n"
            "  return new AlchemyEngine(buildDefaultEngine());\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "Note.js").write_text(
            "export class Note {}\nexport default Note;\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            '"use strict";\n\n'
            'const Note = require("../models/Note");\n\n'
            "class AlchemyEngine {\n"
            "  constructor() {\n"
            "    this.notes = [new Note()];\n"
            "  }\n"
            "}\n\n"
            "function buildDefaultEngine() {\n"
            "  return { notes: [] };\n"
            "}\n\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
            'module.exports.VERSION = "1.0.0";\n',
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-default-import-cjs",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:1\n"
                "SyntaxError: The requested module './engine/AlchemyEngine.js' "
                "does not provide an export named 'default'"
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            materialized_text=repaired,
            original_marker="module.exports = AlchemyEngine;",
        )

    def test_applies_javascript_esm_commonjs_repair_for_namespace_require_binding(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "package.json").write_text(
            json.dumps({"type": "module", "main": "src/index.js", "scripts": {"start": "node src/index.js"}}),
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {}\nexport class Recipe {}\nexport class Note {}\nexport class DreamCard {}\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            'const AlchemyEngine = require("./engine/AlchemyEngine");\n'
            "const { Note, DreamCard, Recipe } = AlchemyEngine;\n"
            "function buildDemoEngine() {\n"
            "  const engine = new AlchemyEngine();\n"
            "  return { engine, Note, DreamCard, Recipe };\n"
            "}\n"
            "module.exports = { buildDemoEngine };\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-cjs-namespace-binding",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:1\n"
                "ReferenceError: require is not defined in ES module scope\n"
                'package.json contains "type": "module"'
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            materialized_text=repaired,
            original_marker='const AlchemyEngine = require("./engine/AlchemyEngine");',
        )

    def test_applies_javascript_missing_method_runtime_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "index.js").write_text(
            'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
            "function main() {\n"
            "  const engine = new AlchemyEngine();\n"
            "  const notes = [{ id: 'n1' }];\n"
            "  engine.addRecipe({ name: 'moon' });\n"
            "  const { dreamCards, rituals } = engine.transmute(notes);\n"
            "  return { dreamCards, rituals };\n"
            "}\n"
            "main();\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {\n"
            "  constructor({ recipes = [] } = {}) {\n"
            "    this.recipes = recipes;\n"
            "  }\n\n"
            "  refine(notes) {\n"
            "    return { dreamCards: notes, unconsumed: [] };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-missing-method",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:4\n"
                "  engine.addRecipe({ name: 'moon' });\n"
                "         ^\n\n"
                "TypeError: engine.addRecipe is not a function"
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="refine(notes)",
        )

    def test_applies_javascript_missing_method_runtime_repair_aliases_run_to_transmute_result_shape(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "index.js").write_text(
            'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
            "function main() {\n"
            "  const engine = new AlchemyEngine();\n"
            "  const notes = [{ id: 'n1' }];\n"
            "  const result = engine.run(notes);\n"
            "  return result.cards.length + result.untouched.length;\n"
            "}\n"
            "main();\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {\n"
            "  transmute(notes) {\n"
            "    return { dreamCards: notes, embers: [] };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-runtime-run-method",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:5\n"
                "  const result = engine.run(notes);\n"
                "                        ^\n\n"
                "TypeError: engine.run is not a function"
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="transmute(notes)",
        )

    def test_applies_javascript_missing_method_runtime_repair_for_imported_loop_variable_class(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "index.js").write_text(
            'import { Recipe } from "./models/Recipe.js";\n'
            'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
            "const recipes = [new Recipe({ name: 'moon', keywords: ['moon'], absurdityBoost: 4, ritual: 'hum' })];\n"
            "new AlchemyEngine({ recipes }).transmute([{ content: 'moon', matchesAllTags: () => true, intensity: 1 }]);\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            'import { Recipe } from "../models/Recipe.js";\n'
            "export class AlchemyEngine {\n"
            "  constructor({ recipes = [] } = {}) { this.recipes = recipes; }\n"
            "  pickRecipeFor(notes) {\n"
            "    for (const recipe of this.recipes) {\n"
            "      if (recipe.matchesAll(notes)) return recipe;\n"
            "    }\n"
            "    return null;\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "Recipe.js").write_text(
            "export class Recipe {\n"
            "  constructor({ name, requiredTags = [] } = {}) {\n"
            "    this.name = name;\n"
            "    this.requiredTags = requiredTags;\n"
            "  }\n"
            "  isSatisfiedBy(notes) { return Array.isArray(notes); }\n"
            "}\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-runtime-loop-var-method",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/engine/AlchemyEngine.js:6\n"
                "      if (recipe.matchesAll(notes)) return recipe;\n"
                "                 ^\n\n"
                "TypeError: recipe.matchesAll is not a function"
            ],
        )

        repaired = (tmp_path / "src" / "models" / "Recipe.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="isSatisfiedBy(notes)",
        )

    def test_applies_javascript_missing_method_runtime_repair_for_constructor_object_contracts(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "models" / "DreamCard.js").write_text(
            "export class DreamCard {\n"
            "  constructor({ id, title, narrative, sourceNoteIds = [] } = {}) {\n"
            '    if (!id) throw new Error("DreamCard requires an id");\n'
            '    if (!title) throw new Error("DreamCard requires a title");\n'
            '    if (!narrative) throw new Error("DreamCard requires a narrative");\n'
            "    this.id = id;\n"
            "    this.title = title;\n"
            "    this.narrative = narrative;\n"
            "    this.sourceNoteIds = sourceNoteIds;\n"
            "  }\n"
            "  toJSON() {\n"
            "    return {\n"
            "      id: this.id,\n"
            "      title: this.title,\n"
            "      narrative: this.narrative,\n"
            "    };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "smoke.test.js").write_text(
            'import { DreamCard } from "../src/models/DreamCard.js";\n'
            "new DreamCard({\n"
            '  title: "Library of Forgotten Names",\n'
            '  body: "Each book whispered a name I almost remembered.",\n'
            '  tags: ["memory", "library"],\n'
            "  createdAt: new Date(),\n"
            "});\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            'import * as DreamCard from "../models/DreamCard.js";\n'
            "DreamCard.composeTitle(0.42);\n"
            "new DreamCard.DreamCard({ title: 'x', fragments: ['a'], absurdity: 4, ritual: 'hum' });\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-constructor-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                "Error: DreamCard requires an id\n"
                f"    at new DreamCard (file://{tmp_path}/src/models/DreamCard.js:3:20)"
            ],
        )

        repaired = (tmp_path / "src" / "models" / "DreamCard.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="DreamCard requires an id",
        )

    def test_applies_javascript_missing_method_runtime_collection_and_refine_alias_repair(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "index.js").write_text(
            'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
            "function main() {\n"
            "  const engine = new AlchemyEngine({ recipes: [] });\n"
            "  const notes = [{ id: 'n1' }];\n"
            "  engine.listRecipes().length;\n"
            "  const { dreamCards, unmatched } = engine.transmute(notes);\n"
            "  return { dreamCards, unmatched };\n"
            "}\n"
            "main();\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {\n"
            "  constructor({ recipes = [] } = {}) {\n"
            "    this.recipes = recipes;\n"
            "  }\n\n"
            "  registerRecipe(recipe) {\n"
            "    this.recipes.push(recipe);\n"
            "    return recipe;\n"
            "  }\n\n"
            "  refine(notes) {\n"
            "    return { cards: notes, unmatched: [] };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-missing-method-list",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:5\n"
                "  engine.listRecipes().length;\n"
                "         ^\n\n"
                "TypeError: engine.listRecipes is not a function"
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="registerRecipe(recipe)",
        )

    def test_applies_javascript_typescript_annotation_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "index.js").write_text(
            "export function refineDreamNotes(..._args: unknown[]): any {\n  return undefined;\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "test_basic.js").write_text(
            'import { refineDreamNotes } from "../src/index.js";\n'
            "const result = refineDreamNotes({ notes: ['有效便签'] });\n"
            "assert.equal(result.count, 1);\n"
            "assert.equal(result.distilled[0], '[提炼] 有效便签');\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-ts-annotation",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:1\n"
                "export function refineDreamNotes(..._args: unknown[]): any {\n"
                "                                         ^\n\n"
                "SyntaxError: Unexpected token ':'"
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_typescript_annotation_repair",
            materialized_text=repaired,
            original_marker=": unknown",
        )

    def test_applies_javascript_missing_export_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "index.js").write_text("console.log('dream note app');\n", encoding="utf-8")
        (tmp_path / "tests" / "test_basic.js").write_text(
            'import { run } from "../src/index.js";\n'
            "const output = run();\n"
            "assert.equal(output.ok, true);\n"
            "assert.match(output.entrypoint, /src[\\\\/]+index\\.js$/);\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-missing-export",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved import symbol 'run' "
                "from '../src/index.js' in tests/test_basic.js"
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="console.log('dream note app');",
        )

    def test_applies_javascript_missing_export_repair_for_iterable_method_contract(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {\n  defaultRecipes() {\n    return [{ name: 'starter' }];\n  }\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "alchemyEngine.test.js").write_text(
            'import { AlchemyEngine, defaultRecipes } from "../src/engine/AlchemyEngine.js";\n'
            "const engine = new AlchemyEngine();\n"
            "for (const recipe of defaultRecipes) engine.addRecipe(recipe);\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-iterable-export",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved import symbol 'defaultRecipes' "
                "from '../src/engine/AlchemyEngine.js' in tests/alchemyEngine.test.js",
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="defaultRecipes()",
        )

    def test_applies_javascript_export_contract_repair_for_wrong_existing_function(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "index.js").write_text(
            "export function refineDreamNotes(cards) {\n  if (!Array.isArray(cards)) return [];\n  return cards;\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "smoke.test.js").write_text(
            'import assert from "node:assert/strict";\n'
            'import { refineDreamNotes } from "../src/index.js";\n'
            "const result = refineDreamNotes('a glowing key', 'silent bell', 'paper moon');\n"
            "assert.equal(result.count, 3);\n"
            "assert.equal(result.summary, 'a glowing key | silent bell | paper moon');\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-export-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                f"file://{tmp_path}/tests/smoke.test.js:5\n"
                "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:\n"
                "\n"
                "undefined !== 3"
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="if (!Array.isArray(cards)) return [];",
        )

    def test_applies_javascript_export_contract_repair_for_text_and_semver(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "package.json").write_text('{"version":"0.2.0"}', encoding="utf-8")
        (tmp_path / "src" / "index.js").write_text(
            "function refineDreamNotes(notes) {\n"
            "  return [];\n"
            "}\n\n"
            "export function getVersion(...args) {\n"
            "  return { ok: true };\n"
            "}\n\n"
            "export { refineDreamNotes };\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "smoke.test.js").write_text(
            'import assert from "node:assert/strict";\n'
            'import { refineDreamNotes, getVersion, VERSION } from "../src/index.js";\n'
            "const result = refineDreamNotes('  first dream  \\n\\n second dream ');\n"
            'assert.equal(result, "[dream] first dream\\n[dream] second dream");\n'
            "const v = getVersion();\n"
            "assert.equal(typeof v, 'string');\n"
            "assert.ok(/^\\d+\\.\\d+\\.\\d+/.test(v));\n"
            "assert.equal(typeof VERSION, 'string');\n"
            "assert.equal(VERSION, getVersion());\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-text-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                f"file://{tmp_path}/tests/smoke.test.js:4\n"
                "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal"
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="function refineDreamNotes(notes)",
        )

    def test_applies_javascript_export_contract_repair_for_app_metadata(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "dream-note-alchemy-furnace",
                    "version": "0.1.0",
                    "description": "Dream note alchemy CLI",
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            "export function getAppInfo() {\n  return { ok: true };\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "version.test.js").write_text(
            'import assert from "node:assert/strict";\n'
            'import { APP_NAME, APP_VERSION, APP_DESCRIPTION, getAppInfo } from "../src/index.js";\n'
            "assert.equal(typeof APP_NAME, 'string');\n"
            "assert.ok(APP_NAME.length > 0);\n"
            "assert.match(APP_VERSION, /^\\d+\\.\\d+\\.\\d+/);\n"
            "assert.equal(typeof APP_DESCRIPTION, 'string');\n"
            "const info = getAppInfo();\n"
            "assert.equal(info.name, APP_NAME);\n"
            "assert.equal(info.version, APP_VERSION);\n"
            "assert.equal(info.description, APP_DESCRIPTION);\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-app-metadata-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                f"file://{tmp_path}/tests/version.test.js:8\n"
                "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal",
                "Artifact quality scan failed: unresolved import symbol 'APP_DESCRIPTION' "
                "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
                "Artifact quality scan failed: unresolved import symbol 'APP_NAME' "
                "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
                "Artifact quality scan failed: unresolved import symbol 'APP_VERSION' "
                "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="return { ok: true };",
        )

    def test_applies_javascript_export_contract_repair_for_asserted_literal_and_note_shape(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "dream-note-alchemy-furnace",
                    "version": "0.1.0",
                    "description": "Dream note alchemy CLI",
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            "export function main() {\n  return true;\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "test_index.js").write_text(
            'import assert from "node:assert/strict";\n'
            'import { ALCHEMY_FURNACE, refineDreamNote } from "../src/index.js";\n'
            'assert.equal(typeof ALCHEMY_FURNACE, "string");\n'
            'assert.equal(ALCHEMY_FURNACE, "dream-note-alchemy-furnace");\n'
            'const result = refineDreamNote("  flying over paper lanterns  ");\n'
            "assert.deepEqual(result, {\n"
            '  source: "  flying over paper lanterns  ",\n'
            '  refined: "flying over paper lanterns",\n'
            '  tag: "dream-fragment",\n'
            "});\n"
            'const empty = refineDreamNote("   ");\n'
            'assert.equal(empty.source, "   ");\n'
            'assert.equal(empty.refined, "");\n'
            'assert.equal(empty.tag, "empty");\n',
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-note-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved import symbol 'ALCHEMY_FURNACE' "
                "from '../src/index.js' in tests/test_index.js (sibling module does not define it)",
                "Artifact quality scan failed: unresolved import symbol 'refineDreamNote' "
                "from '../src/index.js' in tests/test_index.js (sibling module does not define it)",
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="export function main()",
        )


# ---------------------------------------------------------------------------
# Artifact path / read / write / audit
# ---------------------------------------------------------------------------


class TestRunWorkspaceQualityCommand:
    def test_executable_not_found(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command(["definitely-not-a-real-binary-xyz"], 5.0)
        assert result["passed"] is False
        assert result["exit_code"] is None
        assert "executable not found" in result["error"]

    def test_real_subprocess_success(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command([sys.executable, "-c", "print('ok')"], 30.0)
        assert result["exit_code"] == 0
        assert result["passed"] is True
        assert "ok" in result["stdout_tail"]

    def test_real_subprocess_zero_exit_with_typescript_errors_fails(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command(
            [
                sys.executable,
                "-c",
                'print("src/main.ts(1,1): error TS2305: missing export"); print("TypeScript check skipped")',
            ],
            30.0,
        )
        assert result["exit_code"] == 0
        assert result["passed"] is False
        assert "TypeScript compiler errors" in result["error"]

    def test_real_subprocess_zero_exit_with_skipped_javac_failure_fails(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    'print("setUpClass (test_product.JavaCompileAndRunTests) ... '
                    "skipped 'javac (main) failed; cannot continue runtime tests.\\n"
                    "stderr:\\n"
                    "src/main/java/polaris/factory/Main.java:119: error: incompatible types'\", file=sys.stderr)"
                ),
            ],
            30.0,
        )
        assert result["exit_code"] == 0
        assert result["passed"] is False
        assert "skipped tests caused by compile/build failure" in result["error"]

    def test_real_subprocess_enriches_nested_javac_called_process_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        bin_dir = tmp_path / "bin"
        source_path = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "Main.java"
        output_dir = tmp_path / "build" / "classes"
        bin_dir.mkdir()
        source_path.parent.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        source_path.write_text("package polaris.factory;\nclass Main {}\n", encoding="utf-8")
        fake_javac = bin_dir / "javac"
        fake_javac.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print(f'{sys.argv[-1]}:7: error: cannot find symbol', file=sys.stderr)\n"
            "print('  symbol:   class RhythmReport', file=sys.stderr)\n"
            "print('1 error', file=sys.stderr)\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        fake_javac.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

        result = executor._run_workspace_quality_command(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess; "
                    "subprocess.run("
                    f"['javac', '-encoding', 'UTF-8', '-d', {str(output_dir)!r}, {str(source_path)!r}], "
                    "check=True, capture_output=True)"
                ),
            ],
            30.0,
        )

        assert result["exit_code"] == 1
        assert result["passed"] is False
        assert "Nested javac diagnostics from unittest subprocess" in result["stderr_tail"]
        assert "cannot find symbol" in result["stderr_tail"]
        assert "RhythmReport" in result["stderr_tail"]
        assert result["nested_diagnostics"] in result["stderr_tail"]

    def test_real_subprocess_nonzero_exit(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command([sys.executable, "-c", "import sys; sys.exit(3)"], 30.0)
        assert result["exit_code"] == 3
        assert result["passed"] is False

    def test_real_subprocess_nonzero_typescript_error_is_not_marked_masked(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command(
            [
                sys.executable,
                "-c",
                "import sys; print(\"src/engine/renderer.ts(1,3780): error TS1005: '}' expected.\"); sys.exit(2)",
            ],
            30.0,
        )
        assert result["exit_code"] == 2
        assert result["passed"] is False
        assert "error" not in result

    def test_real_subprocess_timeout(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command([sys.executable, "-c", "import time; time.sleep(5)"], 0.5)
        assert result["passed"] is False
        assert result["exit_code"] is None
        assert "timeout after" in result["error"]

    def test_trim_command_output_preserves_early_tap_failure_and_final_summary(self) -> None:
        output = (
            "TAP version 13\n"
            "# Subtest: failing behavior\n"
            "not ok 2 - failing behavior\n"
            "  failureType: 'testCodeFailure'\n"
            "  error: assert.ok(keywords.includes('火焰'))\n"
            "  code: 'ERR_ASSERTION'\n" + ("ok 99 - unrelated passing test\n" * 500) + "# pass 22\n# fail 1\n"
        )

        trimmed = OrchestrationStageExecutor._trim_command_output(output, limit=2_000)

        assert len(trimmed) <= 2_000
        assert "not ok 2 - failing behavior" in trimmed
        assert trimmed.count("not ok 2 - failing behavior") == 1
        assert "assert.ok(keywords.includes('火焰'))" in trimmed
        assert "# fail 1" in trimmed

    def test_trim_command_output_slices_never_cut_a_diagnostic_line_in_half(self) -> None:
        """Preserved segments must start on line boundaries.

        Live L1-06: the g++ failure excerpt slice landed inside
        ``src/engi|ne/generator.hpp:52:52`` and the mangled first line became
        the only diagnostic the runtime normalizer accepted, so the repair
        claim found no canonical owner and the gate failed without a single
        LLM repair round.  A byte-exact slice that opens mid-line is never
        valid verifier evidence.
        """

        filler_line = "ok 99 - unrelated passing test with a fairly long body for budget pressure\n"
        head_filler = "compiling translation units\n" * 6
        error_block = (
            "src/engine/generator.hpp:52:52: error: 'StampError' has not been declared\n"
            "   52 |                           StampError stamp_error = StampError::Ok);\n"
            "src/engine/generator.hpp:97:42: error: 'Moon' in namespace 'moonpost' does not name a type\n"
        )
        # Marker (first "error" line) sits well past the head budget, and the
        # tail budget lands mid-filler, so every slice boundary is exercised.
        output = head_filler + filler_line * 400 + error_block + filler_line * 200 + "# fail 3\n"

        trimmed = OrchestrationStageExecutor._trim_command_output(output, limit=2_000)

        assert len(trimmed) <= 2_000
        # The diagnostic path must survive intact, never a mid-path fragment.
        assert "src/engine/generator.hpp:52:52:" in trimmed
        for line in trimmed.splitlines():
            if "generator.hpp" in line or "has not been declared" in line:
                assert not line.startswith("ne/") and not line.startswith("gine")


class TestWorkspaceQualityDeterministicRepairExecution:
    def test_synthetic_repair_task_id_is_event_store_safe(self) -> None:
        task_id = stage_executor_module._workspace_quality_repair_external_task_id(
            "factory:run/with spaces",
            2,
        )

        assert ":" not in task_id
        assert "/" not in task_id
        assert " " not in task_id
        assert task_id.startswith("factory-quality-gate-factory-run-with-spaces-repair-2-")

    def test_deterministic_repair_preserves_exact_task_scope(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        captured: dict[str, Any] = {}

        def fake_schedule(_adapter: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            captured.update(kwargs)
            return [], {"attempted": False}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.run_director_materialization_quality_repair_schedule",
            fake_schedule,
        )

        executor._apply_workspace_quality_repairs(
            run_id="factory-scope",
            artifact_quality_errors=["src/engine/rules.js failed"],
            task_id="TASK-2",
            repair_task={
                "id": "TASK-2",
                "goal": "Repair engine rule",
                "target_files": ["src/engine/rules.js"],
                "metadata": {"blueprint_id": "ce_TASK-2"},
            },
        )

        task = captured["task"]
        assert task["id"] == "TASK-2"
        assert task["target_files"] == ["src/engine/rules.js"]
        assert task["metadata"]["blueprint_id"] == "ce_TASK-2"
        assert "factory_workspace_quality_repair" not in task["metadata"]

    @pytest.mark.asyncio
    async def test_claims_commits_and_settles_deferred_repair_on_director_task(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-deferred",
            config=FactoryConfig(name="quality-deferred"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-10T00:00:00+00:00",
        )
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=7,
            external_task_id="TASK-3",
            session_id="quality-repair-session",
            attempt=2,
            role_id="director",
            worker_id="director",
            run_id=run.id,
            lease_expires_at="2026-08-10T00:05:00+00:00",
        )
        deferred_result = {
            "success": True,
            "result": {
                "status": "deferred_repair_effects_pending",
                "deferred_request": {"request_id": "repair-request-1"},
            },
        }
        commit_calls: list[dict[str, Any]] = []
        heartbeat_calls: list[dict[str, Any]] = []

        class FakeAttemptAuthority:
            def heartbeat(self, **kwargs: Any) -> SimpleNamespace:
                heartbeat_calls.append(kwargs)
                return SimpleNamespace(success=True, code="heartbeat_renewed")

        monkeypatch.setattr(
            factory_workspace_quality_impl,
            "_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS",
            0.001,
        )
        monkeypatch.setattr(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            lambda _identity: FakeAttemptAuthority(),
        )

        monkeypatch.setattr(
            executor,
            "_director_stage_materialization_settle_target_files",
            lambda *, diagnostics: ["package.json", "tests/verify.test.js"],
        )
        monkeypatch.setattr(
            executor,
            "_claim_workspace_quality_repair_attempt",
            lambda **_kwargs: ("TASK-3", 7, identity, {"id": "TASK-3"}),
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_repairs",
            lambda **_kwargs: (
                [deferred_result],
                {
                    "source_tools": ["deterministic_javascript_test_missing_target_repair"],
                    "tool_results": 1,
                },
            ),
        )
        monkeypatch.setattr(
            executor,
            "_director_stage_materialization_settle_commit_context",
            lambda **_kwargs: {"factory_stage": "quality_gate"},
        )
        monkeypatch.setattr(
            executor,
            "_settle_director_stage_materialization_attempt",
            lambda **_kwargs: {"success": True},
        )

        async def fake_commit_materialization_deferred_repairs(**kwargs: Any) -> list[dict[str, Any]]:
            commit_calls.append(kwargs)
            await asyncio.sleep(0.01)
            return [
                {
                    "success_count": 1,
                    "failure_count": 0,
                    "results": [{"status": "success"}],
                }
            ]

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.commit_materialization_deferred_repairs",
            fake_commit_materialization_deferred_repairs,
        )

        results, summary = await executor._apply_workspace_quality_deterministic_repairs(
            run=run,
            artifact_quality_errors=["Could not find 'tests/verify.test.js'"],
            repair_attempt=2,
        )

        assert results == [deferred_result]
        assert len(commit_calls) == 1
        assert commit_calls[0]["execution_attempt"] == identity
        assert heartbeat_calls
        assert heartbeat_calls[0]["lease_ttl_seconds"] == 300
        assert heartbeat_calls[0]["context_summary"] == "director_workspace_quality_deterministic_repair"
        assert summary["success"] is True
        assert summary["write_tool_evidence"] is True
        assert summary["committed_receipt_count"] == 1
        assert summary["task_runtime_repair_attempt"] == {
            "task_id": "TASK-3",
            "session_id": "quality-repair-session",
            "settled": False,
            "outcome": "pending_revalidation",
        }
        assert summary["_pending_task_runtime_repair_attempt"]["execution_attempt"] == identity
        settled = await factory_workspace_quality_impl._settle_pending_workspace_quality_repair_attempt(
            executor,
            summary.pop("_pending_task_runtime_repair_attempt"),
            accepted=True,
            reason="test_post_repair_verifier_passed",
        )
        assert settled is not None
        assert settled["outcome"] == "completed"

    @pytest.mark.asyncio
    async def test_deterministic_repair_heartbeat_failure_invalidates_committed_receipt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-heartbeat-failed",
            config=FactoryConfig(name="quality-heartbeat-failed"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-12T00:00:00+00:00",
        )
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=8,
            external_task_id="TASK-4",
            session_id="quality-heartbeat-failed-session",
            attempt=1,
            role_id="director",
            worker_id="director",
            run_id=run.id,
            lease_expires_at="2026-08-12T00:05:00+00:00",
        )
        settled: dict[str, Any] = {}

        async def fail_heartbeat(
            _authority: Any,
            *,
            stop: asyncio.Event,
            failures: list[dict[str, Any]],
            context_summary: str,
        ) -> None:
            failures.append({"code": "lease_expired", "context_summary": context_summary})
            await stop.wait()

        monkeypatch.setattr(factory_workspace_quality_impl, "_run_workspace_quality_repair_heartbeat", fail_heartbeat)
        monkeypatch.setattr(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            lambda _identity: object(),
        )
        monkeypatch.setattr(
            executor,
            "_director_stage_materialization_settle_target_files",
            lambda *, diagnostics: ["src/app.js"],
        )
        monkeypatch.setattr(
            executor,
            "_claim_workspace_quality_repair_attempt",
            lambda **_kwargs: ("TASK-4", 8, identity, {"id": "TASK-4", "target_files": ["src/app.js"]}),
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_repairs",
            lambda **_kwargs: (
                [{"success": True, "result": {"status": "deferred_repair_effects_pending", "deferred_request": {}}}],
                {"source_tools": ["deterministic_test_repair"]},
            ),
        )
        monkeypatch.setattr(
            executor,
            "_director_stage_materialization_settle_commit_context",
            lambda **_kwargs: {},
        )
        monkeypatch.setattr(
            executor,
            "_settle_director_stage_materialization_attempt",
            lambda **kwargs: settled.update(kwargs) or {"success": True},
        )

        async def fake_commit(**_kwargs: Any) -> list[dict[str, Any]]:
            await asyncio.sleep(0)
            return [{"success_count": 1, "failure_count": 0, "results": [{"status": "success"}]}]

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.commit_materialization_deferred_repairs",
            fake_commit,
        )

        _, summary = await executor._apply_workspace_quality_deterministic_repairs(
            run=run,
            artifact_quality_errors=["src/app.js failed"],
            repair_attempt=1,
        )

        assert summary["success"] is False
        assert summary["write_tool_evidence"] is False
        assert summary["committed_receipt_count"] == 1
        assert summary["execution_attempt_heartbeat_failures"][0]["code"] == "lease_expired"
        assert summary["task_runtime_repair_attempt"]["outcome"] == "failed"
        assert settled["stage_status"] == "failed"

    @pytest.mark.asyncio
    async def test_llm_repair_heartbeat_failure_invalidates_physical_mutation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl

        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "app.js").write_text("export const app = 1;\n", encoding="utf-8")
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=9,
            external_task_id="TASK-5",
            session_id="quality-llm-heartbeat-failed-session",
            attempt=1,
            role_id="director",
            worker_id="director",
            run_id="factory-quality-llm-heartbeat-failed",
            lease_expires_at="2026-08-12T00:05:00+00:00",
        )
        settled: dict[str, Any] = {}

        async def fail_heartbeat(
            _authority: Any,
            *,
            stop: asyncio.Event,
            failures: list[dict[str, Any]],
            context_summary: str,
        ) -> None:
            failures.append({"code": "heartbeat_rejected", "context_summary": context_summary})
            await stop.wait()

        async def fake_llm_repair(*_args: Any, **_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "src/app.js",
                            "before_sha256": "a" * 64,
                            "after_sha256": "b" * 64,
                        },
                    }
                ],
                {"attempted": True, "success": True},
            )

        monkeypatch.setattr(factory_workspace_quality_impl, "_run_workspace_quality_repair_heartbeat", fail_heartbeat)
        monkeypatch.setattr(
            "polaris.cells.runtime.task_runtime.public.create_task_runtime_execution_attempt_authority",
            lambda _identity: object(),
        )
        monkeypatch.setattr(
            executor,
            "_claim_workspace_quality_repair_attempt",
            lambda **_kwargs: ("TASK-5", 9, identity, {"id": "TASK-5", "target_files": ["src/app.js"]}),
        )
        monkeypatch.setattr(
            executor,
            "_settle_director_stage_materialization_attempt",
            lambda **kwargs: settled.update(kwargs) or {"success": True},
        )
        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.run_director_materialization_quality_repair",
            fake_llm_repair,
        )

        run = FactoryRun(
            id="factory-quality-llm-heartbeat-failed",
            config=FactoryConfig(name="quality-llm-heartbeat", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-13T00:00:00+00:00",
        )
        _, summary = await executor._apply_workspace_quality_llm_repairs(
            run=run,
            context={},
            artifact_quality_errors=["src/app.js failed"],
            repair_attempt=1,
        )

        assert summary["execution_attempt_heartbeat_failures"][0]["code"] == "heartbeat_rejected"
        assert summary["task_runtime_repair_attempt"]["outcome"] == "failed"
        assert settled["stage_status"] == "failed"

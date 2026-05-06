from __future__ import annotations

import tempfile
from types import SimpleNamespace

from polaris.delivery.cli.pm.orchestration.core import load_state_and_context
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path


def test_load_state_and_context_falls_back_to_persistent_docs() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        requirements_full = resolve_artifact_path(
            workspace,
            "",
            "workspace/docs/product/requirements.md",
        )
        plan_full = resolve_artifact_path(
            workspace,
            "",
            "workspace/docs/product/plan.md",
        )
        write_text_atomic(requirements_full, "# Requirements\nPERSISTENT-REQ\n")
        write_text_atomic(plan_full, "# Plan\nPERSISTENT-PLAN\n")
        args = SimpleNamespace(
            plan_path="runtime/contracts/plan.md",
            gap_report_path="runtime/contracts/gap_report.md",
            qa_path="runtime/results/qa.review.md",
            requirements_path="runtime/contracts/requirements.md",
            pm_out="runtime/contracts/pm_tasks.contract.json",
            state_path="runtime/state/pm.state.json",
            clear_spin_guard=False,
            directive="",
            directive_file="",
            directive_stdin=False,
            directive_max_chars=200000,
            start_from="pm",
        )

        context = load_state_and_context(workspace, "", args, 1)

        assert "PERSISTENT-REQ" in str(context["requirements"])
        assert "PERSISTENT-PLAN" in str(context["plan_text"])

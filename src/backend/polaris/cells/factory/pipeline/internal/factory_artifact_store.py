"""Artifact filesystem store for the factory stage executor.

Holds the path-resolution / read / write / copy / mirror / audit / exists I/O
extracted verbatim from ``OrchestrationStageExecutor``. The store owns a
``workspace`` root and a ``KernelFileSystem`` handle; ``OrchestrationStageExecutor``
keeps same-named delegating shims so every test-called / monkeypatched entry
point (``_artifact_path``, ``_artifact_exists``, ``_ensure_docs_artifacts`` …)
is preserved with identical names and signatures.

Behavior preservation notes:

* ``artifact_path`` keeps the test-asserted rewrite ordering: ``docs`` /
  ``docs/*`` → ``workspace/...`` BEFORE the ``tasks/`` / ``dispatch/`` →
  ``runtime/...`` branch, then ``resolve_logical_path``.
* Mirror helpers reuse the same module-level ``extend_artifacts`` /
  ``copy_text_artifact_if_present`` paths so dedupe + normalization stay
  identical.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.storage import resolve_logical_path

from . import factory_stage_helpers as helpers

if TYPE_CHECKING:
    from polaris.kernelone.fs import KernelFileSystem

logger = logging.getLogger(__name__)


class ArtifactStore:
    """Filesystem-backed artifact store for factory stage outputs."""

    def __init__(self, workspace: Path, fs: KernelFileSystem) -> None:
        self.workspace = Path(workspace)
        self._fs = fs

    def artifact_path(self, relative_path: str) -> Path:
        rel = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
        if rel == "docs" or rel.startswith("docs/"):
            rel = f"workspace/{rel}"
        elif rel.startswith(("tasks/", "dispatch/")):
            rel = f"runtime/{rel}"
        # 使用逻辑路径解析：workspace/* -> runtime/workspace/*, runtime/* -> runtime/...
        resolved = resolve_logical_path(str(self.workspace), rel)
        return Path(resolved).resolve()

    def write_json_artifact(self, relative_path: str, payload: dict[str, Any]) -> Path:
        target = self.artifact_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._fs.write_json(str(target), payload)
        return target

    def write_text_artifact(self, relative_path: str, content: str) -> Path:
        target = self.artifact_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._fs.write_text(str(target), str(content or ""))
        return target

    def write_stage_signal_artifact(
        self,
        *,
        stage: str,
        run_id: str,
        signals: list[dict[str, Any]],
    ) -> str:
        target_rel = f"runtime/signals/{stage}.signals.json"
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run_id,
            "stage": stage,
            "signals": signals,
        }
        self.write_json_artifact(target_rel, payload)
        return target_rel

    def copy_text_artifact(self, source_relative_path: str, target_relative_path: str) -> str:
        source = self.artifact_path(source_relative_path)
        if not source.exists() or not source.is_file():
            return ""
        content = source.read_text(encoding="utf-8")
        self.write_text_artifact(target_relative_path, content)
        return str(target_relative_path or "").replace("\\", "/").strip().lstrip("/")

    def copy_text_artifact_if_present(
        self,
        source_relative_path: str,
        target_relative_path: str,
        *,
        min_chars: int = 1,
    ) -> str:
        if not self.artifact_exists(source_relative_path, min_chars=min_chars):
            return ""
        try:
            return self.copy_text_artifact(source_relative_path, target_relative_path)
        except (OSError, UnicodeDecodeError):
            logger.debug(
                "Failed to mirror factory artifact: source=%s target=%s",
                source_relative_path,
                target_relative_path,
            )
            return ""

    def read_text_artifact(self, relative_path: str, *, min_chars: int = 1) -> str:
        target = self.artifact_path(relative_path)
        if not target.exists() or not target.is_file():
            return ""
        try:
            text = target.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""
        if len(text) < min_chars:
            return ""
        return text

    def emit_audit_event(self, event_type: str, **kwargs: Any) -> None:
        """Emit an audit event for tracking purposes."""
        audit_entry = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs,
        }
        # Write to audit trail
        audit_path = self.workspace / ".polaris" / "audit" / f"{event_type}.json"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = []
            if audit_path.exists():
                existing = json.loads(audit_path.read_text(encoding="utf-8"))
            existing.append(audit_entry)
            audit_path.write_text(
                json.dumps(existing, ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
        except (OSError, json.JSONDecodeError):
            logger.debug("Failed to write audit event: %s", event_type)

    def artifact_exists(self, relative_path: str, *, min_chars: int = 1) -> bool:
        target = self.artifact_path(relative_path)
        if not target.exists() or not target.is_file():
            return False
        if min_chars <= 0:
            return True
        try:
            return len(target.read_text(encoding="utf-8").strip()) >= min_chars
        except OSError:
            return False

    def missing_artifacts(self, artifacts: list[str], *, min_chars: int = 1) -> list[str]:
        return [item for item in artifacts if not self.artifact_exists(item, min_chars=min_chars)]

    def mirror_docs_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        role_root = f"workspace/roles/architect/{run_id}"
        for source_rel, filename in (
            ("docs/plan.md", "plan.md"),
            ("docs/architecture.md", "architecture.md"),
        ):
            mirrored = self.copy_text_artifact_if_present(
                source_rel,
                f"{role_root}/{filename}",
                min_chars=1,
            )
            helpers.extend_artifacts(artifacts, mirrored)

    def mirror_pm_plan_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        mirrors = (
            f"workspace/roles/pm/{run_id}/plan.json",
            f"workspace/plans/{run_id}.plan.json",
            "workspace/plans/latest.plan.json",
        )
        copied = [
            self.copy_text_artifact_if_present("tasks/plan.json", target_rel, min_chars=1) for target_rel in mirrors
        ]
        helpers.extend_artifacts(artifacts, *copied)

    def mirror_chief_engineer_artifacts(
        self,
        run_id: str,
        blueprint_rows: list[dict[str, Any]],
        review_artifact: str,
        artifacts: list[str],
    ) -> None:
        review_mirrors = (
            f"workspace/roles/chief_engineer/{run_id}/review.json",
            f"workspace/blueprints/{run_id}.review.json",
            "workspace/blueprints/latest.review.json",
        )
        copied_review = [
            self.copy_text_artifact_if_present(review_artifact, target_rel, min_chars=1)
            for target_rel in review_mirrors
            if review_artifact
        ]
        helpers.extend_artifacts(artifacts, *copied_review)

        copied_blueprints: list[str] = []
        for row in blueprint_rows:
            source_rel = str(row.get("blueprint_path") or "").strip()
            blueprint_id = str(row.get("blueprint_id") or Path(source_rel).stem).strip()
            if not source_rel or not blueprint_id:
                continue
            copied_blueprints.append(
                self.copy_text_artifact_if_present(
                    source_rel,
                    f"workspace/roles/chief_engineer/{run_id}/blueprints/{blueprint_id}.json",
                    min_chars=1,
                )
            )
            copied_blueprints.append(
                self.copy_text_artifact_if_present(
                    source_rel,
                    f"workspace/blueprints/{blueprint_id}.json",
                    min_chars=1,
                )
            )
        helpers.extend_artifacts(artifacts, *copied_blueprints)

    def mirror_director_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        mirrors = (
            f"workspace/roles/director/{run_id}/dispatch.log.json",
            f"workspace/dispatch/{run_id}.log.json",
            "workspace/dispatch/latest.log.json",
        )
        copied = [
            self.copy_text_artifact_if_present("dispatch/log.json", target_rel, min_chars=1) for target_rel in mirrors
        ]
        helpers.extend_artifacts(artifacts, *copied)

    def mirror_quality_gate_artifacts(self, run_id: str, artifacts: list[str]) -> None:
        report_mirrors = (
            f"workspace/roles/qa/{run_id}/report.json",
            f"workspace/qa/{run_id}.report.json",
            "workspace/qa/latest.report.json",
        )
        copied = [
            self.copy_text_artifact_if_present("runtime/qa/report.json", target_rel, min_chars=1)
            for target_rel in report_mirrors
        ]
        validation_mirrors = (
            f"workspace/roles/qa/{run_id}/workspace-validation.json",
            f"workspace/qa/{run_id}.workspace-validation.json",
            "workspace/qa/latest.workspace-validation.json",
        )
        copied.extend(
            self.copy_text_artifact_if_present("runtime/qa/workspace-validation.json", target_rel, min_chars=1)
            for target_rel in validation_mirrors
        )
        helpers.extend_artifacts(artifacts, *copied)

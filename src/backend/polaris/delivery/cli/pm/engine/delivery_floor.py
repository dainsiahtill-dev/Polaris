"""Delivery floor validation module for Polaris engine.

This module handles delivery threshold validation to ensure
sufficient code/test output before marking iteration complete.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.pm.engine.helpers import (
    _env_non_negative_int,
    _estimate_code_lines_from_workspace,
    _is_truthy_env,
    _looks_like_code_file,
    _looks_like_test_file,
    _safe_int,
)
from polaris.delivery.cli.pm.utils import normalize_path_list
from polaris.kernelone.runtime.shared_types import normalize_path

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_DELIVERY_FLOOR_DEFAULTS = {
    "small": {"code_files": 2, "code_lines": 40, "test_files": 1},
    "medium": {"code_files": 4, "code_lines": 120, "test_files": 1},
    "large": {"code_files": 8, "code_lines": 250, "test_files": 2},
    "default": {"code_files": 2, "code_lines": 40, "test_files": 1},
}


def _looks_like_stress_workspace(workspace_full: str) -> bool:
    """Check if workspace is a stress test workspace."""
    token = str(workspace_full or "").strip().replace("\\", "/").lower()
    return "polaris_stress" in token


def _detect_project_scale(workspace_full: str) -> str:
    """Detect project scale from workspace name."""
    token = str(workspace_full or "").strip().replace("\\", "/").lower()
    for marker in ("small", "medium", "large"):
        if f"-{marker}-" in token or token.endswith(f"-{marker}"):
            return marker
    return "default"


def _resolve_delivery_floor_thresholds(scale: str) -> dict[str, int]:
    """Resolve delivery floor thresholds for project scale."""
    defaults = _DELIVERY_FLOOR_DEFAULTS.get(scale) or _DELIVERY_FLOOR_DEFAULTS["default"]
    scale_token = str(scale or "default").strip().upper()
    return {
        "code_files": _env_non_negative_int(
            f"KERNELONE_DELIVERY_FLOOR_{scale_token}_CODE_FILES",
            int(defaults["code_files"]),
        ),
        "code_lines": _env_non_negative_int(
            f"KERNELONE_DELIVERY_FLOOR_{scale_token}_CODE_LINES",
            int(defaults["code_lines"]),
        ),
        "test_files": _env_non_negative_int(
            f"KERNELONE_DELIVERY_FLOOR_{scale_token}_TEST_FILES",
            int(defaults["test_files"]),
        ),
    }


def _evaluate_delivery_floor(
    records: Sequence[dict[str, Any]],
    *,
    workspace_full: str,
) -> dict[str, Any]:
    """Evaluate delivery floor thresholds against task results."""
    explicit_toggle = _is_truthy_env("KERNELONE_DELIVERY_FLOOR_ENABLED")
    enabled = explicit_toggle if explicit_toggle is not None else _looks_like_stress_workspace(workspace_full)
    scale = _detect_project_scale(workspace_full)
    thresholds = _resolve_delivery_floor_thresholds(scale)
    if not enabled:
        return {
            "enabled": False,
            "passed": True,
            "scale": scale,
            "thresholds": thresholds,
            "metrics": {},
            "reasons": [],
        }

    done_records = [item for item in (records or []) if str(item.get("pm_status") or "").strip().lower() == "done"]
    code_files: set[str] = set()
    test_files: set[str] = set()
    code_lines = 0

    for record in done_records:
        payload = record.get("result_payload")
        payload = payload if isinstance(payload, dict) else {}
        changed_files = normalize_path_list(payload.get("changed_files") or [])
        target_files = normalize_path_list(record.get("target_files") or [])
        candidate_files = normalize_path_list(changed_files + target_files)
        has_code_change = False
        for rel in candidate_files:
            normalized = normalize_path(rel)
            if not normalized:
                continue
            if _looks_like_test_file(normalized):
                test_files.add(normalized)
            if _looks_like_code_file(normalized):
                has_code_change = True
                code_files.add(normalized)
        factors = (payload.get("patch_risk") or {}).get("factors")
        factors = factors if isinstance(factors, dict) else {}
        lines_added_raw = factors.get("lines_added")
        lines_added: int | None = None
        if lines_added_raw not in (None, ""):
            lines_added = max(0, _safe_int(lines_added_raw, default=0))
        if has_code_change:
            if lines_added is not None:
                code_lines += int(lines_added)
            else:
                code_lines += _estimate_code_lines_from_workspace(workspace_full, candidate_files)

    metrics = {
        "done_tasks": len(done_records),
        "code_files": len(code_files),
        "code_lines": code_lines,
        "test_files": len(test_files),
    }
    reasons: list[str] = []
    if metrics["code_files"] < thresholds["code_files"]:
        reasons.append(f"code_files<{thresholds['code_files']} (actual={metrics['code_files']})")
    if metrics["code_lines"] < thresholds["code_lines"]:
        reasons.append(f"code_lines<{thresholds['code_lines']} (actual={metrics['code_lines']})")
    if metrics["test_files"] < thresholds["test_files"]:
        reasons.append(f"test_files<{thresholds['test_files']} (actual={metrics['test_files']})")

    return {
        "enabled": True,
        "passed": len(reasons) == 0,
        "scale": scale,
        "thresholds": thresholds,
        "metrics": metrics,
        "reasons": reasons,
    }


__all__ = [
    "_DELIVERY_FLOOR_DEFAULTS",
    "_detect_project_scale",
    "_evaluate_delivery_floor",
    "_looks_like_stress_workspace",
    "_resolve_delivery_floor_thresholds",
]

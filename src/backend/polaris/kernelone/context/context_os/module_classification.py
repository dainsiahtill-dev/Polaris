"""ContextOS module classification diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def get_contextos_module_classification_diagnostics() -> dict[str, Any]:
    """Return diagnostics for the ContextOS hot-path/dormant module manifest."""

    manifest_path = _default_manifest_path()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "state": "manifest_unavailable",
            "ok": False,
            "details": {
                "manifest_path": str(manifest_path),
                "error": f"{type(exc).__name__}: {exc}",
            },
            "evidence": [str(manifest_path)],
        }

    dormant_modules = manifest.get("dormant_modules") if isinstance(manifest, dict) else []
    hot_path_modules = manifest.get("hot_path_modules") if isinstance(manifest, dict) else []
    dormant_count = len(dormant_modules) if isinstance(dormant_modules, list) else 0
    hot_path_count = len(hot_path_modules) if isinstance(hot_path_modules, list) else 0
    return {
        "state": "classified",
        "ok": True,
        "details": {
            "manifest_path": str(manifest_path),
            "schema_version": str(manifest.get("schema_version") or "") if isinstance(manifest, dict) else "",
            "last_reviewed": str(manifest.get("last_reviewed") or "") if isinstance(manifest, dict) else "",
            "owner_cell": str(manifest.get("owner_cell") or "") if isinstance(manifest, dict) else "",
            "hot_path_count": hot_path_count,
            "dormant_count": dormant_count,
            "dormant_modules": dormant_modules if isinstance(dormant_modules, list) else [],
        },
        "evidence": [str(manifest_path)],
    }


def _default_manifest_path() -> Path:
    backend_root = Path(__file__).resolve().parents[4]
    return backend_root / "docs" / "governance" / "contextos_module_classification.json"


__all__ = ["get_contextos_module_classification_diagnostics"]

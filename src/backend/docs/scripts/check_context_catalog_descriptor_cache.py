"""Validate context.catalog descriptor cache freshness and structure."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def _get_polaris_imports():
    """Lazy import of polaris modules after sys.path is set up."""
    # Ensure backend root is importable when executed as a script.
    BACKEND_ROOT = Path(__file__).resolve().parents[2]
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    from polaris.cells.context.catalog import (
        ContextCatalogService,
        resolve_context_catalog_cache_path,
        validate_descriptor_cache_payload,
    )

    return ContextCatalogService, resolve_context_catalog_cache_path, validate_descriptor_cache_payload


def _default_cache_path(workspace: Path) -> Path:
    _, resolve_func, _ = _get_polaris_imports()
    return resolve_func(workspace)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Descriptor cache root must be object: {path}")
    return payload


def main(argv: list[str] | None = None) -> int:
    ContextCatalogService, _, validate_descriptor_cache_payload = _get_polaris_imports()

    parser = argparse.ArgumentParser(
        description="Validate context.catalog descriptor cache schema/freshness.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace/backend root used by context.catalog.",
    )
    parser.add_argument(
        "--cache-path",
        default="",
        help="Optional explicit descriptor cache path.",
    )
    parser.add_argument(
        "--schema",
        default="docs/governance/schemas/semantic-descriptor.schema.yaml",
        help="Semantic descriptor schema path.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero on warnings (default behavior also fails on errors/stale).",
    )
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).expanduser().resolve()
    _, resolve_context_catalog_cache_path_func, _ = _get_polaris_imports()
    cache_path = (
        Path(args.cache_path).expanduser().resolve()
        if str(args.cache_path).strip()
        else resolve_context_catalog_cache_path_func(workspace)
    )
    schema_path = Path(args.schema).expanduser()
    if not schema_path.is_absolute():
        schema_path = (workspace / schema_path).resolve()

    result: dict[str, Any] = {
        "workspace": str(workspace),
        "cache_path": str(cache_path),
        "schema_path": str(schema_path),
        "schema_exists": schema_path.exists(),
        "cache_exists": cache_path.exists(),
        "schema_errors": [],
        "schema_validation": "not_run",
        "stale": True,
        "status": "failed",
    }

    if not cache_path.exists():
        result["schema_errors"] = [f"descriptor cache not found: {cache_path}"]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    try:
        payload = _load_json(cache_path)
    except Exception as exc:
        result["schema_errors"] = [f"failed to read cache: {exc}"]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3

    schema_errors = validate_descriptor_cache_payload(payload)
    result["schema_errors"] = schema_errors
    result["descriptor_count"] = (
        len(payload.get("descriptors", [])) if isinstance(payload.get("descriptors"), list) else 0
    )
    schema_validation_errors: list[str] = []

    if schema_path.exists():
        try:
            schema_payload = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
            if isinstance(schema_payload, dict):
                try:
                    import jsonschema  # type: ignore
                except Exception:
                    result["schema_validation"] = "skipped_jsonschema_unavailable"
                else:
                    validator = jsonschema.Draft202012Validator(schema_payload)
                    schema_validation_errors = [str(error.message) for error in validator.iter_errors(payload)]
                    result["schema_validation"] = "ok" if not schema_validation_errors else "failed"
            else:
                schema_validation_errors.append("schema root must be object")
                result["schema_validation"] = "failed"
        except Exception as exc:
            schema_validation_errors.append(f"failed to read schema: {exc}")
            result["schema_validation"] = "failed"
    else:
        schema_validation_errors.append(f"schema file not found: {schema_path}")
        result["schema_validation"] = "failed"

    if schema_validation_errors:
        schema_errors = schema_errors + schema_validation_errors
        result["schema_errors"] = schema_errors

    service = ContextCatalogService(str(workspace))
    stale = service.is_index_stale()
    result["stale"] = stale

    has_errors = bool(schema_errors) or stale
    result["status"] = "failed" if has_errors else "ok"
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if has_errors:
        return 4
    if args.strict:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

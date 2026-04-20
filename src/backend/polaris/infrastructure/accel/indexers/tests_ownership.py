from __future__ import annotations

from pathlib import Path
from typing import Any


def _is_test_file(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lower()
    name = Path(normalized).name
    return (
        "/tests/" in normalized
        or name.startswith("test_")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
        or name.endswith("_test.py")
    )


def _tokenize_name(path: str) -> set[str]:
    stem = Path(path).stem.lower()
    for marker in ("test_", "_test", ".test", ".spec", "spec_", "tests_"):
        stem = stem.replace(marker, "")
    tokens = {token for token in stem.replace("-", "_").split("_") if token}
    return tokens


def build_test_ownership(
    all_files: list[str],
    dependencies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tests = [path for path in all_files if _is_test_file(path)]
    sources = [path for path in all_files if not _is_test_file(path)]
    source_by_name = {Path(path).name.lower(): path for path in sources}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    dep_map: dict[str, list[str]] = {}
    for dep in dependencies:
        dep_map.setdefault(str(dep.get("edge_from", "")), []).append(str(dep.get("edge_to", "")))

    for test_file in tests:
        tokens = _tokenize_name(test_file)
        for source_file in sources:
            source_tokens = _tokenize_name(source_file)
            if tokens and source_tokens and tokens.intersection(source_tokens):
                key = (test_file, source_file, "naming")
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "test_file": test_file,
                        "owns_file": source_file,
                        "owns_symbol": "",
                        "source": "naming",
                        "confidence": 0.6,
                    }
                )

        for imported in dep_map.get(test_file, []):
            imported_key = imported.strip().lower()
            if imported_key in source_by_name:
                source_file = source_by_name[imported_key]
                key = (test_file, source_file, "import")
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "test_file": test_file,
                        "owns_file": source_file,
                        "owns_symbol": "",
                        "source": "import",
                        "confidence": 0.8,
                    }
                )
    return rows

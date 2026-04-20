from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def project_hash(project_dir: Path) -> str:
    normalized = str(Path(os.path.abspath(str(project_dir)))).replace("\\", "/").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def project_paths(accel_home: Path, project_dir: Path) -> dict[str, Path]:
    p_hash = project_hash(project_dir)
    base = accel_home / "projects" / p_hash
    return {
        "base": base,
        "index": base / "index",
        "index_units": base / "index" / "file_units",
        "context": base / "context",
        "verify": base / "verify",
        "telemetry": base / "telemetry",
        "state": base / "state",
    }


def ensure_project_dirs(paths: dict[str, Path]) -> None:
    for key, value in paths.items():
        if key == "base":
            continue
        value.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    path.write_text(("\n".join(lines) + ("\n" if lines else "")), encoding="utf-8")

from __future__ import annotations

import ast
import os
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
COMPAT_ROOT = BACKEND_ROOT / "polaris" / "infrastructure" / "compat"
IO_UTILS_PATH = COMPAT_ROOT / "io_utils.py"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_io_utils_has_no_duplicate_top_level_defs() -> None:
    tree = ast.parse(_read_text(IO_UTILS_PATH))
    counts = defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[node.name].append(node.lineno)
    duplicates = {name: lines for name, lines in counts.items() if len(lines) > 1}
    assert duplicates == {}


def test_io_utils_exports_canonical_jsonl_facades(tmp_path: Path, monkeypatch) -> None:
    backend_root_abs = os.path.abspath(BACKEND_ROOT)
    if backend_root_abs not in sys.path:
        sys.path.insert(0, backend_root_abs)

    from polaris.infrastructure.compat import io_utils

    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))

    buffered_logical_path = "runtime/events/buffered.jsonl"
    atomic_logical_path = "runtime/events/atomic.jsonl"

    io_utils.append_jsonl(buffered_logical_path, {"kind": "buffered"})
    io_utils.flush_jsonl_buffers(force=True)
    io_utils.append_jsonl_atomic(atomic_logical_path, {"kind": "atomic"})

    from polaris.kernelone.storage import resolve_runtime_path

    buffered_path = Path(resolve_runtime_path(str(tmp_path), buffered_logical_path))
    atomic_path = Path(resolve_runtime_path(str(tmp_path), atomic_logical_path))

    assert buffered_path.is_file()
    assert atomic_path.is_file()


def test_io_utils_no_longer_keeps_legacy_stop_flag_aliases() -> None:
    text = _read_text(IO_UTILS_PATH)
    assert "PM_STOP.flag" not in text
    assert "DIRECTOR_STOP.flag" not in text

from __future__ import annotations

import ast
import os
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
COMPAT_ROOT = BACKEND_ROOT / "polaris" / "infrastructure" / "compat"
JSONL_OPS_PATH = BACKEND_ROOT / "polaris" / "kernelone" / "fs" / "jsonl" / "ops.py"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_jsonl_ops_has_no_duplicate_top_level_defs() -> None:
    tree = ast.parse(_read_text(JSONL_OPS_PATH))
    counts = defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            counts[node.name].append(node.lineno)
    duplicates = {name: lines for name, lines in counts.items() if len(lines) > 1}
    assert duplicates == {}


def test_kernelone_jsonl_ops_write_buffered_and_atomic_records(tmp_path: Path, monkeypatch) -> None:
    backend_root_abs = os.path.abspath(BACKEND_ROOT)
    if backend_root_abs not in sys.path:
        sys.path.insert(0, backend_root_abs)

    from polaris.kernelone.fs.jsonl.ops import append_jsonl, append_jsonl_atomic, flush_jsonl_buffers

    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))

    buffered_path = tmp_path / "runtime" / "events" / "buffered.jsonl"
    atomic_path = tmp_path / "runtime" / "events" / "atomic.jsonl"

    append_jsonl(str(buffered_path), {"kind": "buffered"})
    flush_jsonl_buffers(force=True)
    append_jsonl_atomic(str(atomic_path), {"kind": "atomic"})

    assert buffered_path.is_file()
    assert atomic_path.is_file()


def test_infrastructure_compat_package_is_removed() -> None:
    assert not COMPAT_ROOT.exists()

"""Canonical guarded-source distribution to a stdlib-only isolated runtime."""

from __future__ import annotations

import ast
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import polaris.kernelone.fs as public_fs
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import (
    KernelFileSystem,
    guarded_regular_file_snapshot as snapshot_module,
    read_guarded_regular_file_snapshot,
)


def test_public_runtime_source_is_exact_canonical_utf8_and_stdlib_only() -> None:
    source = public_fs.get_guarded_snapshot_runtime_source()
    assert public_fs.get_guarded_snapshot_runtime_source is snapshot_module.get_guarded_snapshot_runtime_source
    package_root = os.path.dirname(os.path.abspath(snapshot_module.__file__))
    canonical = read_guarded_regular_file_snapshot(package_root, "guarded_regular_file_snapshot.py", 256 * 1024)
    assert source.encode("utf-8") == canonical.content
    assert 0 < len(canonical.content) <= 256 * 1024
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            assert node.module.split(".")[0] in sys.stdlib_module_names


_CHILD = r"""
import base64, json, sys, types
request = json.loads(sys.stdin.read())
module = types.ModuleType("_trusted_guarded_snapshot_runtime")
sys.modules[module.__name__] = module
exec(compile(request["source"], "<trusted-guarded-snapshot-source>", "exec"), module.__dict__)
results = []
for path, bound in [("payload.txt", 4096), ("linked.txt", 4096), ("../outside.txt", 4096), ("payload.txt", 1)]:
    try:
        snapshot = module.read_guarded_regular_file_snapshot(request["root"], path, bound)
        results.append({"path": path, "content_base64": base64.b64encode(snapshot.content).decode("ascii"),
                        "identity": [snapshot.size, snapshot.device, snapshot.inode, snapshot.mtime_ns,
                                     snapshot.ctime_ns, snapshot.root_device, snapshot.root_inode]})
    except module.GuardedRegularFileSnapshotError as error:
        results.append({"path": path, "error": error.code})
    except ValueError:
        results.append({"path": path, "error": "ValueError"})
print(json.dumps({"results": results, "isolated": sys.flags.isolated,
                  "polaris_imported": any(name == "polaris" or name.startswith("polaris.") for name in sys.modules)}))
"""


def test_fresh_isolated_runtime_reads_real_identity_and_rejects_unsafe_inputs(tmp_path: Path) -> None:
    source = public_fs.get_guarded_snapshot_runtime_source()
    fixture = KernelFileSystem(str(tmp_path), LocalFileSystemAdapter(), read_only_paths=True)
    fixture.workspace_mkdir("root", parents=True, exist_ok=True)
    payload = "Canonical bytes 雪原\n".encode()
    fixture.workspace_write_bytes("root/payload.txt", payload)
    fixture.workspace_write_text_atomic("outside.txt", "outside", encoding="utf-8")
    # Deliberate adversarial fixture, never an ordinary file-I/O bypass.
    os.symlink("payload.txt", tmp_path / "root" / "linked.txt")
    root = str(tmp_path / "root")
    expected = read_guarded_regular_file_snapshot(root, "payload.txt", 4096)
    process = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _CHILD],
        input=json.dumps({"source": source, "root": root}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=15,
    )
    assert process.returncode == 0, process.stderr
    assert process.stderr == ""
    response = json.loads(process.stdout)
    assert response["isolated"] == 1
    assert response["polaris_imported"] is False
    results = response["results"]
    assert base64.b64decode(results[0]["content_base64"], validate=True) == payload
    assert results[0]["identity"] == [
        expected.size,
        expected.device,
        expected.inode,
        expected.mtime_ns,
        expected.ctime_ns,
        expected.root_device,
        expected.root_inode,
    ]
    assert results[1] == {"path": "linked.txt", "error": "guarded_snapshot_symlink_rejected"}
    assert results[2] == {"path": "../outside.txt", "error": "ValueError"}
    assert results[3] == {"path": "payload.txt", "error": "guarded_snapshot_max_bytes_exceeded"}

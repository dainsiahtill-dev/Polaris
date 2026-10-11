"""Private protocol component tests, not immutable worker qualification."""

from __future__ import annotations

import importlib
import io
import sys
import unittest
from pathlib import Path

MODULE = "polaris.cells.factory.pipeline.internal.native_validation_worker"


class WorkerTests(unittest.TestCase):
    def test_actual_worker_entrypoint_eof_rejects_through_process_owner(self) -> None:
        from polaris.kernelone.process import ProcessTreeRunControl, run_process_tree_safe

        result = run_process_tree_safe(
            [sys.executable, "-B", "-m", MODULE],
            timeout=10,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).parents[5]), "LANG": "C.UTF-8"},
            cancel_control=ProcessTreeRunControl(),
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '{"version":1,"event":"ready"}\n')
        self.assertIn("native_validation_worker_protocol_size", result.stderr)

    def test_bootstrap_exact_v1_then_stop(self) -> None:
        worker = importlib.import_module(MODULE)
        source = io.BytesIO(
            b'{"version":1,"command":"register","bootstrapPortId":"port",'
            b'"challengeId":"challenge","nonce":"' + b"a" * 64 + b'"}\n'
            b'{"version":1,"command":"stop"}\n'
        )
        output = io.BytesIO()
        self.assertEqual(worker.run_worker(source, output), 0)
        self.assertEqual(
            output.getvalue(),
            b'{"version":1,"event":"ready"}\n{"version":1,"event":"registered","challengeId":"challenge"}\n',
        )

    def test_bootstrap_nonce_is_not_operation_authority(self) -> None:
        worker = importlib.import_module(MODULE)
        source = io.BytesIO(
            b'{"version":1,"command":"register","bootstrapPortId":"port",'
            b'"challengeId":"challenge","nonce":"' + b"a" * 64 + b'"}\n'
            b'{"version":1,"command":"execute"}\n'
        )
        with self.assertRaisesRegex(worker.WorkerProtocolError, "protocol"):
            worker.run_worker(source, io.BytesIO())

    def test_duplicate_and_oversized_control_lines_rejected(self) -> None:
        worker = importlib.import_module(MODULE)
        for data in [b'{"version":1,"version":1}\n', b" " * 1024 + b"\n", b"{}"]:
            with self.assertRaises(worker.WorkerProtocolError):
                worker.read_control(io.BytesIO(data))


if __name__ == "__main__":
    unittest.main()

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


def _import_io_utils():
    here = os.path.dirname(__file__)
    module_dir = os.path.abspath(os.path.join(here, "..", "modules", "polaris-loop"))
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    import io_utils  # type: ignore

    return io_utils


class TestJsonlIo(unittest.TestCase):
    def test_jsonl_flush_keeps_buffer_when_locked(self):
        io_utils = _import_io_utils()
        io_utils.configure_jsonl_buffer(buffered=True, flush_interval_sec=0.0, flush_batch=1, max_buffer=100)
        with io_utils._JSONL_BUFFER_LOCK:
            io_utils._JSONL_BUFFER.clear()

        with tempfile.TemporaryDirectory() as temp_dir:
            jsonl_path = str(Path(temp_dir) / "events.jsonl")
            lock_path = jsonl_path + ".lock"
            with open(lock_path, "w", encoding="utf-8") as handle:
                handle.write("locked")

            io_utils.append_jsonl(jsonl_path, {"kind": "test", "n": 1}, lock_timeout_sec=0.01, buffered=True)
            with io_utils._JSONL_BUFFER_LOCK:
                self.assertIn(jsonl_path, io_utils._JSONL_BUFFER)
                self.assertEqual(len(io_utils._JSONL_BUFFER[jsonl_path]["lines"]), 1)

            self.assertTrue(not os.path.exists(jsonl_path) or os.path.getsize(jsonl_path) == 0)

            os.remove(lock_path)
            io_utils.flush_jsonl_buffers(force=True, lock_timeout_sec=1.0)

            with io_utils._JSONL_BUFFER_LOCK:
                self.assertEqual(io_utils._JSONL_BUFFER[jsonl_path]["lines"], [])

            with open(jsonl_path, "r", encoding="utf-8") as handle:
                content = handle.read().strip()
            self.assertTrue(bool(content))

    def test_jsonl_stale_lock_is_removed(self):
        io_utils = _import_io_utils()
        io_utils._JSONL_LOCK_STALE_SEC = 0.05

        with tempfile.TemporaryDirectory() as temp_dir:
            jsonl_path = str(Path(temp_dir) / "dialogue.jsonl")
            lock_path = jsonl_path + ".lock"
            with open(lock_path, "w", encoding="utf-8") as handle:
                handle.write("stale")
            os.utime(lock_path, (time.time() - 10, time.time() - 10))

            io_utils.append_jsonl_atomic(jsonl_path, {"kind": "test", "n": 2}, lock_timeout_sec=0.2)

            self.assertTrue(os.path.isfile(jsonl_path))
            self.assertFalse(os.path.exists(lock_path))
            with open(jsonl_path, "r", encoding="utf-8") as handle:
                content = handle.read().strip()
            self.assertTrue(bool(content))


if __name__ == "__main__":
    raise SystemExit(unittest.main())

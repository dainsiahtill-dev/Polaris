import os
import tempfile
import unittest


def _import_io_paths():
    from polaris.kernelone.storage import io_paths

    return io_paths


class TestRamdiskPaths(unittest.TestCase):
    def test_resolve_artifact_path_routes_hot_files_to_cache_root(self):
        io_paths = _import_io_paths()
        prev_state = os.environ.get("KERNELONE_STATE_TO_RAMDISK")
        os.environ["KERNELONE_STATE_TO_RAMDISK"] = "0"
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as ramdisk_dir:
            workspace = os.path.abspath(workspace_dir)
            cache_root = io_paths.build_cache_root(ramdisk_dir, workspace)
            self.assertTrue(cache_root)

            hot_jsonl = io_paths.resolve_artifact_path(
                workspace,
                cache_root,
                "runtime/events/runtime.events.jsonl",
            )
            self.assertTrue(os.path.commonpath([hot_jsonl, cache_root]) == cache_root)

            hot_runlog = io_paths.resolve_artifact_path(
                workspace,
                cache_root,
                "runtime/logs/director.runlog.md",
            )
            self.assertTrue(os.path.commonpath([hot_runlog, cache_root]) == cache_root)

            hot_memory = io_paths.resolve_artifact_path(workspace, cache_root, "runtime/memory/last_state.json")
            self.assertTrue(os.path.commonpath([hot_memory, cache_root]) == cache_root)

            cold = io_paths.resolve_artifact_path(
                workspace,
                cache_root,
                "runtime/contracts/pm_tasks.contract.json",
            )
            self.assertTrue(os.path.commonpath([cold, cache_root]) == cache_root)
        if prev_state is None:
            os.environ.pop("KERNELONE_STATE_TO_RAMDISK", None)
        else:
            os.environ["KERNELONE_STATE_TO_RAMDISK"] = prev_state


if __name__ == "__main__":
    raise SystemExit(unittest.main())

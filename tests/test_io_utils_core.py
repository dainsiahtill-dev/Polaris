import os
import json
import tempfile
import unittest
from pathlib import Path


def _import_io_utils():
    repo_root = Path(__file__).resolve().parents[1]
    module_dir = repo_root / "src" / "backend" / "core" / "polaris_loop"
    if str(module_dir) not in os.sys.path:
        os.sys.path.insert(0, str(module_dir))
    import io_utils  # type: ignore

    return io_utils


class TestIoUtilsCore(unittest.TestCase):
    def test_find_workspace_root_uses_docs(self):
        io_utils = _import_io_utils()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "docs").mkdir()
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            found = io_utils.find_workspace_root(str(nested))
            self.assertEqual(os.path.abspath(found), os.path.abspath(str(root)))

    def test_resolve_workspace_path_raises_without_docs(self):
        io_utils = _import_io_utils()
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                io_utils.resolve_workspace_path(temp_dir, require_docs=True)

    def test_resolve_workspace_path_allows_missing_docs(self):
        io_utils = _import_io_utils()
        with tempfile.TemporaryDirectory() as temp_dir:
            resolved = io_utils.resolve_workspace_path(temp_dir, require_docs=False)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(temp_dir))

    def test_is_hot_artifact_path(self):
        io_utils = _import_io_utils()
        prev_state = os.environ.get("POLARIS_STATE_TO_RAMDISK")
        os.environ["POLARIS_STATE_TO_RAMDISK"] = "1"
        self.assertTrue(io_utils.is_hot_artifact_path("runtime/events/runtime.events.jsonl"))
        self.assertTrue(io_utils.is_hot_artifact_path("runtime/logs/director.runlog.md"))
        os.environ["POLARIS_STATE_TO_RAMDISK"] = "0"
        self.assertFalse(io_utils.is_hot_artifact_path("runtime/events/runtime.events.jsonl"))
        if prev_state is None:
            os.environ.pop("POLARIS_STATE_TO_RAMDISK", None)
        else:
            os.environ["POLARIS_STATE_TO_RAMDISK"] = prev_state

    def test_resolve_artifact_path_routes_hot_files(self):
        io_utils = _import_io_utils()
        prev_state = os.environ.get("POLARIS_STATE_TO_RAMDISK")
        os.environ["POLARIS_STATE_TO_RAMDISK"] = "0"
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as ramdisk_dir:
            workspace = os.path.abspath(workspace_dir)
            cache_root = io_utils.build_cache_root(ramdisk_dir, workspace)
            self.assertTrue(cache_root)
            hot = io_utils.resolve_artifact_path(
                workspace,
                cache_root,
                "runtime/events/runtime.events.jsonl",
            )
            self.assertTrue(os.path.commonpath([hot, cache_root]) == cache_root)
            cold = io_utils.resolve_artifact_path(
                workspace,
                cache_root,
                "runtime/contracts/pm_tasks.contract.json",
            )
            self.assertTrue(os.path.commonpath([cold, cache_root]) == cache_root)
        if prev_state is None:
            os.environ.pop("POLARIS_STATE_TO_RAMDISK", None)
        else:
            os.environ["POLARIS_STATE_TO_RAMDISK"] = prev_state

    def test_emit_event_and_dialogue(self):
        io_utils = _import_io_utils()
        with tempfile.TemporaryDirectory() as temp_dir:
            dialogue_path = os.path.join(temp_dir, "dialogue.jsonl")
            events_path = os.path.join(temp_dir, "events.jsonl")
            io_utils.emit_dialogue(dialogue_path, speaker="PM", type="say", text="hello")
            io_utils.emit_event(events_path, kind="action", actor="System", name="noop", summary="test")
            io_utils.flush_jsonl_buffers(force=True)

            with open(dialogue_path, "r", encoding="utf-8") as handle:
                dialogue = json.loads(handle.readline())
            with open(events_path, "r", encoding="utf-8") as handle:
                event = json.loads(handle.readline())

            self.assertEqual(dialogue["speaker"], "PM")
            self.assertEqual(event["kind"], "action")
            self.assertIn("event_id", event)

    def test_emit_llm_event_suppresses_semantic_duplicates(self):
        io_utils = _import_io_utils()
        with tempfile.TemporaryDirectory() as temp_dir:
            llm_path = os.path.join(temp_dir, "pm.llm.events.jsonl")
            original_window = io_utils._LLM_EVENT_DEDUP_WINDOW_SEC
            io_utils._LLM_EVENT_DEDUP_WINDOW_SEC = 5.0
            io_utils._llm_event_last_by_path.clear()
            try:
                payload = {
                    "iteration": 1,
                    "backend": "runtime_provider",
                    "stage": "failed",
                    "error": "invoke_failed",
                }
                io_utils.emit_llm_event(
                    llm_path,
                    event="iteration",
                    role="pm",
                    data=payload,
                    run_id="pm-1",
                    iteration=1,
                    source="system",
                )
                io_utils.emit_llm_event(
                    llm_path,
                    event="iteration",
                    role="pm",
                    data=dict(payload),
                    run_id="pm-1",
                    iteration=1,
                    source="system",
                )
                io_utils.flush_jsonl_buffers(force=True)

                with open(llm_path, "r", encoding="utf-8") as handle:
                    rows = [json.loads(line) for line in handle if line.strip()]
                self.assertEqual(len(rows), 1)

                changed = dict(payload)
                changed["error"] = "invoke_failed_with_timeout"
                io_utils.emit_llm_event(
                    llm_path,
                    event="iteration",
                    role="pm",
                    data=changed,
                    run_id="pm-1",
                    iteration=1,
                    source="system",
                )
                io_utils.flush_jsonl_buffers(force=True)

                with open(llm_path, "r", encoding="utf-8") as handle:
                    rows = [json.loads(line) for line in handle if line.strip()]
                self.assertEqual(len(rows), 2)
            finally:
                io_utils._LLM_EVENT_DEDUP_WINDOW_SEC = original_window
                io_utils._llm_event_last_by_path.clear()

    def test_emit_event_suppresses_semantic_duplicates(self):
        io_utils = _import_io_utils()
        with tempfile.TemporaryDirectory() as temp_dir:
            events_path = os.path.join(temp_dir, "runtime.events.jsonl")
            original_window = io_utils._EVENT_DEDUP_WINDOW_SEC
            io_utils._EVENT_DEDUP_WINDOW_SEC = 5.0
            io_utils._event_last_by_path.clear()
            try:
                kwargs = {
                    "kind": "observation",
                    "actor": "System",
                    "name": "init_docs",
                    "refs": {"phase": "docs_init"},
                    "summary": "Initialized docs via onboarding wizard",
                    "ok": True,
                    "output": {"artifacts": ["workspace/docs/product/plan.md"]},
                }
                io_utils.emit_event(events_path, **kwargs)
                io_utils.emit_event(events_path, **kwargs)
                io_utils.flush_jsonl_buffers(force=True)

                with open(events_path, "r", encoding="utf-8") as handle:
                    rows = [json.loads(line) for line in handle if line.strip()]
                self.assertEqual(len(rows), 1)

                io_utils.emit_event(
                    events_path,
                    **{
                        **kwargs,
                        "output": {"artifacts": ["workspace/docs/product/plan.md", "workspace/docs/product/adr.md"]},
                    },
                )
                io_utils.flush_jsonl_buffers(force=True)

                with open(events_path, "r", encoding="utf-8") as handle:
                    rows = [json.loads(line) for line in handle if line.strip()]
                self.assertEqual(len(rows), 2)
            finally:
                io_utils._EVENT_DEDUP_WINDOW_SEC = original_window
                io_utils._event_last_by_path.clear()

    def test_stop_flag_helpers(self):
        io_utils = _import_io_utils()
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "docs").mkdir()
            stop_path = io_utils.stop_flag_path(str(workspace))
            self.assertFalse(io_utils.stop_requested(str(workspace)))
            Path(stop_path).parent.mkdir(parents=True, exist_ok=True)
            Path(stop_path).write_text("stop", encoding="utf-8")
            self.assertTrue(io_utils.stop_requested(str(workspace)))


if __name__ == "__main__":
    raise SystemExit(unittest.main())

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


def _load_runtime_module():
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "src" / "backend" / "core" / "polaris_loop",
        repo_root / "polaris" / "modules" / "polaris-loop",
    ]
    for module_dir in candidates:
        if not module_dir.is_dir():
            continue
        if str(module_dir) not in sys.path:
            sys.path.insert(0, str(module_dir))
        module_path = module_dir / "director_policy_runtime.py"
        if not module_path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("director_policy_runtime", module_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Failed to load director_policy_runtime.py")


class TestDirectorPolicyRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = _load_runtime_module()

    def test_apply_policy_to_state_updates_context(self):
        state = SimpleNamespace(
            auto_repair=True,
            repair_rounds=1,
            reviewer_enabled=True,
            reviewer_rounds=1,
            rollback_on_fail=True,
            risk_block_threshold=0,
            rollback_on_block=True,
            evidence_verbosity="summary",
            evidence_write_enabled=True,
            rag_topk=5,
            memory_enabled=True,
            memory_backend="lancedb",
            memory_store_enabled=True,
            memory_store_every=1,
            memory_store_on_accept=False,
            memory_dir_full="",
            memory_snapshot=None,
            memory_snapshot_path="",
            budget_max_rounds=6,
            budget_max_lines=1200,
            default_tools_enabled=True,
            context_pm_tasks_max_chars=8000,
            context_known_files_max_chars=2000,
            context_last_result_max_chars=2000,
            context_tool_output_max_chars=9000,
            context_planner_output_max_chars=6000,
            context_ollama_output_max_chars=6000,
            build_round_budget=4,
            stall_round_threshold=2,
            verify_requires_ready=True,
        )
        policy = {
            "repair": {"auto_repair": False, "max_attempts": 2},
            "rag": {"topk": 3},
            "context": {"pm_tasks_max_chars": 123},
            "build_loop": {
                "budget": 6,
                "stall_round_threshold": 3,
                "verify_requires_ready": False,
            },
        }
        env_key = "KERNELONE_RAG_TOPK"
        original_env = os.environ.get(env_key)
        try:
            self.runtime.apply_policy_to_state(state, policy)
            self.assertEqual(state.auto_repair, False)
            self.assertEqual(state.repair_rounds, 2)
            self.assertEqual(state.rag_topk, 3)
            self.assertEqual(state.context_pm_tasks_max_chars, 123)
            self.assertEqual(state.build_round_budget, 6)
            self.assertEqual(state.stall_round_threshold, 3)
            self.assertEqual(state.verify_requires_ready, False)
            self.assertEqual(os.environ.get(env_key), "3")
        finally:
            if original_env is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = original_env

    def test_factory_defect_loop_overrides_rollback(self):
        state = SimpleNamespace(
            auto_repair=True,
            repair_rounds=1,
            reviewer_enabled=True,
            reviewer_rounds=1,
            rollback_on_fail=True,
            risk_block_threshold=0,
            rollback_on_block=True,
            evidence_verbosity="summary",
            evidence_write_enabled=True,
            rag_topk=5,
            memory_enabled=True,
            memory_backend="lancedb",
            memory_store_enabled=True,
            memory_store_every=1,
            memory_store_on_accept=False,
            memory_dir_full="",
            memory_snapshot=None,
            memory_snapshot_path="",
            budget_max_rounds=6,
            budget_max_lines=1200,
            default_tools_enabled=True,
            qa_enabled=True,
            context_pm_tasks_max_chars=8000,
            context_known_files_max_chars=2000,
            context_last_result_max_chars=2000,
            context_tool_output_max_chars=9000,
            context_planner_output_max_chars=6000,
            context_ollama_output_max_chars=6000,
        )
        policy = {
            "repair": {"rollback_on_fail": True, "max_attempts": 3},
            "factory": {
                "default_strategy": "defect_loop",
                "auditor_failure_action": "return_to_director_with_evidence",
                "max_fix_attempts": 3,
                "require_defect_ticket": True,
                "defect_ticket_fields": [
                    "defect_id",
                    "severity",
                    "repro_steps",
                    "expected",
                    "actual",
                    "artifact_path",
                    "suspected_scope",
                ],
                "require_evidence_run": True,
                "require_fast_loop_before_evidence_run": True,
                "hard_rollback_enabled": True,
                "hard_rollback_trigger_conditions": ["security_incident"],
                "enforce_hp_flow": True,
                "required_pipeline": [
                    "start_run",
                    "blueprint",
                    "policy_check",
                    "snapshot",
                    "implementation",
                    "verify",
                    "finalize",
                ],
                "standalone_allowed": True,
            },
        }
        env_key = "KERNELONE_MANUAL_ROLLBACK_CONFIRMED"
        original_env = os.environ.get(env_key)
        try:
            os.environ[env_key] = "1"
            self.runtime.apply_policy_to_state(state, policy)
        finally:
            if original_env is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = original_env
        self.assertEqual(state.repair_rounds, 3)
        self.assertEqual(state.rollback_on_fail, False)
        self.assertEqual(state.failure_default_strategy, "defect_loop")
        self.assertEqual(state.auditor_failure_action, "return_to_director_with_evidence")
        self.assertEqual(state.require_defect_ticket, True)
        self.assertEqual(
            state.defect_ticket_fields,
            ["defect_id", "severity", "repro_steps", "expected", "actual", "artifact_path", "suspected_scope"],
        )
        self.assertEqual(state.require_evidence_run, True)
        self.assertEqual(state.require_fast_loop_before_evidence_run, True)
        self.assertEqual(state.hard_rollback_enabled, True)
        self.assertEqual(state.hard_rollback_trigger_conditions, ["security_incident"])
        self.assertEqual(state.enforce_hp_flow, True)
        self.assertEqual(
            state.hp_required_pipeline,
            ["start_run", "blueprint", "policy_check", "snapshot", "implementation", "verify", "finalize"],
        )
        self.assertEqual(state.standalone_allowed, True)


if __name__ == "__main__":
    raise SystemExit(unittest.main())

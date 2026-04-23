import os
import json
import tempfile
import unittest
from pathlib import Path
import importlib.util


def _load_policy_module():
    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "src" / "backend" / "core" / "polaris_loop" / "policy.py",
        repo_root / "polaris" / "modules" / "polaris-loop" / "policy.py",
    ]
    for module_path in candidates:
        if not module_path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("policy", module_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("Failed to load policy.py")


class TestPolicyMerge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = _load_policy_module()

    def test_build_cli_overrides(self):
        overrides = self.policy.build_cli_overrides([
            "--auto-repair",
            "--risk-block-threshold", "5",
            "--rag-topk=3",
            "--memory-backend", "file",
        ])
        self.assertEqual(overrides["repair"]["auto_repair"], True)
        self.assertEqual(overrides["risk"]["block_threshold"], "5")
        self.assertEqual(overrides["rag"]["topk"], "3")
        self.assertEqual(overrides["memory"]["backend"], "file")

    def test_build_env_overrides(self):
        env_keys = [
            "KERNELONE_JSONL_BUFFERED",
            "KERNELONE_JSONL_FLUSH_INTERVAL",
            "KERNELONE_RAG_TOPK",
        ]
        originals = {key: os.environ.get(key) for key in env_keys}
        try:
            os.environ["KERNELONE_JSONL_BUFFERED"] = "0"
            os.environ["KERNELONE_JSONL_FLUSH_INTERVAL"] = "0.5"
            os.environ["KERNELONE_RAG_TOPK"] = "4"
            overrides = self.policy.build_env_overrides()
            self.assertEqual(overrides["io"]["jsonl_buffered"], "0")
            self.assertEqual(overrides["io"]["flush_interval_sec"], "0.5")
            self.assertEqual(overrides["rag"]["topk"], "4")
        finally:
            for key, value in originals.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_build_base_policy_with_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "policy.json"
            policy_path.write_text(json.dumps({
                "memory": {"enabled": False, "backend": "lancedb"},
                "budgets": {"max_total_lines_read": 50}
            }, ensure_ascii=False), encoding="utf-8")
            policy, sources = self.policy.build_base_policy(str(policy_path), {})
            self.assertEqual(policy["memory"]["enabled"], False)
            self.assertEqual(policy["memory"]["backend"], "none")
            self.assertEqual(policy["budgets"]["max_total_lines_read"], 1200)
            self.assertTrue("memory.enabled" in sources)

    def test_build_loop_defaults_and_overrides(self):
        policy, _ = self.policy.build_base_policy("", {})
        self.assertEqual(policy["build_loop"]["budget"], 4)
        self.assertEqual(policy["build_loop"]["stall_round_threshold"], 2)
        self.assertEqual(policy["build_loop"]["verify_requires_ready"], True)

        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "build_loop": {
                            "budget": 6,
                            "stall_round_threshold": 3,
                            "verify_requires_ready": False,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            overridden, _ = self.policy.build_base_policy(str(policy_path), {})
            self.assertEqual(overridden["build_loop"]["budget"], 6)
            self.assertEqual(overridden["build_loop"]["stall_round_threshold"], 3)
            self.assertEqual(overridden["build_loop"]["verify_requires_ready"], False)

    def test_build_base_policy_with_factory_v3_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "policies.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": "3.0",
                        "mode": "factory",
                        "fail_closed": True,
                        "workflow": {
                            "required_pipeline": [
                                "start_run",
                                "blueprint",
                                "policy_check",
                                "snapshot",
                                "implementation",
                                "verify",
                                "finalize",
                            ],
                            "mcp": {"final_accept_requires_evidence_run": True},
                            "verification": {
                                "gate_set_default": "full",
                                "allow_reduced_gate": True,
                                "evidence_log_required": True,
                            },
                        },
                        "failure_handling": {
                            "default_strategy": "defect_loop",
                            "auditor_failure_action": "return_to_director_with_evidence",
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
                            "fix_loop": {
                                "max_fix_attempts": 3,
                                "require_fast_loop_before_evidence_run": True,
                            },
                            "hard_rollback": {
                                "enabled": True,
                                "trigger_conditions": ["security_incident"],
                            },
                        },
                        "policy_gate": {
                            "block_conditions": {
                                "budget_overflow_ratio": 0.5,
                                "forbid_missing_evidence": True,
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            policy, sources = self.policy.build_base_policy(str(policy_path), {})
            self.assertEqual(policy["repair"]["max_attempts"], 3)
            self.assertEqual(policy["repair"]["rollback_on_fail"], False)
            self.assertEqual(policy["factory"]["mode"], "factory")
            self.assertEqual(policy["factory"]["default_strategy"], "defect_loop")
            self.assertEqual(policy["factory"]["auditor_failure_action"], "return_to_director_with_evidence")
            self.assertEqual(policy["factory"]["max_fix_attempts"], 3)
            self.assertEqual(policy["factory"]["require_defect_ticket"], True)
            self.assertEqual(
                policy["factory"]["defect_ticket_fields"],
                ["defect_id", "severity", "repro_steps", "expected", "actual", "artifact_path", "suspected_scope"],
            )
            self.assertEqual(policy["factory"]["require_evidence_run"], True)
            self.assertEqual(policy["factory"]["hard_rollback_enabled"], True)
            self.assertEqual(policy["factory"]["hard_rollback_trigger_conditions"], ["security_incident"])
            self.assertEqual(
                policy["factory"]["required_pipeline"],
                ["start_run", "blueprint", "policy_check", "snapshot", "implementation", "verify", "finalize"],
            )
            self.assertEqual(policy["factory"]["budget_overflow_ratio"], 0.5)
            self.assertTrue("factory.default_strategy" in sources)

    def test_factory_pipeline_misordered_is_canonicalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "policies.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": "3.0",
                        "mode": "factory",
                        "workflow": {
                            "required_pipeline": [
                                "verify",
                                "start_run",
                                "blueprint",
                                "policy_check",
                                "snapshot",
                                "implementation",
                                "finalize",
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            policy, _ = self.policy.build_base_policy(str(policy_path), {})
            self.assertEqual(
                policy["factory"]["required_pipeline"],
                ["start_run", "blueprint", "policy_check", "snapshot", "implementation", "verify", "finalize"],
            )

    def test_factory_pipeline_missing_phase_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "policies.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": "3.0",
                        "mode": "factory",
                        "workflow": {
                            "required_pipeline": [
                                "start_run",
                                "blueprint",
                                "policy_check",
                                "implementation",
                                "verify",
                                "finalize",
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            policy, _ = self.policy.build_base_policy(str(policy_path), {})
            self.assertEqual(
                policy["factory"]["required_pipeline"],
                ["start_run", "blueprint", "policy_check", "snapshot", "implementation", "verify", "finalize"],
            )

    def test_factory_require_defect_ticket_backfills_default_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "policies.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "schema_version": "3.0",
                        "mode": "factory",
                        "failure_handling": {
                            "require_defect_ticket": True,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            policy, _ = self.policy.build_base_policy(str(policy_path), {})
            self.assertTrue(policy["factory"]["require_defect_ticket"])
            self.assertEqual(
                policy["factory"]["defect_ticket_fields"],
                ["defect_id", "severity", "repro_steps", "expected", "actual", "artifact_path", "suspected_scope"],
            )


if __name__ == "__main__":
    raise SystemExit(unittest.main())

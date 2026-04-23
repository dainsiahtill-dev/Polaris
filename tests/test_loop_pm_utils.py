import unittest
from pathlib import Path
import importlib.util
import tempfile
import os
from types import SimpleNamespace
from unittest.mock import patch


def _load_loop_pm():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "scripts" / "loop-pm.py"
    if not module_path.is_file():
        raise RuntimeError(f"Failed to locate loop-pm.py: {module_path}")
    spec = importlib.util.spec_from_file_location("loop_pm", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-pm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLoopPmUtils(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop_pm = _load_loop_pm()

    def test_normalize_tasks_generates_id(self):
        raw = [{"title": "Do thing", "goal": "Do it", "target_files": ["a.py"]}]
        tasks = self.loop_pm.normalize_tasks(raw, iteration=1)
        self.assertTrue(tasks)
        self.assertTrue(tasks[0]["id"].startswith("PM-"))

    def test_normalize_tasks_defaults_to_module_scope(self):
        raw = [
            {
                "title": "Add API module",
                "goal": "Implement upload handlers",
                "target_files": ["src/api/upload.py", "src/api/download.py"],
            }
        ]
        tasks = self.loop_pm.normalize_tasks(raw, iteration=1)
        self.assertTrue(tasks)
        self.assertEqual(tasks[0]["scope_mode"], "module")
        self.assertIn("src/api", tasks[0]["scope_paths"])

    def test_normalize_tasks_keeps_exact_file_scope_mode(self):
        raw = [
            {
                "title": "Hotfix one file",
                "goal": "Patch critical bug in parser",
                "scope_mode": "exact_files",
                "target_files": ["src/parser/core.py"],
            }
        ]
        tasks = self.loop_pm.normalize_tasks(raw, iteration=1)
        self.assertTrue(tasks)
        self.assertEqual(tasks[0]["scope_mode"], "exact_files")
        self.assertEqual(tasks[0]["target_files"], ["src/parser/core.py"])

    def test_normalize_pm_payload_defaults(self):
        payload = {"tasks": []}
        normalized = self.loop_pm.normalize_pm_payload(payload, iteration=2, timestamp="2024-01-01")
        self.assertEqual(normalized["overall_goal"], "Advance global requirements")
        self.assertEqual(normalized["pm_iteration"], 2)
        self.assertEqual(normalized["schema_version"], 2)

    def test_normalize_tasks_rewrites_legacy_required_evidence_read_hints(self):
        tasks = self.loop_pm.normalize_tasks(
            [
                {
                    "title": "Implement server",
                    "goal": "Create a Flask entrypoint",
                    "target_files": ["app.py"],
                    "required_evidence": {
                        "must_read": [{"file": "app.py"}, {"path": "requirements.txt"}],
                        "must_find_calls": ["Flask("],
                    },
                }
            ],
            iteration=1,
        )
        self.assertEqual(len(tasks), 1)
        required = tasks[0].get("required_evidence") or {}
        self.assertNotIn("must_read", required)
        self.assertNotIn("must_find_calls", required)
        self.assertIn("validation_paths", required)
        self.assertIn("app.py", required["validation_paths"])

    def test_split_director_tasks_routes_non_director_assignees(self):
        tasks = [
            {
                "id": "TASK-A",
                "title": "Director task",
                "goal": "Do director work",
                "target_files": ["src/app.py"],
                "assigned_to": "Director",
            },
            {
                "id": "TASK-B",
                "title": "Audit task",
                "goal": "Audit output",
                "target_files": ["src/app.py"],
                "assigned_to": "Auditor",
            },
            {
                "id": "TASK-C",
                "title": "Docs task",
                "goal": "Update docs",
                "target_files": ["docs/guide.md"],
                "assigned_to": "Director",
            },
        ]
        director_tasks, docs_only_tasks, non_director_tasks = self.loop_pm.split_director_tasks(tasks)
        self.assertEqual([task["id"] for task in director_tasks], ["TASK-A"])
        self.assertEqual([task["id"] for task in docs_only_tasks], ["TASK-C"])
        self.assertEqual([task["id"] for task in non_director_tasks], ["TASK-B"])

    def test_normalize_tasks_keeps_assigned_to(self):
        tasks = self.loop_pm.normalize_tasks(
            [
                {
                    "title": "Gate check",
                    "goal": "Policy validation",
                    "target_files": ["src/policy.py"],
                    "assigned_to": "PolicyGate",
                    "acceptance_criteria": ["No blocked risks"],
                }
            ],
            iteration=1,
        )
        self.assertEqual(tasks[0]["assigned_to"], "PolicyGate")
        self.assertEqual(len(tasks[0]["acceptance"]), 1)
        self.assertIn("No blocked risks", tasks[0]["acceptance"][0])

    def test_normalize_tasks_supports_chief_engineer_assignee(self):
        tasks = self.loop_pm.normalize_tasks(
            [
                {
                    "title": "Blueprint refinement",
                    "goal": "Generate dependency graph and method-level construction plan",
                    "target_files": ["src/server/index.ts"],
                    "assigned_to": "ChiefEngineer",
                    "acceptance_criteria": ["Construction blueprint is generated"],
                }
            ],
            iteration=1,
        )
        self.assertEqual(tasks[0]["assigned_to"], "ChiefEngineer")

    def test_split_director_tasks_auto_routes_chief_engineer(self):
        tasks = [
            {
                "id": "TASK-CE",
                "title": "Dependency graph",
                "goal": "Generate code blueprint index and verify readiness plan",
                "assigned_to": "auto",
            }
        ]
        director_tasks, docs_only_tasks, non_director_tasks = self.loop_pm.split_director_tasks(tasks)
        self.assertEqual(director_tasks, [])
        self.assertEqual(docs_only_tasks, [])
        self.assertEqual(len(non_director_tasks), 1)
        self.assertEqual(non_director_tasks[0]["assigned_to"], "ChiefEngineer")

    def test_normalize_tasks_supports_readme_protocol_fields(self):
        tasks = self.loop_pm.normalize_tasks(
            [
                {
                    "id": "P0-INT",
                    "title": "Urgent interrupt",
                    "goal": "Handle urgent action",
                    "priority": "P0",
                    "status": "todo",
                    "dependencies": ["BASE-1"],
                    "spec": "spec.md",
                    "acceptance_criteria": ["Done with evidence"],
                    "assigned_to": "Director",
                },
                {
                    "id": "DONE-1",
                    "title": "Already done",
                    "goal": "Skip me",
                    "status": "done",
                },
            ],
            iteration=1,
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "P0-INT")
        self.assertEqual(tasks[0]["priority"], 0)
        self.assertEqual(tasks[0]["status"], "todo")
        self.assertEqual(tasks[0]["dependencies"], ["BASE-1"])
        self.assertEqual(tasks[0]["acceptance_criteria"], ["Done with evidence"])
        self.assertEqual(tasks[0]["backlog_ref"], "")

    def test_normalize_tasks_preserves_parallel_metadata(self):
        tasks = self.loop_pm.normalize_tasks(
            [
                {
                    "id": "TASK-PAR-1",
                    "title": "Parallelizable task",
                    "goal": "Prepare isolated feature shard",
                    "priority": "P1",
                    "status": "todo",
                    "deps": ["BASE-READY"],
                    "assigned_to": "Director",
                    "acceptance_criteria": ["Shard is independently executable"],
                    "parallel_group": "feature-x",
                    "shardable": True,
                    "max_parallel_hint": 3,
                    "capability": "frontend",
                }
            ],
            iteration=1,
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["dependencies"], ["BASE-READY"])
        self.assertEqual(tasks[0]["parallel_group"], "feature-x")
        self.assertEqual(tasks[0]["shardable"], True)
        self.assertEqual(tasks[0]["max_parallel_hint"], 3)
        self.assertEqual(tasks[0]["capability"], "frontend")

    def test_normalize_tasks_adds_default_qa_contract(self):
        tasks = self.loop_pm.normalize_tasks(
            [
                {
                    "id": "TASK-QA-CONTRACT",
                    "title": "Implement endpoint",
                    "goal": "Add /health endpoint",
                    "priority": "P1",
                    "status": "todo",
                    "assigned_to": "Director",
                    "acceptance_criteria": ["health returns 200"],
                }
            ],
            iteration=1,
        )
        self.assertEqual(len(tasks), 1)
        contract = tasks[0].get("qa_contract") or {}
        self.assertEqual(contract.get("plugin"), "rules_v1")
        self.assertEqual(contract.get("task_type"), "generic")
        self.assertIn("director_status_success", contract.get("hard_gates", []))

    def test_normalize_pm_payload_keeps_engine_config(self):
        normalized = self.loop_pm.normalize_pm_payload(
            {
                "tasks": [],
                "engine": {
                    "director_execution_mode": "multi",
                    "max_directors": 4,
                    "scheduling_policy": "dag",
                },
            },
            iteration=3,
            timestamp="2026-02-20T00:00:00Z",
        )
        self.assertIn("engine", normalized)
        self.assertEqual(normalized["engine"]["director_execution_mode"], "multi")
        self.assertEqual(normalized["engine"]["max_directors"], 4)
        self.assertEqual(normalized["engine"]["scheduling_policy"], "dag")

    def test_normalize_tasks_backlog_ref_is_preserved_and_trimmed(self):
        backlog_text = "Implement backlog item A " * 30
        tasks = self.loop_pm.normalize_tasks(
            [
                {
                    "id": "TASK-BACKLOG",
                    "title": "Backlog traceability",
                    "goal": "Persist backlog linkage",
                    "target_files": ["src/backend/scripts/loop-pm.py"],
                    "acceptance_criteria": ["Task keeps backlog mapping"],
                    "backlog_ref": backlog_text,
                }
            ],
            iteration=1,
        )
        self.assertEqual(len(tasks), 1)
        self.assertIn("backlog_ref", tasks[0])
        self.assertLessEqual(len(tasks[0]["backlog_ref"]), 400)

    def test_apply_task_status_updates_persists_failure_info(self):
        tasks = [
            {
                "id": "TASK-FAIL-1",
                "status": "review",
            }
        ]
        self.loop_pm.apply_task_status_updates(
            tasks,
            {"TASK-FAIL-1": "failed"},
            failure_info={
                "TASK-FAIL-1": {
                    "error_code": "QA_FAIL",
                    "failure_detail": "Unit tests failed",
                }
            },
        )
        self.assertEqual(tasks[0]["status"], "failed")
        self.assertEqual(tasks[0]["error_code"], "QA_FAIL")
        self.assertEqual(tasks[0]["failure_detail"], "Unit tests failed")
        self.assertIn("failed_at", tasks[0])

    def test_apply_task_status_updates_done_clears_failure_info(self):
        tasks = [
            {
                "id": "TASK-FAIL-2",
                "status": "failed",
                "error_code": "OLD",
                "failure_detail": "old failure",
                "failed_at": "2026-01-01T00:00:00Z",
            }
        ]
        self.loop_pm.apply_task_status_updates(tasks, {"TASK-FAIL-2": "done"})
        self.assertEqual(tasks[0]["status"], "done")
        self.assertNotIn("error_code", tasks[0])
        self.assertNotIn("failure_detail", tasks[0])
        self.assertNotIn("failed_at", tasks[0])

    def test_normalize_tasks_supports_needs_continue_status(self):
        tasks = self.loop_pm.normalize_tasks(
            [
                {
                    "id": "TASK-NC-STATUS",
                    "title": "Continue current task",
                    "goal": "Keep building in same task",
                    "status": "needs_continue",
                    "target_files": ["src/main.py"],
                }
            ],
            iteration=1,
        )
        self.assertEqual(tasks[0]["status"], "needs_continue")

    def test_apply_task_status_updates_needs_continue_clears_failure_info(self):
        tasks = [
            {
                "id": "TASK-NC-1",
                "status": "failed",
                "error_code": "OLD",
                "failure_detail": "old failure",
                "failed_at": "2026-01-01T00:00:00Z",
            }
        ]
        self.loop_pm.apply_task_status_updates(tasks, {"TASK-NC-1": "needs_continue"})
        self.assertEqual(tasks[0]["status"], "needs_continue")
        self.assertNotIn("error_code", tasks[0])
        self.assertNotIn("failure_detail", tasks[0])
        self.assertNotIn("failed_at", tasks[0])

    def test_execute_non_director_tasks_generates_defect_followup(self):
        with tempfile.TemporaryDirectory() as workspace:
            with patch.dict(os.environ, {"KERNELONE_STATE_TO_RAMDISK": "0"}, clear=False):
                payload = self.loop_pm.execute_non_director_tasks(
                    tasks=[
                        {
                            "id": "AUD-1",
                            "title": "Audit director result",
                            "assigned_to": "Auditor",
                            "required_evidence": {
                                "audit_result": "FAIL",
                                "defect_ticket": {
                                    "defect_id": "D-100",
                                    "severity": "high",
                                    "repro_steps": ["run test", "observe fail"],
                                    "expected": "all checks pass",
                                    "actual": "check failed",
                                    "artifact_path": ".polaris/runtime/results/qa.review.md",
                                    "suspected_scope": ["src/backend/scripts/loop-pm.py"],
                                },
                            },
                        }
                    ],
                    workspace_full=workspace,
                    cache_root_full="",
                    run_id="pm-00001",
                    pm_iteration=1,
                    events_path="",
                    dialogue_path="",
                )
                self.assertFalse(payload["hard_block"])
                self.assertEqual(len(payload["generated_director_tasks"]), 1)
                followup = payload["generated_director_tasks"][0]
                self.assertEqual(followup["assigned_to"], "Director")
                self.assertIn("D-100", followup["title"])
                from io_utils import resolve_artifact_path
                from pm.director_mgmt import build_run_dir

                summary_path = Path(
                    resolve_artifact_path(
                        workspace,
                        "",
                        "runtime/state/assignee_execution.state.json",
                    )
                )
                run_summary_path = (
                    Path(build_run_dir(workspace, "", 1))
                    / "state"
                    / "assignee_execution.state.json"
                )
                self.assertTrue(summary_path.is_file())
                self.assertTrue(run_summary_path.is_file())

    def test_execute_non_director_tasks_policygate_block_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as workspace:
            with patch.dict(os.environ, {"KERNELONE_STATE_TO_RAMDISK": "0"}, clear=False):
                payload = self.loop_pm.execute_non_director_tasks(
                    tasks=[
                        {
                            "id": "PG-1",
                            "title": "Policy review",
                            "assigned_to": "PolicyGate",
                            "required_evidence": {"policy_decision": "BLOCK"},
                        }
                    ],
                    workspace_full=workspace,
                    cache_root_full="",
                    run_id="pm-00001",
                    pm_iteration=1,
                    events_path="",
                    dialogue_path="",
                )
                self.assertTrue(payload["hard_block"])
                self.assertEqual(payload["results"][0]["status"], "blocked")
                self.assertEqual(payload["results"][0]["error_code"], "POLICY_GATE_BLOCKED")

    def test_execute_non_director_tasks_policygate_missing_decision_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as workspace:
            with patch.dict(os.environ, {"KERNELONE_STATE_TO_RAMDISK": "0"}, clear=False):
                payload = self.loop_pm.execute_non_director_tasks(
                    tasks=[
                        {
                            "id": "PG-2",
                            "title": "Policy review missing decision",
                            "assigned_to": "PolicyGate",
                            "required_evidence": {},
                        }
                    ],
                    workspace_full=workspace,
                    cache_root_full="",
                    run_id="pm-00001",
                    pm_iteration=1,
                    events_path="",
                    dialogue_path="",
                )
                self.assertTrue(payload["hard_block"])
                self.assertEqual(payload["results"][0]["status"], "blocked")
                self.assertEqual(payload["results"][0]["error_code"], "POLICY_GATE_DECISION_MISSING")

    def test_execute_non_director_tasks_policygate_escalate_is_block(self):
        with tempfile.TemporaryDirectory() as workspace:
            with patch.dict(os.environ, {"KERNELONE_STATE_TO_RAMDISK": "0"}, clear=False):
                payload = self.loop_pm.execute_non_director_tasks(
                    tasks=[
                        {
                            "id": "PG-3",
                            "title": "Policy escalate",
                            "assigned_to": "PolicyGate",
                            "required_evidence": {"policy_decision": "ESCALATE"},
                        }
                    ],
                    workspace_full=workspace,
                    cache_root_full="",
                    run_id="pm-00001",
                    pm_iteration=1,
                    events_path="",
                    dialogue_path="",
                    schema_warnings=["PM-WARN-1"],
                )
                self.assertTrue(payload["hard_block"])
                self.assertEqual(payload["results"][0]["error_code"], "POLICY_GATE_ESCALATED")
                self.assertEqual(payload["schema_warning_count"], 1)
                self.assertEqual(payload["schema_warnings"], ["PM-WARN-1"])
                self.assertEqual(len(payload["gate_receipts"]), 1)

    def test_execute_non_director_tasks_auditor_missing_ticket_blocks(self):
        with tempfile.TemporaryDirectory() as workspace:
            with patch.dict(os.environ, {"KERNELONE_STATE_TO_RAMDISK": "0"}, clear=False):
                payload = self.loop_pm.execute_non_director_tasks(
                    tasks=[
                        {
                            "id": "AUD-2",
                            "title": "Audit fails without ticket",
                            "assigned_to": "Auditor",
                            "required_evidence": {"audit_result": "FAIL"},
                        }
                    ],
                    workspace_full=workspace,
                    cache_root_full="",
                    run_id="pm-00001",
                    pm_iteration=1,
                    events_path="",
                    dialogue_path="",
                )
                self.assertTrue(payload["hard_block"])
                self.assertEqual(payload["results"][0]["error_code"], "DEFECT_TICKET_MISSING")

    def test_migrate_tasks_in_place_adds_defaults_for_old_format(self):
        payload = {
            "tasks": [
                {"id": "OLD-1", "status": "todo", "title": "Legacy task"},
                {"id": "OLD-2", "status": "failed", "title": "Failed legacy"},
                {"id": "OLD-3", "status": "done", "title": "Done legacy",
                 "error_code": "STALE", "failure_detail": "stale"},
            ]
        }
        self.loop_pm._migrate_tasks_in_place(payload)
        self.assertEqual(payload["tasks"][0]["backlog_ref"], "")
        self.assertNotIn("error_code", payload["tasks"][0])

        self.assertEqual(payload["tasks"][1]["backlog_ref"], "")
        self.assertEqual(payload["tasks"][1]["error_code"], "")
        self.assertEqual(payload["tasks"][1]["failure_detail"], "")

    def test_build_pm_spin_fingerprint_is_stable(self):
        tasks = [{"id": "PM-1", "fingerprint": "abc123"}]
        fp1 = self.loop_pm.build_pm_spin_fingerprint(tasks, "blocked", 0)
        fp2 = self.loop_pm.build_pm_spin_fingerprint(tasks, "blocked", 0)
        self.assertEqual(fp1, fp2)

    def test_build_pm_spin_fingerprint_changes_on_progress(self):
        tasks = [{"id": "PM-1", "fingerprint": "abc123"}]
        blocked_fp = self.loop_pm.build_pm_spin_fingerprint(tasks, "blocked", 0)
        done_fp = self.loop_pm.build_pm_spin_fingerprint(tasks, "done", 1)
        self.assertNotEqual(blocked_fp, done_fp)

    def test_migrate_tasks_in_place_handles_empty_and_missing(self):
        self.loop_pm._migrate_tasks_in_place({})
        self.loop_pm._migrate_tasks_in_place({"tasks": "not_a_list"})
        self.loop_pm._migrate_tasks_in_place({"tasks": [None, 42, "bad"]})

    def test_apply_task_status_updates_no_failure_info_param(self):
        tasks = [{"id": "T1", "status": "in_progress"}]
        self.loop_pm.apply_task_status_updates(tasks, {"T1": "failed"})
        self.assertEqual(tasks[0]["status"], "failed")
        self.assertIn("failed_at", tasks[0])
        self.assertNotIn("error_code", tasks[0])

    def test_apply_task_status_updates_blocked_stores_failure(self):
        tasks = [{"id": "T1", "status": "in_progress"}]
        self.loop_pm.apply_task_status_updates(
            tasks,
            {"T1": "blocked"},
            failure_info={"T1": {"error_code": "RISK_BLOCKED", "failure_detail": "Risk too high"}},
        )
        self.assertEqual(tasks[0]["status"], "blocked")
        self.assertEqual(tasks[0]["error_code"], "RISK_BLOCKED")
        self.assertEqual(tasks[0]["failure_detail"], "Risk too high")
        self.assertIn("failed_at", tasks[0])

    def test_collect_schema_warnings_required_fields(self):
        payload = {
            "tasks": [
                {
                    "id": "TASK-1",
                    "priority": 1,
                    "dependencies": [],
                    "spec": "spec.md",
                    "acceptance_criteria": ["ok"],
                    "assigned_to": "Director",
                },
                {
                    "id": "TASK-2",
                    "priority": 2,
                },
            ]
        }
        with tempfile.TemporaryDirectory() as workspace:
            warnings = self.loop_pm.collect_schema_warnings(payload, workspace)
            self.assertTrue(warnings)
            self.assertTrue(any("TASK-2" in item for item in warnings))

    def test_consume_interrupt_task_preempts_and_archives(self):
        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            run_dir = workspace_path / "runtime" / "runs" / "pm-00001"
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"KERNELONE_STATE_TO_RAMDISK": "0"}, clear=False):
                from io_utils import interrupt_notice_path

                interrupt_path = Path(interrupt_notice_path(str(workspace_path)))
                interrupt_path.parent.mkdir(parents=True, exist_ok=True)
                interrupt_path.write_text("# Fix now\nApply urgent patch", encoding="utf-8")

                task = self.loop_pm.consume_interrupt_task(
                    str(workspace_path),
                    str(run_dir),
                    iteration=1,
                    run_id="pm-00001",
                )

            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task["priority"], 0)
            self.assertEqual(task["assigned_to"], "Director")
            self.assertFalse(interrupt_path.exists())
            self.assertTrue((run_dir / "INTERRUPT.consumed.md").is_file())

    def test_extract_json_from_llm_output_handles_tool_call_wrappers(self):
        output = (
            "[TOOL_CALL]\n"
            "{tool => \"repo_tool\", args => { --command \"ls -la\" }}\n"
            "[/TOOL_CALL]\n"
            "{\"overall_goal\":\"g\",\"focus\":\"f\",\"tasks\":[]}"
        )
        parsed = self.loop_pm._extract_json_from_llm_output(output)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed.get("overall_goal"), "g")
        self.assertEqual(parsed.get("tasks"), [])

    def test_run_director_once_requires_task_payload(self):
        with tempfile.TemporaryDirectory() as workspace:
            log_path = Path(workspace) / "director-subprocess.log"
            args = SimpleNamespace(
                director_result_path="",
                director_events_path="",
                pm_task_path="",
                planner_response_path="",
                ollama_response_path="",
                qa_response_path="",
                reviewer_response_path="",
                director_show_output=False,
                director_model="",
                director_timeout=0,
                prompt_profile="",
            )
            exit_code = self.loop_pm.run_director_once(
                args,
                workspace,
                iteration=1,
                subprocess_log_path=str(log_path),
            )
            self.assertEqual(exit_code, 1)
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("Director task payload missing", content)

    def test_run_director_once_logs_missing_payload(self):
        with tempfile.TemporaryDirectory() as workspace:
            log_path = Path(workspace) / "director-subprocess.log"
            args = SimpleNamespace(
                director_result_path="",
                director_events_path="",
                pm_task_path="",
                planner_response_path="",
                ollama_response_path="",
                qa_response_path="",
                reviewer_response_path="",
                director_show_output=False,
                director_model="",
                director_timeout=0,
                prompt_profile="",
            )
            exit_code = self.loop_pm.run_director_once(
                args,
                workspace,
                iteration=1,
                subprocess_log_path=str(log_path),
            )
            self.assertEqual(exit_code, 1)
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("Director task payload missing", content)

    def test_should_pause_for_manual_intervention_error_codes(self):
        self.assertTrue(self.loop_pm.should_pause_for_manual_intervention("DIRECTOR_NO_RESULT"))
        self.assertTrue(self.loop_pm.should_pause_for_manual_intervention("DIRECTOR_EXIT_1"))
        self.assertTrue(self.loop_pm.should_pause_for_manual_intervention("DIRECTOR_START_FAILED"))
        self.assertFalse(self.loop_pm.should_pause_for_manual_intervention("PLAN_MISSING"))

    def test_requires_manual_intervention_for_error_distinguishes_started_vs_not_started(self):
        self.assertTrue(
            self.loop_pm.requires_manual_intervention_for_error("DIRECTOR_NO_RESULT", director_started=False)
        )
        self.assertFalse(
            self.loop_pm.requires_manual_intervention_for_error("DIRECTOR_NO_RESULT", director_started=True)
        )
        self.assertTrue(
            self.loop_pm.requires_manual_intervention_for_error(
                "DIRECTOR_NO_RESULT",
                director_started=True,
                execution_started=False,
            )
        )
        self.assertFalse(
            self.loop_pm.requires_manual_intervention_for_error(
                "DIRECTOR_NO_RESULT",
                director_started=True,
                execution_started=True,
            )
        )
        self.assertFalse(
            self.loop_pm.requires_manual_intervention_for_error("PLAN_MISSING", director_started=False)
        )

    def test_classify_director_start_state_prefers_lifecycle_markers(self):
        state = self.loop_pm.classify_director_start_state(
            director_pid_seen=True,
            lifecycle_payload={"startup_completed": True, "execution_started": False},
        )
        self.assertTrue(state["startup_completed"])
        self.assertFalse(state["execution_started"])
        self.assertTrue(state["director_started"])

        state2 = self.loop_pm.classify_director_start_state(
            director_pid_seen=False,
            lifecycle_payload={"startup_completed": True, "execution_started": True},
        )
        self.assertTrue(state2["startup_completed"])
        self.assertTrue(state2["execution_started"])
        self.assertTrue(state2["director_started"])

    def test_build_resume_payload_from_last_tasks(self):
        payload = self.loop_pm.build_resume_payload_from_last_tasks(
            {
                "overall_goal": "Resume goal",
                "focus": "resume",
                "tasks": [
                    {
                        "id": "PM-RESUME-1",
                        "priority": 1,
                        "title": "Resume task",
                        "goal": "Continue original task",
                        "target_files": ["src/main.ts"],
                        "acceptance": ["Task can continue"],
                        "status": "todo",
                    }
                ],
            },
            iteration=7,
            timestamp="2026-02-19 12:00:00",
        )
        self.assertIsInstance(payload, dict)
        assert payload is not None
        self.assertEqual(payload["pm_iteration"], 7)
        self.assertEqual(payload["tasks"][0]["id"], "PM-RESUME-1")
        self.assertIn("Resumed prior task", payload["notes"])

    def test_resolve_agents_approval_mode_auto_switches_by_session_type(self):
        args = SimpleNamespace(agents_approval_mode="auto", loop=False)
        with patch.object(self.loop_pm, "_is_interactive_session", return_value=False):
            self.assertEqual(self.loop_pm.resolve_agents_approval_mode(args), "auto_accept")
        with patch.object(self.loop_pm, "_is_interactive_session", return_value=True):
            self.assertEqual(self.loop_pm.resolve_agents_approval_mode(args), "wait")

    def test_wait_for_agents_confirmation_auto_accept_creates_agents(self):
        with tempfile.TemporaryDirectory() as workspace, patch.dict(os.environ, {"KERNELONE_STATE_TO_RAMDISK": "0"}, clear=False):
            runtime = Path(workspace) / ".polaris" / "runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            draft = runtime / "contracts" / "agents.generated.md"
            draft.parent.mkdir(parents=True, exist_ok=True)
            draft.write_text(
                "\n".join(
                    [
                        "# AGENTS.md",
                        "",
                        "<INSTRUCTIONS>",
                        "- Objective: Auto-generated valid draft for test.",
                        "- Use UTF-8 explicitly for all text read/write operations.",
                        "- Keep runtime files under .polaris/runtime.",
                        "- Prefer small, verifiable increments with concrete commands.",
                        "- This line pads content length so the draft passes usability checks.",
                        "- This line pads content length so the draft passes usability checks.",
                        "</INSTRUCTIONS>",
                        "",
                        "Auto",
                    ]
                ),
                encoding="utf-8",
            )

            pm_state = {}
            pm_state_path = runtime / "state" / "pm.state.json"
            pm_report_path = runtime / "results" / "pm.report.md"
            args = SimpleNamespace(
                agents_approval_mode="auto_accept",
                agents_approval_timeout=5,
                loop=False,
            )

            with patch.object(self.loop_pm, "maybe_generate_agents_draft", return_value=str(draft)):
                ok = self.loop_pm.wait_for_agents_confirmation(
                    workspace,
                    "",
                    str(pm_state_path),
                    pm_state,
                    str(pm_report_path),
                    "",
                    "pm-test-1",
                    1,
                    "2026-02-19 15:00:00",
                    args,
                    poll_sec=0.01,
                )

            self.assertTrue(ok)
            agents_path = Path(workspace) / "AGENTS.md"
            self.assertTrue(agents_path.is_file())
            self.assertIn("Auto", agents_path.read_text(encoding="utf-8"))

    def test_wait_for_agents_confirmation_wait_timeout_pauses_for_manual(self):
        with tempfile.TemporaryDirectory() as workspace, patch.dict(os.environ, {"KERNELONE_STATE_TO_RAMDISK": "0"}, clear=False):
            runtime = Path(workspace) / ".polaris" / "runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            draft = runtime / "contracts" / "agents.generated.md"
            draft.parent.mkdir(parents=True, exist_ok=True)
            draft.write_text("# AGENTS (Draft)\n\nNeed review\n", encoding="utf-8")

            pm_state = {}
            pm_state_path = runtime / "state" / "pm.state.json"
            pm_report_path = runtime / "results" / "pm.report.md"
            args = SimpleNamespace(
                agents_approval_mode="wait",
                agents_approval_timeout=1,
                loop=False,
            )

            with patch.object(self.loop_pm, "maybe_generate_agents_draft", return_value=str(draft)):
                ok = self.loop_pm.wait_for_agents_confirmation(
                    workspace,
                    "",
                    str(pm_state_path),
                    pm_state,
                    str(pm_report_path),
                    "",
                    "pm-test-2",
                    2,
                    "2026-02-19 15:05:00",
                    args,
                    poll_sec=0.01,
                )

            self.assertFalse(ok)
            self.assertTrue(pm_state.get("awaiting_manual_intervention"))
            self.assertEqual(pm_state.get("manual_intervention_reason_code"), "AGENTS_APPROVAL_TIMEOUT")
            pause_path = Path(self.loop_pm.pause_flag_path(workspace))
            self.assertFalse(pause_path.exists())
            self.assertFalse((Path(workspace) / "AGENTS.md").exists())

    def test_wait_for_agents_confirmation_wait_timeout_can_pause_when_configured(self):
        with tempfile.TemporaryDirectory() as workspace, patch.dict(
            os.environ,
            {
                "KERNELONE_STATE_TO_RAMDISK": "0",
                "KERNELONE_MANUAL_INTERVENTION_MODE": "pause",
            },
            clear=False,
        ):
            runtime = Path(workspace) / ".polaris" / "runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            draft = runtime / "contracts" / "agents.generated.md"
            draft.parent.mkdir(parents=True, exist_ok=True)
            draft.write_text("# AGENTS (Draft)\n\nNeed review\n", encoding="utf-8")

            pm_state = {}
            pm_state_path = runtime / "state" / "pm.state.json"
            pm_report_path = runtime / "results" / "pm.report.md"
            args = SimpleNamespace(
                agents_approval_mode="wait",
                agents_approval_timeout=1,
                loop=False,
            )

            with patch.object(self.loop_pm, "maybe_generate_agents_draft", return_value=str(draft)):
                ok = self.loop_pm.wait_for_agents_confirmation(
                    workspace,
                    "",
                    str(pm_state_path),
                    pm_state,
                    str(pm_report_path),
                    "",
                    "pm-test-2b",
                    3,
                    "2026-02-19 15:10:00",
                    args,
                    poll_sec=0.01,
                )

            self.assertFalse(ok)
            self.assertTrue(pm_state.get("awaiting_manual_intervention"))
            self.assertEqual(pm_state.get("manual_intervention_mode"), "pause")
            pause_path = Path(self.loop_pm.pause_flag_path(workspace))
            self.assertTrue(pause_path.is_file())

    def test_build_pm_prompt_includes_bootstrap_first_rules(self):
        mock_ctx = {
            "persona_instruction": "",
            "anthropomorphic_context": "",
            "prompt_context_obj": SimpleNamespace(model_dump=lambda: {}),
            "context_pack": None,
        }
        with patch.object(self.loop_pm, "_use_context_engine_v2", return_value=False):
            with patch.object(self.loop_pm, "get_anthropomorphic_context", return_value=mock_ctx):
                prompt = self.loop_pm.build_pm_prompt(
                    requirements="req",
                    plan_text="plan",
                    gap_report="",
                    last_qa="",
                    last_tasks={},
                    director_result={},
                    pm_state={},
                    iteration=1,
                    run_id="pm-test",
                    events_path="",
                )
        self.assertIn("Bootstrap-first planning rule (required):", prompt)
        self.assertIn("Do NOT schedule verification-only tasks", prompt)
        self.assertIn("default to `scope_mode: \"module\"` with `scope_paths`", prompt)
        self.assertIn("Only use `scope_mode: \"exact_files\"`", prompt)


if __name__ == "__main__":
    raise SystemExit(unittest.main())

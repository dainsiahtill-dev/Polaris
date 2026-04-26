import json
import unittest
from pathlib import Path


class TestPmTaskSchemaContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo_root = Path(__file__).resolve().parents[1]
        schema_path = repo_root / "schema" / "pm_tasks.schema.json"
        if not schema_path.is_file():
            raise unittest.SkipTest(f"Schema not found: {schema_path}")
        with open(schema_path, "r", encoding="utf-8") as handle:
            cls.schema = json.load(handle)
        try:
            import jsonschema

            cls.jsonschema = jsonschema
        except ImportError:
            raise unittest.SkipTest("jsonschema not installed")

    def _validate(self, payload):
        self.jsonschema.validate(payload, self.schema)

    def _base_task(self):
        return {
            "id": "PM-0001-1",
            "priority": 1,
            "dependencies": [],
            "spec": "",
            "acceptance_criteria": ["health endpoint returns 200"],
            "assigned_to": "Director",
        }

    def test_schema_version_v2_is_valid(self):
        task = self._base_task()
        task["required_evidence"] = {"validation_paths": ["src/index.ts", "package.json"]}
        payload = {
            "schema_version": 2,
            "run_id": "pm-2026-0001",
            "pm_iteration": 1,
            "tasks": [task],
        }
        self._validate(payload)

    def test_schema_version_v1_remains_valid(self):
        payload = {
            "schema_version": 1,
            "run_id": "pm-legacy-0001",
            "pm_iteration": 1,
            "tasks": [self._base_task()],
        }
        self._validate(payload)

    def test_required_evidence_legacy_array_is_valid(self):
        task = self._base_task()
        task["required_evidence"] = ["src/index.ts", "package.json"]
        payload = {
            "schema_version": 2,
            "run_id": "pm-2026-0002",
            "pm_iteration": 2,
            "tasks": [task],
        }
        self._validate(payload)

    def test_required_evidence_object_for_gate_roles_is_valid(self):
        task = self._base_task()
        task["assigned_to"] = "PolicyGate"
        task["required_evidence"] = {"policy_decision": "ALLOW"}
        payload = {
            "schema_version": 2,
            "run_id": "pm-2026-0003",
            "pm_iteration": 3,
            "tasks": [task],
        }
        self._validate(payload)

    def test_parallel_engine_fields_are_valid(self):
        task = self._base_task()
        task["parallel_group"] = "group-a"
        task["shardable"] = True
        task["max_parallel_hint"] = 2
        task["capability"] = "frontend"
        payload = {
            "schema_version": 2,
            "run_id": "pm-2026-0100",
            "pm_iteration": 1,
            "engine": {
                "director_execution_mode": "multi",
                "max_directors": 2,
                "scheduling_policy": "dag",
            },
            "tasks": [task],
        }
        self._validate(payload)

    def test_qa_contract_object_is_valid(self):
        task = self._base_task()
        task["qa_contract"] = {
            "schema_version": 1,
            "plugin": "rules_v1",
            "task_type": "backend_api",
            "hard_gates": ["director_status_success"],
            "regression_gates": [{"kind": "changed_files_min", "min": 1}],
            "evidence_required": ["runtime/results/director.result.json"],
            "retry_policy": {"max_director_retries": 3},
        }
        payload = {
            "schema_version": 2,
            "run_id": "pm-2026-0101",
            "pm_iteration": 1,
            "tasks": [task],
        }
        self._validate(payload)

    def test_qa_contract_with_coordination_is_valid(self):
        task = self._base_task()
        task["qa_contract"] = {
            "schema_version": 1,
            "plugin": "rules_v1",
            "plugin_hint": "pytest_api",
            "task_type": "backend_api",
            "hard_gates": ["director_status_success"],
            "evidence_required": ["runtime/results/director.result.json"],
            "retry_policy": {"max_director_retries": 3},
            "coordination": {
                "enabled": True,
                "max_rounds": 2,
                "triggers": ["qa_fail", "qa_inconclusive", "complex_task"],
            },
        }
        payload = {
            "schema_version": 2,
            "run_id": "pm-2026-0102",
            "pm_iteration": 1,
            "tasks": [task],
        }
        self._validate(payload)


if __name__ == "__main__":
    raise SystemExit(unittest.main())

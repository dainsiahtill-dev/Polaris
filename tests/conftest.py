"""Pytest configuration for tests in project root."""

import contextlib
import importlib
import importlib.util
import os
import sys

# Legacy tests importing removed top-level modules (app, core, api, domain, pm, application).
# These modules were migrated to the polaris.* namespace; the old imports are unresolvable.
# Per CLAUDE.md §2 / §3: root tests/ are legacy; new tests live in src/backend/tests.
collect_ignore = [
    # tests/ root — legacy tests with stale imports
    "test_agent_stress_backend_bootstrap_cleanup.py",
    "test_agent_stress_framework.py",
    "test_agent_stress_projection_streaming.py",
    "test_auditor_evidence_markers.py",
    "test_director_exec_utils.py",
    "test_director_hp_end_to_end.py",
    "test_director_policy_runtime_rollback_guard.py",
    "test_director_scope_guards.py",
    "test_director_stop.py",
    "test_director_tool_first_contract.py",
    "test_director_tooling.py",
    "test_execution_phase_recovery.py",
    "test_loop_director_bootstrap.py",
    "test_loop_director_required_evidence.py",
    "test_loop_director_verification_commands.py",
    "test_orchestration_runtime_mode.py",
    "test_plan_act_context.py",
    "test_policy_contract.py",
    "test_precision_editor.py",
    "test_qa_auditor_verify_gate.py",
    "test_qa_task_type_classification.py",
    # tests/integration/ — legacy test with stale imports
    "integration/test_task_trace_integration.py",
    # tests/unit/ — legacy test with stale imports
    "unit/test_task_trace.py",
]

# Add backend tests directory so that 'tests.agent_stress' resolves correctly
BACKEND_TESTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "backend", "tests"))
if BACKEND_TESTS_DIR not in sys.path:
    sys.path.insert(0, BACKEND_TESTS_DIR)

# Add backend directory to sys.path so we can import core modules
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Pre-load agent_stress modules to handle namespace package issues
_agent_stress_dir = os.path.join(BACKEND_TESTS_DIR, "agent_stress")
if os.path.isdir(_agent_stress_dir):
    _init_file = os.path.join(_agent_stress_dir, "__init__.py")
    if os.path.exists(_init_file):
        spec = importlib.util.spec_from_file_location("tests.agent_stress", _init_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules["tests.agent_stress"] = module
            spec.loader.exec_module(module)

    # Pre-load submodules
    for root, _dirs, files in os.walk(_agent_stress_dir):
        for filename in files:
            if filename.endswith(".py") and filename != "__init__.py":
                rel_path = os.path.relpath(os.path.join(root, filename), BACKEND_TESTS_DIR)
                module_name = "tests.agent_stress." + rel_path.replace(os.sep, ".")[:-3]
                if module_name not in sys.modules:
                    full_path = os.path.join(root, filename)
                    spec = importlib.util.spec_from_file_location(module_name, full_path)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        with contextlib.suppress(Exception):
                            spec.loader.exec_module(module)

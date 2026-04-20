"""Pytest configuration for tests in project root."""

import importlib
import importlib.util
import os
import sys
import types

# Add backend tests directory so that 'tests.agent_stress' resolves correctly
BACKEND_TESTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src", "backend", "tests")
)
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
    for root, dirs, files in os.walk(_agent_stress_dir):
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
                        try:
                            spec.loader.exec_module(module)
                        except Exception:
                            # Some modules may fail to load due to circular imports or missing deps
                            pass

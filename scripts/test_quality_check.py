"""Tests for quality_check.py."""

import sys
from pathlib import Path

# Get the script path
SCRIPT_PATH = Path(__file__).parent / "quality_check.py"


def test_quality_check_import():
    """Test that quality_check.py can be imported."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("quality_check", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
    assert hasattr(module, "run_command")
    print("Import test passed")


if __name__ == "__main__":
    test_quality_check_import()
    print("All tests passed")

from __future__ import annotations

try:
    import pages  # type: ignore  # noqa: F401
except Exception:
    import pytest

    pytest.skip("e2e dashboard tests require Playwright pages module", allow_module_level=True)

from _shim_helper import load_root_test

load_root_test(globals(), "tests/e2e/tests/test_dashboard.py")

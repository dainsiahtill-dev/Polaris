"""Architecture fence for retired KernelOne time utility aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.utils as utils_package
import polaris.kernelone.utils.time_utils as time_utils

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TIME_UTILS = BACKEND_ROOT / "polaris" / "kernelone" / "utils" / "time_utils.py"
UTILS_PACKAGE = BACKEND_ROOT / "polaris" / "kernelone" / "utils" / "__init__.py"
CONTEXT_ENGINE_FILES = (
    BACKEND_ROOT / "polaris" / "kernelone" / "context" / "engine" / "models.py",
    BACKEND_ROOT / "polaris" / "kernelone" / "context" / "engine" / "engine.py",
    BACKEND_ROOT / "polaris" / "kernelone" / "context" / "engine" / "__init__.py",
)
RETIRED_TIME_ALIASES = ("_utc_now", "_utc_now_iso", "_utc_now_str")


def test_time_private_aliases_are_not_public_exports() -> None:
    """KernelOne time helpers should expose canonical public names only."""
    for alias in RETIRED_TIME_ALIASES:
        assert not hasattr(time_utils, alias)
        assert alias not in time_utils.__all__
        assert not hasattr(utils_package, alias)
        assert alias not in utils_package.__all__


def test_time_sources_do_not_reintroduce_alias_assignments() -> None:
    """Source-level fence blocks package-level time compatibility aliases."""
    for path in (TIME_UTILS, UTILS_PACKAGE):
        source = path.read_text(encoding="utf-8")
        for alias in RETIRED_TIME_ALIASES:
            assert f"{alias} =" not in source
            assert f'"{alias}"' not in source


def test_context_engine_uses_canonical_time_helper() -> None:
    """Context Engine should not import the retired private time helper."""
    for path in CONTEXT_ENGINE_FILES:
        source = path.read_text(encoding="utf-8")
        assert "from polaris.kernelone.utils.time_utils import _utc_now" not in source

"""Architecture fence for retired KernelOne JSON utility aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.utils as utils_package
import polaris.kernelone.utils.json_utils as json_utils

BACKEND_ROOT = Path(__file__).resolve().parents[3]
JSON_UTILS = BACKEND_ROOT / "polaris" / "kernelone" / "utils" / "json_utils.py"
UTILS_PACKAGE = BACKEND_ROOT / "polaris" / "kernelone" / "utils" / "__init__.py"
RETIRED_JSON_ALIASES = ("_safe_json_loads", "_parse_json_payload")


def test_json_private_aliases_are_not_public_exports() -> None:
    """KernelOne JSON helpers should expose canonical public names only."""
    for alias in RETIRED_JSON_ALIASES:
        assert not hasattr(json_utils, alias)
        assert alias not in json_utils.__all__
        assert not hasattr(utils_package, alias)
        assert alias not in utils_package.__all__


def test_json_sources_do_not_reintroduce_private_aliases() -> None:
    """Source-level fence blocks package-level compatibility aliases."""
    for path in (JSON_UTILS, UTILS_PACKAGE):
        source = path.read_text(encoding="utf-8")
        for alias in RETIRED_JSON_ALIASES:
            assert f"{alias} =" not in source
            assert f'"{alias}"' not in source

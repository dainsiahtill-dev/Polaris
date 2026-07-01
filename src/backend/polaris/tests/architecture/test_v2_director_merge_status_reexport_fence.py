from __future__ import annotations

import json
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DIRECTOR_V2_MODULE = BACKEND_ROOT / "polaris" / "delivery" / "http" / "v2" / "director.py"
DIRECTOR_EXECUTION_DESCRIPTOR = (
    BACKEND_ROOT / "polaris" / "cells" / "director" / "execution" / "generated" / "descriptor.pack.json"
)


def test_v2_director_does_not_reexport_merge_director_status_shim() -> None:
    source = DIRECTOR_V2_MODULE.read_text(encoding="utf-8")

    assert "def _merge_director_status" not in source
    assert "_merge_director_status re-export is deprecated" not in source
    assert "warnings.warn" not in source


def test_director_execution_descriptor_does_not_publish_retired_merge_shim() -> None:
    descriptor = json.loads(DIRECTOR_EXECUTION_DESCRIPTOR.read_text(encoding="utf-8"))
    capability_names = {str(capability.get("name", "")) for capability in descriptor.get("capabilities", [])}

    assert "_merge_director_status" not in capability_names

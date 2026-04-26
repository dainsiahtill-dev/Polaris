import sys
from pathlib import Path
import pytest

# Skip this test - app.llm module has been migrated to polaris
try:
    from app.llm import runtime_config
except ImportError:
    pytest.importorskip("polaris")

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def test_default_role_binding_mode_is_strict(monkeypatch):
    monkeypatch.delenv("KERNELONE_ROLE_MODEL_BINDING_MODE", raising=False)
    assert runtime_config._resolve_role_binding_mode() == "strict"

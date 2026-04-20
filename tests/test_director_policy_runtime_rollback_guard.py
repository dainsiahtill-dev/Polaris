import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
LOOP_MODULE_ROOT = BACKEND_ROOT / "core" / "polaris_loop"
for entry in (str(BACKEND_ROOT), str(LOOP_MODULE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from core.polaris_loop.director_policy_runtime import apply_policy_to_state


class _DummyState:
    rollback_on_fail = True
    rollback_on_block = True
    memory_enabled = False
    memory_dir_full = ""


def test_auto_rollback_disabled_without_manual_confirmation(monkeypatch):
    monkeypatch.delenv("POLARIS_MANUAL_ROLLBACK_CONFIRMED", raising=False)
    state = _DummyState()
    policy = {
        "repair": {"rollback_on_fail": True},
        "risk": {"rollback_on_block": True},
        "factory": {"hard_rollback_enabled": True},
    }
    apply_policy_to_state(state, policy)
    assert state.rollback_on_fail is False
    assert state.rollback_on_block is False
    assert getattr(state, "hard_rollback_enabled", False) is False


def test_manual_confirmation_can_enable_rollback(monkeypatch):
    monkeypatch.setenv("POLARIS_MANUAL_ROLLBACK_CONFIRMED", "1")
    state = _DummyState()
    policy = {
        "repair": {"rollback_on_fail": True},
        "risk": {"rollback_on_block": True},
        "factory": {"hard_rollback_enabled": True},
    }
    apply_policy_to_state(state, policy)
    assert state.rollback_on_fail is True
    assert state.rollback_on_block is True
    assert getattr(state, "hard_rollback_enabled", False) is True

"""Architecture fence for retired config snapshot exception aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.domain as domain
import polaris.domain.models as domain_models
import polaris.domain.models.config_snapshot as config_snapshot

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CONFIG_SNAPSHOT_MODULE = BACKEND_ROOT / "polaris" / "domain" / "models" / "config_snapshot.py"
DOMAIN_MODELS_INIT = BACKEND_ROOT / "polaris" / "domain" / "models" / "__init__.py"
DOMAIN_INIT = BACKEND_ROOT / "polaris" / "domain" / "__init__.py"


def test_frozen_instance_error_alias_is_retired() -> None:
    """ConfigSnapshotImmutableError is the single Polaris config immutability error."""
    assert hasattr(config_snapshot, "ConfigSnapshotImmutableError")
    assert hasattr(domain_models, "ConfigSnapshotImmutableError")
    assert hasattr(domain, "ConfigSnapshotImmutableError")

    assert not hasattr(config_snapshot, "FrozenInstanceError")
    assert not hasattr(domain_models, "FrozenInstanceError")
    assert "FrozenInstanceError" not in domain_models.__all__
    assert not hasattr(domain, "FrozenInstanceError")
    assert "FrozenInstanceError" not in domain.__all__


def test_config_snapshot_sources_do_not_reintroduce_alias() -> None:
    """Source-level fence blocks the old FrozenInstanceError compatibility export."""
    for path in (CONFIG_SNAPSHOT_MODULE, DOMAIN_MODELS_INIT, DOMAIN_INIT):
        source = path.read_text(encoding="utf-8")
        assert "FrozenInstanceError = ConfigSnapshotImmutableError" not in source
        assert "FrozenInstanceError" not in source

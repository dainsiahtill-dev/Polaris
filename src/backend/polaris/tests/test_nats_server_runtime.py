from __future__ import annotations

from pathlib import Path

from polaris.infrastructure.messaging.nats import server_runtime


def test_should_manage_local_nats_accepts_loopback_urls() -> None:
    assert server_runtime.should_manage_local_nats("nats://127.0.0.1:4222") is True
    assert server_runtime.should_manage_local_nats("nats://localhost:4222") is True


def test_should_manage_local_nats_rejects_remote_urls() -> None:
    assert server_runtime.should_manage_local_nats("nats://10.0.0.8:4222") is False
    assert server_runtime.should_manage_local_nats("nats://example.com:4222") is False


def test_resolve_managed_nats_storage_root_uses_polaris_home(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str((tmp_path / ".polaris").resolve()))

    resolved = server_runtime.resolve_managed_nats_storage_root()

    expected = (tmp_path / ".polaris" / "runtime" / "nats" / "jetstream").resolve()
    assert resolved == expected


def test_resolve_managed_nats_storage_root_uses_kernelone_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("KERNELONE_HOME", raising=False)
    monkeypatch.setenv("KERNELONE_ROOT", str(tmp_path))

    resolved = server_runtime.resolve_managed_nats_storage_root()

    expected = (tmp_path / ".polaris" / "runtime" / "nats" / "jetstream").resolve()
    assert resolved == expected

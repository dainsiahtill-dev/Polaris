"""Deterministic configuration operations retain old values and KFS bytes."""

from __future__ import annotations

import importlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        value = cls(2026, 1, 2, 3, 4, 5, 678901)
        return value if tz is None else value.replace(tzinfo=tz)


def _private_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Path]:
    home = tmp_path / "private-home"
    for key, value in {
        "KERNELONE_HOME": str(home),
        "KERNELONE_RUNTIME_ROOT": "",
        "KERNELONE_RUNTIME_CACHE_ROOT": "",
        "KERNELONE_STATE_TO_RAMDISK": "0",
        "KERNELONE_RAMDISK_ROOT": "",
    }.items():
        monkeypatch.setenv(key, value)
    module = importlib.import_module("polaris.cells.llm.control_plane.internal.llm_config_agent")
    # Audit paths contain an ancestor .polaris: the unchanged legacy inference
    # selects that ancestor. Bind a real private KFS explicitly; this fixture
    # does not establish the correctness of default workspace inference.
    monkeypatch.setattr(module, "KernelFileSystem", lambda _inferred, adapter: KernelFileSystem(str(tmp_path), adapter))
    return module, home / "config" / "llm"


def _freeze(module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "datetime", FrozenDateTime)
    monkeypatch.setattr(module.uuid, "uuid4", lambda: UUID("12345678-1234-5678-1234-567812345678"))


def _inputs() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("get_llm_config", {"role": "missing"}),
        ("list_all_configs", {}),
        (
            "set_llm_config",
            {
                "role": "pm",
                "provider_id": "p",
                "provider_type": "generic",
                "model": "m",
                "profile": "p",
                "provider_cfg": {"temperature": 3.0},
            },
        ),
        (
            "set_llm_config",
            {
                "role": " pm ",
                "provider_id": " local ",
                "provider_type": " OLLAMA ",
                "model": " 模型甲 ",
                "profile": " 默认 ",
                "provider_cfg": {
                    "api_key": "synthetic-key",
                    "API_TOKEN": "synthetic-token",
                    "access_token": "synthetic-access",
                    "secret": "synthetic-secret",
                    "password": "synthetic-password",
                    "auth_token": "synthetic-auth",
                    "temperature": 0.7,
                    "nested": {"api_key": "synthetic-nested"},
                    "label": "中文",
                },
            },
        ),
        ("get_llm_config", {"role": "pm"}),
        (
            "set_llm_config",
            {
                "role": "qa",
                "provider_id": "my-cli",
                "provider_type": "cli",
                "model": "模型乙",
                "profile": "qa",
                "provider_cfg": {"command": "codex synthetic", "max_tokens": 1000000},
            },
        ),
        (
            "set_llm_config",
            {
                "role": "ce",
                "provider_id": "sdk",
                "provider_type": "codex_sdk",
                "model": "模型丙",
                "profile": "ce",
                "provider_cfg": {"max_tokens": True},
            },
        ),
        (
            "update_llm_config",
            {
                "role": "qa",
                "provider_id": " generic ",
                "model": " 新模型 ",
                "profile": " 新配置 ",
                "provider_cfg": {"command": "ordinary", "temperature": 0.2},
            },
        ),
        (
            "update_llm_config",
            {
                "role": "pm",
                "provider_id": "codex_cli",
                "provider_cfg": {
                    "command": "codex synthetic",
                    "max_tokens": "4096",
                    "api_key": "synthetic-replacement",
                },
            },
        ),
        ("update_llm_config", {"role": "missing", "model": "x"}),
        ("update_llm_config", {"role": "pm", "provider_cfg": {"max_tokens": 1000001}}),
        ("deactivate_config", {"role": "missing"}),
        ("activate_config", {"role": "missing"}),
        ("deactivate_config", {"role": "pm"}),
        ("get_llm_config", {"role": "pm"}),
        ("get_config", {"role": "pm"}),
        ("activate_config", {"role": "pm"}),
        ("list_all_configs", {}),
        ("get_all_configs", {}),
        ("delete_config", {"role": "qa"}),
        ("delete_config", {"role": "missing"}),
        ("delete_config", {"role": ""}),
        ("list_all_configs", {}),
        ("get_config", {"role": "missing"}),
    ]


def _run_operations(service: Any, config_dir: Path) -> list[dict[str, Any]]:
    rows = []
    adapter = get_default_adapter()
    path = config_dir / "configs.json"
    for name, arguments in _inputs():
        value = getattr(service, name)(**arguments)
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        elif isinstance(value, list):
            value = [item.to_dict() for item in value]
        encoded = adapter.read_text(str(path), encoding="utf-8") if adapter.exists(str(path)) else None
        rows.append({"operation": name, "arguments": arguments, "result": value, "persisted_utf8": encoded})
    return rows


def test_named_old_operations_preserve_exact_returns_and_json_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, config_dir = _private_storage(tmp_path, monkeypatch)
    _freeze(module, monkeypatch)
    public = importlib.import_module("polaris.cells.llm.control_plane.public.service")
    service = public.LLMConfigService(config_dir=str(config_dir))
    actual = _run_operations(service, config_dir)
    fixture = Path(__file__).with_name("fixtures") / "llm_config_service_golden_v1.json"
    expected = json.loads(get_default_adapter().read_text(str(fixture), encoding="utf-8"))
    assert actual == expected["hr_operations"]
    assert actual[3]["result"]["config"]["provider_cfg"]["api_key"] == "***"
    assert actual[3]["result"]["config"]["provider_cfg"]["nested"]["api_key"] == "synthetic-nested"
    assert actual[15]["result"]["provider_cfg"]["api_key"] == "synthetic-replacement"


def test_ordinary_public_save_get_keeps_distinct_projection_and_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _module, config_dir = _private_storage(tmp_path, monkeypatch)
    public = importlib.import_module("polaris.cells.llm.control_plane.public.service")
    monkeypatch.setattr(public, "datetime", FrozenDateTime)
    service = public.LlmControlPlaneService()
    monkeypatch.setattr(public, "_resolve_control_plane_storage_path", lambda workspace: str(config_dir))
    commands = importlib.import_module("polaris.cells.llm.control_plane.public.contracts")
    command = commands.SaveLlmConfigCommandV1(
        workspace=str(tmp_path),
        role="pm",
        provider_id="synthetic-provider",
        model="synthetic-model",
        config={
            "type": "cli",
            "command": "codex synthetic",
            "provider_kind": " explicit-kind ",
            "api_key": "synthetic-key",
            "temperature": 9.0,
        },
    )
    saved = service.save_config(command)
    loaded = service.get_config(commands.GetLlmConfigQueryV1(workspace=str(tmp_path), role="pm"))
    actual = {
        "saved": {
            "provider_id": saved.provider_id,
            "model": saved.model,
            "ready": saved.ready,
            "metadata": dict(saved.metadata),
        },
        "loaded": {
            "provider_id": loaded.provider_id,
            "model": loaded.model,
            "ready": loaded.ready,
            "metadata": dict(loaded.metadata),
        },
        "persisted_utf8": get_default_adapter().read_text(str(config_dir / "configs.json"), encoding="utf-8"),
    }
    fixture = Path(__file__).with_name("fixtures") / "llm_config_service_golden_v1.json"
    expected = json.loads(get_default_adapter().read_text(str(fixture), encoding="utf-8"))
    assert actual == expected["ordinary_service"]
    assert saved.workspace == loaded.workspace == str(tmp_path)
    assert saved.role == loaded.role == "pm"
    assert actual["loaded"]["metadata"]["provider_kind"] == "explicit-kind"
    assert actual["loaded"]["metadata"]["provider_cfg"]["api_key"] == "synthetic-key"


def test_actual_store_ttl_preload_invalidate_and_cold_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, config_dir = _private_storage(tmp_path, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    store = module.LLMConfigStore(str(config_dir))
    store.preload()
    other = module.LLMConfigStore(str(config_dir))
    item = module.LLMConfig(
        config_id="fixture",
        role="pm",
        provider_id="synthetic",
        provider_type="cli",
        provider_kind="generic",
        model="m",
        profile="p",
        provider_cfg={"api_key": "synthetic"},
        created_at=datetime(2026, 1, 2),
        updated_at=datetime(2026, 1, 2),
    )
    other.save(item)
    clock[0] = 399.999
    assert store.get("pm") is None
    clock[0] = 400.0
    assert store.get("pm").to_dict() == item.to_dict()
    loaded = store.get("pm")
    loaded.provider_cfg["api_key"] = "local mutation"
    assert store.get("pm").provider_cfg["api_key"] == "synthetic"
    assert other.delete("pm") is True
    assert store.get("pm") is not None
    store.invalidate_cache()
    assert store.get("pm") is None
    other.save(item)
    store.invalidate_cache()
    store.preload()
    encoded = get_default_adapter().read_bytes(str(config_dir / "configs.json"))
    assert store.get_all()[0].role == "pm"
    reopened = module.LLMConfigStore(str(config_dir), cache_ttl=0.0)
    assert reopened.get_all()[0].to_dict() == item.to_dict()
    assert get_default_adapter().read_bytes(str(config_dir / "configs.json")) == encoded
    assert other.delete("pm") is True
    assert reopened.get("pm") is None


def test_invalid_json_is_observable_instead_of_empty_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, config_dir = _private_storage(tmp_path, monkeypatch)
    get_default_adapter().write_text(str(config_dir / "configs.json"), "{torn", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        module.LLMConfigStore(str(config_dir)).preload()
    public = importlib.import_module("polaris.cells.llm.control_plane.public.service")
    with pytest.raises(json.JSONDecodeError):
        public.LLMConfigService(config_dir=str(config_dir))


@pytest.mark.parametrize(
    "provider_id,provider_type,cfg,want",
    [
        ("local", "ollama", {}, "ollama"),
        ("sdk", "codex_sdk", {}, "codex"),
        ("x", "cli", {"command": "CODEX synthetic"}, "codex"),
        ("codex_cli", "cli", {}, "codex"),
        ("x", "anthropic", {}, "generic"),
    ],
)
def test_provider_kind_cases_keep_existing_inference(
    provider_id: str, provider_type: str, cfg: dict[str, Any], want: str
) -> None:
    public = importlib.import_module("polaris.cells.llm.control_plane.public.service")
    assert public.LLMConfigService.resolve_provider_kind(provider_id, provider_type, cfg) == want


@pytest.mark.parametrize(
    "cfg",
    [
        {"temperature": float("nan")},
        {"top_p": float("inf")},
        {"max_tokens": False},
        {"max_tokens": None},
        {"max_tokens": "invalid"},
    ],
)
def test_invalid_generation_config_is_rejected_before_any_file_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: dict[str, Any]
) -> None:
    _module, config_dir = _private_storage(tmp_path, monkeypatch)
    public = importlib.import_module("polaris.cells.llm.control_plane.public.service")
    service = public.LLMConfigService(config_dir=str(config_dir))
    result = service.set_llm_config("pm", "synthetic", "cli", "model", "profile", cfg)
    assert result["ok"] is False and result["error"] == "invalid_provider_cfg"
    assert not get_default_adapter().exists(str(config_dir / "configs.json"))


def test_public_data_and_validation_aliases_keep_original_class_module_identity() -> None:
    core = importlib.import_module("polaris.cells.llm.control_plane.internal.llm_config_agent")
    public = importlib.import_module("polaris.cells.llm.control_plane.public.service")
    facade = importlib.import_module("polaris.cells.llm.control_plane.public")
    root = importlib.import_module("polaris.cells.llm.control_plane")
    assert public.LLMConfig is facade.LLMConfig is root.LLMConfig is core.LLMConfig
    assert public.LLMConfigStore is facade.LLMConfigStore is root.LLMConfigStore is core.LLMConfigStore
    assert (
        public.LLMConfig.__module__
        == public.LLMConfigStore.__module__
        == "polaris.cells.llm.control_plane.internal.llm_config_agent"
    )
    assert public.validate_llm_provider_cfg is core._validate_provider_cfg

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings

from polaris.cells.policy.workspace_guard.service import SELF_UPGRADE_MODE_ENV
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.runtime.defaults import DEFAULT_PM_LOG
from polaris.kernelone.storage.io_paths import normalize_artifact_rel_path

logger = logging.getLogger(__name__)


def get_legacy_settings_path() -> str:
    from polaris.cells.storage.layout import polaris_home

    return os.path.join(polaris_home(), "settings.json")


def get_polaris_root() -> str:
    def _expand(path: str) -> str:
        return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))

    root_override = str(os.environ.get("KERNELONE_ROOT") or "").strip()
    if root_override:
        return _expand(root_override)

    home_override = str(os.environ.get("KERNELONE_HOME") or "").strip()
    if home_override:
        expanded = _expand(home_override)
        trimmed = expanded.rstrip("\\/")
        if os.path.basename(trimmed).lower() == ".polaris":
            parent = os.path.dirname(trimmed)
            return parent or trimmed
        return expanded

    if os.name == "nt":
        appdata = str(os.environ.get("APPDATA") or "").strip()
        if appdata:
            return _expand(appdata)

    xdg = str(os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return _expand(xdg)

    return _expand("~")


def get_workspace_settings_path(workspace: str) -> str:
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    workspace_root = str(workspace or "").strip()
    if not workspace_root:
        return ""
    return os.path.join(
        os.path.abspath(workspace_root),
        get_workspace_metadata_dir_name(),
        "settings.json",
    )


def get_settings_path(workspace: str = "") -> str:
    del workspace
    from polaris.cells.storage.layout import polaris_home

    return os.path.join(polaris_home(), "config", "settings.json")


def _load_json_dict(path: str) -> dict[str, Any]:
    """Load a JSON object from *path*.

    Returns an empty dict when the file does not exist (normal first-run
    situation). Logs at ERROR for any other failure so that permissions issues
    and malformed files surface in monitoring rather than silently producing
    empty configuration that would cascade into unexpected behaviour.
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as exc:
        logger.error(
            "Settings file %s contains invalid JSON: %s (line %s, col %s)",
            path,
            exc.msg,
            exc.lineno,
            exc.colno,
        )
        return {}
    except PermissionError as exc:
        logger.error("Permission denied reading settings file %s: %s", path, exc)
        return {}
    except OSError as exc:
        logger.error("OS error reading settings file %s: %s", path, exc)
        return {}
    except (RuntimeError, ValueError) as exc:
        logger.error(
            "Unexpected error reading settings file %s: %s",
            path,
            exc,
            exc_info=True,
        )
        return {}


def _resolve_workspace_hint(workspace: str, legacy_settings: dict[str, Any]) -> str:
    workspace_hint = str(workspace or "").strip()
    if workspace_hint:
        return os.path.abspath(workspace_hint)
    legacy_workspace = str(legacy_settings.get("workspace") or "").strip()
    if legacy_workspace:
        return os.path.abspath(legacy_workspace)
    return ""


def _write_json_dict(path: str, payload: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _write_text_atomic(path: str, content: str) -> None:
    """Delegate to KernelOne atomic write for consistency and durability."""
    write_text_atomic(path, content, encoding="utf-8")


def _normalize_abs_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(raw)))


def _runtime_cache_env_value(settings: "Settings") -> str:
    runtime_settings = getattr(settings, "runtime", None)
    explicit_cache_root = _normalize_abs_path(getattr(runtime_settings, "cache_root", ""))
    if explicit_cache_root:
        return explicit_cache_root
    try:
        from polaris.bootstrap.config import default_system_cache_base

        return _normalize_abs_path(default_system_cache_base())
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.debug("Failed to resolve default runtime cache root: %s", exc)
        return ""


def sync_process_settings_environment(settings: "Settings") -> None:
    """Synchronize process environment with the active settings snapshot.

    This keeps env-driven code paths aligned with the in-memory settings object,
    especially for workspace and ramdisk resolution during a long-lived backend
    process.
    """
    workspace_root = _normalize_abs_path(getattr(settings, "workspace", ""))
    if workspace_root:
        os.environ["KERNELONE_WORKSPACE"] = workspace_root
    else:
        os.environ.pop("KERNELONE_WORKSPACE", None)

    if bool(getattr(settings, "self_upgrade_mode", False)):
        os.environ[SELF_UPGRADE_MODE_ENV] = "1"
    else:
        os.environ.pop(SELF_UPGRADE_MODE_ENV, None)

    runtime_settings = getattr(settings, "runtime", None)
    runtime_root = _normalize_abs_path(getattr(runtime_settings, "root", ""))
    if runtime_root:
        os.environ["KERNELONE_RUNTIME_ROOT"] = runtime_root
    else:
        os.environ.pop("KERNELONE_RUNTIME_ROOT", None)

    runtime_cache_root = _runtime_cache_env_value(settings)
    if runtime_cache_root:
        os.environ["KERNELONE_RUNTIME_CACHE_ROOT"] = runtime_cache_root
    else:
        os.environ.pop("KERNELONE_RUNTIME_CACHE_ROOT", None)

    try:
        from polaris.kernelone.storage.layout import clear_storage_roots_cache

        clear_storage_roots_cache()
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.debug("Failed to clear storage roots cache after settings sync: %s", exc)

    ramdisk_root = _normalize_abs_path(getattr(settings, "ramdisk_root", ""))
    if ramdisk_root:
        os.environ["KERNELONE_RAMDISK_ROOT"] = ramdisk_root
    else:
        os.environ.pop("KERNELONE_RAMDISK_ROOT", None)

    nats_settings = getattr(settings, "nats", None)
    if nats_settings is not None:
        os.environ["KERNELONE_NATS_ENABLED"] = "1" if bool(getattr(nats_settings, "enabled", True)) else "0"
        os.environ["KERNELONE_NATS_REQUIRED"] = "1" if bool(getattr(nats_settings, "required", True)) else "0"
        nats_url = str(getattr(nats_settings, "url", "") or "").strip()
        if nats_url:
            os.environ["KERNELONE_NATS_URL"] = nats_url
        else:
            os.environ.pop("KERNELONE_NATS_URL", None)
        nats_user = str(getattr(nats_settings, "user", "") or "").strip()
        if nats_user:
            os.environ["KERNELONE_NATS_USER"] = nats_user
        else:
            os.environ.pop("KERNELONE_NATS_USER", None)
        nats_password = str(getattr(nats_settings, "password", "") or "").strip()
        if nats_password:
            os.environ["KERNELONE_NATS_PASSWORD"] = nats_password
        else:
            os.environ.pop("KERNELONE_NATS_PASSWORD", None)
        os.environ["KERNELONE_NATS_CONNECT_TIMEOUT"] = str(
            float(getattr(nats_settings, "connect_timeout_sec", 3.0) or 3.0)
        )
        os.environ["KERNELONE_NATS_RECONNECT_WAIT"] = str(
            float(getattr(nats_settings, "reconnect_wait_sec", 1.0) or 1.0)
        )
        os.environ["KERNELONE_NATS_MAX_RECONNECT"] = str(
            int(getattr(nats_settings, "max_reconnect_attempts", -1) or -1)
        )
        nats_stream_name = str(getattr(nats_settings, "stream_name", "") or "").strip()
        if nats_stream_name:
            os.environ["KERNELONE_NATS_STREAM_NAME"] = nats_stream_name
        else:
            os.environ.pop("KERNELONE_NATS_STREAM_NAME", None)

    os.environ["KERNELONE_AUDIT_LLM_ENABLED"] = "1" if bool(getattr(settings, "audit_llm_enabled", True)) else "0"
    audit_role = str(getattr(settings, "audit_llm_role", "qa") or "").strip().lower()
    if audit_role:
        os.environ["KERNELONE_AUDIT_LLM_ROLE"] = audit_role
    else:
        os.environ.pop("KERNELONE_AUDIT_LLM_ROLE", None)
    os.environ["KERNELONE_AUDIT_LLM_TIMEOUT"] = str(int(getattr(settings, "audit_llm_timeout", 180) or 180))
    os.environ["KERNELONE_AUDIT_LLM_PREFER_LOCAL_OLLAMA"] = (
        "1" if bool(getattr(settings, "audit_llm_prefer_local_ollama", True)) else "0"
    )
    os.environ["KERNELONE_AUDIT_LLM_ALLOW_REMOTE_FALLBACK"] = (
        "1" if bool(getattr(settings, "audit_llm_allow_remote_fallback", True)) else "0"
    )


def _normalize_persisted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    normalized = dict(payload)
    raw_json_log_path = str(normalized.get("json_log_path") or "").strip()
    if raw_json_log_path:
        normalized["json_log_path"] = normalize_artifact_rel_path(raw_json_log_path) or DEFAULT_PM_LOG
    return normalized


def load_persisted_settings(workspace: str = "") -> dict[str, Any]:
    global_path = get_settings_path(workspace)
    logger.debug(f"Loading persisted settings from: {global_path}")
    raw_global_settings = _load_json_dict(global_path)
    logger.debug(
        f"Raw global settings loaded: {bool(raw_global_settings)}, workspace={raw_global_settings.get('workspace', 'NOT_SET')}"
    )
    global_settings = _normalize_persisted_payload(raw_global_settings)
    if global_settings and global_settings != raw_global_settings:
        try:
            _write_json_dict(global_path, global_settings)
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to write global settings: {e}")
    if global_settings:
        workspace_hint = _resolve_workspace_hint(workspace, global_settings)
        if workspace_hint and not global_settings.get("workspace"):
            global_settings = {**global_settings, "workspace": workspace_hint}
            try:
                _write_json_dict(global_path, global_settings)
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to update workspace hint: {e}")
        logger.info(f"Loaded persisted settings: workspace={global_settings.get('workspace', 'NOT_SET')}")
        return global_settings

    candidates = []
    workspace_settings = get_workspace_settings_path(workspace)
    if workspace_settings:
        candidates.append(workspace_settings)
    candidates.append(get_legacy_settings_path())

    for path in candidates:
        raw_payload = _load_json_dict(path)
        payload = _normalize_persisted_payload(raw_payload)
        if not payload:
            continue
        workspace_hint = _resolve_workspace_hint(workspace, payload)
        if workspace_hint and not payload.get("workspace"):
            payload = {**payload, "workspace": workspace_hint}
        try:
            _write_json_dict(global_path, payload)
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to write settings: {e}")
        return payload

    return {}


def save_persisted_settings(settings: "Settings") -> None:
    sync_process_settings_environment(settings)
    workspace = str(getattr(settings, "workspace", "") or "").strip()
    workspace_root = os.path.abspath(workspace) if workspace else ""
    payload = settings.to_payload()
    payload.pop("docs_init_api_key", None)
    payload.pop("architect_spec_api_key", None)
    if workspace_root:
        payload["workspace"] = workspace_root
    settings_path = get_settings_path()
    try:
        _write_json_dict(settings_path, payload)
        logger.info(f"Saved persisted settings: workspace={workspace_root}, path={settings_path}")
    except (RuntimeError, ValueError) as e:
        logger.warning(f"Failed to save settings: {e}")
    legacy_payload = {"workspace": workspace_root} if workspace_root else {}
    try:
        _write_json_dict(get_legacy_settings_path(), legacy_payload)
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to save legacy settings: {e}")

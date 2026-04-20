from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

_BUILTIN_PROFILE_REGISTRY: dict[str, dict[str, Any]] = {
    "python": {
        "extensions": [".py"],
        "verify_group": "python",
    },
    "typescript": {
        "extensions": [".ts", ".tsx", ".js", ".jsx"],
        "verify_group": "node",
    },
}


def _normalize_profile_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_extension(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    if not token.startswith("."):
        token = "." + token
    return token


def _normalize_verify_group(value: Any) -> str:
    return str(value or "").strip().lower()


def resolve_language_profile_registry(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {
        name: {"extensions": list(profile.get("extensions", [])), "verify_group": str(profile.get("verify_group", ""))}
        for name, profile in _BUILTIN_PROFILE_REGISTRY.items()
    }
    custom_registry = config.get("language_profile_registry", {})
    if not isinstance(custom_registry, dict):
        return registry
    for raw_name, raw_profile in custom_registry.items():
        name = _normalize_profile_name(raw_name)
        if not name:
            continue
        profile = raw_profile if isinstance(raw_profile, dict) else {}
        raw_extensions = profile.get("extensions", [])
        extensions: list[str] = []
        if isinstance(raw_extensions, list):
            for item in raw_extensions:
                normalized = _normalize_extension(item)
                if normalized and normalized not in extensions:
                    extensions.append(normalized)
        verify_group = _normalize_verify_group(profile.get("verify_group", ""))
        if not extensions:
            continue
        registry[name] = {
            "extensions": extensions,
            "verify_group": verify_group,
        }
    return registry


def resolve_selected_language_profiles(config: dict[str, Any]) -> list[str]:
    registry = resolve_language_profile_registry(config)
    raw_profiles = config.get("language_profiles", list(registry.keys()))
    selected: list[str] = []
    if isinstance(raw_profiles, list):
        for raw_name in raw_profiles:
            name = _normalize_profile_name(raw_name)
            if not name or name in selected:
                continue
            if name in registry:
                selected.append(name)
    if selected:
        return selected
    fallback = [name for name in ("python", "typescript") if name in registry]
    if fallback:
        return fallback
    return list(registry.keys())


def resolve_extension_language_map(config: dict[str, Any]) -> dict[str, str]:
    registry = resolve_language_profile_registry(config)
    selected = resolve_selected_language_profiles(config)
    mapping: dict[str, str] = {}
    for profile_name in selected:
        profile = registry.get(profile_name, {})
        extensions = profile.get("extensions", [])
        if not isinstance(extensions, list):
            continue
        for raw_ext in extensions:
            ext = _normalize_extension(raw_ext)
            if not ext:
                continue
            mapping[ext] = profile_name
    return mapping


def resolve_extension_verify_group_map(config: dict[str, Any]) -> dict[str, str]:
    registry = resolve_language_profile_registry(config)
    selected = resolve_selected_language_profiles(config)
    mapping: dict[str, str] = {}
    for profile_name in selected:
        profile = registry.get(profile_name, {})
        verify_group = _normalize_verify_group(profile.get("verify_group", ""))
        if not verify_group:
            continue
        extensions = profile.get("extensions", [])
        if not isinstance(extensions, list):
            continue
        for raw_ext in extensions:
            ext = _normalize_extension(raw_ext)
            if not ext:
                continue
            mapping[ext] = verify_group
    return mapping


def resolve_enabled_verify_groups(config: dict[str, Any]) -> list[str]:
    registry = resolve_language_profile_registry(config)
    selected = resolve_selected_language_profiles(config)
    groups: list[str] = []
    for profile_name in selected:
        profile = registry.get(profile_name, {})
        verify_group = _normalize_verify_group(profile.get("verify_group", ""))
        if not verify_group or verify_group in groups:
            continue
        groups.append(verify_group)
    return groups


def detect_language_for_path(path: Path, extension_map: dict[str, str]) -> str:
    return str(extension_map.get(path.suffix.lower(), "")).strip()

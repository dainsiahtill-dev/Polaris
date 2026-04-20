from __future__ import annotations

from polaris.cells.roles.kernel.internal.speculation.fingerprints import (
    build_env_fingerprint,
    build_spec_key,
    normalize_args,
)


def test_normalize_args_sorts_keys() -> None:
    args = {"b": 2, "a": 1}
    normalized = normalize_args("test_tool", args)
    assert list(normalized.keys()) == ["a", "b"]


def test_normalize_args_normalizes_strings() -> None:
    args = {"text": "  hello\r\nworld  "}
    normalized = normalize_args("test_tool", args)
    assert normalized["text"] == "hello\nworld"


def test_normalize_args_nested_dicts() -> None:
    args = {"outer": {"z": 1, "a": 2}}
    normalized = normalize_args("test_tool", args)
    assert list(normalized["outer"].keys()) == ["a", "z"]


def test_normalize_args_nested_lists() -> None:
    args = {"items": [{"b": 2, "a": 1}]}
    normalized = normalize_args("test_tool", args)
    assert list(normalized["items"][0].keys()) == ["a", "b"]


def test_normalize_args_non_dict_returns_empty() -> None:
    assert normalize_args("test_tool", "not_a_dict") == {}  # type: ignore[arg-type]


def test_build_spec_key_deterministic() -> None:
    key1 = build_spec_key("read_file", {"path": "a.py"}, corpus_version="v1", auth_scope="user", env_fingerprint="fp")
    key2 = build_spec_key("read_file", {"path": "a.py"}, corpus_version="v1", auth_scope="user", env_fingerprint="fp")
    assert key1 == key2
    assert len(key1) == 64  # sha256 hex


def test_build_spec_key_sensitive_to_tool_name() -> None:
    key1 = build_spec_key("read_file", {"path": "a.py"})
    key2 = build_spec_key("write_file", {"path": "a.py"})
    assert key1 != key2


def test_build_spec_key_sensitive_to_env_fingerprint() -> None:
    key1 = build_spec_key("read_file", {"path": "a.py"}, env_fingerprint="fp1")
    key2 = build_spec_key("read_file", {"path": "a.py"}, env_fingerprint="fp2")
    assert key1 != key2


def test_build_env_fingerprint_returns_prefix() -> None:
    fp = build_env_fingerprint()
    assert fp.startswith("git:") or fp.startswith("mtime:") or fp == "env:unknown"

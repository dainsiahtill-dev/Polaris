"""Tests for polaris.domain.models.config_snapshot."""

from __future__ import annotations

import pytest
from polaris.domain.models.config_snapshot import (
    ConfigSnapshot,
    ConfigSnapshotImmutableError,
    ConfigValidationResult,
    FrozenInstanceError,
    SourceType,
)


class TestSourceType:
    def test_values(self) -> None:
        assert SourceType.DEFAULT == 0
        assert SourceType.PERSISTED == 1
        assert SourceType.ENV == 2
        assert SourceType.CLI == 3

    def test_str(self) -> None:
        assert str(SourceType.DEFAULT) == "default"
        assert str(SourceType.CLI) == "cli"


class TestConfigValidationResult:
    def test_defaults(self) -> None:
        result = ConfigValidationResult()
        assert result.is_valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_add_error(self) -> None:
        result = ConfigValidationResult().add_error("bad port")
        assert result.is_valid is False
        assert "bad port" in result.errors

    def test_add_warning(self) -> None:
        result = ConfigValidationResult().add_warning("odd level")
        assert result.is_valid is True
        assert "odd level" in result.warnings

    def test_merge(self) -> None:
        r1 = ConfigValidationResult(is_valid=True, errors=["e1"], warnings=["w1"])
        r2 = ConfigValidationResult(is_valid=False, errors=["e2"], warnings=["w2"])
        merged = r1.merge(r2)
        assert merged.is_valid is False
        assert merged.errors == ["e1", "e2"]
        assert merged.warnings == ["w1", "w2"]


class TestConfigSnapshot:
    def test_empty(self) -> None:
        snapshot = ConfigSnapshot.empty()
        assert snapshot.get("any") is None
        assert snapshot.has("any") is False

    def test_get_simple(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"key": "value"})
        assert snapshot.get("key") == "value"

    def test_get_nested(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"server": {"port": 8080}})
        assert snapshot.get("server.port") == 8080

    def test_get_missing(self) -> None:
        snapshot = ConfigSnapshot.empty()
        assert snapshot.get("missing", "default") == "default"

    def test_has(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"a": {"b": 1}})
        assert snapshot.has("a.b") is True
        assert snapshot.has("a.c") is False

    def test_get_typed(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"port": "8080"})
        assert snapshot.get_typed("port", int) == 8080

    def test_get_typed_bool_from_string(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"flag": "true"})
        assert snapshot.get_typed("flag", bool) is True
        snapshot2 = ConfigSnapshot.merge_sources(default={"flag": "false"})
        assert snapshot2.get_typed("flag", bool) is False

    def test_get_typed_invalid_returns_default(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"port": "abc"})
        assert snapshot.get_typed("port", int, 80) == 80

    def test_get_section(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"server": {"host": "127.0.0.1"}})
        section = snapshot.get_section("server")
        assert section["host"] == "127.0.0.1"

    def test_get_section_missing(self) -> None:
        snapshot = ConfigSnapshot.empty()
        with pytest.raises(KeyError):
            snapshot.get_section("missing")

    def test_get_section_not_dict(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"server": "localhost"})
        with pytest.raises(KeyError):
            snapshot.get_section("server")

    def test_get_source(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(cli={"key": "value"})
        assert snapshot.get_source("key") == SourceType.CLI

    def test_merge_sources_priority(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(
            default={"key": "default"},
            persisted={"key": "persisted"},
            env={"key": "env"},
            cli={"key": "cli"},
        )
        assert snapshot.get("key") == "cli"
        assert snapshot.get_source("key") == SourceType.CLI

    def test_with_override(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"key": "default"})
        new_snapshot = snapshot.with_override({"key": "override"}, SourceType.ENV)
        assert new_snapshot.get("key") == "override"
        assert new_snapshot.get_source("key") == SourceType.ENV
        # Original unchanged
        assert snapshot.get("key") == "default"

    def test_with_defaults(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"a": "1"})
        new_snapshot = snapshot.with_defaults({"a": "2", "b": "3"})
        assert new_snapshot.get("a") == "1"
        assert new_snapshot.get("b") == "3"

    def test_from_flat_dict(self) -> None:
        snapshot = ConfigSnapshot.from_flat_dict({"server.port": 8080, "server.host": "127.0.0.1"})
        assert snapshot.get("server.port") == 8080
        assert snapshot.get("server.host") == "127.0.0.1"

    def test_to_mutable_dict(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"a": {"b": 1}})
        d = snapshot.to_mutable_dict()
        assert d["a"]["b"] == 1
        # Mutating doesn't affect snapshot
        d["a"]["b"] = 2
        assert snapshot.get("a.b") == 1

    def test_to_json(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"key": "value"})
        json_str = snapshot.to_json()
        assert '"key": "value"' in json_str

    def test_validate_port(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"server": {"port": 99999}})
        result = snapshot.validate()
        assert result.is_valid is False
        assert any("port" in e for e in result.errors)

    def test_validate_port_invalid_type(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"server": {"port": "abc"}})
        result = snapshot.validate()
        assert result.is_valid is False

    def test_validate_log_level_warning(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"logging": {"level": "VERBOSE"}})
        result = snapshot.validate()
        assert result.is_valid is True
        assert any("log level" in w for w in result.warnings)

    def test_diff(self) -> None:
        s1 = ConfigSnapshot.merge_sources(default={"a": "1", "b": "2"})
        s2 = ConfigSnapshot.merge_sources(default={"a": "1", "b": "3"})
        diff = s1.diff(s2)
        assert "b" in diff
        assert diff["b"] == ("2", "3")

    def test_eq_and_hash(self) -> None:
        s1 = ConfigSnapshot.merge_sources(default={"a": "1"})
        s2 = ConfigSnapshot.merge_sources(default={"a": "1"})
        assert s1 == s2
        assert hash(s1) == hash(s2)

    def test_eq_different(self) -> None:
        s1 = ConfigSnapshot.merge_sources(default={"a": "1"})
        s2 = ConfigSnapshot.merge_sources(default={"a": "2"})
        assert s1 != s2

    def test_eq_non_snapshot(self) -> None:
        s1 = ConfigSnapshot.empty()
        assert s1 != "not a snapshot"

    def test_repr(self) -> None:
        snapshot = ConfigSnapshot.merge_sources(default={"a": "1"})
        r = repr(snapshot)
        assert "ConfigSnapshot" in r
        assert "keys=1" in r


class TestFrozenInstanceError:
    def test_alias(self) -> None:
        assert FrozenInstanceError is ConfigSnapshotImmutableError

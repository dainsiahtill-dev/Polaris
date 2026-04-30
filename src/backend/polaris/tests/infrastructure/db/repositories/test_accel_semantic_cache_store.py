"""Tests for polaris.infrastructure.db.repositories.accel_semantic_cache_store module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from polaris.infrastructure.db.repositories.accel_semantic_cache_store import (
    SemanticCacheStore,
    _normalize_path_token,
    _parse_utc,
    _utc_text,
    _validate_sql_identifier,
    context_changed_fingerprint,
    jaccard_similarity,
    make_stable_hash,
    normalize_changed_files,
    normalize_token_list,
    task_signature,
)


# =============================================================================
# Helpers
# =============================================================================

@pytest.fixture
def cache_store(tmp_path: Path) -> SemanticCacheStore:
    db_path = tmp_path / "cache.db"
    return SemanticCacheStore(db_path)


# =============================================================================
# _validate_sql_identifier
# =============================================================================

def test_validate_sql_identifier_valid() -> None:
    _validate_sql_identifier("context_cache", "table")
    _validate_sql_identifier("cache_key", "column")


def test_validate_sql_identifier_empty() -> None:
    with pytest.raises(ValueError, match="Invalid"):
        _validate_sql_identifier("", "table")


def test_validate_sql_identifier_invalid_chars() -> None:
    with pytest.raises(ValueError, match="invalid characters"):
        _validate_sql_identifier("table;drop", "table")


# =============================================================================
# _utc_text / _parse_utc
# =============================================================================

def test_utc_text_roundtrip() -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    text = _utc_text(now)
    parsed = _parse_utc(text)
    assert parsed is not None
    assert parsed.year == now.year
    assert parsed.month == now.month


def test_parse_utc_empty() -> None:
    assert _parse_utc("") is None
    assert _parse_utc(None) is None


def test_parse_utc_z_suffix() -> None:
    parsed = _parse_utc("2024-01-01T00:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_utc_naive() -> None:
    parsed = _parse_utc("2024-01-01T00:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_parse_utc_invalid() -> None:
    assert _parse_utc("not-a-date") is None


# =============================================================================
# normalize_token_list
# =============================================================================

def test_normalize_token_list_basic() -> None:
    assert normalize_token_list(["A", "b", " C "]) == ["a", "b", "c"]


def test_normalize_token_list_dedup() -> None:
    assert normalize_token_list(["a", "a", "b"]) == ["a", "b"]


def test_normalize_token_list_none() -> None:
    assert normalize_token_list(None) == []


def test_normalize_token_list_empty_items() -> None:
    assert normalize_token_list(["a", "", "b"]) == ["a", "b"]


# =============================================================================
# normalize_changed_files
# =============================================================================

def test_normalize_changed_files_paths() -> None:
    result = normalize_changed_files(["Src/App.PY", "lib/Foo.py"])
    assert result == ["src/app.py", "lib/foo.py"]


def test_normalize_changed_files_backslash() -> None:
    result = normalize_changed_files(["src\\app.py"])
    assert result == ["src/app.py"]


# =============================================================================
# make_stable_hash / context_changed_fingerprint / task_signature
# =============================================================================

def test_make_stable_hash_deterministic() -> None:
    payload: dict[str, Any] = {"a": 1, "b": [2, 3]}
    h1 = make_stable_hash(payload)
    h2 = make_stable_hash(payload)
    assert h1 == h2
    assert len(h1) == 64


def test_context_changed_fingerprint() -> None:
    fp = context_changed_fingerprint(["src/app.py"])
    assert len(fp) == 64


def test_task_signature() -> None:
    sig = task_signature(["fix", "bug"], ["hint"])
    assert len(sig) == 64


# =============================================================================
# jaccard_similarity
# =============================================================================

def test_jaccard_both_empty() -> None:
    assert jaccard_similarity(set(), set()) == 1.0


def test_jaccard_one_empty() -> None:
    assert jaccard_similarity({"a"}, set()) == 0.0


def test_jaccard_identical() -> None:
    assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_partial() -> None:
    assert jaccard_similarity({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


# =============================================================================
# SemanticCacheStore.init + schema
# =============================================================================

def test_init_creates_db(cache_store: SemanticCacheStore) -> None:
    assert cache_store._db_path.exists()


# =============================================================================
# put_context + get_context_exact
# =============================================================================

def test_put_and_get_context_exact(cache_store: SemanticCacheStore) -> None:
    cache_store.put_context(
        cache_key="ck1",
        task_signature_value="ts1",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello"},
        ttl_seconds=3600,
        max_entries=100,
    )
    result = cache_store.get_context_exact("ck1")
    assert result == {"data": "hello"}


def test_get_context_exact_miss(cache_store: SemanticCacheStore) -> None:
    assert cache_store.get_context_exact("missing") is None


def test_get_context_exact_expired(cache_store: SemanticCacheStore) -> None:
    cache_store.put_context(
        cache_key="ck1",
        task_signature_value="ts1",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello"},
        ttl_seconds=1,
        max_entries=100,
    )
    import time
    time.sleep(1.1)
    assert cache_store.get_context_exact("ck1") is None


# =============================================================================
# put_context upsert
# =============================================================================

def test_put_context_upserts(cache_store: SemanticCacheStore) -> None:
    for i in range(2):
        cache_store.put_context(
            cache_key="ck1",
            task_signature_value="ts1",
            task_tokens=["fix"],
            hint_tokens=["bug"],
            changed_files=["src/a.py"],
            changed_fingerprint="cf1",
            budget_fingerprint="bf1",
            config_hash="ch1",
            payload={"data": f"v{i}"},
            ttl_seconds=3600,
            max_entries=100,
        )
    result = cache_store.get_context_exact("ck1")
    assert result == {"data": "v1"}


# =============================================================================
# get_context_hybrid
# =============================================================================

def test_get_context_hybrid_match(cache_store: SemanticCacheStore) -> None:
    cache_store.put_context(
        cache_key="ck1",
        task_signature_value="ts1",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello"},
        ttl_seconds=3600,
        max_entries=100,
    )
    payload, score = cache_store.get_context_hybrid(
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        budget_fingerprint="bf1",
        config_hash="ch1",
        threshold=0.5,
    )
    assert payload is not None
    assert score > 0.5


def test_get_context_hybrid_no_match_below_threshold(cache_store: SemanticCacheStore) -> None:
    cache_store.put_context(
        cache_key="ck1",
        task_signature_value="ts1",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello"},
        ttl_seconds=3600,
        max_entries=100,
    )
    payload, score = cache_store.get_context_hybrid(
        task_tokens=["completely", "different"],
        hint_tokens=["tokens"],
        changed_files=["other.py"],
        budget_fingerprint="bf1",
        config_hash="ch1",
        threshold=0.99,
    )
    assert payload is None
    assert score < 0.99


def test_get_context_hybrid_no_budget_match(cache_store: SemanticCacheStore) -> None:
    cache_store.put_context(
        cache_key="ck1",
        task_signature_value="ts1",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello"},
        ttl_seconds=3600,
        max_entries=100,
    )
    payload, score = cache_store.get_context_hybrid(
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        budget_fingerprint="bf2",
        config_hash="ch1",
        threshold=0.0,
    )
    assert payload is None
    assert score == 0.0


# =============================================================================
# explain_context_miss
# =============================================================================

def test_explain_context_miss_no_prior(cache_store: SemanticCacheStore) -> None:
    result = cache_store.explain_context_miss(
        task_signature_value="ts1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        safety_fingerprint="sf1",
        changed_fingerprint="cf1",
        git_head="gh1",
    )
    assert result["reason"] == "no_prior_entry"


def test_explain_context_miss_expired(cache_store: SemanticCacheStore) -> None:
    cache_store.put_context(
        cache_key="ck1",
        task_signature_value="ts1",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello"},
        ttl_seconds=1,
        max_entries=100,
    )
    import time
    time.sleep(1.1)
    result = cache_store.explain_context_miss(
        task_signature_value="ts1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        safety_fingerprint="sf1",
        changed_fingerprint="cf1",
        git_head="gh1",
    )
    assert result["reason"] == "expired"


def test_explain_context_miss_changed_files_set(cache_store: SemanticCacheStore) -> None:
    cache_store.put_context(
        cache_key="ck1",
        task_signature_value="ts1",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello"},
        ttl_seconds=3600,
        max_entries=100,
    )
    result = cache_store.explain_context_miss(
        task_signature_value="ts1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        safety_fingerprint="",
        changed_fingerprint="cf2",
        git_head="gh1",
    )
    assert result["reason"] == "changed_files_set_changed"


# =============================================================================
# verify_plan cache
# =============================================================================

def test_put_and_get_verify_plan(cache_store: SemanticCacheStore) -> None:
    cache_store.put_verify_plan(
        cache_key="vp1",
        changed_fingerprint="cf1",
        runtime_fingerprint="rf1",
        config_hash="ch1",
        commands=["cmd1", "cmd2"],
        ttl_seconds=3600,
        max_entries=100,
    )
    result = cache_store.get_verify_plan("vp1")
    assert result == ["cmd1", "cmd2"]


def test_get_verify_plan_expired(cache_store: SemanticCacheStore) -> None:
    cache_store.put_verify_plan(
        cache_key="vp1",
        changed_fingerprint="cf1",
        runtime_fingerprint="rf1",
        config_hash="ch1",
        commands=["cmd1"],
        ttl_seconds=1,
        max_entries=100,
    )
    import time
    time.sleep(1.1)
    assert cache_store.get_verify_plan("vp1") is None


def test_put_verify_plan_upserts(cache_store: SemanticCacheStore) -> None:
    cache_store.put_verify_plan(
        cache_key="vp1",
        changed_fingerprint="cf1",
        runtime_fingerprint="rf1",
        config_hash="ch1",
        commands=["cmd1"],
        ttl_seconds=3600,
        max_entries=100,
    )
    cache_store.put_verify_plan(
        cache_key="vp1",
        changed_fingerprint="cf2",
        runtime_fingerprint="rf2",
        config_hash="ch1",
        commands=["cmd2"],
        ttl_seconds=3600,
        max_entries=100,
    )
    assert cache_store.get_verify_plan("vp1") == ["cmd2"]


# =============================================================================
# prune_table
# =============================================================================

def test_prune_table_removes_expired(cache_store: SemanticCacheStore) -> None:
    cache_store.put_context(
        cache_key="ck1",
        task_signature_value="ts1",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello"},
        ttl_seconds=1,
        max_entries=100,
    )
    import time
    time.sleep(1.1)
    cache_store.put_context(
        cache_key="ck2",
        task_signature_value="ts2",
        task_tokens=["fix"],
        hint_tokens=["bug"],
        changed_files=["src/a.py"],
        changed_fingerprint="cf1",
        budget_fingerprint="bf1",
        config_hash="ch1",
        payload={"data": "hello2"},
        ttl_seconds=3600,
        max_entries=100,
    )
    assert cache_store.get_context_exact("ck1") is None
    assert cache_store.get_context_exact("ck2") is not None


def test_prune_table_max_entries_overflow(cache_store: SemanticCacheStore) -> None:
    for i in range(5):
        cache_store.put_context(
            cache_key=f"ck{i}",
            task_signature_value=f"ts{i}",
            task_tokens=["fix"],
            hint_tokens=["bug"],
            changed_files=["src/a.py"],
            changed_fingerprint="cf1",
            budget_fingerprint="bf1",
            config_hash="ch1",
            payload={"data": f"v{i}"},
            ttl_seconds=3600,
            max_entries=2,
        )
    assert cache_store.get_context_exact("ck0") is None
    assert cache_store.get_context_exact("ck4") is not None

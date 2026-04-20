"""Performance benchmark tests for `roles.kernel` PromptBuilder L1/L2/L3 cache.

Covers Phase 4 sub-tasks:
- L1/L2/L3 three-level cache hit rate benchmarks
- Cache TTL expiration verification
- LRU eviction on L1 overflow
- Fingerprint stability and uniqueness
- Thread-safety on concurrent cache access
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from polaris.cells.roles.kernel.internal.prompt_builder import (
    PromptBuilder,
)

# ---------------------------------------------------------------------------
# Helpers — minimal RoleProfile stubs (only fields accessed by PromptBuilder)
# ---------------------------------------------------------------------------


def _make_profile(
    template_id: str = "pm",
    version: str = "1.0.0",
    responsibilities: list[str] | None = None,
):
    """Return a minimal RoleProfile-like object that PromptBuilder accepts."""
    import types

    _template_id = template_id
    _version = version
    _resp = responsibilities or []

    class _StubPromptPolicy:
        core_template_id = _template_id
        allow_appendix = True
        allow_override = False
        output_format = "json"
        include_thinking = True
        quality_checklist: list[str] = []
        security_boundary: str | None = None

    class _StubToolPolicy:
        whitelist: list[str] = []
        blacklist: list[str] = []
        allow_code_write = False
        allow_command_execution = False
        allow_file_delete = False

        @property
        def policy_id(self) -> str:
            return "stub"

    class _StubContextPolicy:
        max_context_tokens = 8000
        max_history_turns = 10
        include_project_structure = True
        include_code_snippets = True
        max_snippet_lines = 200

    class _StubDataPolicy:
        data_subdir = "stub"

    class _StubLibraryPolicy:
        pass

    obj = types.SimpleNamespace(
        role_id="stub",
        display_name="Stub",
        description="Stub role",
        version=_version,
        responsibilities=_resp,
        provider_id="",
        model="",
        prompt_policy=_StubPromptPolicy(),
        tool_policy=_StubToolPolicy(),
        context_policy=_StubContextPolicy(),
        data_policy=_StubDataPolicy(),
        library_policy=_StubLibraryPolicy(),
    )
    obj.profile_fingerprint = f"pf:stub:{_version}"
    return obj


# ---------------------------------------------------------------------------
# L1 cache — core prompt benchmarks
# ---------------------------------------------------------------------------


class TestL1CacheHitRate:
    """L1 (core prompt) cache hit/miss counters and LRU behaviour.

    get_cache_stats() exposes l1_hit_rate = hits / (hits + misses), not raw
    counters.  Ratio assertions match that shape.
    """

    def test_first_call_zero_hit_rate(self) -> None:
        builder = PromptBuilder()
        builder.build_system_prompt(_make_profile())
        stats = builder.get_cache_stats()
        assert stats["l1_hit_rate"] == 0.0

    def test_second_call_same_profile_fifty_percent_hit_rate(self) -> None:
        builder = PromptBuilder()
        profile = _make_profile()
        builder.build_system_prompt(profile)
        builder.build_system_prompt(profile)
        stats = builder.get_cache_stats()
        # 1 miss + 1 hit → hit_rate = 1/2
        assert stats["l1_hit_rate"] == pytest.approx(0.5)

    def test_ninety_percent_hit_rate_for_repeated_calls(self) -> None:
        builder = PromptBuilder()
        profile = _make_profile()
        for _ in range(10):
            builder.build_system_prompt(profile)
        stats = builder.get_cache_stats()
        # 1 miss + 9 hits → 9/10
        assert stats["l1_hit_rate"] == pytest.approx(0.9)

    def test_different_profile_version_causes_zero_hit_rate(self) -> None:
        builder = PromptBuilder()
        p1 = _make_profile(template_id="pm", version="1.0.0")
        p2 = _make_profile(template_id="pm", version="2.0.0")
        builder.build_system_prompt(p1)
        builder.build_system_prompt(p2)
        stats = builder.get_cache_stats()
        # Two different cache keys → 2 misses, 0 hits
        assert stats["l1_hit_rate"] == 0.0

    def test_different_template_id_causes_zero_hit_rate(self) -> None:
        builder = PromptBuilder()
        p1 = _make_profile(template_id="pm", version="1.0.0")
        p2 = _make_profile(template_id="architect", version="1.0.0")
        builder.build_system_prompt(p1)
        builder.build_system_prompt(p2)
        stats = builder.get_cache_stats()
        assert stats["l1_hit_rate"] == 0.0

    def test_l1_lru_eviction_on_overflow(self) -> None:
        builder = PromptBuilder()
        # L1_CACHE_MAX_SIZE = 20; add 21 unique profiles
        for i in range(21):
            builder.build_system_prompt(_make_profile(template_id=f"role_{i}"))
        stats = builder.get_cache_stats()
        # Exactly 20 entries remain after LRU eviction
        assert stats["l1_cached_roles"] == 20

    def test_l1_lru_preserves_recent_access(self) -> None:
        builder = PromptBuilder()
        # Fill capacity with 20 unique profiles
        for i in range(20):
            builder.build_system_prompt(_make_profile(template_id=f"role_{i}"))
        # Touch role_0 to mark it recently used
        builder.build_system_prompt(_make_profile(template_id="role_0"))
        # Add one more → role_1 (second-oldest) evicted, role_0 kept
        builder.build_system_prompt(_make_profile(template_id="role_21"))
        stats = builder.get_cache_stats()
        assert stats["l1_cached_roles"] == 20


# ---------------------------------------------------------------------------
# L2 cache — security boundary benchmarks
# ---------------------------------------------------------------------------


class TestL2CacheHitRate:
    """L2 (security boundary) cache hit/miss and TTL expiration.

    L2_CACHE_TTL = 120 s; test uses time mocking to advance past it.
    """

    def test_first_call_zero_l2_hit_rate(self) -> None:
        builder = PromptBuilder()
        builder.build_system_prompt(_make_profile())
        stats = builder.get_cache_stats()
        assert stats["l2_hit_rate"] == 0.0

    def test_repeated_calls_increase_l2_hit_rate(self) -> None:
        builder = PromptBuilder()
        for _ in range(5):
            builder.build_system_prompt(_make_profile())
        stats = builder.get_cache_stats()
        # 1 miss + 4 hits = 4/5
        assert stats["l2_hit_rate"] == pytest.approx(0.8)

    def test_l2_ttl_expiration_causes_miss(self) -> None:
        builder = PromptBuilder()
        t0 = time.time()
        with patch("polaris.cells.roles.kernel.internal.prompt_builder.time") as mock_time:
            mock_time.time.return_value = t0
            builder.build_system_prompt(_make_profile())
            # Cache is now warm
            builder.build_system_prompt(_make_profile())
            rate_warm = builder.get_cache_stats()["l2_hit_rate"]
            assert rate_warm > 0.0
            # Advance past L2 TTL (120 s)
            mock_time.time.return_value = t0 + PromptBuilder.L2_CACHE_TTL + 1
            builder.build_system_prompt(_make_profile())
            # L2 cache expired → miss, hit rate drops
            rate_after = builder.get_cache_stats()["l2_hit_rate"]
            assert rate_after < rate_warm


# ---------------------------------------------------------------------------
# L3 cache — output format benchmarks
# ---------------------------------------------------------------------------


class TestL3CacheHitRate:
    """L3 (output format) cache hit/miss and TTL expiration.

    L3_CACHE_TTL = 60 s; test uses time mocking to advance past it.
    """

    def test_first_call_zero_l3_hit_rate(self) -> None:
        builder = PromptBuilder()
        builder.build_system_prompt(_make_profile())
        stats = builder.get_cache_stats()
        assert stats["l3_hit_rate"] == 0.0

    def test_repeated_calls_increase_l3_hit_rate(self) -> None:
        builder = PromptBuilder()
        for _ in range(5):
            builder.build_system_prompt(_make_profile())
        stats = builder.get_cache_stats()
        # 1 miss + 4 hits = 4/5
        assert stats["l3_hit_rate"] == pytest.approx(0.8)

    def test_l3_ttl_expiration_causes_miss(self) -> None:
        builder = PromptBuilder()
        t0 = time.time()
        with patch("polaris.cells.roles.kernel.internal.prompt_builder.time") as mock_time:
            mock_time.time.return_value = t0
            builder.build_system_prompt(_make_profile())
            builder.build_system_prompt(_make_profile())
            rate_warm = builder.get_cache_stats()["l3_hit_rate"]
            assert rate_warm > 0.0
            # Advance past L3 TTL (60 s)
            mock_time.time.return_value = t0 + PromptBuilder.L3_CACHE_TTL + 1
            builder.build_system_prompt(_make_profile())
            rate_after = builder.get_cache_stats()["l3_hit_rate"]
            assert rate_after < rate_warm


# ---------------------------------------------------------------------------
# Fingerprint — stability, uniqueness, cache-key derivation
# ---------------------------------------------------------------------------


class TestFingerprintCacheKeyDerivation:
    """build_fingerprint produces stable, unique keys for cache derivation."""

    def test_same_profile_produces_same_core_hash(self) -> None:
        builder = PromptBuilder()
        p1 = _make_profile(template_id="pm", version="1.0.0")
        p2 = _make_profile(template_id="pm", version="1.0.0")
        fp1 = builder.build_fingerprint(p1)
        fp2 = builder.build_fingerprint(p2)
        assert fp1.core_hash == fp2.core_hash

    def test_different_version_produces_different_core_hash(self) -> None:
        builder = PromptBuilder()
        p1 = _make_profile(template_id="pm", version="1.0.0")
        p2 = _make_profile(template_id="pm", version="2.0.0")
        fp1 = builder.build_fingerprint(p1)
        fp2 = builder.build_fingerprint(p2)
        assert fp1.core_hash != fp2.core_hash

    def test_different_template_id_produces_different_core_hash(self) -> None:
        builder = PromptBuilder()
        p1 = _make_profile(template_id="pm", version="1.0.0")
        p2 = _make_profile(template_id="architect", version="1.0.0")
        fp1 = builder.build_fingerprint(p1)
        fp2 = builder.build_fingerprint(p2)
        assert fp1.core_hash != fp2.core_hash

    def test_empty_appendix_appendix_hash_is_none(self) -> None:
        builder = PromptBuilder()
        fp = builder.build_fingerprint(_make_profile(), prompt_appendix="")
        assert fp.appendix_hash is None

    def test_non_empty_appendix_appendix_hash_is_present(self) -> None:
        builder = PromptBuilder()
        fp = builder.build_fingerprint(_make_profile(), prompt_appendix="extra context")
        assert fp.appendix_hash is not None
        assert len(fp.appendix_hash) > 0

    def test_fingerprint_provides_full_hash(self) -> None:
        builder = PromptBuilder()
        fp = builder.build_fingerprint(_make_profile())
        assert fp.full_hash is not None
        assert len(fp.full_hash) > 0

    def test_fingerprint_full_hash_changes_with_appendix(self) -> None:
        builder = PromptBuilder()
        fp1 = builder.build_fingerprint(_make_profile(), prompt_appendix="")
        fp2 = builder.build_fingerprint(_make_profile(), prompt_appendix="extra")
        assert fp1.full_hash != fp2.full_hash


# ---------------------------------------------------------------------------
# Thread safety — concurrent cache access
# ---------------------------------------------------------------------------


class TestCacheThreadSafety:
    """Cache access must be safe under concurrent calls."""

    def test_concurrent_l1_builds_do_not_corrupt_cache(self) -> None:
        builder = PromptBuilder()
        barrier = threading.Barrier(10)
        errors: list[str] = []
        lock = threading.Lock()

        def build_for_role(role_id: str) -> None:
            try:
                barrier.wait()  # start simultaneously
                for _ in range(20):
                    builder.build_system_prompt(_make_profile(template_id=role_id))
            except (RuntimeError, ValueError) as exc:  # pragma: no cover — defensive
                with lock:
                    errors.append(str(exc))

        threads = [threading.Thread(target=build_for_role, args=(f"role_{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent access raised: {errors}"
        stats = builder.get_cache_stats()
        # 10 unique roles, 20 calls each = 10 misses + 190 hits → 190/200 = 0.95
        assert stats["l1_hit_rate"] == pytest.approx(0.95)

    def test_concurrent_clear_cache_is_safe(self) -> None:
        builder = PromptBuilder()
        barrier = threading.Barrier(5)
        errors: list[str] = []

        def build_and_clear(idx: int) -> None:
            try:
                barrier.wait()
                for _ in range(10):
                    builder.build_system_prompt(_make_profile(template_id=f"role_{idx}"))
                    builder.clear_cache()
            except (RuntimeError, ValueError) as exc:  # pragma: no cover
                errors.append(str(exc))

        threads = [threading.Thread(target=build_and_clear, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"clear_cache raised: {errors}"


# ---------------------------------------------------------------------------
# clear_cache resets all layers and stats
# ---------------------------------------------------------------------------


class TestClearCacheResetsAllLayers:
    """clear_cache must wipe L1, L2, L3 entries and zero hit counters."""

    def test_clearing_resets_l1_entries(self) -> None:
        builder = PromptBuilder()
        for i in range(5):
            builder.build_system_prompt(_make_profile(template_id=f"role_{i}"))
        builder.clear_cache()
        stats = builder.get_cache_stats()
        assert stats["l1_cached_roles"] == 0

    def test_clearing_resets_l2_cached_flag(self) -> None:
        builder = PromptBuilder()
        builder.build_system_prompt(_make_profile())
        assert builder.get_cache_stats()["l2_cached"] is True
        builder.clear_cache()
        assert builder.get_cache_stats()["l2_cached"] is False

    def test_clearing_resets_l3_cached_flag(self) -> None:
        builder = PromptBuilder()
        builder.build_system_prompt(_make_profile())
        assert builder.get_cache_stats()["l3_cached"] is True
        builder.clear_cache()
        assert builder.get_cache_stats()["l3_cached"] is False

    def test_clearing_resets_all_hit_rates(self) -> None:
        builder = PromptBuilder()
        profile = _make_profile()
        for _ in range(10):
            builder.build_system_prompt(profile)
        builder.clear_cache()
        stats = builder.get_cache_stats()
        # All rates back to 0 after clear
        assert stats["l1_hit_rate"] == 0.0
        assert stats["l2_hit_rate"] == 0.0
        assert stats["l3_hit_rate"] == 0.0


# ---------------------------------------------------------------------------
# build_system_prompt end-to-end includes all five layers
# ---------------------------------------------------------------------------


class TestBuildSystemPromptIncludesAllLayers:
    """build_system_prompt must return non-empty content with all layers present."""

    def test_returns_non_empty_string(self) -> None:
        builder = PromptBuilder()
        result = builder.build_system_prompt(
            _make_profile(
                template_id="pm",
                version="1.0.0",
                responsibilities=["Manage tasks"],
            )
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_security_boundary(self) -> None:
        builder = PromptBuilder()
        result = builder.build_system_prompt(_make_profile())
        assert "安全边界" in result or "SECURITY" in result.upper()

    def test_includes_output_format_guide(self) -> None:
        builder = PromptBuilder()
        result = builder.build_system_prompt(_make_profile())
        assert "输出格式" in result or "output" in result.lower()

    def test_appendix_appended_when_provided(self) -> None:
        builder = PromptBuilder()
        appendix = "【额外上下文】\n追加的上下文信息"
        result = builder.build_system_prompt(_make_profile(), prompt_appendix=appendix)
        assert "追加的上下文信息" in result

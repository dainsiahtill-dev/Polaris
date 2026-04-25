"""Tests for deterministic_judge.py JSON depth limit security fix (S1.1).

This module tests the JSON stack overflow protection implemented via
_safe_json_loads, _count_json_depth, and _ExcessiveNestingError.

Run these tests directly with:
    python tests/evaluation_security/test_deterministic_judge_security.py
"""

from __future__ import annotations

import json

# Add backend path for imports
import os as _os
import sys

_backend_path = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)

from polaris.cells.llm.evaluation.internal.deterministic_judge import (
    _DEFAULT_JSON_MAX_DEPTH,
    _count_json_depth,
    _ExcessiveNestingError,
    _extract_json_dict,
    _safe_json_loads,
)


def test_normal_json_parsing() -> None:
    """Normal JSON parsing should work without any issues."""
    result = _safe_json_loads('{"key": "value"}')
    assert result == {"key": "value"}


def test_shallow_json() -> None:
    """Very shallow JSON should parse successfully."""
    result = _safe_json_loads('{"a": 1}')
    assert result == {"a": 1}


def test_empty_json_object() -> None:
    """Empty JSON object should parse successfully."""
    result = _safe_json_loads("{}")
    assert result == {}


def test_empty_json_array() -> None:
    """Empty JSON array should parse successfully."""
    result = _safe_json_loads("[]")
    assert result == []


def test_deeply_nested_json_rejected_at_default_depth() -> None:
    """JSON exceeding default depth of 100 should be rejected."""
    # Create JSON with depth 101 (exceeds default limit of 100)
    json_str = '{"a": ' * 101 + '"x"' + "}" * 101
    try:
        _safe_json_loads(json_str)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError as exc:
        assert exc.max_depth == _DEFAULT_JSON_MAX_DEPTH


def test_deeply_nested_json_rejected_at_custom_depth() -> None:
    """JSON exceeding custom depth limit should be rejected."""
    json_str = '{"a": {"b": {"c": "value"}}}'
    try:
        _safe_json_loads(json_str, max_depth=2)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError as exc:
        assert exc.max_depth == 2


def test_json_at_exact_depth_limit_succeeds() -> None:
    """JSON at exactly the depth limit should parse successfully."""
    # depth 2: root -> a -> b
    json_str = '{"a": {"b": "value"}}'
    result = _safe_json_loads(json_str, max_depth=2)
    assert result == {"a": {"b": "value"}}


def test_json_one_beyond_depth_limit_fails() -> None:
    """JSON one level beyond depth limit should fail."""
    # depth 3: root -> a -> b -> c
    json_str = '{"a": {"b": {"c": "value"}}}'
    try:
        _safe_json_loads(json_str, max_depth=2)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_deep_array_nesting_rejected() -> None:
    """Deeply nested arrays should also be depth-limited."""
    # Create array nesting exceeding depth 10
    json_str = "[[[[[[[[[[[[]]]]]]]]]]]]]"
    try:
        _safe_json_loads(json_str, max_depth=10)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_mixed_array_object_nesting_rejected() -> None:
    """Mixed array and object nesting exceeding depth should be rejected."""
    # 7 levels of nesting: {"a": [{"b": [{"c": [{"d": [1]}]}]}]}
    # Depth = 6 (root + a + [ + b + [ + c + [)
    json_str = '{"a": [{"b": [{"c": [{"d": [1]}]}]}]}'
    try:
        _safe_json_loads(json_str, max_depth=5)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_invalid_json_raises_json_decode_error() -> None:
    """Invalid JSON should raise standard JSONDecodeError, not nesting error."""
    try:
        _safe_json_loads('{"a": invalid}')
        assert False, "Should have raised JSONDecodeError"
    except json.JSONDecodeError:
        pass  # Expected


def test_invalid_json_syntax_raises_json_decode_error() -> None:
    """JSON syntax errors should raise JSONDecodeError, not nesting error."""
    try:
        _safe_json_loads("{a: 1}")
        assert False, "Should have raised JSONDecodeError"
    except json.JSONDecodeError:
        pass  # Expected


def test_truncated_json_raises_json_decode_error() -> None:
    """Truncated JSON should raise JSONDecodeError."""
    try:
        _safe_json_loads('{"a": 1, "b":')
        assert False, "Should have raised JSONDecodeError"
    except json.JSONDecodeError:
        pass  # Expected


def test_excessive_nesting_error_is_value_error_subclass() -> None:
    """_ExcessiveNestingError should be a ValueError subclass."""
    error = _ExcessiveNestingError(100)
    assert isinstance(error, ValueError)


def test_excessive_nesting_error_message_includes_depth() -> None:
    """_ExcessiveNestingError message should include depth information."""
    error = _ExcessiveNestingError(50)
    assert "50" in str(error)


def test_excessive_nesting_error_max_depth_attribute() -> None:
    """_ExcessiveNestingError should store the max_depth attribute."""
    error = _ExcessiveNestingError(75)
    assert error.max_depth == 75


def test_zero_max_depth_treated_as_one() -> None:
    """max_depth of 0 or less should be treated as 1."""
    # This should fail because depth 1 (root) is OK but depth 2 exceeds
    json_str = '{"a": {"b": "value"}}'
    try:
        _safe_json_loads(json_str, max_depth=0)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_negative_max_depth_treated_as_one() -> None:
    """Negative max_depth should be treated as 1."""
    json_str = '{"a": {"b": "value"}}'
    try:
        _safe_json_loads(json_str, max_depth=-5)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_simple_object() -> None:
    """Simple JSON object should parse correctly."""
    result = _safe_json_loads('{"key": "value"}')
    assert result == {"key": "value"}


def test_simple_array() -> None:
    """Simple JSON array should parse correctly."""
    result = _safe_json_loads("[1, 2, 3]")
    assert result == [1, 2, 3]


def test_nested_object() -> None:
    """Nested JSON object should parse correctly."""
    result = _safe_json_loads('{"a": {"b": {"c": 1}}}')
    assert result == {"a": {"b": {"c": 1}}}


def test_complex_json_structure() -> None:
    """Complex JSON with mixed types should parse correctly."""
    data = {
        "string": "hello",
        "number": 42,
        "float": 3.14,
        "boolean": True,
        "null": None,
        "array": [1, "two", {"three": 3}],
        "object": {"nested": "value"},
    }
    result = _safe_json_loads(json.dumps(data))
    assert result == data


def test_unicode_json() -> None:
    """JSON with Unicode characters should parse correctly."""
    result = _safe_json_loads('{"unicode": "你好世界"}')
    assert result == {"unicode": "你好世界"}


def test_returns_dict_for_object_json() -> None:
    """Object JSON should return a dict."""
    result = _safe_json_loads('{"key": "value"}')
    assert isinstance(result, dict)


def test_returns_list_for_array_json() -> None:
    """Array JSON should return a list."""
    result = _safe_json_loads("[1, 2, 3]")
    assert isinstance(result, list)


def test_extract_from_standalone_json() -> None:
    """Should extract JSON from standalone JSON text."""
    result = _extract_json_dict('{"key": "value"}')
    assert result == {"key": "value"}


def test_extract_from_markdown_code_block() -> None:
    """Should extract JSON from markdown code fences."""
    text = '```json\n{"key": "value"}\n```'
    result = _extract_json_dict(text)
    assert result == {"key": "value"}


def test_extract_from_markdown_code_block_without_json_tag() -> None:
    """Should extract JSON from code fences without json tag."""
    text = '```\n{"key": "value"}\n```'
    result = _extract_json_dict(text)
    assert result == {"key": "value"}


def test_extract_from_text_with_markdown() -> None:
    """Should extract JSON from text containing markdown."""
    text = 'Here is the result:\n```json\n{"status": "ok"}\n```\nDone.'
    result = _extract_json_dict(text)
    assert result == {"status": "ok"}


def test_return_none_for_invalid_json() -> None:
    """Should return None for invalid JSON text."""
    result = _extract_json_dict("not json at all")
    assert result is None


def test_return_none_for_empty_string() -> None:
    """Should return None for empty string."""
    result = _extract_json_dict("")
    assert result is None


def test_return_none_for_whitespace_only() -> None:
    """Should return None for whitespace-only input."""
    result = _extract_json_dict("   \n\t  ")
    assert result is None


def test_return_none_for_none_input() -> None:
    """Should return None for None input."""
    result = _extract_json_dict(None)  # type: ignore
    assert result is None


def test_return_none_for_array_json() -> None:
    """Should return None for JSON arrays (not dicts)."""
    result = _extract_json_dict("[1, 2, 3]")
    assert result is None


def test_re_raises_excessive_nesting_error() -> None:
    """Should re-raise _ExcessiveNestingError (security issue)."""
    # Create text with deeply nested JSON
    json_str = '{"a": ' * 150 + '"x"' + "}" * 150
    try:
        _extract_json_dict(json_str)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_excessive_nesting_in_code_block_rejected() -> None:
    """Deeply nested JSON in code block should be rejected."""
    json_inner = '{"a": ' * 150 + '"x"' + "}" * 150
    text = f"```json\n{json_inner}\n```"
    try:
        _extract_json_dict(text)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_multiple_code_blocks_returns_first_valid() -> None:
    """Should return first valid JSON when multiple code blocks exist."""
    text = """
        First block:
        ```json
        {"first": "valid"}
        ```
        Second block:
        ```json
        {"second": "valid"}
        ```
        """
    result = _extract_json_dict(text)
    assert result == {"first": "valid"}


def test_max_depth_one_allows_only_primitives() -> None:
    """max_depth=1 should allow only root object, not nested objects."""
    # Single level object should work
    result = _safe_json_loads('{"key": "value"}', max_depth=1)
    assert result == {"key": "value"}

    # Nested object should fail
    try:
        _safe_json_loads('{"key": {"nested": "value"}}', max_depth=1)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_max_depth_one_allows_array_of_primitives() -> None:
    """max_depth=1 should allow arrays of primitives."""
    result = _safe_json_loads("[1, 2, 3]", max_depth=1)
    assert result == [1, 2, 3]


def test_max_depth_one_rejects_nested_arrays() -> None:
    """max_depth=1 should reject nested arrays."""
    try:
        _safe_json_loads("[[1, 2]]", max_depth=1)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_max_depth_two_allows_two_levels() -> None:
    """max_depth=2 should allow two levels of nesting."""
    result = _safe_json_loads('{"a": {"b": "value"}}', max_depth=2)
    assert result == {"a": {"b": "value"}}


def test_max_depth_two_rejects_three_levels() -> None:
    """max_depth=2 should reject three levels of nesting."""
    try:
        _safe_json_loads('{"a": {"b": {"c": "value"}}}', max_depth=2)
        assert False, "Should have raised _ExcessiveNestingError"
    except _ExcessiveNestingError:
        pass  # Expected


def test_very_large_max_depth() -> None:
    """Very large max_depth should not cause issues."""
    result = _safe_json_loads('{"a": {"b": {"c": "value"}}}', max_depth=10000)
    assert result is not None


def test_default_max_depth_value() -> None:
    """Default max depth should be 100."""
    assert _DEFAULT_JSON_MAX_DEPTH == 100


def test_large_but_valid_json() -> None:
    """Large but valid JSON within depth limit should parse."""
    # Create a wide (not deep) structure
    data = {"level1": {f"key{i}": f"value{i}" for i in range(50)}}
    result = _safe_json_loads(json.dumps(data), max_depth=3)
    assert len(result["level1"]) == 50


def test_attack_vector_deeply_nested_object_rejected() -> None:
    """Malicious deeply nested object attack should be blocked."""
    # Classic billion laughs attack vector (deep nesting)
    attack = '{"a": ' * 1000 + '"x"' + "}" * 1000
    try:
        _safe_json_loads(attack)
        assert False, "Attack should have been blocked"
    except _ExcessiveNestingError:
        pass  # Expected


def test_attack_vector_deeply_nested_array_rejected() -> None:
    """Malicious deeply nested array attack should be blocked."""
    attack = "[" * 1000 + "1" + "]" * 1000
    try:
        _safe_json_loads(attack)
        assert False, "Attack should have been blocked"
    except _ExcessiveNestingError:
        pass  # Expected


def test_attack_vector_alternating_nesting_rejected() -> None:
    """Malicious alternating array/object nesting should be blocked."""
    parts = ['{"a":'] * 500 + ["["] * 500 + ["1"] + ["]"] * 500 + ["}"] * 500
    attack = "".join(parts)
    try:
        _safe_json_loads(attack)
        assert False, "Attack should have been blocked"
    except _ExcessiveNestingError:
        pass  # Expected


def test_benign_wide_json_succeeds() -> None:
    """Benign wide JSON (many siblings, shallow depth) should succeed."""
    # 10 levels wide but only 2 deep
    data = {"l1": {f"k{i}": f"v{i}" for i in range(100)}}
    result = _safe_json_loads(json.dumps(data), max_depth=3)
    assert len(result["l1"]) == 100


def test_dos_prevention_does_not_affect_normal_usage() -> None:
    """Normal usage patterns should not be affected by depth limits."""
    # Typical API response structure (5-10 levels max)
    api_response = {
        "data": {
            "users": [
                {
                    "id": 1,
                    "profile": {
                        "name": "John",
                        "settings": {"theme": "dark"},
                    },
                }
            ]
        }
    }
    result = _safe_json_loads(json.dumps(api_response))
    assert result == api_response


def test_existing_json_functionality_preserved() -> None:
    """Basic JSON parsing functionality should remain unchanged."""
    test_cases = [
        "{}",
        '{"key": "value"}',
        '{"nested": {"inner": "value"}}',
        "[]",
        "[1, 2, 3]",
        '{"array": [1, 2, 3]}',
        '{"mixed": [1, "two", {"three": 3}]}',
    ]
    for case in test_cases:
        result = _safe_json_loads(case)
        expected = json.loads(case)
        assert result == expected


def test_extract_json_dict_basic_functionality() -> None:
    """_extract_json_dict basic functionality should remain unchanged."""
    test_cases = [
        ('{"key": "value"}', {"key": "value"}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Some text ```json\n{"b": 2}\n``` more text', {"b": 2}),
    ]
    for text, expected in test_cases:
        result = _extract_json_dict(text)
        assert result == expected


def test_no_false_positives_on_normal_json() -> None:
    """Normal JSON should not trigger excessive nesting errors."""
    # Common benchmark case structures
    normal_cases = [
        '{"status": "success", "data": {"items": [1, 2, 3]}}',
        '{"result": {"subtasks": [{"id": 1}, {"id": 2}]}}',
        '{"plan": [{"step": 1, "action": "read"}, {"step": 2, "action": "write"}]}',
    ]
    for case in normal_cases:
        result = _safe_json_loads(case)
        assert isinstance(result, dict)


def test_count_json_depth_basic() -> None:
    """_count_json_depth should correctly count nesting depth."""
    assert _count_json_depth('{"a": 1}') == 1
    assert _count_json_depth('{"a": {"b": 1}}') == 2
    assert _count_json_depth('{"a": {"b": {"c": 1}}}') == 3


def test_count_json_depth_with_arrays() -> None:
    """_count_json_depth should correctly count depth with arrays."""
    assert _count_json_depth("[1, 2, 3]") == 1
    assert _count_json_depth("[[1, 2]]") == 2
    assert _count_json_depth('{"a": [1, 2]}') == 2
    assert _count_json_depth('{"a": [{"b": 1}]}') == 3


def test_count_json_depth_escaped_quotes() -> None:
    """_count_json_depth should handle escaped quotes correctly."""
    # Braces inside strings should not affect depth counting
    result1 = _count_json_depth('{"a": "value with { braces }"}')
    assert result1 == 1, f"Expected 1 (braces in string), got {result1}"

    # Actual nested braces should increase depth
    result2 = _count_json_depth('{"outer": {"inner": "text"}}')
    assert result2 == 2, f"Expected 2, got {result2}"


def test_module_exports_correct_public_api() -> None:
    """Verify the module exports expected symbols (for internal module)."""
    assert _DEFAULT_JSON_MAX_DEPTH == 100
    assert _ExcessiveNestingError.__bases__ == (ValueError,)
    assert callable(_safe_json_loads)
    assert callable(_extract_json_dict)
    assert callable(_count_json_depth)


def run_all_tests() -> tuple[int, int]:
    """Run all tests and return (passed, failed) counts."""
    import traceback

    tests = [
        test_normal_json_parsing,
        test_shallow_json,
        test_empty_json_object,
        test_empty_json_array,
        test_deeply_nested_json_rejected_at_default_depth,
        test_deeply_nested_json_rejected_at_custom_depth,
        test_json_at_exact_depth_limit_succeeds,
        test_json_one_beyond_depth_limit_fails,
        test_deep_array_nesting_rejected,
        test_mixed_array_object_nesting_rejected,
        test_invalid_json_raises_json_decode_error,
        test_invalid_json_syntax_raises_json_decode_error,
        test_truncated_json_raises_json_decode_error,
        test_excessive_nesting_error_is_value_error_subclass,
        test_excessive_nesting_error_message_includes_depth,
        test_excessive_nesting_error_max_depth_attribute,
        test_zero_max_depth_treated_as_one,
        test_negative_max_depth_treated_as_one,
        test_simple_object,
        test_simple_array,
        test_nested_object,
        test_complex_json_structure,
        test_unicode_json,
        test_returns_dict_for_object_json,
        test_returns_list_for_array_json,
        test_extract_from_standalone_json,
        test_extract_from_markdown_code_block,
        test_extract_from_markdown_code_block_without_json_tag,
        test_extract_from_text_with_markdown,
        test_return_none_for_invalid_json,
        test_return_none_for_empty_string,
        test_return_none_for_whitespace_only,
        test_return_none_for_none_input,
        test_return_none_for_array_json,
        test_re_raises_excessive_nesting_error,
        test_excessive_nesting_in_code_block_rejected,
        test_multiple_code_blocks_returns_first_valid,
        test_max_depth_one_allows_only_primitives,
        test_max_depth_one_allows_array_of_primitives,
        test_max_depth_one_rejects_nested_arrays,
        test_max_depth_two_allows_two_levels,
        test_max_depth_two_rejects_three_levels,
        test_very_large_max_depth,
        test_default_max_depth_value,
        test_large_but_valid_json,
        test_attack_vector_deeply_nested_object_rejected,
        test_attack_vector_deeply_nested_array_rejected,
        test_attack_vector_alternating_nesting_rejected,
        test_benign_wide_json_succeeds,
        test_dos_prevention_does_not_affect_normal_usage,
        test_existing_json_functionality_preserved,
        test_extract_json_dict_basic_functionality,
        test_no_false_positives_on_normal_json,
        test_count_json_depth_basic,
        test_count_json_depth_with_arrays,
        test_count_json_depth_escaped_quotes,
        test_module_exports_correct_public_api,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            # Intentionally catch all test exceptions (AssertionError, ValueError, etc.)
            # to count failures. Do NOT catch SystemExit/KeyboardInterrupt so the test
            # runner can be interrupted normally.
            print(f"FAIL: {test.__name__} - {e}")
            traceback.print_exc()
            failed += 1

    return passed, failed


if __name__ == "__main__":
    print("=" * 60)
    print("JSON DEPTH LIMIT SECURITY TESTS (S1.1)")
    print("=" * 60)
    print()

    passed, failed = run_all_tests()

    print()
    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed == 0:
        print("ALL TESTS PASSED!")
        sys.exit(0)
    else:
        print(f"WARNING: {failed} tests failed")
        sys.exit(1)

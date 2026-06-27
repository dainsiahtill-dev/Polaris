from __future__ import annotations

from polaris.kernelone.benchmark.factory_audit import _has_unfinished_placeholder


def test_placeholder_markers_inside_pure_comments_are_not_unfinished_code() -> None:
    text = """
package museum

// pass a stub from tests instead of wiring an external clock.
// TODO examples in documentation should not make the delivery hollow.
func Tick() int {
    return 1
}
"""

    assert _has_unfinished_placeholder(text) is False


def test_placeholder_markers_inside_code_still_fail() -> None:
    text = """
package museum

func Tick() int {
    TODO := 1
    return TODO
}
"""

    assert _has_unfinished_placeholder(text) is True

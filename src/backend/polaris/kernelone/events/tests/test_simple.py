"""Simple test file for debugging."""


def test_simple() -> None:
    """Simple test."""
    assert 1 + 1 == 2


def test_with_param(x=1) -> None:
    """Test with parameter."""
    assert x > 0

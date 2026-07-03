from __future__ import annotations

from polaris.cells.roles.kernel.internal.policy.layer.syntax import SyntaxPolicy


def test_exact_search_validator_rejects_generated_syntax_glue() -> None:
    policy = SyntaxPolicy()

    result = policy.validate_exact_search_text("return0", file_path="src/main.py")

    assert result.valid is False
    assert "[exact-search 语法拦截]" in result.error
    assert "return 0" in result.suggestion


def test_deprecated_precision_edit_validator_is_not_public_api() -> None:
    policy = SyntaxPolicy()
    retired_name = "validate_" + "precision_" + "edit_search"

    assert not hasattr(policy, retired_name)

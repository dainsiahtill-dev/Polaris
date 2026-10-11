"""Local acceptance polarity must not hide an independent weak gate."""

import pytest
from polaris.cells.orchestration.pm_planning.internal.quality_gate.primitives import (
    _has_placeholder_or_manifest_only_acceptance,
)


@pytest.mark.parametrize(
    "text",
    [
        "package.json 含 start/build/test/typecheck 四个非占位脚本",
        "禁止使用占位输出",
        "不得使用占位测试",
        "不能允许占位输出",
        "不要占位输出",
        "不允许占位测试",
        "no placeholder output",
        "non-placeholder scripts must execute",
        "Do not allow placeholder output",
        "must not use dummy output",
        "禁止只检查 package.json",
        "不只检查 package.json",
        "不仅检查 package.json",
        "No manifest-only checks",
        "Remove placeholder scripts and run real tests",
    ],
)
def test_prohibition_is_not_placeholder_permission(text: str) -> None:
    assert _has_placeholder_or_manifest_only_acceptance([text]) is False


@pytest.mark.parametrize(
    "text",
    [
        "占位输出即可",
        "允许占位",
        "placeholder output is ok",
        "manifest-only",
        "only checks package.json",
        "不要占位输出；允许占位测试",
        "No placeholder output, but manifest-only checks suffice",
        "No network is needed; placeholder output is ok",
        "Remove placeholder scripts; allow dummy output",
        "禁止占位输出，但允许占位测试",
        "Remove placeholder scripts and allow dummy output",
        "禁止网络请求并允许占位测试",
        "不得使用占位输出，测试允许占位",
        "No placeholder scripts and dummy output is ok",
        "Replace placeholder scripts; manifest-only checks suffice",
        "禁止网络请求 占位输出即可",
        "禁止外部依赖 允许占位测试",
        "Remove placeholder scripts and dummy output is ok",
    ],
)
def test_independent_permission_remains_rejected(text: str) -> None:
    assert _has_placeholder_or_manifest_only_acceptance([text]) is True

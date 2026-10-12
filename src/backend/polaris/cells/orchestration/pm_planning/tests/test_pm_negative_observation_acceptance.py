"""Negative observations must not authorize placeholder delivery."""

from __future__ import annotations

import pytest


def test_native_non_placeholder_manifest_declaration_is_not_permission() -> None:
    from polaris.cells.orchestration.pm_planning.internal.quality_gate.primitives import (
        _has_placeholder_or_manifest_only_acceptance,
    )

    assert not _has_placeholder_or_manifest_only_acceptance(
        ["package.json 含 scripts.start、scripts.build、scripts.test，build/test/start 不是 manifest-only 占位。"]
    )


@pytest.mark.parametrize(
    "acceptance",
    [
        "anti-hollow 检查未发现纯占位测试。",
        "检查没有发现任何占位测试。",
        "静态检查未检测到纯 placeholder 测试。",
        "验收未检出任何 stub 实现。",
    ],
)
def test_negative_placeholder_observation_is_not_permission(acceptance: str) -> None:
    from polaris.cells.orchestration.pm_planning.internal.quality_gate.primitives import (
        _has_placeholder_or_manifest_only_acceptance,
    )

    assert _has_placeholder_or_manifest_only_acceptance([acceptance]) is False


def test_negative_placeholder_observation_does_not_hide_separate_permission() -> None:
    from polaris.cells.orchestration.pm_planning.internal.quality_gate.primitives import (
        _has_placeholder_or_manifest_only_acceptance,
    )

    assert _has_placeholder_or_manifest_only_acceptance(["检查未发现纯占位测试，但验收允许使用占位测试。"])


@pytest.mark.parametrize("suffix,expected", [("", False), ("；但允许 manifest-only 占位脚本。", True)])
def test_negative_enumeration_does_not_become_placeholder_permission(suffix: str, expected: bool) -> None:
    from polaris.cells.orchestration.pm_planning.internal.quality_gate.primitives import (
        _has_placeholder_or_manifest_only_acceptance,
    )

    acceptance = (
        "package.json scripts.build 实际编译产品源码,scripts.start 真实启动入口,"
        "scripts.test 调用真实测试模块;不存在 echo/manifest-only 占位脚本。"
    )
    assert _has_placeholder_or_manifest_only_acceptance([acceptance + suffix]) is expected


def test_native_revision_negative_runtime_examples_are_not_permission() -> None:
    from polaris.cells.orchestration.pm_planning.internal.quality_gate.primitives import (
        _has_placeholder_or_manifest_only_acceptance,
    )

    acceptance = (
        "scripts.start 真实加载并运行 dist/entry/main.js 并输出至少一帧数据"
        "(非 echo、cat、true、echo 字符串或仅检查 manifest 存在),"
        "scripts.test 调用真实测试模块;不存在 echo/manifest-only 占位脚本。"
    )
    assert not _has_placeholder_or_manifest_only_acceptance([acceptance])

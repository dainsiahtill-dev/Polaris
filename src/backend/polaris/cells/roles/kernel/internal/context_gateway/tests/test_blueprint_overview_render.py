"""render_blueprint_overview 纯函数单测（蓝图概览渲染 + 优雅降级）。"""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.context_gateway.gateway import (
    ContextGatewayConfig,
    RoleContextGateway,
    render_blueprint_overview,
    render_verdict_history,
)


def _result(ok=True, summary="", recommendations=(), risks=()):
    return SimpleNamespace(ok=ok, summary=summary, recommendations=recommendations, risks=risks)


def test_renders_summary_recommendations_risks() -> None:
    out = render_blueprint_overview(
        _result(summary="v2 layered architecture", recommendations=("split module A",), risks=("tight coupling",))
    )
    assert out is not None
    assert "v2 layered architecture" in out
    assert "推荐:" in out and "split module A" in out
    assert "风险:" in out and "tight coupling" in out


def test_not_ok_returns_none() -> None:
    assert render_blueprint_overview(_result(ok=False, summary="missing")) is None


def test_empty_content_returns_none() -> None:
    assert render_blueprint_overview(_result(ok=True, summary="", recommendations=(), risks=())) is None


def test_summary_only() -> None:
    out = render_blueprint_overview(_result(summary="just a summary"))
    assert out == "just a summary"


def _verdict(ok=True, verdict="FAIL", score=0.0, findings=(), suggestions=()):
    return SimpleNamespace(ok=ok, verdict=verdict, score=score, findings=findings, suggestions=suggestions)


def test_verdict_renders_with_findings_and_suggestions() -> None:
    out = render_verdict_history(
        _verdict(verdict="FAIL", score=0.0, findings=("gate X failed",), suggestions=("fix import",))
    )
    assert out is not None
    assert "最新判定: FAIL" in out and "score=0.00" in out
    assert "问题:" in out and "gate X failed" in out
    assert "建议:" in out and "fix import" in out


def test_verdict_not_ok_returns_none() -> None:
    assert render_verdict_history(_verdict(ok=False, verdict="missing")) is None


def test_verdict_pass_minimal() -> None:
    out = render_verdict_history(_verdict(ok=True, verdict="PASS", score=1.0))
    assert out == "最新判定: PASS (score=1.00)"


def _profile(role_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        role_id=role_id,
        context_domain="generic",
        provider_id=None,
        model=None,
        context_policy=SimpleNamespace(
            include_project_structure=False,
            include_task_history=False,
            include_blueprint_overview=True,
            include_verdict_history=True,
            max_context_tokens=4096,
            compression_strategy="none",
            max_history_turns=4,
        ),
    )


def test_gateway_reads_blueprint_overview_from_configured_provider() -> None:
    gateway = RoleContextGateway(
        _profile("chief_engineer"),
        workspace=".",
        config=ContextGatewayConfig(
            blueprint_overview_provider=lambda task_id, workspace: _result(
                summary=f"{workspace}:{task_id}:v2 layered architecture"
            )
        ),
    )

    assert gateway._get_blueprint_overview("T9") == ".:T9:v2 layered architecture"


def test_gateway_reads_verdict_history_from_configured_provider() -> None:
    gateway = RoleContextGateway(
        _profile("qa"),
        workspace=".",
        config=ContextGatewayConfig(
            verdict_history_provider=lambda task_id, workspace: _verdict(
                verdict="FAIL", score=0.5, findings=(f"{workspace}:{task_id}:gate",)
            )
        ),
    )

    assert gateway._get_verdict_history("T9") == "最新判定: FAIL (score=0.50)\n问题:\n- .:T9:gate"

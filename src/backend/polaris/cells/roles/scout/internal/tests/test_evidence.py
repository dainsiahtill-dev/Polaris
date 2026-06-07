"""Tests for build_content_hash (UTF-8)."""

from polaris.cells.roles.scout.internal.evidence import build_content_hash
from polaris.cells.roles.scout.public.contracts import ScoutFinding


def test_build_content_hash_is_stable_and_changes_with_summary() -> None:
    findings = [ScoutFinding(path="a.py", line=1, snippet="def f()")]
    h1 = build_content_hash(task_id="t1", findings=findings, summary="s", tools_used=["repo_rg"])
    h2 = build_content_hash(task_id="t1", findings=findings, summary="s", tools_used=["repo_rg"])
    h3 = build_content_hash(task_id="t1", findings=findings, summary="DIFFERENT", tools_used=["repo_rg"])
    assert h1 == h2
    assert h1 != h3
    assert isinstance(h1, str) and len(h1) > 0

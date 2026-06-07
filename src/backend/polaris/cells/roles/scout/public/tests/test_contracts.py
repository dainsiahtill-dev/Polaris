import pytest
from polaris.cells.roles.scout.public.contracts import (
    ScoutFinding, ScoutProbeTargetV1, ScoutReportV1,
)


def test_target_requires_query() -> None:
    with pytest.raises(ValueError):
        ScoutProbeTargetV1(query="  ")


def test_target_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        ScoutProbeTargetV1(query="where is x", mode="nonsense")


def test_target_cache_key_is_stable_and_order_independent() -> None:
    a = ScoutProbeTargetV1(query="find pay", hints={"paths": ["a", "b"]})
    b = ScoutProbeTargetV1(query="find pay", hints={"paths": ["b", "a"]})
    assert a.cache_key() == b.cache_key()


def test_report_to_dict_roundtrips_findings() -> None:
    f = ScoutFinding(path="x.py", snippet="def f()", symbol="f", line=1, confidence=0.5)
    r = ScoutReportV1(
        findings=(f,), summary="s", coverage={"truncated": False},
        confidence=0.5, content_hash="h", usage={"tokens": 0},
    )
    d = r.to_dict()
    assert d["findings"][0]["symbol"] == "f"
    assert d["cache_hit"] is False

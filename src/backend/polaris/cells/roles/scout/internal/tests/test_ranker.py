from polaris.cells.roles.scout.public.contracts import ScoutFinding, ScoutProbeTargetV1
from polaris.cells.roles.scout.internal.ranker import rank


def test_rank_dedupes_by_path_and_line() -> None:
    f = ScoutFinding(path="a.py", line=1, snippet="def pay():")
    out = rank([f, f], ScoutProbeTargetV1(query="pay"))
    assert len(out) == 1


def test_rank_scores_symbol_defs_above_plain_text_and_caps() -> None:
    defn = ScoutFinding(path="a.py", line=1, snippet="def payment():")
    text = ScoutFinding(path="b.py", line=9, snippet="# call payment here")
    out = rank([text, defn], ScoutProbeTargetV1(query="payment", max_findings=1))
    assert len(out) == 1
    assert out[0].path == "a.py"
    assert out[0].confidence > 0.0
    assert out[0].symbol == "payment"

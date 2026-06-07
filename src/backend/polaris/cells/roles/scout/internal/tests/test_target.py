from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.internal.target import extract_terms, hint_paths


def test_extract_terms_splits_and_lowercases_and_dedupes() -> None:
    t = ScoutProbeTargetV1(query="Payment Gateway payment")
    assert extract_terms(t) == ["payment", "gateway"]


def test_extract_terms_drops_stopwords_and_short_tokens() -> None:
    t = ScoutProbeTargetV1(query="where is the error handling in a")
    assert extract_terms(t) == ["error", "handling"]


def test_hint_paths_returns_list_or_empty() -> None:
    assert hint_paths(ScoutProbeTargetV1(query="x", hints={"paths": ["src/a"]})) == ["src/a"]
    assert hint_paths(ScoutProbeTargetV1(query="x")) == []

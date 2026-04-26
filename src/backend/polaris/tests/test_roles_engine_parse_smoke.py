"""Smoke tests for ToT/ReAct JSON parsing fallbacks (Agent-24)."""

from polaris.cells.roles.engine.internal.react import ReActEngine
from polaris.cells.roles.engine.internal.tot import ToTEngine


def test_tot_parse_thoughts_accepts_json_list():
    eng = ToTEngine()
    raw = '[{"thought":"a","reasoning":"b","confidence":0.5}]'
    out = eng._parse_thoughts_response(raw)
    assert len(out) == 1
    assert out[0]["thought"] == "a"


def test_react_parse_response_fallback():
    eng = ReActEngine()
    r = eng._parse_response("not valid json at all")
    assert "action" in r
    assert "thought" in r

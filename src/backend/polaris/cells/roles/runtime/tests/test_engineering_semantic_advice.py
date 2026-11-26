from __future__ import annotations

import pytest
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import validate_node_summary


def summary_request() -> dict[str, object]:
    return {
        "node_id": "obs_read_312",
        "snapshot_id": "snapshot_util_v17",
        "coverage": {"observed_line_ranges": [[1, 240]]},
        "allowed_evidence_refs": ["failure_unicode_alignment"],
        "max_summary_tokens": 192,
    }


def valid_summary() -> dict[str, object]:
    return {
        "node_id": "obs_read_312",
        "snapshot_id": "snapshot_util_v17",
        "summary": "该文件负责显示宽度、Unicode 边界与表格换行计算。",
        "summary_kind": "model_inference",
        "coverage": {"observed_line_ranges": [[1, 240]], "summary_complete": False},
        "evidence_refs": ["snapshot_util_v17", "failure_unicode_alignment"],
        "uncertain": True,
        "reason": "摘要仅基于该快照和当前失败。",
    }


def test_node_summary_is_bound_to_snapshot_coverage_and_refs() -> None:
    validated = validate_node_summary(valid_summary(), summary_request())
    assert validated["schema_version"] == "node-summary.v1"
    assert validated["summary_kind"] == "model_inference"


def test_node_summary_rejects_stale_identity_fake_kind_and_bad_coverage() -> None:
    payload = valid_summary()
    payload["snapshot_id"] = "snapshot_newer"
    with pytest.raises(ValueError, match="does not match"):
        validate_node_summary(payload, summary_request())
    payload = valid_summary()
    payload["summary_kind"] = "verified_fact"
    with pytest.raises(ValueError, match="summary_kind"):
        validate_node_summary(payload, summary_request())
    payload = valid_summary()
    payload["coverage"]["observed_line_ranges"] = [[1, 100]]
    with pytest.raises(ValueError, match="coverage differs"):
        validate_node_summary(payload, summary_request())


def test_node_summary_rejects_status_words_and_verbatim_excerpts() -> None:
    content = "74:  private accumulator = 0;\n75:  private error: string | null = null;\n"
    for bad in ("completed", "completed successfully", "ok", "failed"):
        payload = valid_summary()
        payload["summary"] = bad
        with pytest.raises(ValueError, match="describe the payload"):
            validate_node_summary(payload, summary_request())
    payload = valid_summary()
    payload["summary"] = "75:  private error: string | null = null;"
    with pytest.raises(ValueError, match="describe the payload"):
        validate_node_summary(payload, {**summary_request(), "content": content})


@pytest.mark.parametrize(
    "text",
    [
        "The display pipeline computes Unicode line wrapping and table width.",
        "The map is exported by `export const LABEL_TABLE`; all callers share these labels.",
        "`const LABEL_TABLE`",
        "The result is `const LABEL_TABLE` and the module also exposes `REMOTE_LOOKUP`.",
    ],
)
def test_summary_requires_a_real_source_discriminant_and_explanation(text: str) -> None:
    request = {**summary_request(), "node_kind": "file_card", "content": "const LABEL_TABLE = { neutral: 'plain' };"}
    with pytest.raises(ValueError, match="source"):
        validate_node_summary({**valid_summary(), "summary": text}, request)


@pytest.mark.parametrize(
    "text",
    [
        "The `const LABEL_TABLE` declaration binds the neutral state to the plain label.",
        "LABEL_TABLE associates the neutral state with the plain label.",
    ],
)
def test_summary_accepts_explained_source_evidence_with_or_without_inline_quotes(text: str) -> None:
    request = {**summary_request(), "node_kind": "file_card", "content": "const LABEL_TABLE = { neutral: 'plain' };"}
    result = validate_node_summary({**valid_summary(), "summary": text}, request)
    assert result["summary"] == text
    assert result["source_anchor"] in {"const LABEL_TABLE", "LABEL_TABLE"}


@pytest.mark.parametrize(
    ("source", "text", "admit"),
    [
        (
            "export class ApiFactory {}\nexport interface PublicInput {}",
            "The module exports ApiFactory and PublicInput.",
            True,
        ),
        ("export class Converter {}\nconst LABEL_TABLE = {};", "导出 Converter 类与标签映射表 LABEL_TABLE。", False),
        ("export class Converter {}\nconst LABEL_TABLE = {};", "The table is exported by `const LABEL_TABLE`.", False),
        (
            "export class Converter {}\nconst LABEL_TABLE = {};",
            "Exports `LABEL_TABLE` and `export class Converter`.",
            False,
        ),
        (
            "const local = 1;\nexport { local as API_NAME };",
            "`export { local as API_NAME }` exposes the existing value under the public API name.",
            True,
        ),
        (
            "__all__ = ['compute']\ndef compute(): return 7",
            "The public API lists the computation through `__all__ = ['compute']`.",
            True,
        ),
        (
            "pub use crate::inner::Value as Item;",
            "The module exports an alias via `pub use crate::inner::Value as Item`.",
            True,
        ),
        (
            "const LABEL_TABLE = {};",
            "`const LABEL_TABLE` binds the lookup locally. Whether it is exported is unknown.",
            True,
        ),
    ],
)
def test_visibility_claims_need_source_evidence_or_explicit_uncertainty(source: str, text: str, admit: bool) -> None:
    request = {**summary_request(), "node_kind": "file_card", "content": source}
    payload = {**valid_summary(), "summary": text}
    if admit:
        assert validate_node_summary(payload, request)["summary"] == text
    else:
        with pytest.raises(ValueError, match="source"):
            validate_node_summary(payload, request)


@pytest.mark.parametrize(
    ("line", "claim"),
    [
        (2, "The input is sent to the handler."),
        (3, "The input is sent to the handler."),
        (1, "UnseenGateway receives every update from this condition."),
        (1, "The module exports a policy for consumers."),
    ],
)
def test_selected_source_line_cannot_establish_an_unseen_symbol_or_interface(line: int, claim: str) -> None:
    request = {**summary_request(), "node_kind": "file_card", "content": "if enabled: dispatch(value)\n}"}
    with pytest.raises(ValueError, match="source"):
        validate_node_summary({**valid_summary(), "summary": claim}, request, selected_line=line)


@pytest.mark.parametrize(
    ("name", "source", "line", "is_doc"),
    [
        ("draw.ts", "/**\n * Only draws; never changes state.\n */\nfunction draw() {}", 2, True),
        ("engine.ts", "// Only draws the current state.\nupdate();", 1, True),
        ("engine.py", "# Emit the current state.\nrender()", 1, True),
        ("engine.c", "#include <stdio.h>\n#define WIDTH 72", 1, False),
        ("engine.c", "#include <stdio.h>\n#define WIDTH 72", 2, False),
        ("engine.c", "int *value;\n*value = count;", 2, False),
        ("engine.c", "int total = count\n  * width;", 2, False),
        ("engine.ts", "if (value >= 0 && value <= 7) notify(value); // bounded", 1, False),
        ("engine.ts", "const example = `\n// This is string data.\n`;", 2, False),
    ],
)
def test_distiller_separates_documented_intent_from_source_observations(name, source, line, is_doc):
    request = {**summary_request(), "node_kind": "file_card", "node_id": "file:" + name, "content": source}
    raw = {
        **valid_summary(),
        "node_id": request["node_id"],
        "uncertain": False,
        "summary": "This operation restricts how the state can change.",
    }
    result = validate_node_summary(raw, request, selected_line=line)
    assert result["uncertain"] is is_doc
    assert result["summary"].startswith("Documented intent") is is_doc
    assert result["source_anchor"] in result["summary"]

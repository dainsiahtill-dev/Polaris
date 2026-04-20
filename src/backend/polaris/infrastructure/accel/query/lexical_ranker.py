from __future__ import annotations

import importlib
import math
import re
from typing import Any

_LEXICAL_PROVIDERS = {"off", "auto", "tantivy"}
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,63}")


def normalize_lexical_provider(value: Any, default_value: str = "off") -> str:
    token = str(value or default_value).strip().lower()
    if token in _LEXICAL_PROVIDERS:
        return token
    fallback = str(default_value or "off").strip().lower()
    return fallback if fallback in _LEXICAL_PROVIDERS else "off"


def _clamp_ratio(value: Any, default_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default_value)
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return float(parsed)


def _resolve_provider(runtime_cfg: dict[str, Any]) -> str:
    provider = normalize_lexical_provider(
        runtime_cfg.get("lexical_ranker_provider", "off"),
        "off",
    )
    if provider == "auto":
        return "tantivy"
    return provider


def _load_tantivy_module() -> Any:
    return importlib.import_module("tantivy")


def probe_lexical_runtime(config: dict[str, Any]) -> dict[str, Any]:
    runtime_cfg = dict(config.get("runtime", {}))
    enabled = bool(runtime_cfg.get("lexical_ranker_enabled", False))
    requested_provider = normalize_lexical_provider(
        runtime_cfg.get("lexical_ranker_provider", "off"),
        "off",
    )
    provider = _resolve_provider(runtime_cfg)
    tantivy_available = False
    tantivy_reason = ""

    if enabled and provider == "tantivy":
        try:
            _load_tantivy_module()
            tantivy_available = True
        except (RuntimeError, ValueError) as exc:
            tantivy_reason = f"tantivy_unavailable:{exc.__class__.__name__}"

    reason = "disabled_by_config"
    if enabled:
        if provider == "off":
            reason = "provider_off"
        elif provider != "tantivy":
            reason = "provider_unsupported"
        elif not tantivy_available:
            reason = "tantivy_unavailable"
        else:
            reason = "ready"

    return {
        "enabled": enabled,
        "provider_requested": requested_provider,
        "provider_resolved": provider,
        "tantivy_available": tantivy_available,
        "reason": reason,
        "reason_detail": tantivy_reason,
    }


def _tokenize(text: str) -> list[str]:
    return [item.lower() for item in _TOKEN_RE.findall(str(text or ""))]


def _build_doc_text(
    *,
    file_path: str,
    symbols_by_file: dict[str, list[dict[str, Any]]],
    references_by_file: dict[str, list[dict[str, Any]]],
    deps_by_file: dict[str, list[dict[str, Any]]],
    tests_by_file: dict[str, list[dict[str, Any]]],
) -> str:
    symbol_rows = symbols_by_file.get(file_path, [])
    ref_rows = references_by_file.get(file_path, [])
    dep_rows = deps_by_file.get(file_path, [])
    test_rows = tests_by_file.get(file_path, [])

    symbols: list[str] = []
    refs: list[str] = []
    deps: list[str] = []
    tests: list[str] = []

    for row in symbol_rows[:50]:
        symbol = str(row.get("symbol", "")).strip()
        qn = str(row.get("qualified_name", "")).strip()
        signature = str(row.get("signature", "")).strip()
        scope = str(row.get("scope", "")).strip()
        return_type = str(row.get("return_type", "")).strip()
        kind = str(row.get("kind", "")).strip()
        if symbol:
            symbols.append(symbol)
        if qn:
            symbols.append(qn)
        if signature:
            symbols.append(signature)
        if scope:
            symbols.append(scope)
        if return_type:
            symbols.append(return_type)
        if kind:
            symbols.append(kind)
        for key in ("parameters", "decorators", "bases", "relation_targets"):
            value = row.get(key, [])
            if isinstance(value, list):
                symbols.extend(str(item).strip() for item in value[:12] if str(item).strip())
    for row in ref_rows[:50]:
        target = str(row.get("target_symbol", "")).strip()
        if target:
            refs.append(target)
    for row in dep_rows[:40]:
        edge_to = str(row.get("edge_to", "")).strip()
        if edge_to:
            deps.append(edge_to)
    for row in test_rows[:20]:
        test_file = str(row.get("test_file", "")).strip()
        if test_file:
            tests.append(test_file)

    parts = [f"path {file_path}"]
    if symbols:
        parts.append("symbols " + " ".join(symbols[:40]))
    if refs:
        parts.append("references " + " ".join(refs[:40]))
    if deps:
        parts.append("dependencies " + " ".join(deps[:30]))
    if tests:
        parts.append("tests " + " ".join(tests[:20]))
    return "\n".join(parts)


def _score_with_bm25(
    *,
    docs: list[tuple[str, str]],
    query_tokens: list[str],
) -> dict[str, float]:
    if not docs or not query_tokens:
        return {}
    tokenized_docs: list[tuple[str, list[str]]] = [(path, _tokenize(content)) for path, content in docs]
    tokenized_docs = [(path, toks) for path, toks in tokenized_docs if toks]
    if not tokenized_docs:
        return {}

    doc_count = len(tokenized_docs)
    avg_len = sum(len(tokens) for _, tokens in tokenized_docs) / float(doc_count)
    if avg_len <= 0.0:
        avg_len = 1.0

    unique_query = list(dict.fromkeys(query_tokens))
    doc_freq: dict[str, int] = {}
    for term in unique_query:
        freq = 0
        for _, tokens in tokenized_docs:
            if term in tokens:
                freq += 1
        doc_freq[term] = freq

    k1 = 1.5
    b = 0.75
    scored: dict[str, float] = {}
    for path, tokens in tokenized_docs:
        tf: dict[str, int] = {}
        for token in tokens:
            tf[token] = int(tf.get(token, 0)) + 1
        dl = len(tokens)
        score = 0.0
        for term in unique_query:
            freq = int(tf.get(term, 0))
            if freq <= 0:
                continue
            df = int(doc_freq.get(term, 0))
            idf = math.log(1.0 + ((doc_count - df + 0.5) / (df + 0.5)))
            denom = freq + (k1 * (1.0 - b + b * (dl / avg_len)))
            score += idf * ((freq * (k1 + 1.0)) / max(1e-9, denom))
        scored[path] = score
    return scored


def _extract_path_from_stored_doc(doc: Any) -> str:
    if doc is None:
        return ""

    getter = getattr(doc, "get_first", None)
    if callable(getter):
        try:
            value = getter("path")
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                for key in ("text", "value", "string"):
                    raw = value.get(key)
                    if isinstance(raw, str):
                        return raw
        except (AttributeError, TypeError, KeyError):
            pass

    candidate: Any = None
    if hasattr(doc, "to_dict"):
        try:
            candidate = doc.to_dict()
        except (AttributeError, TypeError):
            candidate = None
    if candidate is None and hasattr(doc, "as_dict"):
        try:
            candidate = doc.as_dict()
        except (AttributeError, TypeError):
            candidate = None
    if candidate is None and isinstance(doc, dict):
        candidate = doc

    if isinstance(candidate, dict):
        raw = candidate.get("path")
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list) and raw:
            head = raw[0]
            if isinstance(head, str):
                return head
            if isinstance(head, dict):
                for key in ("text", "value", "string"):
                    token = head.get(key)
                    if isinstance(token, str):
                        return token
    return ""


def _score_with_tantivy(
    *,
    docs: list[tuple[str, str]],
    query_text: str,
    limit: int,
) -> dict[str, float]:
    tantivy = _load_tantivy_module()

    schema_builder = tantivy.SchemaBuilder()
    schema_builder.add_text_field("path", stored=True)
    schema_builder.add_text_field("body", stored=False)
    schema = schema_builder.build()

    index = tantivy.Index(schema)
    writer = index.writer()
    for path, body in docs:
        added = False
        add_errors: list[str] = []
        for payload in (
            {"path": path, "body": body},
            {"path": [path], "body": [body]},
        ):
            try:
                writer.add_document(tantivy.Document(**payload))
                added = True
                break
            except (ValueError, TypeError) as exc:
                add_errors.append(exc.__class__.__name__)
        if not added:
            raise RuntimeError(f"tantivy_document_add_failed:{','.join(add_errors)}")
    writer.commit()
    writer.wait_merging_threads()

    searcher = index.searcher()
    try:
        query = index.parse_query(query_text, ["path", "body"])
    except TypeError:
        query = index.parse_query(query_text)
    result = searcher.search(query, limit=max(1, int(limit)))
    hits = getattr(result, "hits", result)
    if not isinstance(hits, list):
        return {}

    scored: dict[str, float] = {}
    for item in hits:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        raw_score = float(item[0])
        doc_addr = item[1]
        try:
            stored = searcher.doc(doc_addr)
        except (ValueError, TypeError):
            continue
        path = _extract_path_from_stored_doc(stored)
        if path:
            scored[path] = max(raw_score, float(scored.get(path, raw_score)))
    return scored


def _normalize_scores(
    raw_scores: dict[str, float],
    *,
    candidate_paths: list[str],
) -> dict[str, float]:
    values = [float(raw_scores.get(path, 0.0)) for path in candidate_paths]
    if not values:
        return {}
    low = min(values)
    high = max(values)
    if abs(high - low) <= 1e-9:
        if high <= 0.0:
            return dict.fromkeys(candidate_paths, 0.0)
        return dict.fromkeys(candidate_paths, 0.5)
    scale = high - low
    return {path: max(0.0, min(1.0, (float(raw_scores.get(path, 0.0)) - low) / scale)) for path in candidate_paths}


def apply_lexical_ranking(
    *,
    ranked: list[dict[str, Any]],
    config: dict[str, Any],
    task: str,
    task_tokens: list[str],
    hints: list[str] | None,
    symbols_by_file: dict[str, list[dict[str, Any]]],
    references_by_file: dict[str, list[dict[str, Any]]],
    deps_by_file: dict[str, list[dict[str, Any]]],
    tests_by_file: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    probe = probe_lexical_runtime(config)
    metadata: dict[str, Any] = {
        "enabled": bool(probe.get("enabled", False)),
        "provider_requested": str(probe.get("provider_requested", "off")),
        "provider_resolved": str(probe.get("provider_resolved", "off")),
        "reason": str(probe.get("reason", "disabled_by_config")),
        "applied": False,
        "engine": "none",
        "candidate_count": 0,
        "hits_count": 0,
    }
    if str(probe.get("reason", "")) != "ready":
        return ranked, metadata

    runtime_cfg = dict(config.get("runtime", {}))
    max_candidates = max(1, int(runtime_cfg.get("lexical_ranker_max_candidates", 200)))
    weight = _clamp_ratio(runtime_cfg.get("lexical_ranker_weight", 0.2), 0.2)

    candidates = [dict(item) for item in ranked[:max_candidates]]
    if not candidates:
        metadata["reason"] = "no_candidates"
        return ranked, metadata
    candidate_paths = [str(item.get("path", "")) for item in candidates]
    metadata["candidate_count"] = len(candidates)

    hint_tokens = [str(item).strip() for item in (hints or []) if str(item).strip()]
    query_text = "\n".join(
        item for item in [str(task).strip(), " ".join(task_tokens), " ".join(hint_tokens)] if item
    ).strip()
    if not query_text:
        metadata["reason"] = "empty_query"
        return ranked, metadata

    docs = [
        (
            file_path,
            _build_doc_text(
                file_path=file_path,
                symbols_by_file=symbols_by_file,
                references_by_file=references_by_file,
                deps_by_file=deps_by_file,
                tests_by_file=tests_by_file,
            ),
        )
        for file_path in candidate_paths
    ]

    query_tokens = _tokenize(query_text)
    scored: dict[str, float] | None = None
    engine = "bm25"
    reason = "applied"
    if bool(probe.get("tantivy_available", False)):
        try:
            scored = _score_with_tantivy(
                docs=docs,
                query_text=query_text,
                limit=len(candidates),
            )
            if scored:
                engine = "tantivy"
        except (RuntimeError, ValueError) as exc:
            reason = f"tantivy_failed_fallback:{exc.__class__.__name__}"
            scored = None
    if scored is None:
        scored = _score_with_bm25(docs=docs, query_tokens=query_tokens)

    normalized = _normalize_scores(scored, candidate_paths=candidate_paths)
    if not normalized:
        metadata["reason"] = "no_hits"
        metadata["engine"] = engine
        return ranked, metadata

    hit_count = 0
    for item in candidates:
        path = str(item.get("path", ""))
        lexical_score = float(normalized.get(path, 0.0))
        if lexical_score > 0.0:
            hit_count += 1
        current_score = float(item.get("score", 0.0))
        blended = ((1.0 - weight) * current_score) + (weight * lexical_score)
        item["score"] = round(blended, 6)

        reasons = list(item.get("reasons", []))
        if "lexical_search" not in reasons:
            reasons.append("lexical_search")
        item["reasons"] = reasons

        signals = list(item.get("signals", []))
        signals.append(
            {
                "signal_name": "lexical_search_score",
                "score": round(lexical_score, 6),
            }
        )
        item["signals"] = signals

    candidates.sort(key=lambda row: (-float(row.get("score", 0.0)), str(row.get("path", ""))))

    updated: dict[str, dict[str, Any]] = {
        str(item.get("path", "")): item for item in candidates if str(item.get("path", ""))
    }
    merged = [updated.get(str(item.get("path", "")), dict(item)) for item in ranked]
    merged.sort(key=lambda row: (-float(row.get("score", 0.0)), str(row.get("path", ""))))

    metadata["applied"] = True
    metadata["engine"] = engine
    metadata["reason"] = reason
    metadata["hits_count"] = int(hit_count)
    return merged, metadata

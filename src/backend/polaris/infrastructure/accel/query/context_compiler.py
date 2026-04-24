from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from ..language_profiles import (
    resolve_enabled_verify_groups,
    resolve_extension_verify_group_map,
)
from ..semantic_ranker import apply_semantic_ranking
from ..storage.cache import project_paths
from ..storage.index_cache import load_index_rows
from .adaptive_budget import resolve_adaptive_budget
from .lexical_ranker import apply_lexical_ranking
from .planner import build_candidate_files, normalize_task_tokens
from .ranker import score_file
from .rule_compressor import compress_snippet_content
from .semantic_relations import (
    build_semantic_relation_graph,
    collect_seed_files,
    expand_candidates_with_relations,
    relation_graph_stats,
)
from .snippet_extractor import extract_snippet


def _group_by_file(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        file_value = str(row.get(key, ""))
        if not file_value:
            continue
        grouped.setdefault(file_value, []).append(row)
    return grouped


def _estimate_source_bytes(project_dir: Path, indexed_files: list[str], sample_limit: int = 200) -> int:
    if not indexed_files:
        return 0
    sample = indexed_files[: max(1, int(sample_limit))]
    total = 0
    counted = 0
    for rel_path in sample:
        try:
            total += int((project_dir / rel_path).stat().st_size)
            counted += 1
        except OSError:
            continue
    if counted <= 0:
        return 0
    if len(indexed_files) > counted:
        ratio = float(len(indexed_files)) / float(counted)
        return int(total * ratio)
    return total


def _snippet_fingerprint(content: str) -> str:
    normalized = " ".join(str(content).lower().split())
    return hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()


def _structural_signature(content: str) -> str:
    lowered = str(content or "").lower()
    lowered = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", "id", lowered)
    lowered = re.sub(r"\d+", "num", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _semantic_token_set(content: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,63}", str(content or "").lower())
    return set(tokens[:400])


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return float(len(left & right)) / float(len(union))


def _normalize_path_key(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().lower()


def _changed_scope_prefixes(changed_files: list[str]) -> list[str]:
    prefixes: set[str] = set()
    for raw_path in changed_files:
        normalized = _normalize_path_key(raw_path)
        if not normalized or "/" not in normalized:
            continue
        parent = normalized.rsplit("/", 1)[0]
        if parent:
            prefixes.add(parent + "/")
    return sorted(prefixes, key=len, reverse=True)


def _apply_changed_scope_prioritization(
    ranked: list[dict[str, Any]],
    changed_files: list[str],
) -> list[dict[str, Any]]:
    changed_norm = [_normalize_path_key(item) for item in changed_files]
    changed_set = {item for item in changed_norm if item}
    if not changed_set:
        return ranked
    prefixes = _changed_scope_prefixes(changed_files)

    changed_index: dict[str, int] = {}
    for idx, path in enumerate(changed_norm):
        if path and path not in changed_index:
            changed_index[path] = idx

    adjusted: list[dict[str, Any]] = []
    for item in ranked:
        row = dict(item)
        row_path = _normalize_path_key(str(row.get("path", "")))
        reasons = list(row.get("reasons", []))
        signals = list(row.get("signals", []))
        score = float(row.get("score", 0.0))

        if row_path in changed_set:
            if "changed_scope_pin" not in reasons:
                reasons.append("changed_scope_pin")
            if not any(
                str(signal.get("signal_name", "")) == "changed_scope_pin"
                for signal in signals
                if isinstance(signal, dict)
            ):
                signals.append({"signal_name": "changed_scope_pin", "score": 1.0})
        elif any(row_path.startswith(prefix) for prefix in prefixes):
            score = round(score + 0.08, 6)
            if "scope_affinity" not in reasons:
                reasons.append("scope_affinity")
            if not any(
                str(signal.get("signal_name", "")) == "scope_affinity" for signal in signals if isinstance(signal, dict)
            ):
                signals.append({"signal_name": "scope_affinity", "score": 0.08})

        row["score"] = round(score, 6)
        row["reasons"] = reasons
        row["signals"] = signals
        adjusted.append(row)

    adjusted.sort(key=lambda item: (-float(item["score"]), str(item["path"])))
    pinned = [item for item in adjusted if _normalize_path_key(str(item.get("path", ""))) in changed_set]
    pinned.sort(
        key=lambda item: (
            changed_index.get(_normalize_path_key(str(item.get("path", ""))), 10**9),
            -float(item.get("score", 0.0)),
            str(item.get("path", "")),
        )
    )
    remaining = [item for item in adjusted if _normalize_path_key(str(item.get("path", ""))) not in changed_set]
    return pinned + remaining


def _build_verify_plan(
    config: dict[str, Any],
    top_files: list[dict[str, Any]],
    test_ownership_rows: list[dict[str, Any]],
    changed_files: list[str],
) -> dict[str, Any]:
    selected_files = {item["path"] for item in top_files}
    target_tests: list[str] = []
    seen_tests: set[str] = set()
    for row in test_ownership_rows:
        owns = str(row.get("owns_file", ""))
        test_file = str(row.get("test_file", ""))
        if owns in selected_files and test_file and test_file not in seen_tests:
            seen_tests.add(test_file)
            target_tests.append(test_file)

    verify_cfg = config.get("verify", {})
    extension_group_map = resolve_extension_verify_group_map(config)
    enabled_groups = resolve_enabled_verify_groups(config)
    if not enabled_groups:
        enabled_groups = [
            str(group).strip().lower() for group, commands in dict(verify_cfg).items() if isinstance(commands, list)
        ]

    changed = [item.lower() for item in changed_files]
    run_all = len(changed) == 0
    changed_groups: set[str] = set()
    for item in changed:
        suffix = Path(item).suffix
        if not suffix:
            continue
        group = str(extension_group_map.get(suffix, "")).strip().lower()
        if group:
            changed_groups.add(group)

    checks: list[str] = []
    for group, group_checks in dict(verify_cfg).items():
        group_name = str(group).strip().lower()
        if group_name not in enabled_groups:
            continue
        if not run_all and group_name not in changed_groups:
            continue
        if isinstance(group_checks, list):
            for command in group_checks:
                command_text = str(command).strip()
                if command_text and command_text not in checks:
                    checks.append(command_text)

    selection_evidence: dict[str, Any] = {
        "run_all": bool(run_all),
        "enabled_verify_groups": list(enabled_groups),
        "changed_verify_groups": sorted(changed_groups),
        "targeted_tests_count": int(min(20, len(target_tests))),
        "target_checks_count": len(checks),
        "layers": [
            {
                "layer": "safety_baseline",
                "reason": "run_all" if run_all else "changed_verify_groups_match",
                "commands": list(checks),
            },
            {
                "layer": "incremental_acceleration",
                "targeted_tests": list(target_tests[:20]),
            },
        ],
    }
    return {
        "target_tests": target_tests[:20],
        "target_checks": checks,
        "selection_evidence": selection_evidence,
    }


def _rank_candidate_files(
    *,
    config: dict[str, Any],
    manifest: dict[str, Any],
    changed_files: list[str],
    hints: list[str] | None,
    task: str,
    symbols_by_file: dict[str, list[dict[str, Any]]],
    references_by_file: dict[str, list[dict[str, Any]]],
    deps_by_file: dict[str, list[dict[str, Any]]],
    tests_by_file: dict[str, list[dict[str, Any]]],
) -> tuple[
    list[dict[str, Any]],
    list[str],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    task_tokens = normalize_task_tokens(task)
    changed_files = [item.replace("\\", "/") for item in changed_files]
    runtime_cfg = dict(config.get("runtime", {}))
    relation_enabled = bool(runtime_cfg.get("relation_ranking_enabled", True))
    relation_weight = max(
        0.0,
        min(1.0, float(runtime_cfg.get("relation_ranking_weight", 1.0))),
    )
    indexed_files = [str(item) for item in list(manifest.get("indexed_files", []))]
    candidate_files = build_candidate_files(
        indexed_files,
        changed_files=changed_files,
    )
    relation_graph: dict[str, dict[str, float]] = {}
    relation_seeds: list[str] = []
    if relation_enabled:
        relation_graph = build_semantic_relation_graph(
            symbols_by_file=symbols_by_file,
            references_by_file=references_by_file,
            deps_by_file=deps_by_file,
            indexed_files=indexed_files,
        )
        relation_seeds = collect_seed_files(
            changed_files=changed_files,
            hints=hints,
            candidate_files=candidate_files,
        )
        candidate_files = expand_candidates_with_relations(
            candidates=candidate_files,
            relation_graph=relation_graph,
            seed_files=relation_seeds,
            changed_files=changed_files,
        )
    changed_set = set(changed_files)
    ranked = [
        score_file(
            file_path=file_path,
            task_tokens=task_tokens,
            symbols_by_file=symbols_by_file,
            references_by_file=references_by_file,
            deps_by_file=deps_by_file,
            tests_by_file=tests_by_file,
            changed_file_set=changed_set,
            relation_graph=relation_graph,
            relation_seed_files=relation_seeds,
            relation_weight=relation_weight,
        )
        for file_path in candidate_files
    ]
    ranked.sort(key=lambda item: (-float(item["score"]), str(item["path"])))

    if hints:
        hints_low = [str(hint).lower() for hint in hints]
        for item in ranked:
            path_low = str(item.get("path", "")).lower()
            if any(hint in path_low for hint in hints_low):
                item["score"] = round(float(item["score"]) + 0.05, 6)
                item["reasons"] = list(dict.fromkeys([*list(item.get("reasons", [])), "hint_match"]))
        ranked.sort(key=lambda item: (-float(item["score"]), str(item["path"])))

    ranked, lexical_meta = apply_lexical_ranking(
        ranked=ranked,
        config=config,
        task=task,
        task_tokens=task_tokens,
        hints=hints,
        symbols_by_file=symbols_by_file,
        references_by_file=references_by_file,
        deps_by_file=deps_by_file,
        tests_by_file=tests_by_file,
    )

    ranked, semantic_meta = apply_semantic_ranking(
        ranked=ranked,
        config=config,
        task=task,
        task_tokens=task_tokens,
        hints=hints,
        symbols_by_file=symbols_by_file,
        references_by_file=references_by_file,
        deps_by_file=deps_by_file,
        tests_by_file=tests_by_file,
    )
    ranked = _apply_changed_scope_prioritization(ranked, changed_files)
    relation_meta = {
        "enabled": bool(relation_enabled),
        "weight": round(float(relation_weight), 4),
        "seed_files": relation_seeds[:20],
        "seed_count": len(relation_seeds),
        "graph": relation_graph_stats(relation_graph),
    }
    return ranked, task_tokens, lexical_meta, semantic_meta, relation_meta


def explain_context_selection(
    *,
    project_dir: Path,
    config: dict[str, Any],
    task: str,
    changed_files: list[str] | None = None,
    hints: list[str] | None = None,
    top_n_files: int | None = None,
    alternatives: int = 5,
) -> dict[str, Any]:
    changed_files = [item.replace("\\", "/") for item in (changed_files or [])]
    accel_home = Path(config["runtime"]["accel_home"]).resolve()
    paths = project_paths(accel_home, project_dir)
    index_dir = paths["index"]
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Index manifest is missing. Run `accel index build` first.")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except JSONDecodeError as e:
        raise ValueError(f"Index manifest is corrupted ({e}). Run `accel index build` to rebuild.") from e
    symbols_rows = load_index_rows(index_dir=index_dir, kind="symbols", key_field="file")
    references_rows = load_index_rows(index_dir=index_dir, kind="references", key_field="file")
    deps_rows = load_index_rows(index_dir=index_dir, kind="dependencies", key_field="edge_from")
    ownership_rows = load_index_rows(index_dir=index_dir, kind="test_ownership", key_field="owns_file")

    symbols_by_file = _group_by_file(symbols_rows, "file")
    references_by_file = _group_by_file(references_rows, "file")
    deps_by_file = _group_by_file(deps_rows, "edge_from")
    tests_by_file = _group_by_file(ownership_rows, "owns_file")

    ranked, task_tokens, lexical_meta, semantic_meta, relation_meta = _rank_candidate_files(
        config=config,
        manifest=manifest,
        changed_files=changed_files,
        hints=hints,
        task=task,
        symbols_by_file=symbols_by_file,
        references_by_file=references_by_file,
        deps_by_file=deps_by_file,
        tests_by_file=tests_by_file,
    )

    context_cfg = dict(config.get("context", {}))
    top_n = int(top_n_files if top_n_files is not None else context_cfg.get("top_n_files", 12))
    top_n = max(1, top_n)
    alternatives_count = max(0, int(alternatives))

    selected = ranked[:top_n]
    alternative_rows = ranked[top_n : top_n + alternatives_count]
    threshold_score = float(selected[-1]["score"]) if selected else 0.0

    alternatives_enriched: list[dict[str, Any]] = []
    for row in alternative_rows:
        gap = round(threshold_score - float(row.get("score", 0.0)), 6)
        alternatives_enriched.append(
            {
                **dict(row),
                "score_gap_to_threshold": gap,
            }
        )

    return {
        "version": 1,
        "schema_version": 1,
        "task": task,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "changed_files": changed_files,
        "hints": list(hints or []),
        "task_tokens": task_tokens,
        "top_n_files": top_n,
        "candidate_count": len(ranked),
        "selected_count": len(selected),
        "threshold_score": round(float(threshold_score), 6),
        "lexical_ranking": lexical_meta,
        "semantic_ranking": semantic_meta,
        "relation_ranking": relation_meta,
        "selected": selected,
        "alternatives": alternatives_enriched,
    }


def compile_context_pack(
    project_dir: Path,
    config: dict[str, Any],
    task: str,
    changed_files: list[str] | None = None,
    hints: list[str] | None = None,
    previous_attempt_feedback: dict[str, Any] | None = None,
    budget_override: dict[str, int] | None = None,
) -> dict[str, Any]:
    changed_files = [item.replace("\\", "/") for item in (changed_files or [])]
    accel_home = Path(config["runtime"]["accel_home"]).resolve()
    paths = project_paths(accel_home, project_dir)
    index_dir = paths["index"]
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("Index manifest is missing. Run `accel index build` first.")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except JSONDecodeError as e:
        raise ValueError(f"Index manifest is corrupted ({e}). Run `accel index build` to rebuild.") from e
    symbols_rows = load_index_rows(index_dir=index_dir, kind="symbols", key_field="file")
    references_rows = load_index_rows(index_dir=index_dir, kind="references", key_field="file")
    deps_rows = load_index_rows(index_dir=index_dir, kind="dependencies", key_field="edge_from")
    ownership_rows = load_index_rows(index_dir=index_dir, kind="test_ownership", key_field="owns_file")

    symbols_by_file = _group_by_file(symbols_rows, "file")
    references_by_file = _group_by_file(references_rows, "file")
    deps_by_file = _group_by_file(deps_rows, "edge_from")
    tests_by_file = _group_by_file(ownership_rows, "owns_file")

    context_cfg = dict(config.get("context", {}))
    runtime_cfg = dict(config.get("runtime", {}))

    ranked, task_tokens, lexical_meta, semantic_meta, relation_meta = _rank_candidate_files(
        config=config,
        manifest=manifest,
        changed_files=changed_files,
        hints=hints,
        task=task,
        symbols_by_file=symbols_by_file,
        references_by_file=references_by_file,
        deps_by_file=deps_by_file,
        tests_by_file=tests_by_file,
    )

    effective_budget, budget_strategy = resolve_adaptive_budget(
        context_cfg=context_cfg,
        runtime_cfg=runtime_cfg,
        task=task,
        task_tokens=task_tokens,
        changed_files=changed_files,
        ranked_files=ranked,
        budget_override=budget_override,
    )
    max_chars = int(effective_budget["max_chars"])
    max_snippets = int(effective_budget["max_snippets"])
    top_n_files = int(effective_budget["top_n_files"])
    snippet_radius = int(effective_budget["snippet_radius"])
    effective_file_window = max(1, min(max_snippets, top_n_files))
    computed_per_snippet_max_chars = max(
        800,
        min(6000, int((max_chars / effective_file_window) * 2)),
    )
    per_snippet_max_chars = max(
        800,
        min(
            6000,
            int(context_cfg.get("per_snippet_max_chars", computed_per_snippet_max_chars)),
        ),
    )

    top_files = ranked[:top_n_files]

    snippets: list[dict[str, Any]] = []
    current_chars = 0
    raw_snippet_chars = 0
    compacted_snippet_chars = 0
    deduped_snippet_count = 0
    similarity_deduped_count = 0
    low_signal_dropped_count = 0
    seen_fingerprints: set[str] = set()
    seen_structural_tokens: list[set[str]] = []
    seen_semantic_tokens: list[set[str]] = []
    compression_rule_counts: dict[str, int] = {}
    structural_threshold = max(
        0.0,
        min(
            1.0,
            float(runtime_cfg.get("snippet_dedup_structural_threshold", 0.92)),
        ),
    )
    semantic_threshold = max(
        0.0,
        min(
            1.0,
            float(runtime_cfg.get("snippet_dedup_semantic_threshold", 0.82)),
        ),
    )
    for top_item in top_files:
        file_path = top_item["path"]
        symbol_rows = symbols_by_file.get(file_path, [])
        snippet = extract_snippet(
            project_dir=project_dir,
            rel_path=file_path,
            task_tokens=task_tokens,
            symbol_rows=symbol_rows,
            snippet_radius=snippet_radius,
            max_chars=per_snippet_max_chars,
        )
        if snippet is None:
            continue
        raw_content = str(snippet.get("content", ""))
        compact_content, compression = compress_snippet_content(
            raw_content,
            max_chars=per_snippet_max_chars,
            task_tokens=task_tokens,
            symbol=str(snippet.get("symbol", "")),
            enable_rules=bool(config.get("runtime", {}).get("rule_compression_enabled", True)),
        )
        for rule_name, count in dict(compression.get("rules", {})).items():
            compression_rule_counts[rule_name] = int(compression_rule_counts.get(rule_name, 0)) + int(count)
        if bool(compression.get("dropped", False)):
            low_signal_dropped_count += 1
            continue
        if not compact_content.strip():
            continue
        snippet["content"] = compact_content

        fingerprint = _snippet_fingerprint(compact_content)
        if fingerprint in seen_fingerprints:
            deduped_snippet_count += 1
            continue

        structural_tokens = _semantic_token_set(_structural_signature(compact_content))
        semantic_tokens = _semantic_token_set(compact_content)
        is_similar = any(
            _jaccard_similarity(structural_tokens, existing_structural) >= structural_threshold
            or _jaccard_similarity(semantic_tokens, existing_semantic) >= semantic_threshold
            for existing_structural, existing_semantic in zip(
                seen_structural_tokens, seen_semantic_tokens, strict=False
            )
        )
        if is_similar:
            similarity_deduped_count += 1
            continue

        seen_fingerprints.add(fingerprint)
        seen_structural_tokens.append(structural_tokens)
        seen_semantic_tokens.append(semantic_tokens)

        raw_snippet_chars += len(raw_content)
        size = len(compact_content)
        compacted_snippet_chars += size
        if current_chars + size > max_chars:
            continue
        snippets.append(snippet)
        current_chars += size
        if len(snippets) >= max_snippets:
            break

    verify_plan = _build_verify_plan(config, top_files, ownership_rows, changed_files)
    counts = dict(manifest.get("counts", {}))
    source_bytes = int(counts.get("source_bytes", 0))
    source_chars_est = int(counts.get("source_chars_est", 0))
    if source_bytes <= 0:
        source_bytes = _estimate_source_bytes(project_dir, list(manifest.get("indexed_files", [])))
    if source_chars_est <= 0:
        source_chars_est = source_bytes

    budget = {
        "max_chars": max_chars,
        "max_snippets": max_snippets,
        "top_n_files": top_n_files,
        "snippet_radius": snippet_radius,
        "per_snippet_max_chars": per_snippet_max_chars,
    }

    pack: dict[str, Any] = {
        "version": 1,
        "schema_version": 1,
        "task": task,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "budget": budget,
        "top_files": top_files,
        "snippets": snippets,
        "verify_plan": verify_plan,
        "meta": {
            "task_tokens": task_tokens,
            "changed_files": changed_files,
            "source_bytes": source_bytes,
            "source_chars_est": source_chars_est,
            "snippet_raw_chars": raw_snippet_chars,
            "snippet_chars": compacted_snippet_chars,
            "snippet_saved_chars": max(0, raw_snippet_chars - compacted_snippet_chars),
            "snippet_deduped_count": deduped_snippet_count,
            "snippet_similarity_deduped_count": similarity_deduped_count,
            "snippet_low_signal_dropped_count": low_signal_dropped_count,
            "snippet_dedup_structural_threshold": structural_threshold,
            "snippet_dedup_semantic_threshold": semantic_threshold,
            "compression_rules_applied": compression_rule_counts,
            "compression_saved_chars": max(0, raw_snippet_chars - compacted_snippet_chars),
            "drift_reason": "",
            "budget_strategy": budget_strategy,
            "lexical_ranking": lexical_meta,
            "semantic_ranking": semantic_meta,
            "relation_ranking": relation_meta,
        },
    }
    if previous_attempt_feedback:
        pack["previous_attempt_feedback"] = previous_attempt_feedback
    return pack


def write_context_pack(output_path: Path, pack: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

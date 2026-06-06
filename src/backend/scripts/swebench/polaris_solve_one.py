#!/usr/bin/env python3
"""Single-instance Polaris solver for SWE-bench Phase A (host-only).

Asymmetric cost architecture (SWAPPED variant under test):

  1. ChiefEngineer = MID-CLOUD Kimi: localize the bug to a single file + root cause.
     The reasoning-heavy localization step runs on the stronger cloud model.
  2. Precise edit (Director) = LOCAL gemma-4-26B: draft an Aider SEARCH/REPLACE block
     whose SEARCH is copied verbatim from the exact current file content. The
     code-writing "grunt work" runs on the self-hosted GPU (≈ free at the margin).
  3. Apply through Polaris's OFFICIAL `edit_blocks` tool executor (read-before-edit +
     post-edit syntax gate). Phase B Task 2 root-caused the validator false-positive
     that used to force a bypass, so the official handler now lands valid edits
     natively; a deterministic apply remains only as a transparent fallback.
  4. QA (local, host-only): `python -m compileall`. No containers.

Token accounting: the edit model's usage is taken from the API response (authoritative);
the localization model's usage is estimated (the role facade does not surface usage). If
KERNELONE_TOKEN_LEDGER is set, one JSON line per instance is appended there.

Subprocess per instance; reads problem_statement from stdin; emits one-line JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

# Headless benchmark automation: run the Cognitive Runtime in SHADOW mode (observe/log,
# do NOT gate). The default MAINLINE mode hard-blocks low-confidence CE localizations on
# hard repos (raising cognitive_runtime_blocked) and would crash the role call; SHADOW
# keeps the runtime productionized/observing without gating legitimate automation.
os.environ.setdefault("KERNELONE_COGNITIVE_RUNTIME_MODE", "shadow")

from polaris.cells.llm.dialogue.internal.role_dialogue import generate_role_response

MAX_CONTENT_CHARS = 48000
MODEL_CTX_TOKENS = 32768  # local gemma served context window (input + output must fit)
EDIT_MAX_OUT_CAP = 8192  # never request more output tokens than this (model upper bound)
EDIT_OUT_MARGIN = 1024  # safety headroom kept under the context window
MIN_ANCHOR_LINES = 3  # a SEARCH block needs >= this many non-blank lines OR a def/class anchor
MAX_HYPOTHESES = 3  # cascade depth over the ranked candidate files
MAX_DRAFT_RETRIES = 1  # re-ask gemma once with corrective feedback on malformed/ambiguous blocks
MAX_REPAIR_ITERS = 2  # compile-feedback self-correction iterations per hypothesis


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (≈4 chars/token), matching the repo's chunk taxonomy."""
    return max(1, len(text) // 4)


def _as_int(value: object) -> int:
    """Coerce a telemetry dict value (statically typed ``object``) to int for arithmetic."""
    return value if isinstance(value, int) else 0


def _repo_relpaths(workspace: str, limit: int = 100000) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=workspace, capture_output=True, text=True, timeout=120, check=False)
    files = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip().endswith(".py")]
    return files[:limit]


_STOPWORDS = frozenset(
    [
        "the",
        "this",
        "that",
        "when",
        "which",
        "should",
        "would",
        "could",
        "there",
        "value",
        "error",
        "class",
        "object",
        "content",
        "true",
        "false",
        "none",
        "from",
        "with",
        "into",
        "your",
        "what",
        "return",
        "when",
        "while",
        "where",
        "because",
        "should",
        "about",
    ]
)


def _extract_identifiers(text: str, limit: int = 25) -> list[str]:
    """Pull likely code identifiers from an issue (CamelCase, backticked code, long snake_case)."""
    scores: dict[str, int] = {}
    for tok in re.findall(r"\b[A-Z][a-zA-Z0-9]{3,}\b", text):
        scores[tok] = scores.get(tok, 0) + 3
    for blk in re.findall(r"`([^`]+)`", text):
        for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", blk):
            scores[word] = scores.get(word, 0) + 3
    for tok in re.findall(r"\b[a-z_][a-z0-9_]{4,}\b", text):
        if tok not in _STOPWORDS:
            scores[tok] = scores.get(tok, 0) + 1
    return [k for k, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


def _ranked_candidates(workspace: str, problem: str) -> tuple[list[str], dict[str, object]]:
    """Rank repo files by relevance to the issue via RepoIntelligence (Aider-style repo map).

    This replaces the alphabetical-first-N file dump (which silently excluded the target
    file on large repos) with relevance-ranked candidates personalized by the identifiers
    mentioned in the issue. Returns (ranked_rel_paths, telemetry). Degrades to [] on ANY
    failure so the caller falls back to the flat list — localization must never crash.
    """
    tel: dict[str, object] = {"ranked": 0, "degraded": False}
    try:
        from polaris.kernelone.context.repo_intelligence.facade import (
            clear_repo_intelligence,
            get_repo_intelligence,
        )

        idents = _extract_identifiers(problem)
        clear_repo_intelligence(workspace)
        ri = get_repo_intelligence(workspace)
        ri.scan_repository(max_files=4000)
        repo_map = ri.get_repo_map(mentioned_idents=idents, max_files=20)
        # get_ranked_files populates RankedCandidate.fname (the rel path); rel_fname is
        # an empty default. Read fname, falling back to rel_fname for other rankers.
        ranked = [(getattr(c, "rel_fname", "") or getattr(c, "fname", "")) for c in repo_map.ranked_files]
        ranked = [r for r in ranked if r]
        tel["ranked"] = len(ranked)
        tel["idents"] = idents[:10]
        return ranked, tel
    except (ImportError, RuntimeError, ValueError, OSError, TypeError, AttributeError) as exc:
        tel["degraded"] = True
        tel["degraded_reason"] = f"{type(exc).__name__}: {exc}"[:160]
        return [], tel


CONTENT_FALLBACK_MIN_RANKED = 8


def _is_test_path(path: str) -> bool:
    """True for test files/dirs — the solver must edit source, not tests."""
    parts = path.replace("\\", "/").lower().split("/")
    base = parts[-1] if parts else ""
    if any(p in ("test", "tests") for p in parts[:-1]):
        return True
    return base.startswith("test_") or base.endswith("_test.py")


def _content_ranked_candidates(workspace: str, idents: list[str], limit: int = 20) -> list[str]:
    """Lexical content retrieval (BM25-style, offline via ``git grep``).

    Ranks ``.py`` files by how many issue identifiers occur in their CONTENT — not just in
    their top-level symbol names. This complements the symbol/PageRank ranker for issues
    that mention no symbol DEFINED in the target file (the dominant remaining miss mode):
    a dense/embedding retriever is the ideal tool here, but no embedding backend is
    provisioned in this environment (``get_default_embedding_port`` is unset and no
    embedding model is served), so we use exact lexical co-occurrence — deterministic,
    dependency-free, and still able to surface content-correlated files. Never raises:
    degrades to ``[]`` so localization keeps working.
    """
    scored: dict[str, float] = {}
    for term in idents[:15]:
        if len(term) < 4:
            continue
        try:
            out = subprocess.run(
                ["git", "grep", "-I", "-c", "-F", "-e", term, "--", "*.py"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in (out.stdout or "").splitlines():
            path, _, cnt = line.rpartition(":")
            if not path or _is_test_path(path):
                continue
            try:
                count = int(cnt)
            except ValueError:
                continue
            # +1 for presence (rewards term diversity) + damped frequency (caps big files).
            scored[path] = scored.get(path, 0.0) + 1.0 + float(min(count, 5))
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    return [p for p, _ in ranked[:limit]]


def _parse_target(ce_text: str, candidates: list[str]) -> str:
    match = re.search(r"FILE:\s*([^\n`]+)", ce_text)
    if match:
        cand = match.group(1).strip().strip("`").strip()
        if cand in candidates:
            return cand
        for filepath in candidates:
            if filepath.endswith(cand) or cand.endswith(filepath):
                return filepath
    for filepath in candidates:
        if filepath in ce_text:
            return filepath
    return ""


def _git_has_changes(workspace: str) -> bool:
    return subprocess.run(["git", "diff", "--quiet"], cwd=workspace, check=False).returncode != 0


def _cloud_complete(prompt: str, provider_id: str, max_tokens: int = 4000) -> tuple[str, dict[str, int]]:
    """Call the mid-cloud edit model; return (text, usage) with REAL token counts."""
    import httpx

    cfg_path = os.path.expanduser("~/.polaris/config/llm/llm_config.json")
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    provider = cfg["providers"][provider_id]
    base = str(provider["base_url"]).rstrip("/")
    resp = httpx.post(
        f"{base}/v1/messages",
        headers={
            "x-api-key": str(provider["api_key"]),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": provider["model"],
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=300,
    )
    body = resp.json()
    if not isinstance(body, dict):
        return "", {"input_tokens": 0, "output_tokens": 0}
    text = "".join(part.get("text", "") for part in body.get("content", []) if isinstance(part, dict))
    raw_usage = body.get("usage") or {}
    usage = {
        "input_tokens": int(raw_usage.get("input_tokens", 0) or 0),
        "output_tokens": int(raw_usage.get("output_tokens", 0) or 0),
    }
    return text, usage


def _openai_complete(
    prompt: str, provider: dict[str, object], max_tokens: int, temperature: float
) -> tuple[str, dict[str, int]]:
    """Call an OpenAI-compatible chat endpoint (e.g. the local vLLM gemma server)."""
    import httpx

    base = str(provider["base_url"]).rstrip("/")
    resp = httpx.post(
        f"{base}/chat/completions",
        headers={
            "Authorization": f"Bearer {provider.get('api_key') or 'x'}",
            "content-type": "application/json",
        },
        json={
            "model": str(provider["model"]),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=300,
    )
    body = resp.json()
    if not isinstance(body, dict):
        return "", {"input_tokens": 0, "output_tokens": 0}
    choices = body.get("choices") or []
    text = ""
    if choices and isinstance(choices[0], dict):
        text = str((choices[0].get("message") or {}).get("content") or "")
    raw_usage = body.get("usage") or {}
    usage = {
        "input_tokens": int(raw_usage.get("prompt_tokens", 0) or 0),
        "output_tokens": int(raw_usage.get("completion_tokens", 0) or 0),
    }
    return text, usage


def _complete_for_role(
    role: str, prompt: str, max_tokens: int = 4000, temperature: float = 0.1
) -> tuple[str, dict[str, int]]:
    """Complete `prompt` with the model BOUND TO `role` in llm_config (config-driven).

    Honors the role->provider binding and dispatches on provider type
    (anthropic_compat -> /v1/messages, openai_compat -> /v1/chat/completions). This is how
    the swapped architecture routes the edit step to whatever model `director` is bound to
    (now the local gemma) without dragging in the Director role's tool-calling system
    prompt, which would pollute raw SEARCH/REPLACE output.
    """
    cfg_path = os.path.expanduser("~/.polaris/config/llm/llm_config.json")
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    provider_id = str(cfg["roles"][role]["provider_id"])
    provider = cfg["providers"][provider_id]
    if str(provider.get("type")) == "anthropic_compat":
        return _cloud_complete(prompt, provider_id, max_tokens)
    return _openai_complete(prompt, provider, max_tokens, temperature)


def _apply_via_handler(workspace: str, target: str, blocks_text: str) -> tuple[bool, str] | None:
    """Apply through Polaris's OFFICIAL edit_blocks executor (read-before-edit + gate).

    Returns (applied, detail) when the official path was exercised, or None when the
    executor could not be used at all (caller then falls back to deterministic apply).
    """
    try:
        from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor

        ex = AgentAccelToolExecutor(workspace=workspace, worker_id="swebench")
        read = ex.execute("read_file", {"file": target})
        if not read.get("ok"):
            return None
        res = ex.execute("edit_blocks", {"file": target, "blocks": blocks_text})
        if res.get("ok"):
            applied = int((res.get("result") or {}).get("blocks_applied", 0) or 0)
            return (applied > 0), ("" if applied else "handler applied 0 blocks")
        return False, f"handler rejected: {str(res.get('error'))[:160]}"
    except (ImportError, RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
        _ = exc
        return None


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _indent_tolerant_replace(content: str, search: str, replace: str) -> tuple[str, bool]:
    """Whitespace/indent-tolerant SEARCH match (Advanced Normalization).

    The common local-model failure is correct lines with slightly-off leading whitespace. We
    match SEARCH against a contiguous source window comparing STRIPPED lines, then re-indent
    REPLACE to the matched block's actual indentation. Returns (new_content, applied).
    """
    src = content.splitlines(keepends=True)
    s_raw = search.splitlines()
    while s_raw and not s_raw[0].strip():
        s_raw = s_raw[1:]
    while s_raw and not s_raw[-1].strip():
        s_raw = s_raw[:-1]
    if not s_raw:
        return content, False
    s_key = [ln.strip() for ln in s_raw]
    n = len(s_raw)
    if n > len(src):
        return content, False
    for i in range(0, len(src) - n + 1):
        window = src[i : i + n]
        if [ln.strip() for ln in window] != s_key:
            continue
        src_indent = _leading_ws(window[0])
        search_indent = _leading_ws(s_raw[0])
        out_lines: list[str] = []
        for rl in replace.splitlines():
            if not rl.strip():
                out_lines.append("")
                continue
            stripped = rl[len(search_indent) :] if rl.startswith(search_indent) else rl.lstrip()
            out_lines.append(src_indent + stripped)
        block = "\n".join(out_lines)
        if window[-1].endswith("\n"):
            block += "\n"
        return "".join(src[:i]) + block + "".join(src[i + n :]), True
    return content, False


def _apply_direct(workspace: str, target: str, blocks_text: str) -> tuple[bool, str]:
    """Deterministic fallback: parse + (exact|fuzzy|indent-tolerant) replace, then write."""
    try:
        from polaris.kernelone.editing.editblock_engine import parse_edit_blocks
        from polaris.kernelone.tool_execution.suggestions.precise_matcher import fuzzy_replace

        blocks = parse_edit_blocks(blocks_text, default_filepath=target)
        if not blocks:
            return False, "no parseable SEARCH/REPLACE blocks in model output"
        applied = 0
        for block in blocks:
            rel = block.filepath or target
            path = os.path.join(workspace, rel)
            if not os.path.isfile(path):
                continue
            search = block.search_text or ""
            replace = block.replace_text or ""
            if not search or search == replace:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                current = fh.read()
            if search in current:
                new_content = current.replace(search, replace, 1)
            else:
                new_content, meta = fuzzy_replace(current, search, replace)
                if not (isinstance(meta, dict) and meta.get("success")):
                    # Final fallback: indent/whitespace-tolerant alignment (forces 落盘 on
                    # minor leading-whitespace deviations the exact + fuzzy paths reject).
                    new_content, ok = _indent_tolerant_replace(current, search, replace)
                    if not ok:
                        continue
            if new_content != current:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new_content)
                applied += 1
        return (applied > 0), ("" if applied else "no blocks applied (search not found / no-op)")
    except (RuntimeError, ValueError, OSError, ImportError, TypeError, KeyError) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:240]


def _parse_blocks_safe(blocks_text: str, default_target: str) -> list[object]:
    """Parse SEARCH/REPLACE blocks via the canonical engine; [] on any failure."""
    try:
        from polaris.kernelone.editing.editblock_engine import parse_edit_blocks

        return list(parse_edit_blocks(blocks_text, default_filepath=default_target))
    except (RuntimeError, ValueError, OSError, ImportError, TypeError, KeyError, AttributeError):
        return []


def _distinct_block_files(blocks_text: str, default_target: str) -> list[str]:
    """Distinct file paths targeted by the blocks (for multi-file routing, B6)."""
    seen: dict[str, None] = {}
    for b in _parse_blocks_safe(blocks_text, default_target):
        seen.setdefault(str(getattr(b, "filepath", "") or default_target), None)
    return list(seen)


def _apply_blocks(workspace: str, target: str, blocks_text: str) -> tuple[bool, str, str]:
    """Apply edit blocks. Multi-file drafts route to the per-file direct applier; single-file
    drafts try the OFFICIAL handler first, then fall back to direct (fuzzy) apply.

    Returns (applied, detail, path) where path is "handler", "fallback", or "none".
    """
    files = _distinct_block_files(blocks_text, target)
    if len(files) > 1:
        # The official handler is single-file ({file: target}); a multi-file fix goes through
        # the direct applier, which honors each block's own SEARCH:<file> header.
        applied, detail = _apply_direct(workspace, target, blocks_text)
        return applied, (detail or f"multi-file:{len(files)}"), ("fallback" if applied else "none")
    handler = _apply_via_handler(workspace, target, blocks_text)
    if handler is not None and handler[0]:
        return handler[0], handler[1], "handler"
    direct_applied, direct_detail = _apply_direct(workspace, target, blocks_text)
    if direct_applied:
        note = "" if handler is None else f"(handler: {handler[1]}) "
        return True, f"{note}{direct_detail}".strip(), "fallback"
    detail = direct_detail if handler is None else f"handler[{handler[1]}] direct[{direct_detail}]"
    return False, detail, "none"


def _context_budget_max_tokens(prompt: str) -> int:
    """Max output tokens that respect the model context window: input + max_tokens <= ctx.

    The local gemma serves a 32768-token window; requesting output that would overflow it
    truncates the draft mid-block (the root cause of the sphinx-8474 / django-17087 misses).
    """
    est_input = _estimate_tokens(prompt)
    budget = MODEL_CTX_TOKENS - est_input - EDIT_OUT_MARGIN
    return max(512, min(EDIT_MAX_OUT_CAP, budget))


def _diagnose_blocks(draft: str, target: str, content: str) -> tuple[bool, str]:
    """Validate SEARCH/REPLACE blocks BEFORE applying. Returns (ok, reason).

    ok == at least one block is cleanly applicable to ``target`` (found exactly once,
    non-noop, with a non-thin anchor). ``reason`` is a short machine string reused as
    corrective feedback on retry (block-closure + anchor-density gates: B3/B5).
    """
    if "SEARCH" in draft and ">>>> REPLACE" not in draft:
        return False, "truncated"
    blocks = _parse_blocks_safe(draft, target)
    if not blocks:
        return False, "no_blocks"
    clean = 0
    issues: list[str] = []
    for b in blocks:
        search = str(getattr(b, "search_text", "") or "")
        replace = str(getattr(b, "replace_text", "") or "")
        fpath = str(getattr(b, "filepath", "") or target)
        if not search.strip():
            issues.append("noop")
            continue
        if search == replace:
            issues.append("noop")
            continue
        nonblank = [ln for ln in search.splitlines() if ln.strip()]
        has_def = any(re.match(r"\s*(def |class |@|async def )", ln) for ln in search.splitlines())
        thin = len(nonblank) < MIN_ANCHOR_LINES and not has_def
        if fpath == target and content:
            occ = content.count(search)
            if occ == 0:
                issues.append("not_found")
                continue
            if occ > 1:
                issues.append("ambiguous")
                continue
        if thin:
            issues.append("thin_anchor")
            continue
        clean += 1
    if clean > 0:
        return True, ""
    return False, ":".join(dict.fromkeys(issues)) or "no_clean_block"


_RETRY_HINTS = {
    "truncated": "Your previous output was CUT OFF. Emit FEWER, SHORTER blocks and make sure EVERY block ends with the literal `>>>> REPLACE` line.",
    "ambiguous": "A SEARCH block matched MULTIPLE locations. Include MORE surrounding context (>=5 lines, ideally a full def/class header) so each SEARCH is UNIQUE.",
    "thin_anchor": "SEARCH blocks were too short. Each SEARCH must contain >=5 lines OR a full def/class line so it anchors uniquely.",
    "not_found": "A SEARCH block did not match the file. Copy the SEARCH text CHARACTER-FOR-CHARACTER (exact indentation/whitespace) from the CONTENT shown.",
    "noop": "A block's REPLACE was identical to (or as empty as) its SEARCH. Make the actual fix so REPLACE differs.",
    "no_blocks": "You produced no valid blocks. Output ONLY SEARCH/REPLACE blocks in the exact format.",
}


def _edit_prompt(target: str, content: str, problem: str, ce_text: str, extra: str = "") -> str:
    """Build the edit-draft prompt with the anchor-density rule (B5) baked in."""
    return (
        "You are fixing a real bug. Output ONLY Aider SEARCH/REPLACE edit block(s), no prose.\n"
        "Format each block EXACTLY as:\n"
        f"<<<< SEARCH:{target}\n<lines copied VERBATIM from CONTENT below>\n====\n<fixed lines>\n>>>> REPLACE\n\n"
        "Hard rules:\n"
        "- SEARCH text MUST be copied character-for-character (exact indentation) from CONTENT.\n"
        "- Each SEARCH block MUST be UNIQUE: include >=5 lines of context OR a full def/class\n"
        "  header so it matches exactly one location.\n"
        "- REPLACE MUST differ from SEARCH (make the actual fix; never output an identical block).\n"
        "- Keep blocks minimal and CLOSED (every block ends with `>>>> REPLACE`); do not touch tests.\n"
        "- If the fix spans multiple files, emit a separate block per file with its own SEARCH:<path> header.\n"
        f"{extra}"
        f"\nISSUE:\n{problem}\n\n"
        f"ROOT CAUSE (analysis):\n{ce_text}\n\n"
        f"CONTENT of {target}:\n```\n{content[:MAX_CONTENT_CHARS]}\n```\n"
    )


def _draft_with_validation(target: str, content: str, problem: str, ce_text: str) -> tuple[str, int, int, str]:
    """Draft an edit via gemma, validate blocks, retry once with corrective feedback (B2/B3/B5).

    Returns (best_draft, edit_in, edit_out, last_reason). best_draft is the first validated
    draft, else the last draft produced (so the applier's fuzzy match still gets a shot).
    """
    edit_in = 0
    edit_out = 0
    extra = ""
    last_draft = ""
    last_reason = ""
    for _attempt in range(MAX_DRAFT_RETRIES + 1):
        prompt = _edit_prompt(target, content, problem, ce_text, extra=extra)
        draft, usage = _complete_for_role("director", prompt, max_tokens=_context_budget_max_tokens(prompt))
        edit_in += int(usage.get("input_tokens", 0) or 0)
        edit_out += int(usage.get("output_tokens", 0) or 0)
        last_draft = draft
        ok, reason = _diagnose_blocks(draft, target, content)
        last_reason = reason
        if ok:
            return draft, edit_in, edit_out, ""
        hint = ""
        for token in reason.split(":"):
            if token in _RETRY_HINTS:
                hint = _RETRY_HINTS[token]
                break
        extra = f"- CORRECTION: {hint}\n" if hint else ""
    return last_draft, edit_in, edit_out, last_reason


def _compile_check(workspace: str, target: str) -> tuple[int, str]:
    """Compile the target with py_compile; return (rc, error_text)."""
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", target],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, (proc.stderr or proc.stdout or "")[-1200:]


def _revert_workspace(workspace: str) -> None:
    """Revert all tracked changes so a failed hypothesis leaves no residue for the next."""
    subprocess.run(["git", "checkout", "--", "."], cwd=workspace, capture_output=True, text=True, check=False)


def _blueprint_for_file(target: str, content: str, problem: str, ce_text: str) -> tuple[str, int, int]:
    """Strong-model (Kimi) DETAILED, code-level fix spec for ``target`` so the weak local model
    only has to TRANSCRIBE it into SEARCH/REPLACE.

    This is the quality lever (per the architecture thesis): a precise spec lets a 26B write a
    *correct* edit it could not design from a 2-3 sentence approach. If even a detailed blueprint
    does not yield a correct edit, the defect is architectural — investigate, don't paper over.
    Returns (blueprint_text, cloud_in, cloud_out); ("", 0, 0) on failure (caller falls back).
    """
    prompt = (
        f"You are the Chief Engineer. Produce a FOOLPROOF, line-level fix blueprint for `{target}` "
        "that a fast local code-writer can apply MECHANICALLY with ZERO design decisions left to it.\n\n"
        "OUTPUT (numbered, code-level — NOT prose):\n"
        "1. TARGET SYMBOL: the exact function/method/class to change (by name).\n"
        "2. ANCHOR: quote 5-8 consecutive CURRENT lines verbatim (exact indentation) that locate the\n"
        "   edit UNIQUELY in the file.\n"
        "3. BEFORE: the exact current line(s) to replace, copied verbatim from CONTENT.\n"
        "4. AFTER: the exact replacement line(s), fully written out with correct indentation.\n"
        "5. EDGE CASES: every code path / input the fix must cover.\n\n"
        "GUARDRAILS the writer MUST obey (call out the ones that apply to THIS fix):\n"
        "- Preserve EXACT indentation (spaces vs tabs); Python is whitespace-sensitive.\n"
        "- Watch trailing commas — they change tuple/call semantics; match the file's existing style.\n"
        "- Regex: prefer `\\A`/`\\Z` over `^`/`$` for whole-string matches (MULTILINE pitfalls).\n"
        "- Keep imports and signatures consistent; never rename unrelated symbols.\n"
        "- Closing brackets/parentheses and continuation lines must stay balanced.\n"
        "- Do NOT emit SEARCH/REPLACE markers here — only the numbered spec.\n\n"
        f"ISSUE:\n{problem}\n\n"
        f"LOCALIZATION ANALYSIS:\n{ce_text}\n\n"
        f"CURRENT CONTENT of {target}:\n```\n{content[:MAX_CONTENT_CHARS]}\n```\n"
    )
    try:
        text, usage = _complete_for_role("chief_engineer", prompt, max_tokens=3072)
    except (RuntimeError, ValueError, OSError, KeyError, TypeError):
        return "", 0, 0
    return text, int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def _attempt_fix(workspace: str, target: str, problem: str, ce_text: str) -> dict[str, object]:
    """One hypothesis: draft (validate+retry) -> apply -> compile-repair loop (B1/B4).

    Returns telemetry incl. applied / compile_rc / repair_iters / edit token usage. The caller
    reverts the workspace when this hypothesis is not adopted.
    """
    res: dict[str, object] = {
        "target": target,
        "applied": False,
        "apply_path": "none",
        "compile_rc": None,
        "repair_iters": 0,
        "reason": "",
        "bp_in": 0,
        "bp_out": 0,
        "edit_in": 0,
        "edit_out": 0,
    }
    path = os.path.join(workspace, target)
    if not os.path.isfile(path):
        res["reason"] = "target_missing"
        return res
    with open(path, encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    # Strong-model blueprint (Kimi) -> precise spec; weak local model (gemma) just transcribes it.
    blueprint, bp_in, bp_out = _blueprint_for_file(target, content, problem, ce_text)
    res["bp_in"], res["bp_out"] = bp_in, bp_out
    spec = blueprint.strip() or ce_text
    draft, edit_in, edit_out, reason = _draft_with_validation(target, content, problem, spec)
    res["edit_in"], res["edit_out"], res["reason"] = edit_in, edit_out, reason
    applied, apply_err, apply_path = _apply_blocks(workspace, target, draft)
    res["applied"], res["apply_path"] = applied, apply_path
    if not applied:
        res["reason"] = reason or apply_err
        return res
    rc, errtext = _compile_check(workspace, target)
    res["compile_rc"] = rc
    iters = 0
    while rc != 0 and iters < MAX_REPAIR_ITERS:
        iters += 1
        with open(path, encoding="utf-8", errors="replace") as fh:
            broken = fh.read()
        repair_prompt = (
            "The edit below introduced a SYNTAX error. Fix ONLY the syntax (keep the intended logic).\n"
            "Output ONLY Aider SEARCH/REPLACE block(s) against the file; SEARCH copied verbatim.\n\n"
            f"COMPILER ERROR:\n{errtext}\n\n"
            f"CURRENT CONTENT of {target}:\n```\n{broken[:MAX_CONTENT_CHARS]}\n```\n"
        )
        rdraft, rusage = _complete_for_role(
            "director", repair_prompt, max_tokens=_context_budget_max_tokens(repair_prompt), temperature=0.0
        )
        res["edit_in"] = _as_int(res["edit_in"]) + int(rusage.get("input_tokens", 0) or 0)
        res["edit_out"] = _as_int(res["edit_out"]) + int(rusage.get("output_tokens", 0) or 0)
        rapplied, _, _ = _apply_blocks(workspace, target, rdraft)
        if not rapplied:
            break
        rc, errtext = _compile_check(workspace, target)
        res["compile_rc"] = rc
    res["repair_iters"] = iters
    return res


async def solve(workspace: str, instance_id: str, problem_statement: str) -> dict[str, object]:
    candidates = _repo_relpaths(workspace)
    # Relevance-ranked candidates (RepoIntelligence repo map) replace the alphabetical
    # first-200 dump that silently excluded the target file on large repos. Lead with
    # the ranked set; backfill from the flat list only for breadth.
    ranked, loc_tel = _ranked_candidates(workspace, problem_statement)
    # Degraded/low-confidence branch (telemetry-driven, per the runtime telemetry refactor):
    # when the symbol ranker crashed or surfaced few candidates, enrich with lexical content
    # retrieval so a content-correlated target is forced into the window even if it defines
    # none of the issue's named symbols.
    content_ranked: list[str] = []
    if bool(loc_tel.get("degraded")) or len(ranked) < CONTENT_FALLBACK_MIN_RANKED:
        idents = _extract_identifiers(problem_statement)
        content_ranked = _content_ranked_candidates(workspace, idents)
        loc_tel["content_ranked"] = len(content_ranked)
        loc_tel["content_fallback_used"] = True
    ranked_set = set(ranked)
    merged = ranked + [c for c in content_ranked if c not in ranked_set]
    merged_set = set(merged)
    shown = merged + [c for c in candidates if c not in merged_set]
    file_hint = "\n".join(shown[:60])

    localize_msg = (
        f"A bug report for the repository at this workspace:\n\n{problem_statement}\n\n"
        "Candidate source files (ordered by relevance to the issue):\n"
        f"{file_hint}\n\n"
        "Identify the SINGLE source file most likely to need the fix and the concise root cause. "
        "Prefer the highest-relevance candidate that matches the issue. "
        "Reply EXACTLY in this format:\nFILE: <relative/path.py>\nAPPROACH: <2-3 sentences>"
    )
    ce = await generate_role_response(workspace=workspace, settings=None, role="chief_engineer", message=localize_msg)
    ce_text = str(ce.get("response") or "")
    # Match against the FULL file set so a ranked target beyond the shown window resolves.
    target = _parse_target(ce_text, candidates)
    # Empty-target interception: if the model named nothing usable, take the top merged
    # candidate (symbol-ranked first, then lexical content fallback).
    if not target and merged:
        target = merged[0]

    # Localization (Kimi via the role facade) usage is estimated — the facade does not
    # surface API usage; the edit model (local gemma) reports authoritative usage below.
    localize_in_est = _estimate_tokens(localize_msg)
    localize_out_est = _estimate_tokens(ce_text)
    edit_in = 0
    edit_out = 0
    bp_in = 0
    bp_out = 0

    # Cascading hypothesis list (B1): parsed target first, then the ranked candidates. Each is
    # a real source file; we try them in order until one APPLIES and COMPILES (no silent abort).
    hypotheses: list[str] = []
    for cand in ([target] if target else []) + merged:
        if (
            cand
            and cand not in hypotheses
            and not _is_test_path(cand)
            and os.path.isfile(os.path.join(workspace, cand))
        ):
            hypotheses.append(cand)
        if len(hypotheses) >= MAX_HYPOTHESES:
            break

    attempts: list[dict[str, object]] = []
    applied = False
    apply_path = "none"
    apply_err = "no_hypothesis" if not hypotheses else "all_hypotheses_failed"
    compile_rc: int | None = None
    repair_iters = 0
    adopted = target or (hypotheses[0] if hypotheses else "")
    for cand in hypotheses:
        res = _attempt_fix(workspace, cand, problem_statement, ce_text)
        edit_in += _as_int(res.get("edit_in"))
        edit_out += _as_int(res.get("edit_out"))
        bp_in += _as_int(res.get("bp_in"))
        bp_out += _as_int(res.get("bp_out"))
        attempts.append(
            {k: res.get(k) for k in ("target", "applied", "apply_path", "compile_rc", "repair_iters", "reason")}
        )
        # Adopt only a hypothesis that applied, compiles cleanly, AND actually changed the tree
        # (fixes B4 "unchanged file compiles -> false green").
        if bool(res.get("applied")) and res.get("compile_rc") == 0 and _git_has_changes(workspace):
            adopted = cand
            applied = True
            apply_path = str(res.get("apply_path") or "fallback")
            apply_err = ""
            compile_rc = 0
            repair_iters = _as_int(res.get("repair_iters"))
            break
        # Hypothesis rejected — revert any residue before cascading to the next candidate.
        _revert_workspace(workspace)
        apply_err = str(res.get("reason") or apply_err)

    target = adopted
    has_diff = _git_has_changes(workspace)
    loc_tel["hypotheses"] = hypotheses
    loc_tel["attempts"] = attempts

    result: dict[str, object] = {
        "instance_id": instance_id,
        "target": target,
        "applied": applied,
        "apply_err": apply_err,
        "apply_path": apply_path,
        "has_diff": has_diff,
        "qa_compileall_rc": compile_rc,
        "repair_iters": repair_iters,
        "hypotheses_tried": len(attempts),
        "ce_head": ce_text[:160],
        "localization": loc_tel,
        "tokens": {
            "localize_model": "kimi-for-coding",
            "localize_in_est": localize_in_est,
            "localize_out_est": localize_out_est,
            "blueprint_model": "kimi-for-coding",
            "blueprint_in": bp_in,
            "blueprint_out": bp_out,
            "edit_model": "gemma-4-26b",
            "edit_in": edit_in,
            "edit_out": edit_out,
        },
    }

    ledger = os.environ.get("KERNELONE_TOKEN_LEDGER")
    if ledger:
        try:
            with open(ledger, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "instance_id": instance_id,
                            "target": target,
                            "applied": applied,
                            "apply_path": apply_path,
                            "qa_compile_rc": compile_rc,
                            "repair_iters": repair_iters,
                            "hypotheses_tried": len(attempts),
                            "loc_ranked": loc_tel.get("ranked"),
                            "loc_degraded": loc_tel.get("degraded"),
                            "arch": "ce=kimi-localize,director=gemma-edit",
                            "localize_model": "kimi-for-coding",
                            "edit_model": "gemma-4-26b",
                            "localize_in_est": localize_in_est,
                            "localize_out_est": localize_out_est,
                            "blueprint_in": bp_in,
                            "blueprint_out": bp_out,
                            "edit_in": edit_in,
                            "edit_out": edit_out,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            pass

    return result


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: polaris_solve_one.py <workspace> <instance_id>"}))
        return 2
    workspace, instance_id = sys.argv[1], sys.argv[2]
    problem_statement = sys.stdin.read()
    result = asyncio.run(solve(workspace, instance_id, problem_statement))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

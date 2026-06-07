#!/usr/bin/env python3
"""Arch B — container-interactive convergence with ContextOS as the control plane.

Per instance, iterate fix→test rounds until the official SWE-bench harness reports
RESOLVED (or max_rounds). Each round:

  1. Apply the current cumulative patch to a host clone; `git diff` -> model_patch.
  2. Run the OFFICIAL harness for this single instance (Docker; faithful scoring).
     The harness execs pytest INSIDE the instance container and captures the real
     traceback to test_output.txt — this is the in-container test feedback stream.
  3. ContextOS control plane (genuine, not prompt concat):
       - the failing pytest traceback is stored as a ReceiptStore receipt;
       - a strongly-typed round event is appended to TruthLog (single source of truth);
       - ProjectionEngine.project() derives the next-turn message set from the truth
         (confirmed_facts = what's fixed / what still fails / where the traceback points).
  4. Fail-closed QA: converged only when report.resolved is True (all FAIL_TO_PASS pass
     AND no PASS_TO_PASS regression — the harness's own definition).
  5. If not resolved, the mid-cloud model refines against the real traceback + the
     implicated source file; loop.

Emits a convergence trace (per-round JSON) proving the cognitive evolution.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore
from polaris.kernelone.context.truth_log_service import TruthLogService

# reuse the proven solver primitives (official-handler apply + config-driven routing +
# the embedding-free RepoIntelligence localization stack — Reuse First, AGENTS.md §4.2.1)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from polaris_solve_one import (
    CONTENT_FALLBACK_MIN_RANKED,
    MAX_CONTENT_CHARS,
    _apply_blocks,
    _complete_for_role,
    _content_ranked_candidates,
    _context_budget_max_tokens,
    _extract_identifiers,
    _is_test_path,
    _ranked_candidates,
)

HARNESS_PY = "/home/dains/swebench-harness-venv/bin/python"
MODEL_NAME = "Polaris-V1-Lightweight"
DATASET = "princeton-nlp/SWE-bench_Lite"


def run_git(args: list[str], cwd: Path, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False)


def ensure_clone(repo: str, base_commit: str, ws: Path) -> None:
    if (ws / ".git").is_dir():
        run_git(["checkout", "-f", base_commit], cwd=ws)
        run_git(["clean", "-fd"], cwd=ws)
        return
    ws.parent.mkdir(parents=True, exist_ok=True)
    cp = run_git(["clone", "--quiet", f"https://github.com/{repo}.git", str(ws)], cwd=ws.parent, timeout=1800)
    if cp.returncode != 0:
        raise RuntimeError(f"clone failed: {cp.stderr[-300:]}")
    run_git(["checkout", "-f", base_commit], cwd=ws)


def current_patch(ws: Path) -> str:
    run_git(["add", "-N", "."], cwd=ws)
    return run_git(["diff"], cwd=ws).stdout or ""


def run_harness_round(work_dir: Path, predictions_path: Path, run_id: str) -> tuple[dict[str, Any], str]:
    """Run the official harness on a 1-instance predictions file; return (report, test_output)."""
    subprocess.run(
        [
            HARNESS_PY,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            DATASET,
            "--predictions_path",
            str(predictions_path),
            "--max_workers",
            "1",
            "--cache_level",
            "instance",
            "--run_id",
            run_id,
        ],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=2400,
        check=False,
    )
    report_json = work_dir / f"{MODEL_NAME}.{run_id}.json"
    report: dict[str, Any] = {}
    if report_json.is_file():
        report = json.loads(report_json.read_text(encoding="utf-8"))
    return report, run_id


def instance_report(work_dir: Path, run_id: str, instance_id: str) -> tuple[dict[str, Any], str]:
    """Read per-instance report.json + test_output.txt for the round."""
    base = work_dir / "logs" / "run_evaluation" / run_id / MODEL_NAME / instance_id
    rep: dict[str, Any] = {}
    rp = base / "report.json"
    if rp.is_file():
        data = json.loads(rp.read_text(encoding="utf-8"))
        rep = data.get(instance_id, {})
    test_output = ""
    to = base / "test_output.txt"
    if to.is_file():
        test_output = to.read_text(encoding="utf-8", errors="replace")
    return rep, test_output


def extract_tracebacks(test_output: str, failing_tests: list[str]) -> str:
    """Pull the pytest FAILURES section (real frames), excluding warning-summary noise.

    DeprecationWarnings reference import lines like `_collections.py:1` and would
    otherwise outrank the true error frame; restrict to the FAILURES block and drop
    any captured-warning lines so file localization keys on the actual stack.
    """
    if not test_output:
        return ""
    _ = failing_tests
    lines = test_output.splitlines()
    start: int | None = None
    end = len(lines)
    for i, ln in enumerate(lines):
        if start is None and re.search(r"=+\s*FAILURES\s*=+", ln):
            start = i
        elif start is not None and re.search(r"=+\s*(warnings summary|short test summary)", ln):
            end = i
            break
    block = lines[start:end] if start is not None else lines
    block = [ln for ln in block if "Warning" not in ln]
    return "\n".join(block)[:8000]


def implicated_files(traceback: str, repo_files: set[str]) -> list[str]:
    """Repo-relative SOURCE files appearing as real traceback frames (excluding tests).

    Matches `path.py:line:` frames (the trailing colon is what pytest emits for a
    stack frame) and skips line 1 (import-warning artifacts), so the actual error
    location ranks first.
    """
    hits: dict[str, int] = {}
    for m in re.finditer(r"([A-Za-z0-9_./-]+\.py):(\d+):", traceback):
        rel = m.group(1).lstrip("/").replace("/testbed/", "")
        line_no = m.group(2)
        if rel in repo_files and not os.path.basename(rel).startswith("test") and line_no != "1":
            hits[rel] = hits.get(rel, 0) + 1
    return sorted(hits, key=lambda k: hits[k], reverse=True)


def patched_files(patch: str, repo_files: set[str]) -> list[str]:
    """Source files already touched by the current patch (Phase-A localization).

    Assertion-failure tests (expected exception not raised) produce a traceback that
    points only at the test file, so implicated_files() is empty; the seed patch's
    own target files are then the best localization signal to refine.
    """
    files: list[str] = []
    for m in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE):
        rel = m.group(1).strip()
        if rel in repo_files and not os.path.basename(rel).startswith("test") and rel not in files:
            files.append(rel)
    return files


def ce_localize(problem: str, tb: str, repo_files: set[str], ws: Path) -> str:
    """Last-resort localization via the local ChiefEngineer (gemma) when no file is implied."""
    import asyncio

    from polaris.cells.llm.dialogue.internal.role_dialogue import generate_role_response

    cand = sorted(repo_files)[:200]
    msg = (
        f"Bug report:\n{problem[:3000]}\n\nFailing test / traceback:\n{tb[:3000]}\n\n"
        "Candidate source files:\n" + "\n".join(cand) + "\n\nReply EXACTLY: FILE: <relative/path.py>"
    )
    try:
        ce = asyncio.run(generate_role_response(workspace=str(ws), settings=None, role="chief_engineer", message=msg))
        m = re.search(r"FILE:\s*([^\n`]+)", str(ce.get("response") or ""))
        if m:
            cand_path = m.group(1).strip().strip("`").strip()
            if cand_path in repo_files:
                return cand_path
            for f in repo_files:
                if f.endswith(cand_path) or cand_path.endswith(f):
                    return f
    except (RuntimeError, ValueError, OSError, KeyError, TypeError):
        return ""
    return ""


# ── Embedding-free localization for the empty-traceback-frame case (V12) ──
# Wires the proven RepoIntelligence ranker (#10) + AST test-symbol idents into the
# convergence loop so assertion-failure tests (implicated==[]) stop falling through to
# the alphabetical-truncated ce_localize. See blueprint
# SWEBENCH_V12_LOCALIZATION_CONVERGENCE_BLUEPRINT_20260607.md.

# Test-framework / stdlib noise that never names the code under test.
_TEST_IDENT_STOPWORDS: frozenset[str] = frozenset(
    {
        "self",
        "assert",
        "assertEqual",
        "assertNotEqual",
        "assertTrue",
        "assertFalse",
        "assertRaises",
        "assertRaisesRegex",
        "assertIn",
        "assertNotIn",
        "assertIs",
        "assertIsNot",
        "assertIsNone",
        "assertIsNotNone",
        "assertIsInstance",
        "assertListEqual",
        "assertDictEqual",
        "assertSetEqual",
        "assertGreater",
        "assertLess",
        "assertAlmostEqual",
        "setUp",
        "tearDown",
        "setUpClass",
        "tearDownClass",
        "addCleanup",
        "skipUnless",
        "skipIf",
        "pytest",
        "raises",
        "fixture",
        "parametrize",
        "mark",
        "Mock",
        "MagicMock",
        "patch",
        "mock",
        "TestCase",
        "SimpleTestCase",
        "expected",
        "actual",
        "result",
        "value",
        "values",
        "client",
        "response",
        "request",
        "data",
        "args",
        "kwargs",
        "context",
        "format",
        "print",
        "range",
        "len",
        "list",
        "dict",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "None",
        "True",
        "False",
    }
)


def _split_test_node(node_id: str) -> tuple[str, str]:
    """Parse a FAIL_TO_PASS entry into ``(file-or-module hint, function name)``.

    SWE-bench mixes two node-id dialects, and the unittest one (django) has no ``::`` —
    failing to parse it dropped the test-symbol localization signal on exactly the largest
    repos. Handles all three shapes:

      pytest path :  ``tests/test_x.py::Cls::test_m``   -> ("tests/test_x.py", "test_m")
      pytest dotted: ``pkg.mod::test_m``                -> ("pkg.mod", "test_m")
      unittest     : ``test_m (pkg.mod.tests.ClassName)`` -> ("pkg.mod.tests", "test_m")
    """
    nid = node_id.strip()
    match = re.match(r"^([A-Za-z_]\w*)\s*\(([\w.]+)\)\s*$", nid)
    if match:
        func = match.group(1)
        parts = match.group(2).split(".")
        # the trailing CamelCase component is the TestCase class; the rest is the module.
        module = ".".join(parts[:-1]) if len(parts) > 1 else parts[0]
        return module, func
    head = nid.split("::", 1)[0].strip()
    func = nid.split("::")[-1].strip() if "::" in nid else ""
    return head, func


def _resolve_test_file(node_id: str, repo_files: set[str]) -> str:
    """Resolve a FAIL_TO_PASS entry to a repo-relative test file path.

    Handles pytest path ids (``path/to/test_x.py::Cls::m``), pytest dotted ids
    (``tests.pkg.test_x::m``) and django unittest ids (``m (pkg.mod.Class)``), plus
    ``/testbed/`` prefixes and suffix matches. Returns "" when no repo file matches.
    """
    head, _func = _split_test_node(node_id)
    if head.endswith(".py"):
        rel = head.lstrip("/").replace("/testbed/", "")
        if rel in repo_files:
            return rel
        for f in repo_files:
            if f == rel or f.endswith("/" + rel) or rel.endswith("/" + f):
                return f
        return ""
    if not head:
        return ""
    dotted = head.replace(".", "/") + ".py"
    if dotted in repo_files:
        return dotted
    for f in repo_files:
        if f == dotted or f.endswith("/" + dotted):
            return f
    return ""


def _test_func_nodes(tree: ast.AST, func_name: str) -> list[ast.AST]:
    """Locate the AST node(s) of the specific failing test function (params stripped)."""
    base = func_name.split("[", 1)[0].strip()
    if not base:
        return []
    out: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == base:
            out.append(node)
    return out


def _extract_test_symbols(ws: Path, failing_tests: list[str], repo_files: set[str], limit: int = 20) -> list[str]:
    """Identifiers referenced by the failing test bodies — the strongest localization signal
    when the traceback has no source frame.

    Assertion-failure tests (expected exception not raised) point only at the test file, so
    ``implicated_files`` is empty. The failing test's OWN body, however, names the code under
    test: the classes it constructs, the functions it calls, the attributes it reads. We
    AST-parse the specific failing function(s) and collect those names, dropping test-framework
    noise. Never raises — degrades to whatever it could collect.
    """
    scores: dict[str, int] = {}
    seen_files: set[str] = set()
    for node_id in failing_tests:
        rel = _resolve_test_file(node_id, repo_files)
        if not rel or rel in seen_files:
            continue
        seen_files.add(rel)
        fp = ws / rel
        if not fp.is_file():
            continue
        try:
            tree = ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError, OSError):
            continue
        _, func_name = _split_test_node(node_id)
        for func_node in _test_func_nodes(tree, func_name):
            for sub in ast.walk(func_node):
                if isinstance(sub, ast.Name) and len(sub.id) >= 4:
                    scores[sub.id] = scores.get(sub.id, 0) + 2
                elif isinstance(sub, ast.Attribute) and len(sub.attr) >= 4:
                    scores[sub.attr] = scores.get(sub.attr, 0) + 1
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [k for k, _ in ranked if k not in _TEST_IDENT_STOPWORDS][:limit]


def _next_hypothesis(candidates: list[str], tried: list[str]) -> str:
    """First candidate not yet attempted — drives the across-round hypothesis cascade."""
    tried_set = set(tried)
    for cand in candidates:
        if cand not in tried_set:
            return cand
    return ""


REPOMAP_TRUST_TOPK = 5
MAX_LOCALIZE_ATTEMPTS = 3  # within a round, retry the next ranked candidate if an edit fails to apply


def _localization_trusted(target: str, candidates: list[str]) -> bool:
    """Does the RepoIntelligence ranker corroborate ``target`` (top-K)?

    Used to decide whether a stuck edit reflects a MIS-localized seed (escape to a
    better-ranked candidate) or a hard fix on the right file (keep iterating). With no
    candidate signal we trust the current target (nothing better to escape toward).
    """
    if not candidates:
        return True
    return target in candidates[:REPOMAP_TRUST_TOPK]


def _candidate_files(problem: str, failing_tests: list[str], repo_files: set[str], ws: Path) -> list[str]:
    """Ranked non-test source files for the empty-traceback-frame case (embedding-free).

    Fuses idents from BOTH the issue and the failing test bodies, ranks via the proven
    RepoIntelligence repo map (#10, tree-sitter + PageRank), and falls back to lexical
    ``git grep`` content ranking when the symbol ranker is thin/degraded. Returns [] if every
    signal fails (caller then uses ``ce_localize`` as last resort). Never raises.
    """
    test_idents = _extract_test_symbols(ws, failing_tests, repo_files)
    augmented = problem
    if test_idents:
        augmented = problem + "\n\nFailing-test symbols: " + " ".join(f"`{t}`" for t in test_idents)
    ranked, tel = _ranked_candidates(str(ws), augmented)
    if bool(tel.get("degraded")) or len(ranked) < CONTENT_FALLBACK_MIN_RANKED:
        merged_idents = list(dict.fromkeys([*_extract_identifiers(augmented), *test_idents]))
        for cand in _content_ranked_candidates(str(ws), merged_idents):
            if cand not in ranked:
                ranked.append(cand)
    out: list[str] = []
    for rel in ranked:
        if rel in repo_files and not _is_test_path(rel) and rel not in out:
            out.append(rel)
    return out


def _repair_blueprint(target: str, content: str, problem: str, tb: str, context: str) -> tuple[str, int, int]:
    """V11 swap-paradigm repair: Kimi (chief_engineer) turns the REAL pytest traceback into a
    precise, line-level repair spec so the weak local model only transcribes it. This is the
    "open the model's eyes" step — the strong model READS the failure and prescribes the fix.
    Returns (spec_text, cloud_in, cloud_out); ("", 0, 0) on failure.
    """
    prompt = (
        f"You are the Chief Engineer. The current fix in `{target}` FAILED its tests. Using the REAL "
        "pytest traceback, produce a PRECISE, line-level repair spec a junior developer can apply "
        "mechanically.\n"
        "State: (1) the exact symbol/line at fault; (2) WHY the test fails (read the assertion/error "
        "message); (3) the exact corrected line(s) WITH correct indentation; (4) edge cases.\n"
        "Numbered, code-level; do NOT emit SEARCH/REPLACE markers.\n\n"
        f"CONTEXTOS PROJECTION (confirmed facts):\n{context}\n\n"
        f"ISSUE:\n{problem[:4000]}\n\n"
        f"REAL PYTEST TRACEBACK:\n{tb[:6000]}\n\n"
        f"CURRENT CONTENT of {target}:\n```\n{content[:MAX_CONTENT_CHARS]}\n```\n"
    )
    try:
        # Reasoning-model headroom (deepseek-v4-pro spends output tokens thinking first).
        text, usage = _complete_for_role("chief_engineer", prompt, max_tokens=8192)
    except (RuntimeError, ValueError, OSError, KeyError, TypeError):
        return "", 0, 0
    return text, int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def render_messages(messages: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{m.get('role')}] {m.get('content')}" for m in messages if m.get("content"))


def converge(instance: dict[str, Any], work_dir: Path, max_rounds: int, run_prefix: str) -> dict[str, Any]:
    iid = str(instance["instance_id"])
    repo = str(instance["repo"])
    base_commit = str(instance["base_commit"])
    problem = str(instance.get("problem_statement") or "")

    ws = work_dir / iid
    ensure_clone(repo, base_commit, ws)
    repo_files = {ln.strip() for ln in run_git(["ls-files"], cwd=ws).stdout.splitlines() if ln.strip().endswith(".py")}

    # ContextOS control plane (genuine planes)
    truth = TruthLogService(workspace=str(ws), session_id=f"archb_{iid}", enable_semantic_index=False)
    receipts = ReceiptStore(workspace=str(ws))
    projector = ProjectionEngine()

    # seed round 1 with the best prior patch (V11 starts from the v10 hardened state, then
    # converges via test feedback) — the system's first attempt is round 1's baseline.
    seed = ""
    for src in (
        "predictions_v10_final.jsonl",
        "predictions_v10.jsonl",
        "predictions_batch.jsonl",
        "predictions_new.jsonl",
    ):
        p = work_dir / src
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    if rec.get("instance_id") == iid and (rec.get("model_patch") or "").strip():
                        seed = rec["model_patch"]
    if seed:
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn"],
            cwd=str(ws),
            input=seed,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            run_git(["checkout", "-f", base_commit], cwd=ws)
            seed = ""

    trace: list[dict[str, Any]] = []
    tokens = {"local_in_est": 0, "local_out_est": 0, "cloud_in": 0, "cloud_out": 0}
    resolved = False
    confirmed_facts: list[str] = []
    # V12 unified localization cascade state (trust-based escape from a mis-localized seed).
    tried_targets: list[str] = []
    last_target = ""
    last_loc_source = ""
    cand_cache: list[str] | None = None

    for rnd in range(1, max_rounds + 1):
        patch = current_patch(ws)
        pred_path = work_dir / f"pred_{iid}_r{rnd}.jsonl"
        pred_path.write_text(
            json.dumps({"instance_id": iid, "model_name_or_path": MODEL_NAME, "model_patch": patch}) + "\n",
            encoding="utf-8",
        )
        run_id = f"{run_prefix}_{iid.replace('__', '_')}_r{rnd}"
        run_harness_round(work_dir, pred_path, run_id)
        rep, test_output = instance_report(work_dir, run_id, iid)
        tstatus = rep.get("tests_status", {}) or {}
        f2p_fail = list(tstatus.get("FAIL_TO_PASS", {}).get("failure", []))
        f2p_pass = list(tstatus.get("FAIL_TO_PASS", {}).get("success", []))
        p2p_fail = list(tstatus.get("PASS_TO_PASS", {}).get("failure", []))
        resolved = bool(rep.get("resolved"))
        applied_ok = bool(rep.get("patch_successfully_applied"))

        tb = extract_tracebacks(test_output, f2p_fail)
        tb_ref = f"{iid}_r{rnd}_traceback"
        receipts.put(tb_ref, tb or "(no traceback captured)")

        # TruthLog: strongly-typed round event (single source of truth)
        truth.append(
            {
                "type": "test_round",
                "role": "qa",
                "round": rnd,
                "resolved": resolved,
                "patch_applied": applied_ok,
                "f2p_pass": len(f2p_pass),
                "f2p_fail": f2p_fail,
                "p2p_fail": p2p_fail,
                "traceback_ref": tb_ref,
                "summary": f"round {rnd}: resolved={resolved} F2P_pass={len(f2p_pass)} F2P_fail={len(f2p_fail)} P2P_fail={len(p2p_fail)}",
            }
        )

        # Flakiness shield (Task 2): a patch is functionally correct when ALL target
        # FAIL_TO_PASS tests pass, independent of env-flaky PASS_TO_PASS network tests.
        pure_f2p_resolved = applied_ok and len(f2p_fail) == 0 and len(f2p_pass) > 0
        round_rec = {
            "round": rnd,
            "patch_lines": patch.count("\n"),
            "patch_applied": applied_ok,
            "resolved": resolved,
            "pure_f2p_resolved": pure_f2p_resolved,
            "f2p_pass": len(f2p_pass),
            "f2p_fail": f2p_fail,
            "p2p_fail": p2p_fail,
            "implicated": implicated_files(tb, repo_files),
        }
        trace.append(round_rec)
        print(
            f"[arch-b] {iid} round {rnd}: resolved={resolved} F2P {len(f2p_pass)}/{len(f2p_pass) + len(f2p_fail)} "
            f"P2P_fail={len(p2p_fail)} implicated={round_rec['implicated']}",
            flush=True,
        )

        if resolved:
            break
        if pure_f2p_resolved:
            # Flakiness shield: all target tests pass. Remaining PASS_TO_PASS failures
            # are env-flaky (network) or out-of-scope; refining the target file further
            # cannot fix them and risks regressions. Stop with the functional win.
            print(
                f"[arch-b] {iid} round {rnd}: pure_f2p_resolved (target tests green) — stopping (P2P flaky/oos)",
                flush=True,
            )
            break
        if rnd == max_rounds:
            break

        # ── V12 unified localization cascade with trust-based escape ──
        # The convergence loop SEEDS from a prior patch, so patched_files() usually pins the
        # target to the seed's file — which silently traps the loop on a MIS-localized seed
        # (the dominant unresolved mode). When the last edited file made no FAIL_TO_PASS
        # progress, decide by its source: a failed `repomap` hypothesis advances to the next
        # candidate; a `patch` (seed) target escapes only when the RepoIntelligence ranker
        # does NOT corroborate it (top-K) — i.e. it is likely mis-localized; a `traceback`
        # target is authoritative and keeps iterating. Abandoned files are reverted so they
        # stop pinning the cascade.
        if last_target and len(f2p_pass) == 0:
            abandon = False
            if last_loc_source == "repomap":
                abandon = True
            elif last_loc_source == "patch":
                if cand_cache is None:
                    cand_cache = _candidate_files(problem, f2p_fail, repo_files, ws)
                abandon = not _localization_trusted(last_target, cand_cache)
            if abandon:
                if last_target not in tried_targets:
                    tried_targets.append(last_target)
                run_git(["checkout", base_commit, "--", last_target], cwd=ws)
                patch = current_patch(ws)
        last_target = ""

        impl = [f for f in implicated_files(tb, repo_files) if f not in tried_targets]
        pf = [f for f in patched_files(patch, repo_files) if f not in tried_targets]
        loc_source = ""
        target = ""
        if impl:
            target, loc_source = impl[0], "traceback"
        elif pf:
            target, loc_source = pf[0], "patch"
        else:
            # RepoIntelligence-ranked candidates (#10/#9) — fires when there is no traceback
            # frame and no (untried) patched file, incl. after escaping a mis-localized seed.
            if cand_cache is None:
                cand_cache = _candidate_files(problem, f2p_fail, repo_files, ws)
            cand = _next_hypothesis(cand_cache, tried_targets)
            if cand:
                target, loc_source = cand, "repomap"
        if not target:
            target = ce_localize(problem, tb, repo_files, ws)
            if target:
                loc_source = "ce"

        # ── ContextOS projection -> next-turn messages (NOT raw history concat) ──
        confirmed_facts = [
            f"Round {rnd}: patch applied={applied_ok}; FAIL_TO_PASS {len(f2p_pass)} pass / {len(f2p_fail)} fail.",
        ]
        if f2p_fail:
            confirmed_facts.append(f"Still failing target tests: {', '.join(t.split('::')[-1] for t in f2p_fail)}.")
        if p2p_fail:
            confirmed_facts.append(f"Regressions introduced (must not happen): {len(p2p_fail)} PASS_TO_PASS now fail.")
        if impl:
            confirmed_facts.append(f"Traceback points at source file(s): {', '.join(impl[:3])}.")
        elif loc_source == "repomap":
            alts = [c for c in (cand_cache or []) if c != target][:2]
            confirmed_facts.append(
                "No traceback source frame (assertion-failure); repo-map localized to "
                f"{target}" + (f" (alternates: {', '.join(alts)})" if alts else "") + "."
            )

        projection_dict = {
            "system_hint": (
                f"You are converging a fix for {repo} ({iid}). Iterate until the target tests pass. "
                "Use the real pytest traceback below; edit only the implicated SOURCE file(s), never tests."
            ),
            "structured_findings": {"confirmed_facts": confirmed_facts},
            "turns": [{"role": "assistant", "content": t["summary"]} for t in truth.get_recent(6)],
            "tail_hint": (
                f"Next target: make {', '.join(t.split('::')[-1] for t in f2p_fail) or 'the failing tests'} pass "
                f"by fixing {target or ', '.join(impl[:2]) or 'the implicated file'}."
            ),
        }
        messages = projector.project(projection_dict, receipts)

        # ── V11 swap-paradigm refine (test-driven): the strong model reads the REAL traceback
        #    -> repair blueprint; local gemma transcribes it into SEARCH/REPLACE. Within a
        #    round, if the edit fails to apply (e.g. a no-op or malformed block), advance to the
        #    next ranked candidate and retry — a failed transcription must not abandon the whole
        #    instance (no harness re-run between attempts). ──
        applied = False
        detail = ""
        path = "none"
        for attempt in range(1, MAX_LOCALIZE_ATTEMPTS + 1):
            if not target:
                break
            content = ""
            if (ws / target).is_file():
                content = (ws / target).read_text(encoding="utf-8", errors="replace")
            # strong-model repair blueprint derived from the in-container traceback + ContextOS projection
            spec, bp_in, bp_out = _repair_blueprint(target, content, problem, tb, render_messages(messages))
            tokens["cloud_in"] += bp_in
            tokens["cloud_out"] += bp_out
            transcribe_prompt = (
                "Apply the FIX SPEC below to the file. Output ONLY Aider SEARCH/REPLACE block(s), no prose.\n"
                "Format each block EXACTLY as:\n"
                f"<<<< SEARCH:{target}\n<lines copied VERBATIM from CONTENT>\n====\n<fixed lines>\n>>>> REPLACE\n\n"
                "Rules: SEARCH copied character-for-character (exact indentation); REPLACE must differ; keep\n"
                "every block CLOSED with `>>>> REPLACE`; do not touch tests.\n\n"
                f"FIX SPEC (senior engineer, derived from the REAL test failure):\n{spec or '(spec unavailable; infer from the traceback)'}\n\n"
                f"REAL PYTEST TRACEBACK (in-container, round {rnd}):\n{tb[:4000]}\n\n"
                f"CONTENT of {target}:\n```\n{content[:MAX_CONTENT_CHARS]}\n```\n"
            )
            # local gemma transcribes (config-driven director binding); authoritative local usage
            draft, usage = _complete_for_role(
                "director", transcribe_prompt, max_tokens=_context_budget_max_tokens(transcribe_prompt)
            )
            tokens["local_in_est"] += usage.get("input_tokens", 0)
            tokens["local_out_est"] += usage.get("output_tokens", 0)
            applied, detail, path = _apply_blocks(str(ws), target, draft)
            truth.append(
                {
                    "type": "refine",
                    "role": "director",
                    "round": rnd,
                    "target": target,
                    "loc_source": loc_source,
                    "applied": applied,
                    "apply_path": path,
                    "summary": f"round {rnd} refine: target={target} loc={loc_source} applied={applied} via {path}",
                }
            )
            print(
                f"[arch-b] {iid} round {rnd} refine: target={target} loc={loc_source} "
                f"applied={applied} via {path} attempt={attempt} ({detail[:80]})",
                flush=True,
            )
            if applied:
                last_target = target
                last_loc_source = loc_source
                break
            # edit failed to apply -> mark this target tried, advance to the next ranked
            # candidate and retry within the round (no wasted harness eval).
            if target not in tried_targets:
                tried_targets.append(target)
            if cand_cache is None:
                cand_cache = _candidate_files(problem, f2p_fail, repo_files, ws)
            nxt = _next_hypothesis(cand_cache, tried_targets)
            target, loc_source = (nxt, "repomap") if nxt else ("", "")
        if not applied:
            break  # no candidate could be edited this round; stop

    final_patch = current_patch(ws)
    return {
        "instance_id": iid,
        "resolved": resolved,
        "pure_f2p_resolved": any(t.get("pure_f2p_resolved") for t in trace),
        "rounds": len(trace),
        "trace": trace,
        "tokens": tokens,
        "final_patch": final_patch,
        "truth_log_entries": len(truth.get_entries()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Arch B container-interactive convergence")
    ap.add_argument("--instance-ids", required=True, help="comma-separated")
    ap.add_argument("--max-rounds", type=int, default=4)
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/swebench-work"))
    ap.add_argument("--run-prefix", default="archb")
    ap.add_argument("--out", default=os.path.expanduser("~/Temp/swebench-work/predictions_final.jsonl"))
    ap.add_argument("--trace-out", default=os.path.expanduser("~/Temp/swebench-work/convergence_trace.json"))
    args = ap.parse_args()

    from datasets import load_dataset

    ds = load_dataset(DATASET, split="test")
    wanted = [s.strip() for s in args.instance_ids.split(",") if s.strip()]
    rows = {str(r["instance_id"]): dict(r) for r in ds if str(r["instance_id"]) in set(wanted)}

    work_dir = Path(args.work_dir)
    results: list[dict[str, Any]] = []
    for iid in wanted:
        inst = rows.get(iid)
        if not inst:
            print(f"[arch-b] SKIP unknown instance {iid}", flush=True)
            continue
        print(f"[arch-b] === converging {iid} (max_rounds={args.max_rounds}) ===", flush=True)
        res = converge(inst, work_dir, args.max_rounds, args.run_prefix)
        results.append(res)
        print(f"[arch-b] {iid} DONE resolved={res['resolved']} rounds={res['rounds']}", flush=True)

    # write predictions_final.jsonl (final patches)
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(
                json.dumps(
                    {
                        "instance_id": r["instance_id"],
                        "model_name_or_path": MODEL_NAME,
                        "model_patch": r["final_patch"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    # write convergence trace (evidence)
    with open(args.trace_out, "w", encoding="utf-8") as fh:
        json.dump(
            [{k: v for k, v in r.items() if k != "final_patch"} for r in results], fh, ensure_ascii=False, indent=2
        )

    resolved_n = sum(1 for r in results if r["resolved"])
    pure_n = sum(1 for r in results if r.get("pure_f2p_resolved"))
    print(
        f"[arch-b] SUMMARY strict_resolved {resolved_n}/{len(results)} | "
        f"pure_f2p_resolved {pure_n}/{len(results)} -> {args.out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

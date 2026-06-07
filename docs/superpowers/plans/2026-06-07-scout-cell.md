# Scout (探子) Cell v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-defined-but-unwired `scout` role into a working auxiliary, synchronous, read-only **code/symbol reconnaissance** capability that PM/Director can call inline.

**Architecture:** A new ACGA cell `src/backend/polaris/cells/roles/scout/`. Deterministic-first probe: a `ScoutProbeService.probe()` facade runs read-only retrieval (via the `ToolSpecRegistry` read-tool handlers) → ranks → distills into a token-bounded pack → returns a `ScoutReportV1` with an `EvidencePackage` content hash. All core logic depends on two injected **ports** (`ReadToolPort`, `DistillerPort`) so it unit-tests against fakes; one real adapter resolves read tools through the registry SSOT (which is the fail-closed read-only gate). Optional escalation (P3) bridges to `execute_role_session(role="scout")`. No TaskMarket, no persistence, no separate transaction.

**Tech Stack:** Python 3 (async), frozen `@dataclass` contracts, `ToolSpecRegistry` (`polaris.kernelone.tool_execution.tool_spec_registry`), `EvidenceCollector`/`EvidencePackage` (`polaris.domain.verification.evidence_collector`), `RoleRuntimeService` (`polaris.cells.roles.runtime`), pytest, ruff, mypy.

**Reference spec:** `docs/superpowers/specs/2026-06-07-scout-cell-design.md`

---

## File Structure

All paths under `src/backend/polaris/cells/roles/scout/`:

| File | Responsibility |
|---|---|
| `cell.yaml` | ACGA manifest (id, layer, public exports, deps) — mirror sibling `cells/roles/runtime/cell.yaml` |
| `README.agent.md` | Cell purpose + read-only/no-TaskMarket constraints |
| `context.pack.json` | ACGA context pack (mirror sibling, minimal) |
| `public/__init__.py` | Export `ScoutProbeService`, contracts |
| `public/contracts.py` | `ScoutProbeTargetV1`, `ScoutFinding`, `ScoutReportV1` |
| `public/service.py` | `ScoutProbeService` facade + `build_default_scout_service()` |
| `public/tests/test_contracts.py` | Contract validation/serialization tests |
| `public/tests/test_service.py` | End-to-end probe tests (fakes) |
| `internal/ports.py` | `ReadToolPort`, `DistillerPort` protocols + fakes for tests |
| `internal/target.py` | Normalize/validate target + stable cache key |
| `internal/planner.py` | Build read plan (list of tool calls) from target |
| `internal/retrieval.py` | Execute plan via `ReadToolPort` → raw findings + coverage |
| `internal/ranker.py` | Score, dedupe, cap findings |
| `internal/distiller.py` | Deterministic summary (P1) + LLM-backed (P2) |
| `internal/evidence.py` | Build `EvidencePackage`, return content hash |
| `internal/cache.py` | `TTLCache` (injected clock) |
| `internal/read_tool_adapter.py` | Real `ReadToolPort` via `ToolSpecRegistry` handlers (P1 Task 12) |
| `internal/escalation.py` | Bridge to `execute_role_session(role="scout")` (P3) |
| `internal/tests/*` | Unit tests per internal module |

External touch points (later phases):
- `src/backend/polaris/kernelone/tool_execution/tool_spec_registry.py` — register `scout_probe` (P2)
- `src/backend/polaris/cells/roles/profile/internal/builtin_profiles.py` + role tool whitelists — whitelist `scout_probe` (P2)
- `src/backend/polaris/kernelone/roles/templates/preset_templates.py` — fix fictional SCOUT_TEMPLATE tool names (P2)
- `src/backend/docs/graph/catalog/cells.yaml` — register the new cell (P1 Task 13)

---

# PHASE P1 — Deterministic probe core

## Task 1: Cell scaffold

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/__init__.py`, `public/__init__.py`, `internal/__init__.py`, `public/tests/__init__.py`, `internal/tests/__init__.py`
- Create: `cell.yaml`, `README.agent.md`, `context.pack.json`
- Test: `src/backend/polaris/cells/roles/scout/public/tests/test_smoke.py`

- [ ] **Step 1: Create package dirs with empty `__init__.py`**

```bash
mkdir -p src/backend/polaris/cells/roles/scout/public/tests \
         src/backend/polaris/cells/roles/scout/internal/tests
for d in scout scout/public scout/public/tests scout/internal scout/internal/tests; do
  : > "src/backend/polaris/cells/roles/$d/__init__.py"
done
```

- [ ] **Step 2: Write `cell.yaml`** — first read the sibling for exact schema:

Run: `sed -n '1,40p' src/backend/polaris/cells/roles/runtime/cell.yaml`
Then create `src/backend/polaris/cells/roles/scout/cell.yaml` mirroring its top-level keys, with scout values:

```yaml
# Scout cell — auxiliary read-only code/symbol reconnaissance
id: roles.scout
name: scout
layer: cells
owner: roles
description: >
  Auxiliary, synchronous, read-only code/symbol reconnaissance role.
  Called inline by PM/Director; no TaskMarket, no writes, no persistence.
public:
  - ScoutProbeService
  - ScoutProbeTargetV1
  - ScoutReportV1
  - ScoutFinding
depends_on:
  - kernelone.tool_execution
  - domain.verification
constraints:
  - read_only
  - no_task_market
  - side_effect_free
```

- [ ] **Step 3: Write `README.agent.md`** (short):

```markdown
# roles.scout (探子)

Auxiliary **read-only** code/symbol reconnaissance. Called inline by a main
role (Director/PM) within its own Turn — synchronous, side-effect-free, no
TaskMarket, no persistence. Entry point: `ScoutProbeService.probe()`.

Read-only is enforced at the read-tool adapter boundary (only
`ToolSpecRegistry` tools whose category is `read`). Never wire write/exec tools.
```

- [ ] **Step 4: Write minimal `context.pack.json`**

```json
{"cell": "roles.scout", "version": "1", "public": ["ScoutProbeService"]}
```

- [ ] **Step 5: Write smoke test**

```python
# public/tests/test_smoke.py
def test_scout_package_imports() -> None:
    import polaris.cells.roles.scout  # noqa: F401
    import polaris.cells.roles.scout.public  # noqa: F401
```

- [ ] **Step 6: Run smoke test**

Run: `pytest src/backend/polaris/cells/roles/scout/public/tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/backend/polaris/cells/roles/scout
git commit -m "feat(scout): scaffold roles.scout cell"
```

---

## Task 2: Contracts

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/public/contracts.py`
- Test: `src/backend/polaris/cells/roles/scout/public/tests/test_contracts.py`

- [ ] **Step 1: Write failing test**

```python
# public/tests/test_contracts.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest src/backend/polaris/cells/roles/scout/public/tests/test_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: ...contracts`

- [ ] **Step 3: Write `public/contracts.py`**

```python
"""Public contracts for the roles.scout cell (UTF-8)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

_VALID_MODES = ("locate", "boundary")


@dataclass(frozen=True)
class ScoutFinding:
    """A single read-only reconnaissance finding."""

    path: str
    snippet: str
    symbol: str | None = None
    line: int | None = None
    why_relevant: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "snippet": self.snippet,
            "symbol": self.symbol,
            "line": self.line,
            "why_relevant": self.why_relevant,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ScoutProbeTargetV1:
    """Read-only probe target descriptor (no access to caller control plane)."""

    query: str
    mode: str = "locate"
    hints: dict[str, Any] = field(default_factory=dict)
    max_findings: int = 12
    token_budget: int = 1200
    caller_role: str = ""
    run_id: str = ""
    task_id: str = ""
    allow_escalation: bool = False

    def __post_init__(self) -> None:
        if not str(self.query or "").strip():
            raise ValueError("query must be a non-empty string")
        if self.mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}, got {self.mode!r}")
        if self.max_findings < 1:
            raise ValueError("max_findings must be >= 1")
        if self.token_budget < 1:
            raise ValueError("token_budget must be >= 1")

    def cache_key(self) -> str:
        """Stable, order-independent hash of the semantic target."""
        basis = {
            "query": str(self.query).strip(),
            "mode": self.mode,
            "hints": _normalize(self.hints),
            "max_findings": self.max_findings,
            "token_budget": self.token_budget,
        }
        blob = json.dumps(basis, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ScoutReportV1:
    """Structured reconnaissance result returned to the caller."""

    findings: tuple[ScoutFinding, ...]
    summary: str
    coverage: dict[str, Any]
    confidence: float
    content_hash: str
    usage: dict[str, Any]
    cache_hit: bool = False
    escalated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
            "coverage": self.coverage,
            "confidence": self.confidence,
            "content_hash": self.content_hash,
            "usage": self.usage,
            "cache_hit": self.cache_hit,
            "escalated": self.escalated,
        }


def _normalize(value: Any) -> Any:
    """Recursively sort lists so hint ordering does not affect the cache key."""
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return sorted((json.dumps(_normalize(v), sort_keys=True, ensure_ascii=False) for v in value))
    return value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest src/backend/polaris/cells/roles/scout/public/tests/test_contracts.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/public/contracts.py src/backend/polaris/cells/roles/scout/public/tests/test_contracts.py
git commit -m "feat(scout): add V1 contracts (target/finding/report)"
```

---

## Task 3: Ports + fakes

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/ports.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_ports.py`

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_ports.py
from polaris.cells.roles.scout.internal.ports import FakeReadTool, FakeDistiller


def test_fake_read_tool_returns_scripted_result() -> None:
    fake = FakeReadTool({("repo_rg", ("pay",)): {"ok": True, "hits": [{"file": "a.py", "line": 3, "text": "pay"}]}})
    out = fake.run("repo_rg", ["pay"])
    assert out["hits"][0]["file"] == "a.py"
    assert fake.calls == [("repo_rg", ["pay"])]


def test_fake_read_tool_defaults_to_empty_ok() -> None:
    assert FakeReadTool({}).run("repo_tree", ["."]) == {"ok": True, "hits": [], "stdout": ""}


async def test_fake_distiller_echoes() -> None:
    out = await FakeDistiller("SUMMARY").distill(query="q", findings=[], token_budget=10)
    assert out == "SUMMARY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_ports.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `internal/ports.py`**

```python
"""Ports (interfaces) for roles.scout + in-memory fakes for tests (UTF-8)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.scout.public.contracts import ScoutFinding


@runtime_checkable
class ReadToolPort(Protocol):
    """Synchronous read-only tool runner. Implementations MUST refuse non-read tools."""

    def run(self, tool: str, args: list[str]) -> dict[str, Any]: ...


@runtime_checkable
class DistillerPort(Protocol):
    """Summarize findings into a token-bounded pack."""

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str: ...


class FakeReadTool:
    """Scripted ReadToolPort for tests."""

    def __init__(self, scripted: dict[tuple[str, tuple[str, ...]], dict[str, Any]]) -> None:
        self._scripted = scripted
        self.calls: list[tuple[str, list[str]]] = []

    def run(self, tool: str, args: list[str]) -> dict[str, Any]:
        self.calls.append((tool, list(args)))
        return self._scripted.get((tool, tuple(args)), {"ok": True, "hits": [], "stdout": ""})


class FakeDistiller:
    """Constant-output DistillerPort for tests."""

    def __init__(self, output: str) -> None:
        self._output = output

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str:
        return self._output
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_ports.py -v`
Expected: PASS

> Note: async tests require `pytest-asyncio` (already a project dep — confirm with `pytest -p no:cacheprovider -q -k test_fake_distiller_echoes`). If the marker is needed, add `@pytest.mark.asyncio`; the repo's existing async tests show the convention (grep an existing `async def test_` in `cells/roles/runtime/public/tests/`).

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/ports.py src/backend/polaris/cells/roles/scout/internal/tests/test_ports.py
git commit -m "feat(scout): add ReadToolPort/DistillerPort + fakes"
```

---

## Task 4: Target normalization

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/target.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_target.py`

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_target.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_target.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Write `internal/target.py`**

```python
"""Target descriptor normalization for roles.scout (UTF-8)."""
from __future__ import annotations

import re

from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1

_STOPWORDS = frozenset({
    "the", "is", "in", "a", "an", "of", "to", "for", "and", "or", "where",
    "what", "how", "why", "this", "that", "it", "on", "at", "by",
})
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def extract_terms(target: ScoutProbeTargetV1) -> list[str]:
    """Lowercased, de-duplicated, stopword-filtered search terms (order preserved)."""
    seen: set[str] = set()
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(str(target.query)):
        token = raw.lower()
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    for sym in _as_str_list(target.hints.get("symbols")):
        low = sym.lower()
        if low not in seen:
            seen.add(low)
            terms.append(low)
    return terms


def hint_paths(target: ScoutProbeTargetV1) -> list[str]:
    return _as_str_list(target.hints.get("paths"))


def hint_globs(target: ScoutProbeTargetV1) -> list[str]:
    return _as_str_list(target.hints.get("globs"))


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_target.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/target.py src/backend/polaris/cells/roles/scout/internal/tests/test_target.py
git commit -m "feat(scout): add target term/hint extraction"
```

---

## Task 5: Read planner

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/planner.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_planner.py`

Plan = ordered list of `(tool, args)` mirroring `ExplorationPhase` flow (SEARCH→SLICE). Each `repo_rg` arg list ends with `--max` to bound results.

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_planner.py
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.internal.planner import build_read_plan


def test_locate_plan_searches_each_term_with_bounded_max() -> None:
    plan = build_read_plan(ScoutProbeTargetV1(query="payment gateway", mode="locate"))
    tools = [t for t, _ in plan]
    assert tools.count("repo_rg") >= 2
    rg_args = [a for t, a in plan if t == "repo_rg"][0]
    assert "--max" in rg_args


def test_boundary_plan_includes_repo_tree_for_hint_paths() -> None:
    plan = build_read_plan(ScoutProbeTargetV1(query="auth module", mode="boundary", hints={"paths": ["src/auth"]}))
    assert ("repo_tree", ["src/auth", "--depth", "2"]) in plan


def test_plan_is_empty_safe_when_only_stopwords() -> None:
    # "the a is" -> no terms; plan must still be a list (possibly tree-only / empty)
    assert isinstance(build_read_plan(ScoutProbeTargetV1(query="the a is of")), list)
```

- [ ] **Step 2: Run test — expect FAIL** (`ModuleNotFoundError`)

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_planner.py -v`

- [ ] **Step 3: Write `internal/planner.py`**

```python
"""Build a bounded, read-only retrieval plan for a probe target (UTF-8)."""
from __future__ import annotations

from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.internal.target import extract_terms, hint_globs, hint_paths

_RG_MAX = "40"
_SYMBOL_PREFIXES = ("def ", "class ", "func ", "function ", "interface ", "type ")


def build_read_plan(target: ScoutProbeTargetV1) -> list[tuple[str, list[str]]]:
    """Return an ordered list of (tool, args) read-tool calls."""
    plan: list[tuple[str, list[str]]] = []
    paths = hint_paths(target)
    globs = hint_globs(target)
    terms = extract_terms(target)

    # boundary mode: map the structure of hinted paths first
    if target.mode == "boundary":
        for p in paths or ["."]:
            plan.append(("repo_tree", [p, "--depth", "2"]))

    # search each term; symbol-biased pattern first, then plain text
    for term in terms:
        symbol_pattern = rf"(def|class|func|function|interface|type)\s+\w*{term}"
        plan.append(("repo_rg", _rg_args(symbol_pattern, paths, globs)))
        plan.append(("repo_rg", _rg_args(term, paths, globs)))

    return plan


def _rg_args(pattern: str, paths: list[str], globs: list[str]) -> list[str]:
    args = [pattern, *paths]
    if globs:
        args += ["--glob", globs[0]]
    args += ["--max", _RG_MAX]
    return args
```

> The `_SYMBOL_PREFIXES` constant documents intent; symbol bias is implemented via the regex. Keep the constant for readers (it is referenced in the distiller's symbol detection in Task 8).

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_planner.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/planner.py src/backend/polaris/cells/roles/scout/internal/tests/test_planner.py
git commit -m "feat(scout): add read-plan builder"
```

---

## Task 6: Retrieval

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/retrieval.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_retrieval.py`

`retrieve()` executes the plan via a `ReadToolPort`, parses `repo_rg` `hits` into `ScoutFinding`s, and reports coverage. It never raises on a single tool error — it records it in coverage.

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_retrieval.py
from polaris.cells.roles.scout.internal.ports import FakeReadTool
from polaris.cells.roles.scout.internal.retrieval import retrieve


def test_retrieve_parses_rg_hits_into_findings() -> None:
    fake = FakeReadTool({
        ("repo_rg", ("pay", "--max", "40")): {
            "ok": True, "hits": [{"file": "a.py", "line": 3, "text": "def pay():"}],
        },
    })
    plan = [("repo_rg", ["pay", "--max", "40"])]
    findings, coverage = retrieve(fake, plan)
    assert findings[0].path == "a.py"
    assert findings[0].line == 3
    assert coverage["tools_used"] == ["repo_rg"]
    assert coverage["truncated"] is False


def test_retrieve_marks_truncation_and_survives_tool_error() -> None:
    fake = FakeReadTool({
        ("repo_rg", ("x",)): {"ok": True, "hits": [{"file": "b.py", "line": 1, "text": "x"}], "truncated": True},
        ("repo_tree", ("bad",)): {"ok": False, "error": "boom"},
    })
    findings, coverage = retrieve(fake, [("repo_rg", ["x"]), ("repo_tree", ["bad"])])
    assert coverage["truncated"] is True
    assert any("repo_tree" in e for e in coverage["errors"])
    assert len(findings) == 1
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_retrieval.py -v`

- [ ] **Step 3: Write `internal/retrieval.py`**

```python
"""Execute a read plan via a ReadToolPort and collect findings (UTF-8)."""
from __future__ import annotations

from typing import Any

from polaris.cells.roles.scout.public.contracts import ScoutFinding
from polaris.cells.roles.scout.internal.ports import ReadToolPort


def retrieve(
    port: ReadToolPort,
    plan: list[tuple[str, list[str]]],
) -> tuple[list[ScoutFinding], dict[str, Any]]:
    """Run each (tool, args); collect findings + coverage. Never raises per-call."""
    findings: list[ScoutFinding] = []
    tools_used: list[str] = []
    errors: list[str] = []
    truncated = False

    for tool, args in plan:
        if tool not in tools_used:
            tools_used.append(tool)
        try:
            result = port.run(tool, args)
        except (RuntimeError, ValueError, OSError) as exc:
            errors.append(f"{tool}({args}): {exc}")
            continue
        if not result.get("ok", False):
            errors.append(f"{tool}: {result.get('error') or 'not ok'}")
            continue
        if result.get("truncated"):
            truncated = True
        for hit in result.get("hits", []) or []:
            findings.append(
                ScoutFinding(
                    path=str(hit.get("file") or ""),
                    line=_as_int(hit.get("line")),
                    snippet=str(hit.get("text") or "").strip(),
                )
            )

    coverage = {
        "tools_used": tools_used,
        "errors": errors,
        "truncated": truncated,
        "raw_findings": len(findings),
    }
    return findings, coverage


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_retrieval.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/retrieval.py src/backend/polaris/cells/roles/scout/internal/tests/test_retrieval.py
git commit -m "feat(scout): add retrieval over ReadToolPort"
```

---

## Task 7: Ranker

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/ranker.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_ranker.py`

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_ranker.py
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
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_ranker.py -v`

- [ ] **Step 3: Write `internal/ranker.py`**

```python
"""Score, de-duplicate and cap reconnaissance findings (UTF-8)."""
from __future__ import annotations

import re
from dataclasses import replace

from polaris.cells.roles.scout.public.contracts import ScoutFinding, ScoutProbeTargetV1
from polaris.cells.roles.scout.internal.target import extract_terms

_DEF_RE = re.compile(r"\b(?:def|class|func|function|interface|type)\s+(\w+)")


def rank(findings: list[ScoutFinding], target: ScoutProbeTargetV1) -> list[ScoutFinding]:
    """Return de-duplicated findings sorted by descending relevance, capped."""
    terms = extract_terms(target)
    deduped: dict[tuple[str, int | None], ScoutFinding] = {}
    for f in findings:
        deduped.setdefault((f.path, f.line), f)

    scored: list[tuple[float, ScoutFinding]] = []
    for f in deduped.values():
        score, symbol = _score(f, terms)
        scored.append((score, replace(f, confidence=round(min(score, 1.0), 3), symbol=symbol or f.symbol)))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [f for _, f in scored[: target.max_findings]]


def _score(finding: ScoutFinding, terms: list[str]) -> tuple[float, str | None]:
    snippet = finding.snippet.lower()
    score = 0.0
    symbol: str | None = None

    match = _DEF_RE.search(finding.snippet)
    if match:
        symbol = match.group(1)
        score += 0.6  # a definition is more valuable than a mention

    for term in terms:
        if term in snippet:
            score += 0.2
        if symbol and term in symbol.lower():
            score += 0.3
        if term in finding.path.lower():
            score += 0.15

    return score, symbol
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_ranker.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/ranker.py src/backend/polaris/cells/roles/scout/internal/tests/test_ranker.py
git commit -m "feat(scout): add finding ranker (dedupe/score/cap)"
```

---

## Task 8: Distiller (deterministic)

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/distiller.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_distiller.py`

P1 ships a deterministic, zero-LLM distiller implementing `DistillerPort`.

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_distiller.py
import pytest
from polaris.cells.roles.scout.public.contracts import ScoutFinding
from polaris.cells.roles.scout.internal.distiller import DeterministicDistiller


@pytest.mark.asyncio
async def test_deterministic_distiller_lists_findings_within_budget() -> None:
    findings = [ScoutFinding(path="a.py", line=1, symbol="pay", snippet="def pay():", confidence=0.9)]
    out = await DeterministicDistiller().distill(query="pay", findings=findings, token_budget=200)
    assert "a.py:1" in out
    assert "pay" in out


@pytest.mark.asyncio
async def test_deterministic_distiller_respects_char_budget() -> None:
    findings = [ScoutFinding(path=f"f{i}.py", line=i, snippet="x" * 50, confidence=0.1) for i in range(50)]
    out = await DeterministicDistiller().distill(query="x", findings=findings, token_budget=20)
    assert len(out) <= 20 * 4  # ~4 chars/token
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_distiller.py -v`

- [ ] **Step 3: Write `internal/distiller.py`**

```python
"""Distill findings into a token-bounded summary pack (UTF-8).

P1: deterministic (zero-LLM). P2 adds an LLM-backed implementation behind the
same DistillerPort.
"""
from __future__ import annotations

from polaris.cells.roles.scout.public.contracts import ScoutFinding

_CHARS_PER_TOKEN = 4


class DeterministicDistiller:
    """Zero-cost DistillerPort: format the ranked findings as a compact list."""

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str:
        char_budget = max(40, token_budget * _CHARS_PER_TOKEN)
        header = f"Scout findings for: {query}\n"
        lines: list[str] = []
        used = len(header)
        for f in findings:
            loc = f"{f.path}:{f.line}" if f.line is not None else f.path
            label = f"- {loc}"
            if f.symbol:
                label += f" [{f.symbol}]"
            label += f" — {f.snippet[:120]}"
            if used + len(label) + 1 > char_budget:
                break
            lines.append(label)
            used += len(label) + 1
        if not lines:
            return (header + "(no matching code found)")[:char_budget]
        return (header + "\n".join(lines))[:char_budget]
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_distiller.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/distiller.py src/backend/polaris/cells/roles/scout/internal/tests/test_distiller.py
git commit -m "feat(scout): add deterministic distiller"
```

---

## Task 9: Evidence

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/evidence.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_evidence.py`

Reuse `EvidenceCollector`/`EvidencePackage` (`polaris.domain.verification.evidence_collector`) for the verify-pack hash.

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_evidence.py
from polaris.cells.roles.scout.public.contracts import ScoutFinding
from polaris.cells.roles.scout.internal.evidence import build_content_hash


def test_build_content_hash_is_stable_and_changes_with_summary() -> None:
    findings = [ScoutFinding(path="a.py", line=1, snippet="def f()")]
    h1 = build_content_hash(task_id="t1", findings=findings, summary="s", tools_used=["repo_rg"])
    h2 = build_content_hash(task_id="t1", findings=findings, summary="s", tools_used=["repo_rg"])
    h3 = build_content_hash(task_id="t1", findings=findings, summary="DIFFERENT", tools_used=["repo_rg"])
    assert h1 == h2
    assert h1 != h3
    assert isinstance(h1, str) and len(h1) > 0
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_evidence.py -v`

- [ ] **Step 3: Write `internal/evidence.py`**

```python
"""Build a tamper-evident content hash for a scout report via EvidencePackage (UTF-8)."""
from __future__ import annotations

from polaris.cells.roles.scout.public.contracts import ScoutFinding
from polaris.domain.verification.evidence_collector import EvidenceCollector


def build_content_hash(
    *,
    task_id: str,
    findings: list[ScoutFinding],
    summary: str,
    tools_used: list[str],
) -> str:
    """Record findings as evidence and return EvidencePackage.compute_hash()."""
    collector = EvidenceCollector(task_id=task_id or "scout-probe", iteration=0)
    for tool in tools_used:
        collector.record_tool_execution(tool_name=tool, command=tool, exit_code=0)
    for f in findings:
        collector.record_audit_entry({
            "kind": "scout_finding",
            "path": f.path,
            "line": f.line,
            "symbol": f.symbol,
            "confidence": f.confidence,
        })
    collector.set_summary(summary, acceptance=None)
    return collector.get_package().compute_hash()
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_evidence.py -v`

> If `record_audit_entry`/`record_tool_execution`/`set_summary`/`compute_hash` differ from this signature, open `src/backend/polaris/domain/verification/evidence_collector.py` and align (these names are taken verbatim from that file).

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/evidence.py src/backend/polaris/cells/roles/scout/internal/tests/test_evidence.py
git commit -m "feat(scout): add evidence content-hash builder"
```

---

## Task 10: TTL cache

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/cache.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_cache.py`

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_cache.py
from polaris.cells.roles.scout.internal.cache import TTLCache


def test_cache_returns_value_within_ttl_then_expires() -> None:
    clock = {"t": 100.0}
    cache: TTLCache[str] = TTLCache(ttl_seconds=30, now=lambda: clock["t"])
    cache.set("k", "v")
    assert cache.get("k") == "v"
    clock["t"] = 131.0  # 31s later > ttl
    assert cache.get("k") is None


def test_cache_miss_returns_none() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=30, now=lambda: 0.0)
    assert cache.get("absent") is None
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_cache.py -v`

- [ ] **Step 3: Write `internal/cache.py`**

```python
"""Tiny in-process TTL cache for in-Turn probe de-duplication (UTF-8)."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Single-process TTL cache. Not shared across processes; cleared on GC."""

    def __init__(self, ttl_seconds: float = 60.0, now: Callable[[], float] | None = None) -> None:
        self._ttl = float(ttl_seconds)
        self._now = now or time.monotonic
        self._store: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> T | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._now() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T) -> None:
        self._store[key] = (self._now() + self._ttl, value)
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_cache.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/cache.py src/backend/polaris/cells/roles/scout/internal/tests/test_cache.py
git commit -m "feat(scout): add in-Turn TTL cache"
```

---

## Task 11: ScoutProbeService (wiring)

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/public/service.py`
- Test: `src/backend/polaris/cells/roles/scout/public/tests/test_service.py`

- [ ] **Step 1: Write failing test**

```python
# public/tests/test_service.py
import pytest
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.public.service import ScoutProbeService
from polaris.cells.roles.scout.internal.ports import FakeReadTool, FakeDistiller


@pytest.mark.asyncio
async def test_probe_returns_report_with_findings_and_hash() -> None:
    fake_reads = FakeReadTool({})
    fake_reads._scripted[("repo_rg", ("(def|class|func|function|interface|type)\\s+\\w*payment", "--max", "40"))] = {
        "ok": True, "hits": [{"file": "pay.py", "line": 10, "text": "def payment():"}],
    }
    svc = ScoutProbeService(read_tool=fake_reads, distiller=FakeDistiller("PACK"))
    report = await svc.probe(ScoutProbeTargetV1(query="payment", mode="locate"))
    assert report.summary == "PACK"
    assert report.findings and report.findings[0].path == "pay.py"
    assert report.content_hash
    assert report.cache_hit is False


@pytest.mark.asyncio
async def test_probe_second_call_is_cache_hit() -> None:
    svc = ScoutProbeService(read_tool=FakeReadTool({}), distiller=FakeDistiller("PACK"))
    target = ScoutProbeTargetV1(query="payment")
    first = await svc.probe(target)
    second = await svc.probe(target)
    assert first.cache_hit is False
    assert second.cache_hit is True
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/public/tests/test_service.py -v`

- [ ] **Step 3: Write `public/service.py`**

```python
"""ScoutProbeService — contract-first facade for roles.scout (UTF-8)."""
from __future__ import annotations

import time

from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1, ScoutReportV1
from polaris.cells.roles.scout.internal.cache import TTLCache
from polaris.cells.roles.scout.internal.distiller import DeterministicDistiller
from polaris.cells.roles.scout.internal.evidence import build_content_hash
from polaris.cells.roles.scout.internal.planner import build_read_plan
from polaris.cells.roles.scout.internal.ports import DistillerPort, ReadToolPort
from polaris.cells.roles.scout.internal.ranker import rank
from polaris.cells.roles.scout.internal.retrieval import retrieve


class ScoutProbeService:
    """Synchronous-inline, read-only code/symbol reconnaissance facade."""

    def __init__(
        self,
        read_tool: ReadToolPort,
        distiller: DistillerPort | None = None,
        cache: TTLCache[ScoutReportV1] | None = None,
    ) -> None:
        self._read_tool = read_tool
        self._distiller: DistillerPort = distiller or DeterministicDistiller()
        self._cache: TTLCache[ScoutReportV1] = cache or TTLCache(ttl_seconds=60.0)

    async def probe(self, target: ScoutProbeTargetV1) -> ScoutReportV1:
        key = target.cache_key()
        cached = self._cache.get(key)
        if cached is not None:
            return _with_cache_hit(cached)

        start = time.monotonic()
        plan = build_read_plan(target)
        raw_findings, coverage = retrieve(self._read_tool, plan)
        findings = rank(raw_findings, target)
        summary = await self._distiller.distill(
            query=target.query, findings=findings, token_budget=target.token_budget,
        )
        content_hash = build_content_hash(
            task_id=target.task_id, findings=findings, summary=summary,
            tools_used=coverage.get("tools_used", []),
        )
        confidence = max((f.confidence for f in findings), default=0.0)
        duration_ms = int((time.monotonic() - start) * 1000)
        report = ScoutReportV1(
            findings=tuple(findings),
            summary=summary,
            coverage=coverage,
            confidence=confidence,
            content_hash=content_hash,
            usage={
                "model": "deterministic",
                "tokens": 0,
                "duration_ms": duration_ms,
                "context_saved": _estimate_context_saved(coverage),
            },
            cache_hit=False,
        )
        self._cache.set(key, report)
        return report


def _with_cache_hit(report: ScoutReportV1) -> ScoutReportV1:
    from dataclasses import replace
    return replace(report, cache_hit=True)


def _estimate_context_saved(coverage: dict) -> int:
    """Rough noise the caller avoided: ~200 tokens per raw finding swept."""
    return int(coverage.get("raw_findings", 0)) * 200
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/public/tests/test_service.py -v`

- [ ] **Step 5: Export from `public/__init__.py`**

```python
"""Public surface for roles.scout (UTF-8)."""
from polaris.cells.roles.scout.public.contracts import (
    ScoutFinding,
    ScoutProbeTargetV1,
    ScoutReportV1,
)
from polaris.cells.roles.scout.public.service import ScoutProbeService

__all__ = ["ScoutFinding", "ScoutProbeService", "ScoutProbeTargetV1", "ScoutReportV1"]
```

- [ ] **Step 6: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/public
git commit -m "feat(scout): wire ScoutProbeService probe pipeline"
```

---

## Task 12: Real read-tool adapter (registry SSOT = read-only gate)

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/read_tool_adapter.py`
- Modify: `src/backend/polaris/cells/roles/scout/public/service.py` (add `build_default_scout_service`)
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_read_tool_adapter.py`

The adapter resolves a tool through `ToolSpecRegistry`; it **refuses any tool that is not `is_read_tool()`** — this is the fail-closed read-only gate.

- [ ] **Step 1: Write failing test**

```python
# internal/tests/test_read_tool_adapter.py
import pytest
from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool, ReadOnlyViolation


def test_adapter_refuses_non_read_tool() -> None:
    adapter = RegistryReadTool(workspace=".")
    with pytest.raises(ReadOnlyViolation):
        adapter.run("repo_write", ["--file", "x", "--content", "y"])


def test_adapter_runs_repo_tree_read_tool(tmp_path) -> None:
    (tmp_path / "sample.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    adapter = RegistryReadTool(workspace=str(tmp_path))
    out = adapter.run("repo_rg", ["hello", "--max", "10"])
    assert out["ok"] is True
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_read_tool_adapter.py -v`

- [ ] **Step 3: Write `internal/read_tool_adapter.py`**

```python
"""Real ReadToolPort: resolve read tools via ToolSpecRegistry handlers (UTF-8).

Read-only is enforced here: only tools whose ToolSpec category is `read` run.
"""
from __future__ import annotations

import importlib
from typing import Any

from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


class ReadOnlyViolation(RuntimeError):
    """Raised when a non-read tool is requested through the scout adapter."""


class RegistryReadTool:
    """ReadToolPort backed by the canonical ToolSpecRegistry handler map."""

    def __init__(self, workspace: str, timeout: int = 30) -> None:
        self._workspace = str(workspace or ".")
        self._timeout = int(timeout)

    def run(self, tool: str, args: list[str]) -> dict[str, Any]:
        spec = ToolSpecRegistry.get(tool)
        if spec is None:
            raise ReadOnlyViolation(f"unknown tool: {tool}")
        if not spec.is_read_tool():
            raise ReadOnlyViolation(f"tool {tool!r} is not a read tool (categories={spec.categories})")
        if not spec.handler_module or not spec.handler_function:
            raise ReadOnlyViolation(f"tool {tool!r} has no resolvable handler")
        module = importlib.import_module(spec.handler_module)
        handler = getattr(module, spec.handler_function)
        return handler(list(args), self._workspace, self._timeout)
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_read_tool_adapter.py -v`

> If `repo_rg`'s registry spec lacks `handler_module`/`handler_function`, the second test will surface it. In that case the handler map is incomplete in the SSOT — resolve by reading `ToolSpecRegistry` builtin specs and either using `generate_handler_registry()` or filing the gap; do NOT hard-import `infrastructure.tools` from the cell.

- [ ] **Step 5: Add `build_default_scout_service` to `public/service.py`**

```python
def build_default_scout_service(workspace: str) -> ScoutProbeService:
    """Production factory: registry-backed read tools + deterministic distiller."""
    from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool
    return ScoutProbeService(read_tool=RegistryReadTool(workspace=workspace))
```

Add `build_default_scout_service` to `public/__init__.py` `__all__` and imports.

- [ ] **Step 6: Run full cell test suite**

Run: `pytest src/backend/polaris/cells/roles/scout/ -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/backend/polaris/cells/roles/scout
git commit -m "feat(scout): registry-backed read-only adapter + default factory"
```

---

## Task 13: P1 quality gate + catalog registration

**Files:**
- Modify: `src/backend/docs/graph/catalog/cells.yaml`

- [ ] **Step 1: Run ruff + format on the cell**

Run: `ruff check src/backend/polaris/cells/roles/scout --fix && ruff format src/backend/polaris/cells/roles/scout`
Expected: no remaining errors/warnings

- [ ] **Step 2: Run mypy on the cell**

Run: `mypy src/backend/polaris/cells/roles/scout`
Expected: `Success: no issues found`
Fix any typing issues inline (no `# type: ignore` except the documented `_as_int` arg).

- [ ] **Step 3: Register the cell in the catalog**

Open `src/backend/docs/graph/catalog/cells.yaml`, find the `roles.*` entries, add a `roles.scout` entry mirroring the shape of `roles.runtime` (id, layer, public exports, description). Match the file's existing YAML structure exactly.

- [ ] **Step 4: Full suite + catalog gate**

Run: `pytest src/backend/polaris/cells/roles/scout -v`
Run (if present): `python src/backend/docs/governance/ci/scripts/run_tool_catalog_consistency_gate.py` (no scout_probe tool yet → should pass unchanged)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/scout src/backend/docs/graph/catalog/cells.yaml
git commit -m "feat(scout): P1 quality gate + catalog registration"
```

**P1 DONE — `ScoutProbeService.build_default_scout_service(ws).probe(target)` works end-to-end, read-only, deterministic.**

---

# PHASE P2 — Tool exposure + LLM distiller + template fix

## Task 14: Register `scout_probe` tool in the SSOT

**Files:**
- Modify: `src/backend/polaris/kernelone/tool_execution/tool_spec_registry.py` (the `_BUILTIN_REGISTRY` dict)
- Create: `src/backend/polaris/cells/roles/scout/internal/tool_handler.py`
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_tool_handler.py`, `src/backend/polaris/kernelone/tool_execution/tests/test_tool_spec_registry.py` (add a case)

- [ ] **Step 1: Write failing test for classification + handler**

```python
# internal/tests/test_tool_handler.py
import pytest
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


def test_scout_probe_is_registered_as_read_tool() -> None:
    spec = ToolSpecRegistry.get("scout_probe")
    assert spec is not None
    assert spec.is_read_tool() is True
    assert spec.is_write_tool() is False


@pytest.mark.asyncio
async def test_scout_probe_handler_returns_summary(tmp_path) -> None:
    (tmp_path / "pay.py").write_text("def payment():\n    return 1\n", encoding="utf-8")
    from polaris.cells.roles.scout.internal.tool_handler import scout_probe
    out = scout_probe(["--query", "payment"], str(tmp_path), 30)
    assert out["ok"] is True
    assert "payment" in out["stdout"].lower()
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_tool_handler.py -v`

- [ ] **Step 3: Write `internal/tool_handler.py`** (sync wrapper; the LLM `scout_probe` tool is read-category and deterministic by default)

```python
"""`scout_probe` tool handler — uniform (args, cwd, timeout) signature (UTF-8)."""
from __future__ import annotations

import asyncio
from typing import Any

from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.public.service import build_default_scout_service


def scout_probe(args: list[str], cwd: str, timeout: int) -> dict[str, Any]:
    """Run a deterministic read-only probe. args: --query <q> [--mode locate|boundary]."""
    _ = timeout
    query = ""
    mode = "locate"
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--query", "-q") and i + 1 < len(args):
            query = args[i + 1]
            i += 2
            continue
        if token == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            i += 2
            continue
        if not query and not token.startswith("--"):
            query = token
        i += 1
    if not query.strip():
        return {"ok": False, "tool": "scout_probe", "error": "Usage: scout_probe --query <text> [--mode locate|boundary]"}

    service = build_default_scout_service(workspace=cwd)
    target = ScoutProbeTargetV1(query=query, mode=mode if mode in ("locate", "boundary") else "locate")
    report = asyncio.run(service.probe(target))
    return {
        "ok": True,
        "tool": "scout_probe",
        "stdout": report.summary,
        "findings": [f.to_dict() for f in report.findings],
        "content_hash": report.content_hash,
        "coverage": report.coverage,
    }
```

- [ ] **Step 4: Register the spec in `_BUILTIN_REGISTRY`**

In `tool_spec_registry.py`, add an entry to `_BUILTIN_REGISTRY` mirroring an existing read tool's dict shape (e.g. copy the `repo_rg` entry and adapt). Required keys: `description`, `aliases`, `categories: ["read"]`, `arguments`, `handler_module`, `handler_function`:

```python
"scout_probe": {
    "description": "Read-only code/symbol reconnaissance: given a fuzzy query, return a distilled findings pack. Side-effect-free.",
    "aliases": [],
    "categories": ["read"],
    "arguments": [
        {"name": "query", "type": "string", "description": "Fuzzy target, e.g. 'where is payment error handling'", "required": True},
        {"name": "mode", "type": "string", "description": "locate | boundary", "required": False, "default": "locate"},
    ],
    "handler_module": "polaris.cells.roles.scout.internal.tool_handler",
    "handler_function": "scout_probe",
},
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_tool_handler.py src/backend/polaris/kernelone/tool_execution/tests/test_tool_spec_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/backend/polaris/kernelone/tool_execution/tool_spec_registry.py src/backend/polaris/cells/roles/scout/internal/tool_handler.py src/backend/polaris/cells/roles/scout/internal/tests/test_tool_handler.py
git commit -m "feat(scout): register scout_probe as a read tool"
```

---

## Task 15: Whitelist `scout_probe` for Director + PM

**Files:**
- Modify: `src/backend/polaris/cells/roles/profile/internal/builtin_profiles.py` (director + pm `tool_policy.whitelist`)
- Test: `src/backend/polaris/cells/roles/profile/tests/test_registry.py` (add cases) or a new test under the scout cell

- [ ] **Step 1: Write failing test**

```python
# add to internal/tests/test_tool_handler.py (or a new test_whitelist.py)
from polaris.cells.roles.profile.internal.builtin_profiles import BUILTIN_PROFILES


def _profile(role: str):
    return next(p for p in BUILTIN_PROFILES if getattr(p, "role_id", None) == role)


def test_director_and_pm_can_call_scout_probe() -> None:
    for role in ("director", "pm"):
        wl = _profile(role).tool_policy.whitelist
        assert "scout_probe" in wl
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_tool_handler.py -v -k scout_probe`
(If `BUILTIN_PROFILES` element/attr access differs, open `builtin_profiles.py` and match the actual structure — adapt `_profile`/`tool_policy.whitelist` accessors to the real shape.)

- [ ] **Step 3: Add `scout_probe` to director + pm whitelists in `builtin_profiles.py`**

Find the director and pm profile definitions; add `"scout_probe"` to each `tool_policy` whitelist tuple/list, matching the file's existing literal style.

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests -v -k scout_probe`

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/profile/internal/builtin_profiles.py src/backend/polaris/cells/roles/scout/internal/tests
git commit -m "feat(scout): whitelist scout_probe for director and pm"
```

---

## Task 16: LLM-backed distiller (cheap model via ProviderManager)

**Files:**
- Modify: `src/backend/polaris/cells/roles/scout/internal/distiller.py` (add `LLMDistiller`)
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_distiller.py` (add a mocked case)

- [ ] **Step 1: Write failing test (mock the LLM call)**

```python
# add to internal/tests/test_distiller.py
import pytest
from polaris.cells.roles.scout.public.contracts import ScoutFinding
from polaris.cells.roles.scout.internal.distiller import LLMDistiller


@pytest.mark.asyncio
async def test_llm_distiller_uses_injected_caller_and_falls_back_on_error() -> None:
    async def good_caller(prompt: str) -> str:
        return "LLM SUMMARY"

    async def bad_caller(prompt: str) -> str:
        raise RuntimeError("provider down")

    findings = [ScoutFinding(path="a.py", line=1, snippet="def pay()", confidence=0.9)]
    ok = await LLMDistiller(call=good_caller).distill(query="pay", findings=findings, token_budget=100)
    assert ok == "LLM SUMMARY"

    fb = await LLMDistiller(call=bad_caller).distill(query="pay", findings=findings, token_budget=100)
    assert "a.py:1" in fb  # falls back to deterministic
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_distiller.py -v -k llm`

- [ ] **Step 3: Add `LLMDistiller` to `internal/distiller.py`**

```python
from collections.abc import Awaitable, Callable


class LLMDistiller:
    """DistillerPort backed by a cheap model; falls back to deterministic on error.

    `call` is an injected async function `(prompt: str) -> str`. The production
    wiring binds it to a cheap model via ProviderManager (see build_default_scout_service).
    """

    def __init__(self, call: Callable[[str], Awaitable[str]]) -> None:
        self._call = call
        self._fallback = DeterministicDistiller()

    async def distill(self, *, query: str, findings: list[ScoutFinding], token_budget: int) -> str:
        baseline = await self._fallback.distill(query=query, findings=findings, token_budget=token_budget)
        prompt = (
            f"Compress these code-search findings into <= {token_budget} tokens answering: {query}\n\n"
            f"{baseline}\n\nReturn only the distilled answer."
        )
        try:
            out = await self._call(prompt)
        except (RuntimeError, ValueError, TimeoutError):
            return baseline
        text = str(out or "").strip()
        return text or baseline
```

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_distiller.py -v`

- [ ] **Step 5: Wire the cheap-model caller in `build_default_scout_service`** (read `polaris.cells.llm.dialogue.internal.role_dialogue._resolve_role_provider_model` and the `ProviderManager` usage there to bind a `call(prompt)->str` to a scout/cheap model; keep deterministic as default if no provider configured). Add a test that `build_default_scout_service` returns a service whose distiller is `LLMDistiller` when a provider is configured, else `DeterministicDistiller`.

> Do NOT bypass `ProviderManager` (CLAUDE.md §7.3). If provider wiring is non-trivial, keep `DeterministicDistiller` as the default and land `LLMDistiller` wiring as its own follow-up commit — P1 already guarantees a working zero-LLM path.

- [ ] **Step 6: Commit**

```bash
git add src/backend/polaris/cells/roles/scout/internal/distiller.py src/backend/polaris/cells/roles/scout/internal/tests/test_distiller.py
git commit -m "feat(scout): add LLM-backed distiller with deterministic fallback"
```

---

## Task 17: Fix fictional SCOUT_TEMPLATE / profile tool names

**Files:**
- Modify: `src/backend/polaris/kernelone/roles/templates/preset_templates.py` (`SCOUT_TEMPLATE.tools`)
- Modify: `src/backend/polaris/cells/roles/profile/internal/builtin_profiles.py` (scout profile tool whitelist, if it lists fictional names)
- Test: `src/backend/polaris/tests/kernelone/roles/templates/test_preset_templates.py` (update `test_scout_has_read_only_tools`)

- [ ] **Step 1: Update the failing/again test to assert REAL tools**

Edit `test_scout_has_read_only_tools` so it asserts the scout tools are a subset of real read tools:

```python
def test_scout_has_read_only_tools() -> None:
    real_read = {"repo_rg", "repo_tree", "repo_glob", "repo_read_slice", "repo_symbols_index", "file_exists", "scout_probe"}
    assert set(SCOUT_TEMPLATE.tools).issubset(real_read)
```

- [ ] **Step 2: Run test — expect FAIL** (current tools are fictional `codebase_search` etc.)

Run: `pytest src/backend/polaris/tests/kernelone/roles/templates/test_preset_templates.py::test_scout_has_read_only_tools -v`

- [ ] **Step 3: Replace `SCOUT_TEMPLATE.tools`** with real tools:

```python
    tools=(
        "repo_rg",
        "repo_tree",
        "repo_glob",
        "repo_read_slice",
        "repo_symbols_index",
        "file_exists",
    ),
```

Apply the same correction to the scout profile in `builtin_profiles.py` if it enumerates the fictional names.

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/tests/kernelone/roles/templates/test_preset_templates.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/kernelone/roles/templates/preset_templates.py src/backend/polaris/cells/roles/profile/internal/builtin_profiles.py src/backend/polaris/tests/kernelone/roles/templates/test_preset_templates.py
git commit -m "fix(scout): replace fictional SCOUT_TEMPLATE tool names with real read tools"
```

---

## Task 18: P2 gate + tool-catalog consistency

- [ ] **Step 1: ruff + mypy**

Run: `ruff check src/backend/polaris/cells/roles/scout --fix && ruff format src/backend/polaris/cells/roles/scout && mypy src/backend/polaris/cells/roles/scout`
Expected: clean / `Success: no issues found`

- [ ] **Step 2: tool-catalog consistency gate (now that scout_probe exists)**

Run: `python src/backend/docs/governance/ci/scripts/run_tool_catalog_consistency_gate.py`
Expected: PASS. If it fails because `scout_probe` is missing from a catalog doc, add `scout_probe` to the catalog the gate names, then re-run.

- [ ] **Step 3: Broader regression (profiles + tool registry + scout)**

Run: `pytest src/backend/polaris/cells/roles/scout src/backend/polaris/cells/roles/profile src/backend/polaris/kernelone/tool_execution -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add -A src/backend/polaris/cells/roles/scout src/backend/docs
git commit -m "chore(scout): P2 gates + catalog consistency for scout_probe"
```

**P2 DONE — PM/Director can call `scout_probe`; templates reflect reality; optional cheap-model distill.**

---

# PHASE P3 — Escalation to a governed scout role-session

## Task 19: Escalation bridge

**Files:**
- Create: `src/backend/polaris/cells/roles/scout/internal/escalation.py`
- Modify: `src/backend/polaris/cells/roles/scout/public/service.py` (escalate on low confidence when `allow_escalation`)
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_escalation.py`

- [ ] **Step 1: Write failing test (mock the role runtime)**

```python
# internal/tests/test_escalation.py
import pytest
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
from polaris.cells.roles.scout.internal.escalation import escalate_probe


class _FakeResult:
    output = "ESCALATED FINDINGS"
    metadata: dict = {}
    error_message = ""


@pytest.mark.asyncio
async def test_escalate_probe_invokes_role_runtime(monkeypatch) -> None:
    captured = {}

    async def fake_exec(self, command):  # noqa: ANN001
        captured["role"] = command.role
        return _FakeResult()

    from polaris.cells.roles.runtime.public import service as runtime_service
    monkeypatch.setattr(runtime_service.RoleRuntimeService, "execute_role_session", fake_exec)

    out = await escalate_probe(ScoutProbeTargetV1(query="x", allow_escalation=True), workspace=".")
    assert captured["role"] == "scout"
    assert out == "ESCALATED FINDINGS"
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_escalation.py -v`

- [ ] **Step 3: Write `internal/escalation.py`**

```python
"""Escalate a fuzzy probe to a governed scout role-session (read-only) (UTF-8)."""
from __future__ import annotations


async def escalate_probe(target, workspace: str) -> str:  # noqa: ANN001
    """Run scout via execute_role_session. Returns the role output text.

    Read-only is enforced by the scout profile (allow_code_write=False) +
    SandboxPolicy at the kernel; this bridge adds no write capability.
    """
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    command = ExecuteRoleSessionCommandV1(
        role="scout",
        session_id=f"scout-probe-{target.cache_key()}",
        workspace=str(workspace or "."),
        user_message=target.query,
        domain="code",
        metadata={
            "source": "roles.scout.escalation",
            "read_only": True,
            "fallback_policy": "fail_closed",
        },
    )
    result = await RoleRuntimeService().execute_role_session(command)
    return str(getattr(result, "output", "") or "").strip()
```

> Confirm `ExecuteRoleSessionCommandV1`'s required fields against `cells/roles/runtime/public/contracts.py` (role/session_id/workspace/user_message are used by the existing `role_dialogue` caller). Add any other required-without-default fields the dataclass enforces.

- [ ] **Step 4: Wire escalation into `ScoutProbeService.probe`** — after computing `confidence`, if `target.allow_escalation and confidence < 0.3 and not findings`, call `escalate_probe`, set `summary` to its output, `escalated=True`. Add a service test (`monkeypatch` `escalate_probe`) asserting `report.escalated is True` for an empty deterministic result with `allow_escalation=True`.

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout -v`

- [ ] **Step 6: Commit**

```bash
git add src/backend/polaris/cells/roles/scout
git commit -m "feat(scout): add read-only escalation to scout role-session"
```

---

## Task 20: Ensure scout profile is resolvable by RoleRuntimeService

**Files:**
- Modify (if needed): `src/backend/polaris/cells/roles/profile/internal/builtin_profiles.py` and/or `registry.py` so `RoleRuntimeService._get_kernel(...).registry` can resolve role `"scout"` with a valid system prompt.
- Test: `src/backend/polaris/cells/roles/scout/internal/tests/test_escalation.py` (add resolution test)

- [ ] **Step 1: Write failing/again test**

```python
def test_scout_profile_is_registered_with_prompt() -> None:
    from polaris.cells.roles.profile.internal.registry import registry, load_core_roles
    if not registry.has_role("scout"):
        load_core_roles()
    profile = registry.get_profile("scout")
    assert profile is not None
    assert profile.prompt_policy.core_template_id  # has a real system prompt
```

- [ ] **Step 2: Run test**

Run: `pytest src/backend/polaris/cells/roles/scout/internal/tests/test_escalation.py::test_scout_profile_is_registered_with_prompt -v`
If it FAILS (scout not registered or no `core_template_id`), proceed to Step 3; if it PASSES, scout is already resolvable — skip to Step 4.

- [ ] **Step 3: Register scout profile with a prompt** (do NOT add scout to `CORE_ROLES`; keep it auxiliary). Ensure `load_core_roles()` (or the builtin loader) also registers the scout profile with a `core_template_id` pointing to a scout system prompt string. Reuse the `SCOUT_TEMPLATE.prompts["system"]` text.

- [ ] **Step 4: Run test — expect PASS**

Run: `pytest src/backend/polaris/cells/roles/scout -v`

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/roles/profile src/backend/polaris/cells/roles/scout
git commit -m "feat(scout): make scout profile resolvable for escalation"
```

---

## Task 21: P3 gate + final regression

- [ ] **Step 1: ruff + mypy on the cell + touched files**

Run: `ruff check src/backend/polaris/cells/roles/scout --fix && ruff format src/backend/polaris/cells/roles/scout && mypy src/backend/polaris/cells/roles/scout`
Expected: clean

- [ ] **Step 2: Targeted regression**

Run: `pytest src/backend/polaris/cells/roles/scout src/backend/polaris/cells/roles/profile src/backend/polaris/cells/roles/runtime src/backend/polaris/kernelone/tool_execution -v`
Expected: PASS

- [ ] **Step 3: Confirm TaskMarket untouched**

Run: `python src/backend/docs/governance/ci/scripts/check_task_market_single_broker.py`
Expected: PASS (no new broker introduced)

- [ ] **Step 4: Commit**

```bash
git add -A src/backend/polaris/cells/roles/scout
git commit -m "chore(scout): P3 gates + final regression"
```

**P3 DONE — fuzzy probes escalate to a governed read-only scout session.**

---

## Deferred (P4, not in this plan)
Akashic persistence of high-value findings; transient vector DB; log-distillation and pre-flight-dry-run jobs; multi-protocol probes; self-throttling. Track separately.

---

## Plan Self-Review

- **Spec coverage:** §3 approach C → Tasks 1–12; §4 cell skeleton → Task 1; §5 contracts → Task 2; §6 pipeline → Tasks 4–11; §7 read-only gate → Task 12 (adapter `is_read_tool` refusal) + Task 19 (profile/sandbox); §8 call sites → Tasks 14–15; §9 model/cost → Tasks 8 & 16 + `context_saved` (Task 11); §10 verification/governance → Tasks 13, 18, 21; §11 phasing P1/P2/P3 → phase headers; §12 decisions (cells/roles/scout, Director+PM, P3) → Tasks 1/15/19. P4 explicitly deferred.
- **Placeholder scan:** No "TBD/implement later". Two honest "confirm signature against <file>" notes (evidence_collector, ExecuteRoleSessionCommandV1, BUILTIN_PROFILES shape) point at exact files with the verbatim names already used — these are alignment checks, not vague hand-waving.
- **Type consistency:** `ReadToolPort.run(tool, args)`, `DistillerPort.distill(*, query, findings, token_budget)`, `ScoutProbeTargetV1.cache_key()`, `ScoutReportV1` fields, `build_content_hash(...)`, `build_default_scout_service(workspace)`, `RegistryReadTool`/`ReadOnlyViolation`, `escalate_probe(target, workspace)` are used identically across tasks.

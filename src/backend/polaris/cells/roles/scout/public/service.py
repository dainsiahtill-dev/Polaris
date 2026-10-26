"""ScoutProbeService — contract-first facade for roles.scout (UTF-8).

Authority model
---------------
The Scout composite never invents read authority. Every physical read runs
through the canonical ``AgentAccelToolExecutor``, which this cell configures
from the HOST's committed facts:

* ``capability_token`` — the host's token, carried verbatim. The canonical
  read-scope gate therefore applies the host's committed scope unchanged: a
  committed scope stays committed, an explicitly empty scope stays empty and
  fails closed, and a missing scope key keeps the legacy undefined scope.
* ``allowed_tools`` — the host's executor-level whitelist, carried verbatim so
  the Scout composite can never execute a tool the host forbade.
* ``read_roots`` — the roots the host committed for reading, used ONLY to plan
  the search so every authorized root is actually searched. Model hints may
  narrow these roots; they never add one and never widen one to a parent
  directory.

``ScoutReadAuthorityV1`` is the public carrier for those facts.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.roles.scout.internal.cache import TTLCache
from polaris.cells.roles.scout.internal.distiller import DeterministicDistiller
from polaris.cells.roles.scout.internal.evidence import build_content_hash
from polaris.cells.roles.scout.internal.ports import DistillerPort, ReadToolPort
from polaris.cells.roles.scout.internal.ranker import rank
from polaris.cells.roles.scout.internal.retrieval import retrieve
from polaris.cells.roles.scout.public.contracts import ScoutFinding, ScoutProbeTargetV1, ScoutReportV1


@dataclass(frozen=True)
class ScoutReadAuthorityV1:
    """Host-committed read authority for one Scout composite call.

    Attributes:
        capability_token: The host executor's committed token, carried
            verbatim to the executor that performs the reads.
        allowed_tools: The host executor's canonical tool whitelist, or None
            when the host declared no executor-level restriction.
        read_roots: Workspace-relative roots the host committed for reading.
            Empty means "no root may be read".
        borrowed_executor: The host's own executor, when the host offers one.
            The Scout cell then performs every read THROUGH it, which
            inherits the host's scope, whitelist and failure budget exactly.
            A borrowed executor is owned by the host and is never closed by
            the Scout cell.
        authority_reader: A callable returning the host's CURRENT authority
            (token + roots + whitelist) at the moment of use. It exists so a
            cached report can be re-validated against the LIVE host authority
            instead of a frozen snapshot: a host that shrinks its scope after
            the report was produced must not keep serving it.
        tool_authorizer: The host's own per-tool authorization decision, as
            ``(allowed, reason)``. The Scout composite expands the host's
            authority surface (one ``scout_probe`` dispatch runs several read
            tools), so each nested read must be authorized by the SAME
            authority that authorized the dispatch — not by the executor's
            whitelist alone, which does not carry the host's blacklist.
    """

    capability_token: Mapping[str, Any] = field(default_factory=dict)
    allowed_tools: frozenset[str] | None = None
    read_roots: tuple[str, ...] = ()
    borrowed_executor: Any | None = None
    authority_reader: Callable[[], "ScoutReadAuthorityV1"] | None = None
    tool_authorizer: Callable[[str, Mapping[str, Any]], tuple[bool, str]] | None = None

    def __post_init__(self) -> None:
        # Defensive snapshot: the caller must not be able to mutate the
        # authority AFTER construction and desynchronize it from the cache
        # key. The authority object is the service's own source of truth.
        object.__setattr__(self, "capability_token", dict(self.capability_token))
        object.__setattr__(self, "read_roots", tuple(self.read_roots))

    @property
    def scope_committed(self) -> bool:
        """True when the host committed a read scope (even an empty one).

        A missing ``allowed_read_paths`` key means the legacy undefined scope:
        the host declared nothing, so this authority does not claim that its
        reads were scope-enforced.
        """
        return "allowed_read_paths" in self.capability_token

    @property
    def cache_hits_permitted(self) -> bool:
        """True when a cached report may be served for this authority.

        A borrowed executor performs the reads, and its own state is what
        actually gates them. That state can change underneath us (the host
        may narrow its scope) and it can only be re-observed through an
        ``authority_reader``. Without such a reader the authority is merely a
        SNAPSHOT of a state we cannot re-read, so an old snapshot must never
        be promoted to the current permission — the cache is then not usable
        for hits and every probe re-reads through the borrowed executor,
        which applies its own current gate.

        Without a borrowed executor the authority object IS the service's
        authority (the reads run under exactly this snapshot), so caching
        stays available.
        """
        return self.authority_reader is not None or self.borrowed_executor is None

    def live(self) -> "ScoutReadAuthorityV1":
        """The host's CURRENT authority, re-read at the moment of use.

        Falls back to this instance when the host offers no reader, which is
        the case for a caller that owns an immutable authority.
        """
        if self.authority_reader is None:
            return self
        return self.authority_reader()


class ScoutProbeDeniedError(RuntimeError):
    """Raised when the host authority denies every read of a probe."""


class ScoutProbeService:
    """Synchronous-inline, read-only code/symbol reconnaissance facade."""

    def __init__(
        self,
        read_tool: ReadToolPort,
        distiller: DistillerPort | None = None,
        cache: TTLCache[ScoutReportV1] | None = None,
        workspace: str = ".",
        authority: ScoutReadAuthorityV1 | None = None,
    ) -> None:
        self._read_tool = read_tool
        self._distiller: DistillerPort = distiller or DeterministicDistiller()
        self._cache: TTLCache[ScoutReportV1] = cache or TTLCache(ttl_seconds=60.0)
        self._workspace = str(workspace or ".")
        self._authority: ScoutReadAuthorityV1 = authority if authority is not None else ScoutReadAuthorityV1()

    async def probe(self, target: ScoutProbeTargetV1) -> ScoutReportV1:
        # The host's CURRENT authority is read at the moment of use. A host
        # that shrinks its scope after a report was cached must not keep
        # serving that report.
        authority = self._authority.live()
        self._assert_readable_authority(authority)

        # A borrowed executor's authority can only be re-observed through an
        # authority_reader. Without one, this authority is a snapshot we
        # cannot re-read, so a cached report must NOT be served as the current
        # permission: the probe re-reads through the borrowed executor, which
        # applies its own current gate. Serving a stale snapshot here would
        # promote an old permission to the current one.
        cached: ScoutReportV1 | None = None
        if authority.cache_hits_permitted:
            cached = self._cache.get(self._cache_key(target, authority))
        if cached is not None:
            self._assert_cached_report_is_authorized(cached, authority)
            return _with_cache_hit(cached)

        start = time.monotonic()
        # Imported lazily: the planner imports the public contracts, whose
        # package __init__ imports this module. A module-level import would
        # close that cycle and make ``roles.scout.internal.planner``
        # unimportable on its own.
        from polaris.cells.roles.scout.internal.planner import build_read_plan

        plan = build_read_plan(target, read_roots=self._planned_roots(authority))
        raw_findings, coverage = retrieve(self._read_tool, plan)
        self._assert_readable_outcome(coverage)
        findings = rank(raw_findings, target)
        summary = await self._distiller.distill(
            query=target.query,
            findings=findings,
            token_budget=target.token_budget,
        )
        content_hash = build_content_hash(
            task_id=target.task_id,
            findings=findings,
            summary=summary,
            tools_used=coverage.get("tools_used", []),
        )
        confidence = max((f.confidence for f in findings), default=0.0)

        escalated = False
        if target.allow_escalation and authority.scope_committed:
            # A role-session escalation cannot carry this service's committed
            # read scope, so it is refused instead of silently widening reads.
            coverage["escalation_blocked"] = "committed read scope cannot be carried into a role-session escalation"
        elif target.allow_escalation and _is_weak_result(findings, confidence):
            escalated_summary = await self._escalate(target)
            if escalated_summary:
                summary = escalated_summary
                escalated = True

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
            escalated=escalated,
        )
        self._cache.set(self._cache_key(target, authority), report)
        return report

    async def _escalate(self, target: ScoutProbeTargetV1) -> str:
        """Bridge to a governed read-only scout role-session. Returns its output."""
        from polaris.cells.roles.scout.internal.escalation import escalate_probe

        escalated = await escalate_probe(target, workspace=self._workspace)
        return str(escalated)

    def close(self) -> None:
        """Release the read tool's own resources.

        The executor performing the reads is owned by this service's read tool;
        the host's own executor is never closed here.
        """
        closer = getattr(self._read_tool, "close", None)
        if callable(closer):
            closer()

    def _cache_key(self, target: ScoutProbeTargetV1, authority: ScoutReadAuthorityV1) -> str:
        """Bind the cache to the workspace AND the authority identity.

        Two probes may only share a cached report when they would have been
        authorized identically. The key therefore includes:

        * the workspace — two workspaces with the same relative roots must
          never share a report;
        * the committed read roots and the tool whitelist;
        * an identity fingerprint of the authorizing token, so a report produced
          under one authority is never reused under another.
        """
        tools = "*" if authority.allowed_tools is None else ",".join(sorted(authority.allowed_tools))
        roots = "|".join(authority.read_roots)
        scope = "1" if authority.scope_committed else "0"
        identity = _authority_identity(authority.capability_token)
        return f"{target.cache_key()}|ws={self._workspace}|scope={scope}|roots={roots}|tools={tools}|id={identity}"

    def _planned_roots(self, authority: ScoutReadAuthorityV1) -> tuple[str, ...] | None:
        """The roots the planner may search.

        None means "no committed scope" (legacy undefined scope): the planner
        keeps its historical hint/default behaviour.
        """
        if not authority.scope_committed:
            return None
        return authority.read_roots

    def _assert_readable_authority(self, authority: ScoutReadAuthorityV1) -> None:
        """Refuse up front when the committed scope authorizes no readable path."""
        if authority.scope_committed and not authority.read_roots:
            raise ScoutProbeDeniedError("committed read scope authorizes no readable path; scout probe refused")

    @staticmethod
    def _assert_cached_report_is_authorized(
        report: ScoutReportV1,
        authority: ScoutReadAuthorityV1,
    ) -> None:
        """Refuse a cached report the CURRENT authority no longer authorizes.

        The cached report was produced under an earlier authority. If the live
        authority now authorizes fewer roots, a report that names a path
        outside the live roots must not be served — it would expose content
        the host no longer permits. Such a report is refused, and the probe
        fails closed instead of falling back to the stale entry.
        """
        if not authority.scope_committed:
            return
        roots = authority.read_roots
        if not roots:
            raise ScoutProbeDeniedError("committed read scope authorizes no readable path; scout probe refused")
        for finding in report.findings:
            if not _path_within_roots(finding.path, roots):
                raise ScoutProbeDeniedError(
                    f"cached scout report names a path outside the current read scope: {finding.path}"
                )

    @staticmethod
    def _assert_readable_outcome(coverage: dict[str, Any]) -> None:
        """Refuse when no read tool was actually permitted to run.

        A partially denied probe keeps its concrete errors AND its real
        results; only a probe where every read was denied fails. A read that
        succeeded with zero hits is a success, not a denial.
        """
        if coverage.get("successful_calls"):
            return
        detail = "; ".join(str(item) for item in coverage.get("errors", [])[:5])
        raise ScoutProbeDeniedError(
            "every read tool was denied by the host read authority"
            + (f" ({detail})" if detail else " (no read tool was executed)")
        )


_WEAK_CONFIDENCE_THRESHOLD = 0.3


def _authority_identity(token: Mapping[str, Any]) -> str:
    """Fingerprint the identity that authorized a probe.

    Two probes may only share a cached report when the authorizing identity is
    the same. The fingerprint covers the identity-bearing fields of the
    committed token (token/run/project/stage ids and the contract hash). The
    read scope itself is bound separately by the roots and the scope flag, so
    a SHRINKING of the scope cannot reuse a report produced under a wider one.
    """
    import hashlib
    import json

    identity_keys = (
        "token_id",
        "run_id",
        "project_id",
        "stage",
        "contract_hash",
        "source",
    )
    basis = {key: str(token.get(key) or "") for key in identity_keys}
    blob = json.dumps(basis, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]



def _path_within_roots(path: str, roots: tuple[str, ...]) -> bool:
    """True when ``path`` denotes a root or something inside one of them."""
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    for root in roots:
        root_norm = str(root or "").replace("\\", "/").strip("/")
        if not root_norm:
            continue
        if normalized == root_norm or normalized.startswith(f"{root_norm}/"):
            return True
    return False

def _is_weak_result(findings: list[ScoutFinding], confidence: float) -> bool:
    """A deterministic result is weak when it has no findings or low confidence."""
    return not findings or confidence < _WEAK_CONFIDENCE_THRESHOLD


def _with_cache_hit(report: ScoutReportV1) -> ScoutReportV1:
    from dataclasses import replace

    return replace(report, cache_hit=True)


def _estimate_context_saved(coverage: dict) -> int:  # type: ignore[type-arg]
    """Rough noise the caller avoided: ~200 tokens per raw finding swept."""
    return int(coverage.get("raw_findings", 0)) * 200


def build_default_scout_service(
    workspace: str,
    *,
    authority: ScoutReadAuthorityV1 | None = None,
) -> ScoutProbeService:
    """Production factory: registry-backed read tools + deterministic distiller.

    ``authority`` carries the host's committed read scope and tool whitelist.
    Without it the factory keeps the legacy behaviour of owning an unscoped
    executor — reachable only by direct API callers that never had a scope.
    """
    from polaris.cells.roles.scout.internal.read_tool_adapter import RegistryReadTool

    return ScoutProbeService(
        read_tool=RegistryReadTool(workspace=workspace, authority=authority),
        workspace=workspace,
        authority=authority,
    )


__all__ = [
    "ScoutProbeDeniedError",
    "ScoutProbeService",
    "ScoutReadAuthorityV1",
    "build_default_scout_service",
]

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

Authority snapshot
------------------
The authority is an IMMUTABLE snapshot of the host's committed facts. It is
taken by value at construction (the host's token is copied deeply and its
nested sequences become immutable), so a caller that mutates the object it
passed in can no longer desynchronize the authority from the cache key.

Cache policy
------------
A cached report is only served when the CURRENT authority still authorizes it:

* the live scope must still authorize every path the report names, and
* the host's CURRENT per-tool decisions for the planned reads must be bound
  into the cache key, so a host that forbids a read tool (its blacklist above
  all) cannot keep serving a report produced while that tool was allowed.

When the authority borrows an executor but offers no ``authority_reader``, the
executor's own gate cannot be re-observed, so no cached report may be promoted
to the current permission: the probe re-reads through the borrowed executor,
which applies its own current gate. That is the ONE case where the cache is
not usable for hits, and it is the case where liveness cannot be proven.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from polaris.cells.roles.scout.internal.cache import TTLCache
from polaris.cells.roles.scout.internal.distiller import DeterministicDistiller
from polaris.cells.roles.scout.internal.evidence import build_content_hash
from polaris.cells.roles.scout.internal.ports import DistillerPort, ReadToolPort
from polaris.cells.roles.scout.internal.ranker import rank
from polaris.cells.roles.scout.internal.retrieval import retrieve
from polaris.cells.roles.scout.public.contracts import ScoutFinding, ScoutProbeTargetV1, ScoutReportV1

logger = logging.getLogger(__name__)


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
    authority_reader: Callable[[], ScoutReadAuthorityV1] | None = None
    tool_authorizer: Callable[[str, Mapping[str, Any]], tuple[bool, str]] | None = None

    def __post_init__(self) -> None:
        # Defensive snapshot: the caller must not be able to mutate the
        # authority AFTER construction and desynchronize it from the cache
        # key. The authority object is the service's own source of truth.
        #
        # The host's token is taken BY VALUE, deeply: a caller that keeps a
        # reference to the list it passed in (the classic
        # ``allowed_read_paths`` list) can no longer empty or widen the
        # authority after the fact. The top level is a read-only mapping and
        # every nested sequence becomes an immutable tuple, so the committed
        # scope facts cannot be edited in place either.
        token = _freeze_token(self.capability_token)
        object.__setattr__(self, "capability_token", token)
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

        This describes the authority HOLDER (the object the host handed to the
        service), because the holder is what knows whether the host's
        authority can be re-read at all:

        * a borrowed executor performs the reads, and its own state is what
          actually gates them. That state can change underneath us (the host
          may narrow its scope) and it can only be re-observed through an
          ``authority_reader``. Without such a reader the authority is merely
          a SNAPSHOT of a state we cannot re-read, so an old snapshot must
          never be promoted to the current permission — the cache is then not
          usable for hits and every probe re-reads through the borrowed
          executor, which applies its own current gate.

        * without a borrowed executor the authority object IS the service's
          authority (the reads run under exactly this snapshot), so caching
          stays available.

        The live snapshot returned by ``live()`` legitimately carries no
        reader of its own, so the service consults the HOLDER's copy — see
        ``ScoutProbeService._cache_hits_permitted``.
        """
        return self.authority_reader is not None or self.borrowed_executor is None

    def live(self) -> ScoutReadAuthorityV1:
        """The host's CURRENT authority, re-read at the moment of use.

        Falls back to this instance when the host offers no reader, which is
        the case for a caller that owns an immutable authority.

        The returned snapshot inherits this holder's ``tool_authorizer``: the
        host's per-tool decision is part of the host's CURRENT authority, so
        the live snapshot must carry it too. Dropping it would make the cache
        key blind to the host's tool policy — the host's blacklist above all —
        and a report produced while a read tool was allowed could then be
        served after the host forbade it.
        """
        if self.authority_reader is None:
            return self
        live = self.authority_reader()
        if live.tool_authorizer is None and self.tool_authorizer is not None:
            live = replace(live, tool_authorizer=self.tool_authorizer)
        return live


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

        # Imported lazily: the planner imports the public contracts, whose
        # package __init__ imports this module. A module-level import would
        # close that cycle and make ``roles.scout.internal.planner``
        # unimportable on its own.
        from polaris.cells.roles.scout.internal.planner import build_read_plan

        plan = build_read_plan(target, read_roots=self._planned_roots(authority))

        # A cached report may only be served when the CURRENT authority still
        # authorizes it. The cache key binds the workspace, the committed read
        # roots and scope, the authorizing identity AND the host's current
        # per-tool decisions for the planned reads, so a host that forbids a
        # read tool (its blacklist above all) cannot keep serving a report
        # produced while that tool was allowed.
        #
        # A borrowed executor's authority can only be re-observed through an
        # authority_reader. Without one, this authority is a snapshot we
        # cannot re-read, so a cached report must NOT be served as the current
        # permission: the probe re-reads through the borrowed executor, which
        # applies its own current gate. Serving a stale snapshot here would
        # promote an old permission to the current one.
        cache_key = self._cache_key(target, authority, plan)
        cached: ScoutReportV1 | None = None
        if self._cache_hits_permitted():
            cached = self._cache.get(cache_key)
        if cached is not None:
            self._assert_cached_report_is_authorized(cached, authority)
            return _with_cache_hit(cached)

        start = time.monotonic()
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
        self._cache.set(cache_key, report)
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

    def _cache_hits_permitted(self) -> bool:
        """True when this service may serve a cached report at all.

        The HOLDER's authority is the one that knows whether the host's
        authority can be re-read at the moment of use. The live snapshot
        returned by ``live()`` carries no reader of its own, so consulting it
        would always disable the cache and punish the legal, stable case the
        host explicitly supports with an ``authority_reader``.
        """
        return self._authority.cache_hits_permitted

    def _cache_key(
        self,
        target: ScoutProbeTargetV1,
        authority: ScoutReadAuthorityV1,
        plan: list[tuple[str, dict[str, Any]]],
    ) -> str:
        """Bind the cache to the workspace AND the authority identity.

        Two probes may only share a cached report when they would have been
        authorized identically. The key therefore includes:

        * the workspace — two workspaces with the same relative roots must
          never share a report;
        * the committed read roots and the tool whitelist;
        * the committed read scope declared in the token, so two authorities
          that commit DIFFERENT scopes never share a report even when they
          declare the same roots;
        * the host's CURRENT per-tool decisions for the planned reads, so a
          host that forbids a read tool (its blacklist above all) cannot keep
          serving a report produced while that tool was allowed;
        * an identity fingerprint of the authorizing token, so a report
          produced under one authority is never reused under another.
        """
        tools = "*" if authority.allowed_tools is None else ",".join(sorted(authority.allowed_tools))
        roots = "|".join(authority.read_roots)
        scope = "1" if authority.scope_committed else "0"
        committed = _committed_scope_fingerprint(authority.capability_token)
        decisions = _tool_decision_fingerprint(authority.tool_authorizer, plan)
        identity = _authority_identity(authority.capability_token)
        return (
            f"{target.cache_key()}|ws={self._workspace}|scope={scope}|roots={roots}"
            f"|tools={tools}|committed={committed}|decisions={decisions}|id={identity}"
        )

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

        The live authority's own scope facts are checked as well: a host that
        shrank its committed read scope must not keep serving the report
        either. The committed scope is checked against the same workspace-
        relative roots the findings are expressed in.
        """
        if not authority.scope_committed:
            return
        roots = authority.read_roots
        if not roots:
            raise ScoutProbeDeniedError("committed read scope authorizes no readable path; scout probe refused")
        # The committed scope entries are the authority's own scope fact. When
        # the host declares workspace-relative entries, they must cover every
        # path the report names, so a shrink of the committed scope refuses
        # the stale report even when the declared roots stayed wide.
        committed = _relative_scope_entries(authority.capability_token)
        for finding in report.findings:
            if not _path_within_roots(finding.path, roots):
                raise ScoutProbeDeniedError(
                    f"cached scout report names a path outside the current read scope: {finding.path}"
                )
            if committed and not _path_within_roots(finding.path, committed):
                raise ScoutProbeDeniedError(
                    f"cached scout report names a path outside the current committed read scope: {finding.path}"
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


def _freeze_token(token: Mapping[str, Any]) -> Mapping[str, Any]:
    """Take the host's capability token BY VALUE, deeply and immutably.

    The authority is the service's own source of truth, so it must not share
    any mutable state with the object the host handed over. A shallow copy
    left the nested ``allowed_read_paths`` list shared with the caller: the
    caller could empty it afterwards, the authority's committed scope silently
    became empty, and the authority then contradicted the cache that had been
    produced under the wider scope.

    The frozen token keeps the same values; only their mutability changes:
    mappings become read-only mappings, sequences become tuples and sets
    become frozensets.

    The read-only mapping is a ``MappingProxyType`` — the platform's own
    immutability idiom (``kernelone.events.sourcing.guarded`` and
    ``kernelone.llm.engine.contracts`` freeze values the same way). It is
    deliberately NOT a ``dict`` subclass: CPython implements ``dict.__ior__``
    and ``dict.__setitem__`` at C level, so a ``dict`` subclass cannot block
    ``token |= {...}`` and a subclass override would be a FALSE immutability
    promise. A ``MappingProxyType`` rejects those operations outright, at
    every level.

    Consumers that need a plain dictionary take ``dict(frozen_token)``; the
    nested values are tuples, so the result stays JSON-serialisable.
    """
    if isinstance(token, MappingProxyType):
        return token
    if isinstance(token, Mapping):
        return MappingProxyType({str(key): _freeze_token(value) for key, value in token.items()})
    if isinstance(token, (list, tuple)):
        return tuple(_freeze_token(item) for item in token)
    if isinstance(token, (set, frozenset)):
        return frozenset(token)
    return token


def _committed_scope_entries(token: Mapping[str, Any]) -> tuple[str, ...]:
    """The host's committed read scope, as declared in the token.

    An absent ``allowed_read_paths`` key means the legacy undefined scope and
    yields an empty tuple; an explicitly empty scope also yields an empty
    tuple. The two are distinguished by ``scope_committed``.
    """
    if "allowed_read_paths" not in token:
        return ()
    scope = token.get("allowed_read_paths")
    if not isinstance(scope, (list, tuple)):
        return ()
    return tuple(str(item) for item in scope)


def _relative_scope_entries(token: Mapping[str, Any]) -> tuple[str, ...]:
    """The committed scope entries that are workspace-relative.

    Absolute entries are resolved by the canonical read-scope gate, which owns
    the workspace binding. The Scout cell only compares workspace-relative
    finding paths, so it checks the relative entries and leaves the absolute
    ones to the gate that can actually resolve them.
    """
    return tuple(entry for entry in _committed_scope_entries(token) if not entry.startswith("/"))


def _committed_scope_fingerprint(token: Mapping[str, Any]) -> str:
    """Bind the committed read scope into the cache key.

    Two authorities that commit DIFFERENT read scopes must never share a
    cached report, even when they declare the same roots and the same
    authorizing identity.
    """
    if "allowed_read_paths" not in token:
        return "undefined"
    entries = sorted(_committed_scope_entries(token))
    blob = json.dumps(entries, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _tool_decision_fingerprint(
    authorizer: Callable[[str, Mapping[str, Any]], tuple[bool, str]] | None,
    plan: list[tuple[str, dict[str, Any]]],
) -> str:
    """Bind the host's CURRENT per-tool decisions into the cache key.

    The host's tool policy (its blacklist above all) is not part of the
    committed token: a host can forbid a read tool at any moment. A cached
    report produced while a read tool was allowed must therefore not be
    served once the host forbids it, so the host's OWN decision for each
    planned read is bound into the key. The decision comes from the host's
    authorizer — the same callable the nested reads are judged by — so this
    introduces no second permission source.
    """
    if authorizer is None:
        return "no-authorizer"
    decisions: list[str] = []
    for tool, args in plan:
        try:
            allowed, _reason = authorizer(tool, dict(args))
        except Exception as exc:  # noqa: BLE001 - fail-closed boundary for one host decision
            # A host decision that cannot be obtained is NOT an authorization.
            # The key then differs from the one of an allowed plan, so the
            # report is re-read and the host's own gate decides again.
            logger.warning("scout host tool decision failed for %s: %s", tool, exc)
            decisions.append(f"{tool}:error")
            continue
        decisions.append(f"{tool}:{'1' if allowed else '0'}")
    blob = "|".join(decisions)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _authority_identity(token: Mapping[str, Any]) -> str:
    """Fingerprint the identity that authorized a probe.

    Two probes may only share a cached report when the authorizing identity is
    the same. The fingerprint covers the identity-bearing fields of the
    committed token (token/run/project/stage ids and the contract hash). The
    read scope itself is bound separately by the roots and the scope flag, so
    a SHRINKING of the scope cannot reuse a report produced under a wider one.
    """
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
        if root_norm == ".":
            # The workspace root contains every workspace-relative path.
            return True
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

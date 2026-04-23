"""Effect Policy Compiler for Polaris Cell boundary enforcement.

Compiles ``effects_allowed`` declarations from ``cell.yaml`` into
runtime-checkable policy objects.  Each declaration follows the format
``category.action:scope_glob`` (e.g. ``fs.read:workspace/**``).

The compiler produces a :class:`CompiledEffectPolicy` that can verify
whether a given tool invocation is permitted by the owning Cell's
declared effect budget.

Enforcement mode is controlled by the ``KERNELONE_EFFECT_POLICY_MODE``
environment variable:

* ``off``    -- no checking at all
* ``warn``   -- check and log warnings for violations (default)
* ``strict`` -- check and raise :class:`EffectPolicyViolation`
"""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Effect-type mapping
# ---------------------------------------------------------------------------

_EFFECT_TYPE_TO_CATEGORY: dict[str, str] = {
    "read": "fs.read",
    "write": "fs.write",
    "execute": "process.spawn",
    "network": "ws.outbound",
    "llm": "llm.invoke",
}
"""Maps tool ``effect_type`` strings to ``category.action`` keys used
in ``effects_allowed`` declarations.

This is intentionally a simple default mapping.  Callers that require
more precise resolution (e.g. distinguishing ``db.read_write`` from
``fs.write``) should supply the full ``category.action`` string
directly when calling :meth:`CompiledEffectPolicy.check`.
"""

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EffectPolicyRule:
    """Single compiled rule from a cell's ``effects_allowed`` list."""

    category: str
    """Effect category, e.g. ``"fs"``."""

    action: str
    """Effect action within the category, e.g. ``"read"``."""

    scope_pattern: str
    """Glob pattern constraining the scope, e.g. ``"workspace/**"``."""

    raw: str
    """Original declaration string from ``cell.yaml``."""


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """Result of an effect policy check."""

    allowed: bool
    """Whether the effect is permitted by the policy."""

    matched_rule: EffectPolicyRule | None
    """The rule that matched, or ``None`` if no rule matched."""

    reason: str
    """Human-readable explanation of the verdict."""


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class EffectPolicyViolationError(RuntimeError):
    """Raised when a tool invocation violates the cell's effect policy."""

    def __init__(self, verdict: PolicyVerdict) -> None:
        self.verdict = verdict
        super().__init__(f"Effect policy violation: {verdict.reason}")


# Backward-compatible alias matching the task specification name.
EffectPolicyViolation = EffectPolicyViolationError


# ---------------------------------------------------------------------------
# Enforcement mode
# ---------------------------------------------------------------------------


def get_effect_policy_mode() -> Literal["off", "warn", "strict"]:
    """Return the effect policy enforcement mode from the environment.

    Reads ``KERNELONE_EFFECT_POLICY_MODE``.  Accepted values are
    ``"off"``, ``"warn"``, and ``"strict"``.  Defaults to ``"warn"``
    if unset or if the value is not recognised.
    """
    raw = os.environ.get("KERNELONE_EFFECT_POLICY_MODE", "warn").lower().strip()
    if raw in ("off", "warn", "strict"):
        return raw  # type: ignore[return-value]
    logger.warning(
        "effect-policy: unrecognised KERNELONE_EFFECT_POLICY_MODE=%r, falling back to 'warn'",
        raw,
    )
    return "warn"


# ---------------------------------------------------------------------------
# Compiled policy
# ---------------------------------------------------------------------------


class CompiledEffectPolicy:
    """Runtime-checkable effect policy compiled from ``cell.yaml`` declarations.

    Instances are created via :meth:`EffectPolicyCompiler.compile`.

    A policy with **no rules** (empty ``effects_allowed``) is treated as
    *deny-all*: every check will return a negative verdict.
    """

    def __init__(self, rules: Sequence[EffectPolicyRule]) -> None:
        self._rules: tuple[EffectPolicyRule, ...] = tuple(rules)

        # Pre-build a lookup structure keyed by ``"category.action"`` for
        # fast matching without scanning the full rule list every time.
        self._by_category_action: dict[str, list[EffectPolicyRule]] = {}
        for rule in self._rules:
            key = f"{rule.category}.{rule.action}"
            self._by_category_action.setdefault(key, []).append(rule)

    # -- public properties --------------------------------------------------

    @property
    def rules(self) -> tuple[EffectPolicyRule, ...]:
        """All compiled rules in declaration order."""
        return self._rules

    # -- matching helpers ---------------------------------------------------

    @staticmethod
    def _scope_matches(pattern: str, scope: str) -> bool:
        """Check whether *scope* matches the glob *pattern*.

        ``**`` in effect declarations conventionally means "any number of
        path segments".  :func:`fnmatch.fnmatch` treats ``*`` as "match
        everything except path separators on some platforms", so we
        normalise ``**`` to ``*`` for a simple, cross-platform match that
        is consistent with the intent of the declarations.

        Both forward-slash and back-slash separators are normalised to
        ``/`` before matching.
        """
        normalised_pattern = pattern.replace("\\", "/").replace("**", "*")
        normalised_scope = scope.replace("\\", "/")
        return fnmatch.fnmatch(normalised_scope, normalised_pattern)

    # -- core check ---------------------------------------------------------

    def check(self, effect_type: str, scope: str = "") -> PolicyVerdict:
        """Check whether an effect of *effect_type* with *scope* is allowed.

        Parameters
        ----------
        effect_type:
            Either a full ``"category.action"`` string (e.g.
            ``"fs.read"``) or a short tool-level effect type (e.g.
            ``"read"``) which will be resolved via
            :data:`_EFFECT_TYPE_TO_CATEGORY`.
        scope:
            The resource path or identifier that the effect targets.
            Matched against the ``scope_pattern`` of applicable rules.
            An empty string matches rules with a wildcard scope.
        """
        # Resolve short names via the mapping table.
        category_action = _EFFECT_TYPE_TO_CATEGORY.get(effect_type, effect_type)

        candidates = self._by_category_action.get(category_action)
        if candidates is None:
            return PolicyVerdict(
                allowed=False,
                matched_rule=None,
                reason=(
                    f"No rule found for effect '{category_action}' "
                    f"(original: '{effect_type}'). "
                    f"Policy contains {len(self._rules)} rule(s)."
                ),
            )

        # Try each candidate rule for a scope match.
        for rule in candidates:
            if self._scope_matches(rule.scope_pattern, scope):
                return PolicyVerdict(
                    allowed=True,
                    matched_rule=rule,
                    reason=(f"Effect '{category_action}' on scope '{scope}' permitted by rule '{rule.raw}'."),
                )

        # Category/action exists in the rules but no scope matched.
        scope_patterns = ", ".join(r.scope_pattern for r in candidates)
        return PolicyVerdict(
            allowed=False,
            matched_rule=None,
            reason=(
                f"Effect '{category_action}' exists in policy but scope "
                f"'{scope}' does not match any allowed pattern "
                f"[{scope_patterns}]."
            ),
        )

    # -- tool-level convenience ---------------------------------------------

    def check_tool_invocation(
        self,
        tool_name: str,
        effect_type: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> PolicyVerdict:
        """Check whether a specific tool invocation is permitted.

        This is a convenience wrapper around :meth:`check` that extracts
        a *scope* hint from the tool's *arguments*.

        The scope is derived by inspecting common argument keys that
        typically carry a path or resource identifier (``path``,
        ``file_path``, ``target``, ``scope``, ``resource``).  If none of
        these keys are present the scope defaults to the *tool_name*
        itself (allowing rules that scope by tool namespace).

        Parameters
        ----------
        tool_name:
            Canonical name of the tool being invoked.
        effect_type:
            The effect type declared on the tool invocation (e.g.
            ``"read"``, ``"write"``, ``"execute"``).
        arguments:
            The argument mapping for the tool invocation.  May be
            ``None`` if unavailable.
        """
        scope = self._extract_scope(tool_name, arguments)
        verdict = self.check(effect_type, scope)

        # Enrich the reason with tool context when the verdict is negative.
        if not verdict.allowed:
            return PolicyVerdict(
                allowed=False,
                matched_rule=None,
                reason=(f"Tool '{tool_name}' (effect_type='{effect_type}', scope='{scope}'): {verdict.reason}"),
            )
        return verdict

    @staticmethod
    def _extract_scope(
        tool_name: str,
        arguments: Mapping[str, Any] | None,
    ) -> str:
        """Derive a scope string from tool arguments.

        Inspects well-known keys in order of priority.  Falls back to
        *tool_name* so that scope-less tools can still be matched by
        tool-namespace rules.
        """
        if arguments is None:
            return tool_name

        scope_keys: tuple[str, ...] = (
            "path",
            "file_path",
            "target",
            "scope",
            "resource",
            "url",
        )
        for key in scope_keys:
            value = arguments.get(key)
            if value is not None:
                return str(value)

        return tool_name


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


class EffectPolicyCompiler:
    """Compiles ``cell.yaml`` ``effects_allowed`` patterns into a
    runtime-checkable :class:`CompiledEffectPolicy`.
    """

    @staticmethod
    def compile(effects_allowed: Sequence[str]) -> CompiledEffectPolicy:
        """Parse effect patterns and compile into a policy object.

        Each pattern must follow the format
        ``category.action:scope_glob``.  Examples::

            fs.read:workspace/**
            llm.invoke:roles/*
            db.read_write:cognitive_runtime
            process.exec:git

        Parameters
        ----------
        effects_allowed:
            The raw list of effect declarations, typically read directly
            from ``cell.yaml``.

        Returns
        -------
        CompiledEffectPolicy
            A compiled, immutable policy object ready for runtime checks.

        Raises
        ------
        ValueError
            If any pattern does not conform to the expected format.
        """
        rules: list[EffectPolicyRule] = []
        for raw_pattern in effects_allowed:
            rule = EffectPolicyCompiler._parse_pattern(raw_pattern)
            rules.append(rule)
        return CompiledEffectPolicy(rules)

    @staticmethod
    def _parse_pattern(raw: str) -> EffectPolicyRule:
        """Parse a single ``category.action:scope_glob`` string.

        The format is strict:

        * Exactly one ``:`` separating the category-action part from
          the scope glob.
        * The category-action part must contain at least one ``.``
          separating the category from the action.
        """
        raw = raw.strip()
        if ":" not in raw:
            raise ValueError(
                f"Invalid effect pattern '{raw}': missing ':' separator. Expected format 'category.action:scope_glob'."
            )

        category_action, scope_pattern = raw.split(":", maxsplit=1)
        category_action = category_action.strip()
        scope_pattern = scope_pattern.strip()

        if "." not in category_action:
            raise ValueError(
                f"Invalid effect pattern '{raw}': category-action part "
                f"'{category_action}' must contain a '.' separating "
                f"category from action (e.g. 'fs.read')."
            )

        # Split on the *first* dot only, so categories like
        # ``network.http_outbound`` parse as category="network",
        # action="http_outbound".
        category, action = category_action.split(".", maxsplit=1)
        category = category.strip()
        action = action.strip()

        if not category:
            raise ValueError(f"Invalid effect pattern '{raw}': empty category.")
        if not action:
            raise ValueError(f"Invalid effect pattern '{raw}': empty action.")
        if not scope_pattern:
            raise ValueError(f"Invalid effect pattern '{raw}': empty scope pattern.")

        return EffectPolicyRule(
            category=category,
            action=action,
            scope_pattern=scope_pattern,
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CompiledEffectPolicy",
    "EffectPolicyCompiler",
    "EffectPolicyRule",
    "EffectPolicyViolation",
    "EffectPolicyViolationError",
    "PolicyVerdict",
    "get_effect_policy_mode",
]

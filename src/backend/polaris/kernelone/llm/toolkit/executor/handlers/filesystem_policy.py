"""Director write-policy gate for filesystem handlers.

Leaf module for ``filesystem.py``: reads the root and nested ``AGENTS.md`` files
that scope a pending write, normalizes the optional Director write scope, and
validates a pending workspace write against the Director write policy. Depends on
no other ``filesystem_*`` sibling, so it sits at the foundation of the import
graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def _read_workspace_agents_policy_text(self: AgentAccelToolExecutor, rel: str = "") -> str:
    """Read root and nested AGENTS.md files that apply to a workspace-relative path."""
    normalized_rel = str(rel or "").replace("\\", "/").strip("/")
    candidates = ["AGENTS.md"]
    parent_parts = [part for part in normalized_rel.split("/")[:-1] if part]
    for index in range(1, len(parent_parts) + 1):
        candidates.append("/".join([*parent_parts[:index], "AGENTS.md"]))

    texts: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            if self._kernel_fs.workspace_exists(candidate) and self._kernel_fs.workspace_is_file(candidate):
                texts.append(self._kernel_fs.workspace_read_text(candidate, encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return "\n".join(texts)


def _coerce_policy_scope_list(value: Any) -> list[str]:
    """Normalize an optional scope-like tool argument into a string list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _director_write_allowed_scope(tool_kwargs: dict[str, Any] | None) -> list[str]:
    """Extract an explicit Director write scope if the call carries one."""
    kwargs = tool_kwargs or {}
    for key in (
        "allowed_scope",
        "allowed_scope_paths",
        "scope_paths",
        "target_files",
        "pm_target_files",
        "act_files",
    ):
        scope = _coerce_policy_scope_list(kwargs.get(key))
        if scope:
            return scope
    return []


def _validate_director_policy_for_write(
    self: AgentAccelToolExecutor,
    *,
    rel: str,
    old_content: str,
    new_content: str,
    operation: str,
    tool_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a pending workspace write and return structured policy evidence."""
    from polaris.kernelone.llm.toolkit.write_policy import validate_tool_write_policy

    normalized_rel = str(rel or "").replace("\\", "/").strip("/")
    package_write = normalized_rel == "package.json" or normalized_rel.endswith("/package.json")
    verdict = validate_tool_write_policy(
        changed_files=[normalized_rel] if normalized_rel else [],
        allowed_scope=_director_write_allowed_scope(tool_kwargs),
        agents_md=_read_workspace_agents_policy_text(self, normalized_rel),
        operation=operation,
        package_before=old_content if package_write else None,
        package_after=new_content if package_write else None,
        require_change=True,
    )
    evidence = verdict.to_dict()
    if verdict.allowed:
        return {"ok": True, "director_policy": evidence}

    reason = "; ".join(verdict.reasons) or "Director write policy denied the write"
    return {
        "ok": False,
        "error": f"Director write policy denied: {reason}",
        "error_type": "director_write_policy_denied",
        "blocked": True,
        "director_policy": evidence,
    }


def _attach_director_policy_evidence(result: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Attach policy evidence to a tool result and its effect receipt."""
    if not evidence:
        return result
    result["director_policy"] = evidence
    receipt = result.get("effect_receipt")
    if isinstance(receipt, dict):
        receipt["director_policy"] = evidence
    return result

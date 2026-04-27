"""Role execution domain policy for the `roles.runtime` cell.

This module centralizes domain normalization and role-default domain routing.
Keeping this policy in one place prevents drift across request builders,
strategy resolution, and streaming paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class ExecutionDomainResolution:
    """Resolved execution domain with explicitness marker."""

    execution_domain: str
    explicit: bool


class RoleDomainPolicy:
    """Canonical role/domain routing policy for runtime execution."""

    DOMAIN_ALIASES: dict[str, str] = {
        "code": "code",
        "coding": "code",
        "dev": "code",
        "engineering": "code",
        "programming": "code",
        "document": "document",
        "documentation": "document",
        "docs": "document",
        "writing": "document",
        "write": "document",
        "research": "research",
        "analysis": "research",
        "investigation": "research",
        "general": "general",
        "other": "general",
        "others": "general",
        "misc": "general",
    }
    DEFAULT_EXECUTION_DOMAIN = "code"

    _ROLE_ALIASES: dict[str, str] = {
        "pm": "pm",
        "product_manager": "pm",
        "project_manager": "pm",
        "PM": "pm",
        "architect": "architect",
        "docs": "architect",
        "Architect": "architect",
        "chief_engineer": "chief_engineer",
        "chiefengineer": "chief_engineer",
        "chief_enginner": "chief_engineer",
        "chiefenginner": "chief_engineer",
        "Chief Engineer": "chief_engineer",
        "director": "director",
        "Director": "director",
    }

    ROLE_DEFAULT_EXECUTION_DOMAIN: dict[str, str] = {
        "pm": "document",
        "architect": "document",
        "chief_engineer": "document",
        "director": "code",
    }

    @classmethod
    def normalize_domain(cls, domain: str | None) -> str | None:
        token = str(domain or "").strip().lower()
        if not token:
            return None
        return cls.DOMAIN_ALIASES.get(token)

    @classmethod
    def normalize_role(cls, role: str | None) -> str | None:
        token = str(role or "").strip().lower()
        if not token:
            return None
        normalized = token.replace("-", "_").replace(" ", "_")
        return cls._ROLE_ALIASES.get(normalized, normalized)

    @classmethod
    def resolve(
        cls,
        *,
        command_domain: str | None = None,
        context: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        role: str | None = None,
    ) -> ExecutionDomainResolution:
        """Resolve execution domain with explicit-domain precedence."""

        candidates = (
            command_domain,
            str((context or {}).get("domain") or ""),
            str((metadata or {}).get("domain") or ""),
        )
        for raw in candidates:
            normalized = cls.normalize_domain(raw)
            if normalized:
                return ExecutionDomainResolution(execution_domain=normalized, explicit=True)

        normalized_role = cls.normalize_role(role)
        if normalized_role:
            default_for_role = cls.ROLE_DEFAULT_EXECUTION_DOMAIN.get(normalized_role)
            if default_for_role:
                return ExecutionDomainResolution(
                    execution_domain=default_for_role,
                    explicit=False,
                )

        return ExecutionDomainResolution(
            execution_domain=cls.DEFAULT_EXECUTION_DOMAIN,
            explicit=False,
        )

    @staticmethod
    def strategy_domain_from_execution(execution_domain: str) -> str:
        if execution_domain == "general":
            return "code"
        return execution_domain

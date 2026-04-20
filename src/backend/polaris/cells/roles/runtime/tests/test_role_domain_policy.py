"""Tests for role execution domain policy in `roles.runtime`."""

from __future__ import annotations

from polaris.cells.roles.runtime.internal.role_domain_policy import RoleDomainPolicy


class TestRoleDomainPolicy:
    def test_resolve_role_default_document_domain(self) -> None:
        resolved = RoleDomainPolicy.resolve(role="pm")
        assert resolved.execution_domain == "document"
        assert resolved.explicit is False

    def test_resolve_role_alias_chief_engineer_typos(self) -> None:
        for alias in ("ChiefEngineer", "ChiefEnginner", "工部尚书"):
            resolved = RoleDomainPolicy.resolve(role=alias)
            assert resolved.execution_domain == "document"
            assert resolved.explicit is False

    def test_resolve_role_default_code_for_director(self) -> None:
        resolved = RoleDomainPolicy.resolve(role="Director")
        assert resolved.execution_domain == "code"
        assert resolved.explicit is False

    def test_explicit_domain_overrides_role_default(self) -> None:
        resolved = RoleDomainPolicy.resolve(
            command_domain="analysis",
            context={"domain": "document"},
            metadata={"domain": "code"},
            role="pm",
        )
        assert resolved.execution_domain == "research"
        assert resolved.explicit is True

    def test_unknown_role_falls_back_to_global_default(self) -> None:
        resolved = RoleDomainPolicy.resolve(role="qa")
        assert resolved.execution_domain == "code"
        assert resolved.explicit is False

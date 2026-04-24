"""Tests for polaris.cells.roles.adapters.internal.schemas.architect_schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from polaris.cells.roles.adapters.internal.schemas.architect_schema import (
    ArchitectOutput,
    ModuleDesign,
    NFRDesign,
    RiskAssessment,
    TechnologyChoice,
)


class TestTechnologyChoice:
    def test_valid_choice(self) -> None:
        choice = TechnologyChoice(
            layer="Data",
            technology="PostgreSQL",
            rationale="ACID compliance and robust feature set for enterprise use cases",
            alternatives=["MySQL", "MongoDB"],
        )
        assert choice.layer == "Data"
        assert choice.alternatives == ["MySQL", "MongoDB"]


class TestModuleDesign:
    def test_valid_module(self) -> None:
        module = ModuleDesign(
            name="AuthModule",
            responsibility="Handles user authentication and authorization flows",
            interfaces=["login()", "logout()", "verify_token()"],
            dependencies=["UserRepository", "TokenService"],
        )
        assert module.name == "AuthModule"
        assert len(module.interfaces) == 3


class TestRiskAssessment:
    def test_valid_assessment(self) -> None:
        assessment = RiskAssessment(
            risk="Database connection pool exhaustion",
            probability="medium",
            impact="high",
            mitigation="Implement connection pooling with proper timeout settings",
        )
        assert assessment.probability == "medium"
        assert assessment.impact == "high"


class TestNFRDesign:
    def test_defaults(self) -> None:
        nfr = NFRDesign()
        assert nfr.performance is None
        assert nfr.availability is None

    def test_with_values(self) -> None:
        nfr = NFRDesign(
            performance="Handle 1000 QPS with p99 latency < 100ms",
            availability="99.9% uptime with automatic failover",
            security="OAuth 2.0 + mTLS for all internal services",
            scalability="Horizontal scaling via Kubernetes HPA",
        )
        assert "1000 QPS" in nfr.performance


class TestArchitectOutput:
    def test_empty_blueprint(self) -> None:
        arch = ArchitectOutput()
        assert arch.system_overview == ""
        assert arch.technology_stack == []
        assert arch.modules == []

    def test_minimum_fields_for_validation(self) -> None:
        arch = ArchitectOutput(
            system_overview="x" * 60,
            architecture_diagram="",
            data_flow="x" * 35,
        )
        assert len(arch.system_overview) >= 50

    def test_with_technology_stack(self) -> None:
        arch = ArchitectOutput(
            system_overview="x" * 60,
            data_flow="x" * 35,
            technology_stack=[
                TechnologyChoice(
                    layer="Data",
                    technology="PostgreSQL",
                    rationale="x" * 30,
                )
            ],
        )
        assert len(arch.technology_stack) == 1

    def test_with_modules(self) -> None:
        arch = ArchitectOutput(
            system_overview="x" * 60,
            data_flow="x" * 35,
            modules=[
                ModuleDesign(
                    name="Core",
                    responsibility="x" * 30,
                )
            ],
        )
        assert len(arch.modules) == 1

    def test_with_risks(self) -> None:
        arch = ArchitectOutput(
            system_overview="x" * 60,
            data_flow="x" * 35,
            risks=[
                RiskAssessment(
                    risk="x" * 10,
                    probability="low",
                    impact="low",
                    mitigation="x" * 15,
                )
            ],
        )
        assert len(arch.risks) == 1

    def test_with_nfr(self) -> None:
        arch = ArchitectOutput(
            system_overview="x" * 60,
            data_flow="x" * 35,
            non_functional=NFRDesign(performance="x" * 10),
        )
        assert arch.non_functional.performance == "x" * 10

    def test_inherits_base_tool_enabled_output(self) -> None:
        arch = ArchitectOutput(
            system_overview="x" * 60,
            data_flow="x" * 35,
            tool_calls=[],
            is_complete=True,
            next_action="call_tools",
        )
        assert arch.next_action == "call_tools"
        assert arch.get_tools_to_execute() == []

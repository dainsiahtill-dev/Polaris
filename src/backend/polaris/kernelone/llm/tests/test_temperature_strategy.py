from __future__ import annotations

from polaris.kernelone.llm.temperature_strategy import (
    TemperatureContext,
    get_phase_temperature,
    get_role_temperature,
    resolve_temperature,
)


def test_resident_agi_has_stable_base_temperature() -> None:
    assert get_role_temperature("resident_agi") == 0.25
    assert resolve_temperature(TemperatureContext(role="resident_agi")) == 0.25


def test_resident_agi_precision_phases_use_low_temperature() -> None:
    assert get_phase_temperature("resident_agi", "decision_boundary") == 0.15
    assert get_phase_temperature("resident_agi", "repair_rule_suggestion") == 0.12
    assert resolve_temperature(TemperatureContext(role="resident_agi", phase="final_request_audit")) == 0.15
    assert resolve_temperature(TemperatureContext(role="resident_agi", phase="director_repair_advisory")) == 0.12


def test_resident_agi_brainstorming_is_allowed_more_exploration() -> None:
    assert resolve_temperature(TemperatureContext(role="resident_agi", phase="brainstorming")) == 0.45

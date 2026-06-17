"""Isolation tests for the CLAUDE.md §8 game/card3d PM domain contracts.

These tests pin the ``KERNELONE_PM_DOMAIN_CONTRACTS`` isolation switch that
gates the project-specific game/card3d domain behavior embedded in
``task_quality_gate`` (the ``_GAME_PM_*`` / ``_CARD3D_PM_*`` tables, detectors,
and synthesizers). They prove three properties:

* (a) With the env UNSET, the three detectors behave IDENTICALLY to HEAD on
  representative game / card3d / non-game contracts (default preserve).
* (b) With ``KERNELONE_PM_DOMAIN_CONTRACTS=0`` all three detectors return
  ``False`` (clean §8 path) and no domain tasks are synthesized.
* (c) The env truthiness table: unset / 1 / true / on -> enabled;
  0 / false / no / off -> disabled.

The detectors are the single chokepoint: every game/card3d branch and both
synthesizers flow through one of ``should_apply_card3d_pm_domain_contract``,
``_is_card3d_pm_contract``, or ``_is_game_pm_contract``, so gating these three
functions disables 100% of the §8 behavior in one place.
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (
    _append_missing_card3d_domain_tasks,
    _append_missing_game_domain_tasks,
    _domain_contracts_enabled,
    _is_card3d_pm_contract,
    _is_game_pm_contract,
    should_apply_card3d_pm_domain_contract,
)

_ENV_NAME = "KERNELONE_PM_DOMAIN_CONTRACTS"

# ---------------------------------------------------------------------------
# Representative contracts (PATH-evidence based so they fire HEAD's default-on
# detection without depending on KERNELONE_PM_DOMAIN_TEXT_HINTS).
# ---------------------------------------------------------------------------


def _card3d_contract() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Card3D contract covering >=2 domains including a core detection domain."""

    normalized: dict[str, Any] = {"overall_goal": "build the multiplayer table"}
    tasks: list[dict[str, Any]] = [
        {
            "id": "T1",
            "scope_paths": ["src/client/three-scene.ts"],
            "target_files": ["src/client/three-scene.ts"],
        },
        {
            "id": "T2",
            "scope_paths": ["src/client/card-table.ts"],
            "target_files": ["src/client/card-table.ts"],
        },
        {
            "id": "T3",
            "scope_paths": ["src/game/card-catalog.ts"],
            "target_files": ["src/game/card-catalog.ts"],
        },
    ]
    return normalized, tasks


def _game_contract() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Game contract covering >=2 game domains via path evidence."""

    normalized: dict[str, Any] = {"overall_goal": "build the tactical game"}
    tasks: list[dict[str, Any]] = [
        {
            "id": "G1",
            "scope_paths": ["src/engine/game-loop.ts"],
            "target_files": ["src/engine/game-loop.ts"],
        },
        {
            "id": "G2",
            "scope_paths": ["src/world/procedural-map.ts"],
            "target_files": ["src/world/procedural-map.ts"],
        },
    ]
    return normalized, tasks


def _non_game_contract() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Plain REST API contract that must not match either domain."""

    normalized: dict[str, Any] = {"overall_goal": "Build a TypeScript REST API with tests."}
    tasks: list[dict[str, Any]] = [
        {"id": "N1", "scope_paths": ["src/server"], "target_files": ["src/server/app.ts"]},
        {"id": "N2", "scope_paths": ["tests"], "target_files": ["tests/task-service.test.ts"]},
    ]
    return normalized, tasks


@pytest.fixture()
def unset_domain_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the isolation env var is unset (the default-preserve state)."""

    monkeypatch.delenv(_ENV_NAME, raising=False)


@pytest.fixture()
def disabled_domain_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the clean §8 path with an explicit disable token."""

    monkeypatch.setenv(_ENV_NAME, "0")


# ---------------------------------------------------------------------------
# (a) Default-preserve: env UNSET -> detectors identical to HEAD.
# ---------------------------------------------------------------------------


class TestDefaultPreserveBehavior:
    def test_card3d_contract_detected_when_unset(self, unset_domain_env: None) -> None:
        normalized, tasks = _card3d_contract()
        assert _is_card3d_pm_contract(normalized, tasks) is True

    def test_game_contract_detected_when_unset(self, unset_domain_env: None) -> None:
        normalized, tasks = _game_contract()
        assert _is_game_pm_contract(normalized, tasks) is True
        # card3d detector must stay False for the game contract (HEAD precedence).
        assert _is_card3d_pm_contract(normalized, tasks) is False

    def test_non_game_contract_matches_neither_when_unset(self, unset_domain_env: None) -> None:
        normalized, tasks = _non_game_contract()
        assert _is_card3d_pm_contract(normalized, tasks) is False
        assert _is_game_pm_contract(normalized, tasks) is False

    def test_should_apply_card3d_prompt_true_when_unset(self, unset_domain_env: None) -> None:
        assert (
            should_apply_card3d_pm_domain_contract(
                "multiplayer 3d card deck game with websocket realtime lobby",
                "",
            )
            is True
        )

    def test_should_apply_card3d_prompt_false_for_plain_text_when_unset(self, unset_domain_env: None) -> None:
        assert should_apply_card3d_pm_domain_contract("Build a REST API with tests", "") is False

    def test_card3d_synthesizer_adds_missing_domains_when_unset(self, unset_domain_env: None) -> None:
        normalized, tasks = _card3d_contract()
        added = _append_missing_card3d_domain_tasks(
            normalized,
            list(tasks),
            workspace_full="",
            verify_command="npm run build",
        )
        assert added > 0

    def test_game_synthesizer_adds_missing_domains_when_unset(self, unset_domain_env: None) -> None:
        normalized, tasks = _game_contract()
        added = _append_missing_game_domain_tasks(
            normalized,
            list(tasks),
            workspace_full="",
            verify_command="npm run build",
        )
        assert added > 0


# ---------------------------------------------------------------------------
# (b) Clean §8 path: KERNELONE_PM_DOMAIN_CONTRACTS=0 disables everything.
# ---------------------------------------------------------------------------


class TestDisabledCleanPath:
    def test_all_three_detectors_return_false_when_disabled(self, disabled_domain_env: None) -> None:
        card3d_norm, card3d_tasks = _card3d_contract()
        game_norm, game_tasks = _game_contract()

        assert _is_card3d_pm_contract(card3d_norm, card3d_tasks) is False
        assert _is_game_pm_contract(game_norm, game_tasks) is False
        assert (
            should_apply_card3d_pm_domain_contract(
                "multiplayer 3d card deck game with websocket realtime lobby",
                "",
            )
            is False
        )

    def test_card3d_synthesizer_adds_nothing_when_disabled(self, disabled_domain_env: None) -> None:
        normalized, tasks = _card3d_contract()
        working = list(tasks)
        added = _append_missing_card3d_domain_tasks(
            normalized,
            working,
            workspace_full="",
            verify_command="npm run build",
        )
        assert added == 0
        assert len(working) == len(tasks)

    def test_game_synthesizer_adds_nothing_when_disabled(self, disabled_domain_env: None) -> None:
        normalized, tasks = _game_contract()
        working = list(tasks)
        added = _append_missing_game_domain_tasks(
            normalized,
            working,
            workspace_full="",
            verify_command="npm run build",
        )
        assert added == 0
        assert len(working) == len(tasks)


# ---------------------------------------------------------------------------
# (c) Env truthiness table.
# ---------------------------------------------------------------------------


class TestDomainContractsEnabledTruthiness:
    @pytest.mark.parametrize(
        "raw_value",
        ["1", "true", "TRUE", "on", "On", "yes", "2", "", "  ", "enabled", "anything-else"],
    )
    def test_enabled_for_non_disable_tokens(self, monkeypatch: pytest.MonkeyPatch, raw_value: str) -> None:
        monkeypatch.setenv(_ENV_NAME, raw_value)
        assert _domain_contracts_enabled() is True

    def test_enabled_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_NAME, raising=False)
        assert _domain_contracts_enabled() is True

    @pytest.mark.parametrize(
        "raw_value",
        ["0", "false", "False", "FALSE", "no", "No", "off", "OFF", " 0 ", "  off  "],
    )
    def test_disabled_for_explicit_disable_tokens(self, monkeypatch: pytest.MonkeyPatch, raw_value: str) -> None:
        monkeypatch.setenv(_ENV_NAME, raw_value)
        assert _domain_contracts_enabled() is False

"""CLAUDE.md §8 game/card3d project-specific PM domain-contract behavior.

This module isolates ALL of the embedded standard-answer game/card3d domain
contracts: the ``_GAME_PM_*`` / ``_CARD3D_PM_*`` tables, the scenario detectors
(``_is_game_pm_contract`` / ``_is_card3d_pm_contract``), and the synthesizers
that inject required domain rows into game/card3d PM contracts.

CRITICAL §8 ISOLATION: every detector and synthesizer remains behind the
single ``_domain_contracts_enabled`` gate (env ``KERNELONE_PM_DOMAIN_CONTRACTS``,
DEFAULT-ENABLED; only an explicit disable token ``0`` / ``false`` / ``no`` /
``off`` turns it off). Default behavior is byte-identical to the original
module — the isolation semantics are unchanged.

It imports only :mod:`primitives` (the leaf layer) and is imported by
:mod:`gate`. Bodies are moved verbatim (lossless decomposition).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.pm_planning.internal.quality_gate.primitives import (
    _append_unique_text_item,
    _collect_task_delivery_paths,
    _collect_task_scope_paths,
    _dedupe_paths,
    _dedupe_text_items,
    _domain_path_roots,
    _fallback_file_evidence_path_for_scope,
    _has_executable_or_file_acceptance_anchor,
    _is_file_like_pm_scope_path,
    _last_task_id,
    _normalize_path,
    _normalize_path_list,
    _normalize_text,
    _representative_workspace_file_for_scope,
    _unique_task_id,
    _workspace_relative_path,
)

_GAME_PM_MIN_TASKS = 12
_GAME_PM_REQUIRED_DOMAINS = (
    "engine",
    "world",
    "combat",
    "ai",
    "content",
    "progression",
    "economy",
    "persistence",
    "renderer",
    "audio",
    "tooling",
    "tests",
)
_CARD3D_PM_REQUIRED_DOMAINS = (
    "client3d",
    "table",
    "networking",
    "server",
    "realtime",
    "matchmaking",
    "rooms",
    "cards",
    "deckbuilder",
    "rules",
    "sync",
    "persistence",
    "moderation",
    "presence",
    "telemetry",
    "auth",
    "lobby",
    "assets",
    "animation",
    "physics",
    "analytics",
    "tests",
)
_GAME_PM_DOMAIN_SCOPE_PATHS = {
    "engine": "src/engine/game-loop.ts",
    "world": "src/world/procedural-map.ts",
    "combat": "src/combat/combat-system.ts",
    "ai": "src/ai/director-ai.ts",
    "content": "src/content/cards.ts",
    "progression": "src/progression/campaign.ts",
    "economy": "src/economy/loot-table.ts",
    "persistence": "src/persistence/save-system.ts",
    "renderer": "src/renderer/scene-view.ts",
    "audio": "src/audio/sound-events.ts",
    "tooling": "src/tools/balance-report.ts",
    "tests": "tests/integration/game-session.test.ts",
}
_CARD3D_PM_DOMAIN_SCOPE_PATHS = {
    "client3d": "src/client/three-scene.ts",
    "table": "src/client/card-table.ts",
    "networking": "src/client/network-client.ts",
    "server": "src/server/app.ts",
    "realtime": "src/server/realtime-gateway.ts",
    "matchmaking": "src/server/matchmaking.ts",
    "rooms": "src/server/room-state.ts",
    "cards": "src/game/card-catalog.ts",
    "deckbuilder": "src/game/deck-builder.ts",
    "rules": "src/game/rules-engine.ts",
    "sync": "src/shared/protocol.ts",
    "persistence": "src/server/session-store.ts",
    "moderation": "src/server/moderation.ts",
    "presence": "src/shared/player-presence.ts",
    "telemetry": "src/shared/telemetry.ts",
    "auth": "src/auth/session-auth.ts",
    "lobby": "src/lobby/lobby-service.ts",
    "assets": "src/assets/card-assets.ts",
    "animation": "src/animation/card-animations.ts",
    "physics": "src/physics/table-layout.ts",
    "analytics": "src/analytics/match-analytics.ts",
    "tests": "tests/integration/multiplayer-flow.test.ts",
}
_CARD3D_PM_TEST_TARGET_FILES = (
    "scripts/build.mjs",
    "scripts/test.mjs",
    "tests/unit/card-rules.test.ts",
    "tests/unit/deck-builder.test.ts",
    "tests/integration/multiplayer-flow.test.ts",
    "tests/integration/realtime-sync.test.ts",
    "tests/e2e/card-table-3d.test.ts",
)
_CARD3D_PM_DOMAIN_TARGET_FILES: dict[str, tuple[str, ...]] = {
    domain: (scope_path,) for domain, scope_path in _CARD3D_PM_DOMAIN_SCOPE_PATHS.items()
}
_CARD3D_PM_DOMAIN_TARGET_FILES["tests"] = _CARD3D_PM_TEST_TARGET_FILES


def build_card3d_pm_required_domain_contract() -> str:
    """Return the canonical Card3D PM task decomposition contract."""

    lines = [
        "CARD3D HARD CONTRACT:",
        "This overrides the default 1-3 task batch limit and any `仅提供 1-3 个任务` instruction.",
        "The tasks array MUST contain one Director task for EVERY row below.",
        "Do not group multiple domains into one task. Do not omit any row. Do not replace this table with bootstrap-only tasks.",
        f"Required task count: at least {len(_CARD3D_PM_REQUIRED_DOMAINS)}.",
        "Compact output rule: each task MUST use exactly 3 execution_checklist items and exactly 2 acceptance_criteria items; keep each item concise and do not include required_evidence.must_read or required_evidence.must_find_calls.",
        "Use the listed id, metadata.domain, target_files, scope_paths, and measurable acceptance for each task:",
    ]
    for index, domain in enumerate(_CARD3D_PM_REQUIRED_DOMAINS, start=1):
        target_files = _CARD3D_PM_DOMAIN_TARGET_FILES.get(
            domain,
            (_CARD3D_PM_DOMAIN_SCOPE_PATHS[domain],),
        )
        task_id = f"PM-CARD3D-{domain.upper()}-{index:02d}"
        primary_scope = _CARD3D_PM_DOMAIN_SCOPE_PATHS[domain]
        lines.append(
            f"- id={task_id}; metadata.domain={domain}; scope_paths=[{primary_scope}]; "
            f"target_files=[{', '.join(target_files)}]; "
            "acceptance must include `npm run build`, `npm run test -- --watch=false`, "
            "and explicit verification that target files contain no audit-seed or planning scenario markers."
        )
    lines.extend(
        [
            "Use depends_on to form a safe implementation chain.",
            "For the tests domain, acceptance and execution_checklist must explicitly require replacing/removing existing trivial arithmetic placeholder tests; appending new tests while leaving old placeholder cases is invalid.",
            "Do not output package.json, tsconfig.json, dependency-install, or framework migration tasks.",
            "Prefer `acceptance_criteria` over `acceptance`; use `required_evidence.validation_paths` only if needed.",
            "If you output fewer than the required domain rows above, the contract is invalid even if bootstrap tasks exist.",
        ]
    )
    return "\n".join(lines)


_DOMAIN_CONTRACTS_DISABLE_TOKENS = frozenset({"0", "false", "no", "off"})


def _domain_contracts_enabled() -> bool:
    """Return whether project-specific game/card3d PM domain contracts are active.

    This is the single isolation switch for the CLAUDE.md §8 project-domain
    behavior embedded in this module (the ``_GAME_PM_*`` / ``_CARD3D_PM_*``
    tables, detectors, and synthesizers that inject standard-answer tasks into
    game/card3d PM contracts). It reads the generic ``KERNELONE_PM_DOMAIN_CONTRACTS``
    environment variable and DEFAULTS TO ENABLED (preserving current behavior) so
    that the three guarded detectors stay no-ops unless an operator explicitly
    opts out.

    Only an explicit disable token (``0`` / ``false`` / ``no`` / ``off``,
    case- and whitespace-insensitive) turns the §8 behavior off; every other
    value (including unset, ``1``, ``true``, ``on``) keeps it enabled. This
    mirrors the fail-closed env-read idiom used elsewhere in this cell while
    inverting the default so the disable path must be requested deliberately
    (it is reserved for the game-bench A/B and the eventual default-flip +
    deletion of the §8 code).

    Returns:
        ``True`` when the domain contracts remain enabled (default), ``False``
        only when an explicit disable token is set.
    """

    raw = os.environ.get("KERNELONE_PM_DOMAIN_CONTRACTS")
    if raw is None:
        return True
    return raw.strip().lower() not in _DOMAIN_CONTRACTS_DISABLE_TOKENS


def should_apply_card3d_pm_domain_contract(*texts: Any) -> bool:
    """Return whether PM prompt text describes the Card3D multiplayer scenario."""

    if not _domain_contracts_enabled():
        return False
    joined = "\n".join(str(text or "") for text in texts)
    return _has_card3d_text_hints(joined)


_CARD3D_PM_DOMAIN_SCOPE_ALIASES = {
    "client3d": (
        "src/client/three-scene.ts",
        "src/client/scene.ts",
        "src/client/app.tsx",
        "src/client/main.ts",
    ),
    "table": (
        "src/client/card-table.ts",
        "src/client/table.ts",
        "src/client/tabletop.ts",
    ),
    "networking": (
        "src/client/network-client.ts",
        "src/client/network.ts",
        "src/client/realtime-client.ts",
    ),
    "server": (
        "src/server/app.ts",
        "src/server/index.ts",
        "src/server/server.ts",
    ),
    "realtime": (
        "src/server/realtime-gateway.ts",
        "src/server/websocket.ts",
        "src/server/ws-gateway.ts",
    ),
    "matchmaking": (
        "src/server/matchmaking.ts",
        "src/server/matchmaker.ts",
    ),
    "rooms": (
        "src/server/room-state.ts",
        "src/server/rooms.ts",
    ),
    "cards": (
        "src/game/card-catalog.ts",
        "src/game/cards.ts",
    ),
    "deckbuilder": (
        "src/game/deck-builder.ts",
        "src/game/deckbuilder.ts",
    ),
    "rules": (
        "src/game/rules-engine.ts",
        "src/game/rules.ts",
    ),
    "sync": (
        "src/shared/protocol.ts",
        "src/shared/sync-protocol.ts",
    ),
    "persistence": (
        "src/server/session-store.ts",
        "src/server/persistence.ts",
    ),
    "moderation": (
        "src/server/moderation.ts",
        "src/server/safety.ts",
    ),
    "presence": (
        "src/shared/player-presence.ts",
        "src/shared/presence.ts",
    ),
    "telemetry": (
        "src/shared/telemetry.ts",
        "src/shared/events.ts",
    ),
    "auth": (
        "src/auth/session-auth.ts",
        "src/auth/auth.ts",
    ),
    "lobby": (
        "src/lobby/lobby-service.ts",
        "src/lobby/index.ts",
    ),
    "assets": (
        "src/assets/card-assets.ts",
        "src/assets/assets.ts",
    ),
    "animation": (
        "src/animation/card-animations.ts",
        "src/animation/animations.ts",
    ),
    "physics": (
        "src/physics/table-layout.ts",
        "src/physics/layout.ts",
    ),
    "analytics": (
        "src/analytics/match-analytics.ts",
        "src/analytics/analytics.ts",
    ),
    "tests": (
        "tests",
        "tests/integration/multiplayer-flow.test.ts",
    ),
}
_CARD3D_PM_DIRECTORY_SCOPE_DOMAINS = {
    "src/client": ("client3d", "table", "networking"),
    "src/server": ("server", "realtime", "matchmaking", "rooms", "persistence", "moderation"),
    "src/game": ("cards", "deckbuilder", "rules"),
    "src/shared": ("sync", "presence", "telemetry"),
    "src/auth": ("auth",),
    "src/lobby": ("lobby",),
    "src/assets": ("assets",),
    "src/animation": ("animation",),
    "src/physics": ("physics",),
    "src/analytics": ("analytics",),
    "tests": ("tests",),
}
_CARD3D_PM_DETECTION_CORE_DOMAINS = {
    "client3d",
    "table",
    "networking",
    "cards",
    "deckbuilder",
    "rules",
    "sync",
    "presence",
    "auth",
    "lobby",
}
_GAME_PM_DOMAIN_TITLES = {
    "engine": "Implement tactical game engine loop",
    "world": "Implement procedural world generation",
    "combat": "Implement turn based combat system",
    "ai": "Implement enemy decision AI",
    "content": "Implement game content catalog",
    "progression": "Implement campaign progression",
    "economy": "Implement loot and shop economy",
    "persistence": "Implement save and load persistence",
    "renderer": "Implement interactive game renderer",
    "audio": "Implement audio event state",
    "tooling": "Implement balance reporting tooling",
    "tests": "Add game integration test coverage",
}
_CARD3D_PM_DOMAIN_TITLES = {
    "client3d": "Implement Three.js client scene",
    "table": "Implement interactive 3D card table",
    "networking": "Implement browser networking client",
    "server": "Implement Node.js backend entrypoint",
    "realtime": "Implement realtime gateway",
    "matchmaking": "Implement multiplayer matchmaking",
    "rooms": "Implement authoritative room state",
    "cards": "Implement creative card catalog",
    "deckbuilder": "Implement deck builder rules",
    "rules": "Implement card rules engine",
    "sync": "Implement shared sync protocol",
    "persistence": "Implement session persistence",
    "moderation": "Implement safety and moderation rules",
    "presence": "Implement shared player presence",
    "telemetry": "Implement gameplay telemetry events",
    "auth": "Implement session authentication",
    "lobby": "Implement multiplayer lobby service",
    "assets": "Implement card asset registry",
    "animation": "Implement card animation timeline",
    "physics": "Implement table layout physics",
    "analytics": "Implement match analytics",
    "tests": "Add multiplayer card integration tests",
}
_GAME_PM_HINT_RE = re.compile(
    r"(game|roguelike|tactical|combat|gameplay|procedural\s+map|terrain|encounter|action\s+point|behavior\s+tree|enemy|loot|游戏|玩家|敌人|战斗|地图|地图生成|世界|回合|存档|地形|遭遇|行动点|行为树|掉落)",
    re.IGNORECASE,
)
_CARD3D_PM_CORE_HINT_RE = re.compile(r"(card|deck|tabletop|卡牌|牌组|卡组|牌桌|桌游)", re.IGNORECASE)
_CARD3D_PM_STACK_HINT_RE = re.compile(
    r"(multiplayer|online|three(?:\.js|3d)?|webgl|node(?:\.js|js)?|websocket|realtime|lobby|room|creative|多人|在线|创意|三维|3d|房间|实时|后端|前端)",
    re.IGNORECASE,
)
_GAME_PM_FRAGILE_ACCEPTANCE_RE = re.compile(
    r"(参考序列|逐位一致|卡方|固定序列|魔法数字|快照序列|硬编码.*预期值|magic[- ]?number|golden[- ]?sequence|chi[- ]?square|snapshot[- ]?sequence|hard[- ]?coded.*expected)",
    re.IGNORECASE,
)
_GAME_PM_FORBIDDEN_DEPENDENCY_POLICY_RE = re.compile(
    r"(npm\s+install|pnpm\s+install|yarn\s+install|package\.json|tsconfig\.json|devdependencies|dev\s+dependencies|vitest|jest|webpack|cargo|rust|\btsc\b)",
    re.IGNORECASE,
)
_GAME_PM_OFF_DOMAIN_CORE_RE = re.compile(
    r"(prng|xorshift|procedural\s+map|map\s+generation|room\s+placement|a\*\s+connectivity)",
    re.IGNORECASE,
)
_GAME_PM_WORKSPACE_SIGNAL_PATTERNS = (
    re.compile(r"\bMAP-\d+\b", re.IGNORECASE),
    re.compile(r"\bCOM-\d+\b", re.IGNORECASE),
    re.compile(r"\bAI-\d+\b", re.IGNORECASE),
    re.compile(r"\bPRNG-\d+\b", re.IGNORECASE),
    re.compile(r"(terrain|encounter|combat|action point|behavior tree|enemy|loot|map generation)", re.IGNORECASE),
    re.compile(r"(地形|遭遇|战斗|行动点|行为树|敌人|掉落|地图生成)", re.IGNORECASE),
)
_CARD3D_PM_WORKSPACE_SIGNAL_PATTERNS = (
    re.compile(r"(three(?:\.js|3d)?|webgl|3d scene|card table|deck builder)", re.IGNORECASE),
    re.compile(r"(multiplayer|matchmaking|room state|websocket|realtime gateway)", re.IGNORECASE),
    re.compile(r"(creative card|card catalog|rules engine|sync protocol)", re.IGNORECASE),
    re.compile(r"(多人|在线|创意卡牌|牌桌|房间状态|实时网关|匹配|规则引擎)", re.IGNORECASE),
)


def _game_domain_path_roots(domain: str) -> set[str]:
    return _domain_path_roots(domain, _GAME_PM_DOMAIN_SCOPE_PATHS)


def _card3d_domain_path_roots(domain: str) -> set[str]:
    aliases = _CARD3D_PM_DOMAIN_SCOPE_ALIASES.get(
        domain,
        (_CARD3D_PM_DOMAIN_SCOPE_PATHS.get(domain, f"src/{domain}/index.ts"),),
    )
    return {root for root in (_normalize_path(alias) for alias in aliases) if root}


def _card3d_domain_target_files(domain: str) -> tuple[str, ...]:
    return _CARD3D_PM_DOMAIN_TARGET_FILES.get(
        domain,
        (_CARD3D_PM_DOMAIN_SCOPE_PATHS.get(domain, f"src/{domain}/index.ts"),),
    )


def _path_matches_game_domain(path: str, domain: str) -> bool:
    normalized = _normalize_path(path)
    if not normalized:
        return False
    return any(normalized == root or normalized.startswith(f"{root}/") for root in _game_domain_path_roots(domain))


def _path_matches_card3d_domain(path: str, domain: str) -> bool:
    normalized = _normalize_path(path)
    if not normalized:
        return False
    directory_domains = _CARD3D_PM_DIRECTORY_SCOPE_DOMAINS.get(normalized)
    if directory_domains is not None:
        return domain in directory_domains
    return any(normalized == root or normalized.startswith(f"{root}/") for root in _card3d_domain_path_roots(domain))


def _path_matches_card3d_detection_domain(path: str, domain: str) -> bool:
    normalized = _normalize_path(path)
    if not normalized:
        return False
    return any(normalized == root or normalized.startswith(f"{root}/") for root in _card3d_domain_path_roots(domain))


def _normalize_game_policy_path(path: str) -> str:
    normalized = _normalize_path(path)
    if normalized == "package.json":
        return ""
    if normalized == "build.mjs":
        return "scripts/build.mjs"
    if normalized == "test.mjs":
        return "scripts/test.mjs"
    if normalized.startswith("test/"):
        return f"tests/{normalized.removeprefix('test/')}"
    return normalized


def _sanitize_game_policy_paths_in_place(task: dict[str, Any]) -> int:
    normalized_count = 0
    for field in ("context_files", "target_files", "scope_paths"):
        original_paths = _normalize_path_list(task.get(field) or [])
        if not original_paths:
            continue
        sanitized = _dedupe_paths([mapped for path in original_paths if (mapped := _normalize_game_policy_path(path))])
        if sanitized != original_paths:
            task[field] = sanitized
            normalized_count += 1
    return normalized_count


def _sanitize_game_dependency_policy_value(value: Any, verify_command: str) -> tuple[Any, int]:
    replacements = [
        "Preserve the existing package.json scripts and do not add external test/build dependencies",
        "Run `node scripts/build.mjs` passes",
        f"Run `{verify_command}` passes",
    ]
    if isinstance(value, str):
        if not _GAME_PM_FORBIDDEN_DEPENDENCY_POLICY_RE.search(value):
            return value, 0
        return "Preserve existing no-external-dependency Node build/test scripts", 1
    if isinstance(value, list):
        normalized_items = [_normalize_text(item) for item in value if _normalize_text(item)]
        if not any(_GAME_PM_FORBIDDEN_DEPENDENCY_POLICY_RE.search(item) for item in normalized_items):
            return value, 0
        kept = [item for item in normalized_items if not _GAME_PM_FORBIDDEN_DEPENDENCY_POLICY_RE.search(item)]
        return _dedupe_text_items([*kept, *replacements]), 1
    return value, 0


def _sanitize_game_dependency_policy_in_place(task: dict[str, Any], verify_command: str) -> int:
    sanitized_count = _sanitize_game_policy_paths_in_place(task)
    for field in ("acceptance", "acceptance_criteria", "execution_checklist", "steps", "goal", "description"):
        if field not in task:
            continue
        sanitized_value, changed = _sanitize_game_dependency_policy_value(task.get(field), verify_command)
        if changed:
            task[field] = sanitized_value
            sanitized_count += changed
    return sanitized_count


def _has_forbidden_game_dependency_policy(task: dict[str, Any]) -> bool:
    parts: list[str] = []
    for field in ("acceptance", "acceptance_criteria", "execution_checklist", "steps", "goal", "description"):
        value = task.get(field)
        if isinstance(value, list):
            parts.extend(_normalize_text(item) for item in value if _normalize_text(item))
        elif isinstance(value, str):
            parts.append(_normalize_text(value))
    parts.extend(_normalize_path_list(task.get("target_files") or []))
    return any(_GAME_PM_FORBIDDEN_DEPENDENCY_POLICY_RE.search(part) for part in parts) or any(
        _normalize_path(path) == "package.json" for path in _normalize_path_list(task.get("target_files") or [])
    )


def _game_domains_for_task(task: dict[str, Any], workspace_full: Any) -> list[str]:
    coverage_paths: set[str] = set()
    for path in _collect_task_delivery_paths(task):
        relative = _workspace_relative_path(path, workspace_full)
        if relative:
            coverage_paths.add(relative)
            continue
        normalized = _normalize_path(path)
        if normalized:
            coverage_paths.add(normalized)

    domains: list[str] = []
    for domain in _GAME_PM_REQUIRED_DOMAINS:
        if any(_path_matches_game_domain(path, domain) for path in coverage_paths):
            domains.append(domain)
    return domains


def _card3d_domains_for_task(task: dict[str, Any], workspace_full: Any) -> list[str]:
    coverage_paths: set[str] = set()
    for path in _collect_task_delivery_paths(task):
        relative = _workspace_relative_path(path, workspace_full)
        if relative:
            coverage_paths.add(relative)
            continue
        normalized = _normalize_path(path)
        if normalized:
            coverage_paths.add(normalized)

    domains: list[str] = []
    for domain in _CARD3D_PM_REQUIRED_DOMAINS:
        if any(_path_matches_card3d_domain(path, domain) for path in coverage_paths):
            domains.append(domain)
    return domains


def _game_task_has_policy_risk(task: dict[str, Any]) -> bool:
    acceptance = task.get("acceptance_criteria")
    if not isinstance(acceptance, list):
        acceptance = task.get("acceptance")
    acceptance_items = [_normalize_text(item) for item in (acceptance or []) if _normalize_text(item)]
    text_parts = [
        _normalize_text(task.get("title")),
        _normalize_text(task.get("goal")),
        _normalize_text(task.get("description")),
        _normalize_text(task.get("backlog_ref")),
    ]
    return (
        _has_forbidden_game_dependency_policy(task)
        or _game_task_has_non_seed_stack_path(task)
        or _has_fragile_game_acceptance(acceptance_items)
        or _GAME_PM_OFF_DOMAIN_CORE_RE.search(" ".join(part for part in text_parts if part)) is not None
    )


def _is_game_stack_mutating_path(normalized: str) -> bool:
    if not normalized:
        return False
    if normalized in {"src", "tests"}:
        return True
    if normalized in {"package.json", "tsconfig.json"}:
        return True
    if normalized.startswith("src/") and not normalized.endswith((".ts", ".tsx")):
        return True
    return normalized.startswith("tests/") and not normalized.endswith((".ts", ".tsx"))


def _task_has_exact_seed_stack_targets(task: dict[str, Any]) -> bool:
    for path in _normalize_path_list(task.get("target_files") or []):
        normalized = _normalize_path(path)
        if normalized.startswith(("src/", "tests/")) and normalized.endswith((".ts", ".tsx")):
            return True
    return False


def _game_task_has_non_seed_stack_path(task: dict[str, Any]) -> bool:
    """Return True when a game task targets paths outside the seed TS stack."""
    for field in ("target_files", "context_files"):
        for path in _normalize_path_list(task.get(field) or []):
            if _is_game_stack_mutating_path(_normalize_path(path)):
                return True

    has_exact_seed_targets = _task_has_exact_seed_stack_targets(task)
    for path in _normalize_path_list(task.get("scope_paths") or []):
        normalized = _normalize_path(path)
        if not _is_game_stack_mutating_path(normalized):
            continue
        if has_exact_seed_targets and normalized not in {"package.json", "tsconfig.json"}:
            continue
        return True
    return False


def _card3d_task_is_unanchored_fallback(task: dict[str, Any], workspace_full: str) -> bool:
    """Return True for generic fallback rows that cannot guide card3d delivery."""
    domains = _card3d_domains_for_task(task, workspace_full)
    if any(domain != "tests" for domain in domains):
        return False

    task_id = _normalize_text(task.get("id")).upper()
    task_text = " ".join(
        _normalize_text(task.get(field))
        for field in ("title", "goal", "description")
        if _normalize_text(task.get(field))
    ).lower()
    if re.match(r"^PM-\d{4}-F\d+$", task_id) or "fallback" in task_text or "requirements " in task_text:
        return True

    acceptance = task.get("acceptance_criteria")
    if not isinstance(acceptance, list):
        acceptance = task.get("acceptance")
    acceptance_items = [_normalize_text(item) for item in (acceptance or []) if _normalize_text(item)]
    return not _has_executable_or_file_acceptance_anchor(acceptance_items)


def _remove_card3d_policy_incompatible_tasks_in_place(tasks: list[dict[str, Any]], workspace_full: str) -> int:
    """Remove stack-mutating tasks from card3d contracts before domain repair."""
    kept: list[dict[str, Any]] = []
    removed = 0
    for task in tasks:
        implementation_domains = [
            domain for domain in _card3d_domains_for_task(task, workspace_full) if domain != "tests"
        ]
        if _game_task_has_non_seed_stack_path(task):
            removed += 1
            continue
        if _card3d_task_is_unanchored_fallback(task, workspace_full):
            removed += 1
            continue
        if _game_task_has_policy_risk(task) and not implementation_domains:
            removed += 1
            continue
        kept.append(task)
    if removed:
        tasks[:] = kept
    return removed


def _remove_game_policy_incompatible_tasks_in_place(tasks: list[dict[str, Any]], workspace_full: str) -> int:
    """Remove narrow or stack-mutating tasks from game contracts.

    The game scenario seed already owns package/test-runner scaffolding. PM may
    still emit a narrow bootstrap/PRNG/map plan from a stale or incomplete
    prompt. Sanitizing those tasks is not enough because they can still give
    Director authority to replace the project stack. Keep risky tasks only when
    their scope is anchored to one of the allowed game delivery domains, where
    later sanitizers can safely normalize acceptance text.
    """
    kept: list[dict[str, Any]] = []
    removed = 0
    for task in tasks:
        implementation_domains = [
            domain for domain in _game_domains_for_task(task, workspace_full) if domain != "tests"
        ]
        if _game_task_has_non_seed_stack_path(task):
            removed += 1
            continue
        if _game_task_has_policy_risk(task) and not implementation_domains:
            removed += 1
            continue
        kept.append(task)
    if removed:
        tasks[:] = kept
    return removed


def _has_card3d_text_hints(text: str) -> bool:
    if not text:
        return False
    if _CARD3D_PM_CORE_HINT_RE.search(text) and _CARD3D_PM_STACK_HINT_RE.search(text):
        return True
    stack_hits = len({match.group(0).lower() for match in _CARD3D_PM_STACK_HINT_RE.finditer(text)})
    return stack_hits >= 2 and _CARD3D_PM_CORE_HINT_RE.search(text) is not None


def _domain_text_hints_enabled() -> bool:
    """Opt-in gate for KEYWORD-driven domain task injection (CLAUDE.md §8 fix).

    Factory-bench live failure (2026-06-12, L1-03 guess-number CLI): a bare
    text hit on "游戏/game" injected 12 roguelike-TypeScript phantom tasks
    (PM-AUTO-ENGINE/WORLD/COMBAT..., targets like src/engine/game-loop.ts)
    into a 2-task Python project; all 12 failed and poisoned the whole chain
    (exit=1, integration QA skipped). Text hints alone are project-blind —
    they now require explicit opt-in; PATH evidence (the workspace actually
    containing game-domain directories) remains authoritative either way.
    """
    return os.environ.get("KERNELONE_PM_DOMAIN_TEXT_HINTS", "0").strip().lower() in {"1", "true", "on", "yes"}


def _is_card3d_pm_contract(normalized: dict[str, Any], tasks: list[Any]) -> bool:
    if not _domain_contracts_enabled():
        return False
    text_parts = [
        _normalize_text(normalized.get("overall_goal")),
        _normalize_text(normalized.get("focus")),
        _normalize_text(normalized.get("notes")),
        _normalize_text(normalized.get("_quality_gate_card3d_context")),
    ]
    coverage_paths: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        text_parts.extend(
            [
                _normalize_text(task.get("title")),
                _normalize_text(task.get("goal")),
                _normalize_text(task.get("description")),
            ]
        )
        coverage_paths.extend(_collect_task_scope_paths(task))

    combined_text = " ".join(part for part in text_parts if part)
    if _has_card3d_text_hints(combined_text) and _domain_text_hints_enabled():
        return True

    normalized_paths = {_normalize_path(path) for path in coverage_paths if _normalize_path(path)}
    covered_domains = {
        domain
        for domain in _CARD3D_PM_REQUIRED_DOMAINS
        if any(_path_matches_card3d_detection_domain(path, domain) for path in normalized_paths)
    }
    return len(covered_domains) >= 2 and bool(covered_domains & _CARD3D_PM_DETECTION_CORE_DOMAINS)


def _is_game_pm_contract(normalized: dict[str, Any], tasks: list[Any]) -> bool:
    if not _domain_contracts_enabled():
        return False
    if _is_card3d_pm_contract(normalized, tasks):
        return False
    text_parts = [
        _normalize_text(normalized.get("overall_goal")),
        _normalize_text(normalized.get("focus")),
        _normalize_text(normalized.get("notes")),
        _normalize_text(normalized.get("_quality_gate_game_context")),
    ]
    coverage_paths: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        text_parts.extend(
            [
                _normalize_text(task.get("title")),
                _normalize_text(task.get("goal")),
                _normalize_text(task.get("description")),
            ]
        )
        coverage_paths.extend(_collect_task_scope_paths(task))

    combined_text = " ".join(part for part in text_parts if part)
    if _GAME_PM_HINT_RE.search(combined_text) and _domain_text_hints_enabled():
        return True

    normalized_paths = {_normalize_path(path) for path in coverage_paths if _normalize_path(path)}
    covered_domains = sum(
        1
        for domain in _GAME_PM_REQUIRED_DOMAINS
        if any(_path_matches_game_domain(path, domain) for path in normalized_paths)
    )
    return covered_domains >= 2


def _read_workspace_planning_hint_text(workspace_full: str) -> str:
    workspace = Path(str(workspace_full or "").strip())
    if not workspace:
        return ""
    candidates = (
        workspace / "runtime" / "contracts" / "plan.md",
        workspace / "runtime" / "contracts" / "requirements.md",
        workspace / ".polaris" / "docs" / "30_backlog.md",
        workspace / ".polaris" / "docs" / "10_requirements.md",
        workspace / ".polaris" / "docs" / "product" / "plan.md",
        workspace / ".polaris" / "docs" / "product" / "requirements.md",
    )
    chunks: list[str] = []
    for candidate in candidates:
        try:
            if candidate.is_file():
                chunks.append(candidate.read_text(encoding="utf-8", errors="replace")[:12_000])
        except OSError:
            continue
    return "\n".join(chunks)


def _workspace_has_game_planning_hints(workspace_full: str) -> bool:
    hint_text = _read_workspace_planning_hint_text(workspace_full)
    if not hint_text:
        return False
    if _GAME_PM_HINT_RE.search(hint_text):
        return True
    signal_count = sum(1 for pattern in _GAME_PM_WORKSPACE_SIGNAL_PATTERNS if pattern.search(hint_text))
    return signal_count >= 3


def _workspace_has_card3d_planning_hints(workspace_full: str) -> bool:
    hint_text = _read_workspace_planning_hint_text(workspace_full)
    if not hint_text:
        return False
    if _has_card3d_text_hints(hint_text):
        return True
    signal_count = sum(1 for pattern in _CARD3D_PM_WORKSPACE_SIGNAL_PATTERNS if pattern.search(hint_text))
    return signal_count >= 2


def _attach_workspace_game_context_if_needed(normalized: dict[str, Any], tasks: list[Any], workspace_full: str) -> bool:
    if not _domain_contracts_enabled():
        return False
    if _is_card3d_pm_contract(normalized, tasks):
        return False
    if _workspace_has_card3d_planning_hints(workspace_full):
        normalized["_quality_gate_card3d_context"] = "card3d workspace_planning_hints"
        return True
    if _is_game_pm_contract(normalized, tasks):
        return False
    if not _workspace_has_game_planning_hints(workspace_full):
        return False
    normalized["_quality_gate_game_context"] = "game workspace_planning_hints"
    return True


def _covered_card3d_domains(tasks: list[Any], workspace_full: Any) -> list[str]:
    coverage_paths: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        for path in _collect_task_delivery_paths(task):
            relative = _workspace_relative_path(path, workspace_full)
            if relative:
                coverage_paths.add(relative)

    covered: list[str] = []
    for domain in _CARD3D_PM_REQUIRED_DOMAINS:
        if any(_path_matches_card3d_domain(path, domain) for path in coverage_paths):
            covered.append(domain)
    return covered


def _missing_card3d_required_test_targets(tasks: list[Any]) -> list[str]:
    declared_targets: set[str] = set()
    has_tests_domain = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if "tests" not in _card3d_domains_for_task(task, workspace_full=None):
            continue
        has_tests_domain = True
        declared_targets.update(_normalize_path(path) for path in _normalize_path_list(task.get("target_files") or []))
    if not has_tests_domain:
        return []
    return [path for path in _CARD3D_PM_TEST_TARGET_FILES if _normalize_path(path) not in declared_targets]


def _card3d_tests_task_has_placeholder_cleanup_contract(tasks: list[Any]) -> bool:
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if "tests" not in _card3d_domains_for_task(task, workspace_full=None):
            continue
        text_parts = [
            _normalize_text(task.get("title")),
            _normalize_text(task.get("goal")),
            _normalize_text(task.get("description")),
            _normalize_text(task.get("backlog_ref")),
        ]
        for field in ("acceptance_criteria", "acceptance", "execution_checklist"):
            values = task.get(field)
            if isinstance(values, list):
                text_parts.extend(_normalize_text(item) for item in values)
        token = " ".join(part for part in text_parts if part).lower()
        has_cleanup = any(
            hint in token
            for hint in (
                "replace",
                "remove",
                "delete",
                "clear",
                "overwrite",
                "替换",
                "移除",
                "删除",
                "清理",
                "覆盖",
            )
        )
        has_placeholder = any(
            hint in token
            for hint in (
                "placeholder",
                "arithmetic",
                "case 1",
                "case 18",
                "占位",
                "算术",
                "占位测试",
            )
        )
        if has_cleanup and has_placeholder:
            return True
    return False


def _covered_game_domains(tasks: list[Any], workspace_full: Any) -> list[str]:
    coverage_paths: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        for path in _collect_task_delivery_paths(task):
            relative = _workspace_relative_path(path, workspace_full)
            if relative:
                coverage_paths.add(relative)

    covered: list[str] = []
    for domain in _GAME_PM_REQUIRED_DOMAINS:
        if any(_path_matches_game_domain(path, domain) for path in coverage_paths):
            covered.append(domain)
    return covered


def _build_game_domain_repair_task(
    *,
    domain: str,
    task_id: str,
    depends_on: str,
    verify_command: str,
    sequence: int,
) -> dict[str, Any]:
    scope_path = _GAME_PM_DOMAIN_SCOPE_PATHS.get(domain, f"src/{domain}/index.ts")
    title = _GAME_PM_DOMAIN_TITLES.get(domain, f"Implement {domain} game capability")
    task: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "goal": f"Add the missing {domain} capability so the PM contract covers the full game delivery scope.",
        "description": (
            f"Quality gate repair task generated because the PM contract omitted the {domain} "
            "delivery domain required by the project goal."
        ),
        "assigned_to": "director",
        "phase": "verification" if domain == "tests" else "implementation",
        "priority": 5000 + sequence,
        "scope_paths": [scope_path],
        "target_files": [scope_path],
        "acceptance_criteria": [
            f"verify {scope_path} exists",
            f"Run `{verify_command}` passes",
        ],
        "execution_checklist": [
            "Review existing generated project structure",
            f"Implement the missing {domain} capability in the scoped files",
            "Run the acceptance command and record the result",
        ],
        "metadata": {
            "autofix": True,
            "autofix_reason": "game_pm_domain_coverage",
            "domain": domain,
        },
    }
    if depends_on:
        task["depends_on"] = [depends_on]
    else:
        task["depends_on"] = []
    return task


def _build_card3d_domain_repair_task(
    *,
    domain: str,
    task_id: str,
    depends_on: str,
    verify_command: str,
    sequence: int,
) -> dict[str, Any]:
    scope_path = _CARD3D_PM_DOMAIN_SCOPE_PATHS.get(domain, f"src/{domain}/index.ts")
    target_files = list(_card3d_domain_target_files(domain))
    title = _CARD3D_PM_DOMAIN_TITLES.get(domain, f"Implement {domain} multiplayer card capability")
    acceptance_criteria = [
        f"verify {', '.join(target_files)} exist",
        f"verify {', '.join(target_files)} contain no audit-seed or planning scenario markers",
        f"Run `{verify_command}` passes",
    ]
    execution_checklist = [
        "Review the existing TypeScript Three.js client and Node.js backend seed",
        f"Replace the seed placeholder content with the missing {domain} capability in the scoped files",
        "Run the acceptance command and record the result",
    ]
    if domain == "tests":
        acceptance_criteria.insert(1, "verify trivial arithmetic placeholder test cases are replaced or removed")
        acceptance_criteria.insert(
            2,
            "verify scripts/build.mjs and scripts/test.mjs no longer perform structural-only existence checks",
        )
        execution_checklist.insert(
            1, "Replace or remove existing trivial arithmetic placeholder tests instead of appending around them"
        )
        execution_checklist.insert(
            2,
            "Replace structural-only build/test scripts with substantive no-external-dependency verification",
        )
    task: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "goal": f"Add the missing {domain} capability for the multiplayer Three.js card game scope.",
        "description": (
            f"Quality gate repair task generated because the PM contract omitted the {domain} "
            "delivery domain required by the multiplayer creative card game goal."
        ),
        "assigned_to": "director",
        "phase": "verification" if domain == "tests" else "implementation",
        "priority": 6000 + sequence,
        "scope_paths": ["tests"] if domain == "tests" else [scope_path],
        "target_files": target_files,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "metadata": {
            "autofix": True,
            "autofix_reason": "card3d_pm_domain_coverage",
            "domain": domain,
        },
    }
    task["depends_on"] = [depends_on] if depends_on else []
    return task


def _append_missing_card3d_domain_tasks(
    normalized: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    workspace_full: str,
    verify_command: str,
) -> int:
    if not _is_card3d_pm_contract(normalized, tasks):
        return 0

    covered_domains = set(_covered_card3d_domains(tasks, workspace_full))
    missing_domains = [domain for domain in _CARD3D_PM_REQUIRED_DOMAINS if domain not in covered_domains]
    if not missing_domains:
        return 0

    existing_ids = {_normalize_text(task.get("id")) for task in tasks if _normalize_text(task.get("id"))}
    dependency_anchor = _last_task_id(tasks)
    added = 0
    for domain in missing_domains:
        task_id = _unique_task_id(existing_ids, f"PM-AUTO-CARD3D-{domain}")
        tasks.append(
            _build_card3d_domain_repair_task(
                domain=domain,
                task_id=task_id,
                depends_on=dependency_anchor,
                verify_command=verify_command,
                sequence=added,
            )
        )
        dependency_anchor = task_id
        added += 1

    return added


def _repair_card3d_tests_task_contract(tasks: list[dict[str, Any]]) -> int:
    repaired = 0
    for task in tasks:
        if "tests" not in _card3d_domains_for_task(task, workspace_full=None):
            continue

        target_files = _normalize_path_list(task.get("target_files") or [])
        target_set = {_normalize_path(path) for path in target_files}
        missing_targets = [path for path in _CARD3D_PM_TEST_TARGET_FILES if _normalize_path(path) not in target_set]
        if missing_targets:
            task["target_files"] = _dedupe_paths([*target_files, *missing_targets])
            repaired += len(missing_targets)

        repaired += _append_unique_text_item(
            task,
            "acceptance_criteria",
            "verify trivial arithmetic placeholder test cases are replaced or removed",
        )
        repaired += _append_unique_text_item(
            task,
            "acceptance_criteria",
            "verify scripts/build.mjs and scripts/test.mjs no longer perform structural-only existence checks",
        )
        repaired += _append_unique_text_item(
            task,
            "execution_checklist",
            "Replace or remove existing trivial arithmetic placeholder tests instead of appending around them",
        )
        repaired += _append_unique_text_item(
            task,
            "execution_checklist",
            "Replace structural-only build/test scripts with substantive no-external-dependency verification",
        )
    return repaired


def _append_missing_game_domain_tasks(
    normalized: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    workspace_full: str,
    verify_command: str,
) -> int:
    if not _is_game_pm_contract(normalized, tasks):
        return 0

    covered_domains = set(_covered_game_domains(tasks, workspace_full))
    missing_domains = [domain for domain in _GAME_PM_REQUIRED_DOMAINS if domain not in covered_domains]
    if not missing_domains:
        return 0

    existing_ids = {_normalize_text(task.get("id")) for task in tasks if _normalize_text(task.get("id"))}
    dependency_anchor = _last_task_id(tasks)
    added = 0
    for domain in missing_domains:
        task_id = _unique_task_id(existing_ids, f"PM-AUTO-{domain}")
        tasks.append(
            _build_game_domain_repair_task(
                domain=domain,
                task_id=task_id,
                depends_on=dependency_anchor,
                verify_command=verify_command,
                sequence=added,
            )
        )
        dependency_anchor = task_id
        added += 1

    return added


def _has_fragile_game_acceptance(acceptance_items: list[str]) -> bool:
    return any(_GAME_PM_FRAGILE_ACCEPTANCE_RE.search(_normalize_text(item)) for item in acceptance_items)


def _primary_task_evidence_path(task: dict[str, Any], workspace_full: Any = "") -> str:
    for path in _normalize_path_list(task.get("target_files") or []):
        normalized = _normalize_path(path)
        if normalized and _is_file_like_pm_scope_path(normalized):
            return normalized
    for path in _collect_task_delivery_paths(task):
        normalized = _normalize_path(path)
        if normalized and _is_file_like_pm_scope_path(normalized):
            return normalized
    for path in _collect_task_delivery_paths(task):
        representative = _representative_workspace_file_for_scope(path, workspace_full)
        if representative:
            return representative
    for path in _collect_task_delivery_paths(task):
        fallback = _fallback_file_evidence_path_for_scope(path)
        if fallback:
            return fallback
    return _GAME_PM_DOMAIN_SCOPE_PATHS["engine"]


def _sanitize_fragile_game_acceptance_in_place(task: dict[str, Any], verify_command: str) -> int:
    """Replace brittle game-randomness acceptance with invariant checks."""
    normalized_fields = 0
    replacement = [
        f"verify {_primary_task_evidence_path(task)} exists",
        f"Run `{verify_command}` passes",
        "Randomness coverage checks deterministic repeatability, numeric bounds, and state restoration without literal output snapshots",
    ]
    for field in ("acceptance_criteria", "acceptance"):
        raw_items = task.get(field)
        if not isinstance(raw_items, list):
            continue
        normalized_items = [_normalize_text(item) for item in raw_items if _normalize_text(item)]
        if not _has_fragile_game_acceptance(normalized_items):
            continue
        kept = [item for item in normalized_items if not _GAME_PM_FRAGILE_ACCEPTANCE_RE.search(item)]
        task[field] = _dedupe_text_items([*kept, *replacement])
        normalized_fields += 1
    return normalized_fields

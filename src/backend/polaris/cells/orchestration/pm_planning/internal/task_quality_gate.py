"""PM Task Quality Gate Module.

This module provides PM task quality evaluation, validation, and autofix capabilities.
It is designed to be testable as pure functions without side effects.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    DependencyCycleError,
    validate_dependency_dag,
)

_PM_PROMPT_LEAK_TOKENS = (
    "you are ",
    "角色设定",
    "system prompt",
    "no yapping",
    "<thinking>",
    "<tool_call>",
)
_PM_CHINESE_PROMPT_LEAK_TOKENS = (
    "系统提示词",
    "开发者提示词",
    "角色提示词",
    "内部提示词",
    "完整提示词",
    "提示词泄露",
    "提示词泄漏",
    "提示词注入",
)
_CJK_CHAR_RE = re.compile(r"[\u3400-\u9fff]")
_PM_ACTION_TOKENS = (
    "add",
    "build",
    "connect",
    "deliver",
    "implement",
    "define",
    "design",
    "extract",
    "fix",
    "harden",
    "integrate",
    "persist",
    "write",
    "create",
    "refactor",
    "test",
    "update",
    "validate",
    "verify",
    "补充",
    "补齐",
    "持久化",
    "抽取",
    "记录",
    "接入",
    "落地",
    "收敛",
    "输出",
    "完成",
    "新增",
    "修复",
    "创建",
    "统一",
    "校验",
    "构建",
    "实现",
    "设计",
    "编写",
    "重构",
    "验证",
)
_PM_MEASURABLE_COMMAND_RE = re.compile(
    r"\b(curl|wget|httpie|npm|pnpm|yarn|npx|node|python|pytest|go\s+test|mvn|gradle|dotnet|cargo|grep|jq|awk|sed|powershell|pwsh)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_ASSERT_RE = re.compile(
    r"\b(verify|assert|expect|should|must|returns?|response|status|校验|验证|断言|应当|必须)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_RESULT_RE = re.compile(
    r"\b(200|201|202|204|400|401|403|404|409|422|500|pass|fail|true|false|ok|error)\b|[<>]=?\s*\d+|\b\d+\s*(ms|s|sec|seconds?|分钟|小时|days?)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[\w.\-]+[\\/][\w.\-/\\]+)",
)
_PM_MEASURABLE_BACKTICK_RE = re.compile(r"`[^`]{2,}`")
_PM_EXECUTABLE_BACKTICK_RE = re.compile(r"`([^`]{2,})`")
_PM_SCOPE_ROOTS = {
    "app",
    "backend",
    "components",
    "docs",
    "electron",
    "frontend",
    "lib",
    "packages",
    "scripts",
    "services",
    "src",
    "tests",
    "workspace",
}
_PM_SCOPE_FILENAMES = {
    "package.json",
    "README.md",
    "tsconfig.json",
    "vite.config.ts",
    "vitest.config.ts",
    "tailwind.config.js",
    "postcss.config.js",
    "pyproject.toml",
}
_PM_SCOPE_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_PM_NON_PATH_TEXT_RE = re.compile(r"[\s,，、；;：:。]|[\u4e00-\u9fff]")
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


def should_apply_card3d_pm_domain_contract(*texts: Any) -> bool:
    """Return whether PM prompt text describes the Card3D multiplayer scenario."""

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


def _strip_wrapping_quotes(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _normalize_path_list(value: Any) -> list[str]:
    if isinstance(value, str):
        entries = [segment.strip() for segment in value.split(",") if segment.strip()]
    elif isinstance(value, list):
        entries = [str(item).strip() for item in value if str(item).strip()]
    else:
        entries = []
    normalized: list[str] = []
    for item in entries:
        token = str(item).strip().replace("\\", "/")
        while token.startswith("./"):
            token = token[2:]
        token = re.sub(r"/+", "/", token)
        if token:
            normalized.append(token)
    return normalized


def _normalize_dep_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = _normalize_text(item)
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _task_id_at_position(tasks: list[dict[str, Any]], position: int) -> str:
    if position <= 0 or position > len(tasks):
        return ""
    return _normalize_text(tasks[position - 1].get("id"))


def _resolve_dependency_ref(token: str, tasks: list[dict[str, Any]], known_ids: set[str]) -> str:
    if token in known_ids:
        return token
    parts = token.split("-")
    if len(parts) == 2 and parts[0].upper() == "PM" and parts[1].isdigit():
        mapped = _task_id_at_position(tasks, int(parts[1]))
        if mapped:
            return mapped
    if len(parts) >= 3 and parts[0].upper() == "PM" and parts[-1].isdigit():
        mapped = _task_id_at_position(tasks, int(parts[-1]))
        if mapped:
            return mapped
    return token


def _normalize_dependency_refs_in_place(tasks: list[dict[str, Any]]) -> int:
    known_ids = {_normalize_text(task.get("id")) for task in tasks if _normalize_text(task.get("id"))}
    normalized_count = 0
    for task in tasks:
        task_id = _normalize_text(task.get("id"))
        raw_deps = task.get("depends_on")
        target_key = "depends_on"
        if not isinstance(raw_deps, list):
            raw_deps = task.get("dependencies")
            target_key = "dependencies"
        deps = _normalize_dep_list(raw_deps)
        if not deps:
            continue
        rewritten: list[str] = []
        seen: set[str] = set()
        for dep in deps:
            resolved = _resolve_dependency_ref(dep, tasks, known_ids)
            if not resolved or resolved == task_id or resolved in seen:
                if resolved != dep:
                    normalized_count += 1
                continue
            seen.add(resolved)
            rewritten.append(resolved)
            if resolved != dep:
                normalized_count += 1
        task[target_key] = rewritten
    return normalized_count


def _unknown_dependency_refs(tasks: list[dict[str, Any]]) -> list[str]:
    known_ids = {_normalize_text(task.get("id")) for task in tasks if _normalize_text(task.get("id"))}
    unknown: list[str] = []
    for task in tasks:
        task_id = _normalize_text(task.get("id"))
        deps = task.get("depends_on")
        if not isinstance(deps, list):
            deps = task.get("dependencies")
        for dep in _normalize_dep_list(deps):
            if dep not in known_ids:
                unknown.append(f"{task_id}: unknown dependency `{dep}`")
    return unknown


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_path(value: Any) -> str:
    token = str(value or "").strip().replace("\\", "/")
    token = re.sub(r"^[A-Za-z]:/", "", token)
    while token.startswith("./"):
        token = token[2:]
    token = token.strip("/")
    token = re.sub(r"/+", "/", token)
    return token.lower()


def _domain_path_roots(domain: str, scope_paths: dict[str, str]) -> set[str]:
    if domain == "tests":
        return {"tests"}
    primary = _normalize_path(scope_paths.get(domain, f"src/{domain}/index.ts"))
    parent = os.path.dirname(primary).replace("\\", "/").strip("/") if primary else ""
    roots = {parent or f"src/{domain}"}
    if primary:
        roots.add(primary)
        if parent:
            roots.add(parent)
    return {root for root in roots if root}


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


def _is_concrete_pm_scope_path(value: Any) -> bool:
    raw = _strip_wrapping_quotes(str(value or "").strip()).replace("\\", "/")
    if not raw:
        return False
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", raw):
        return False
    if _PM_NON_PATH_TEXT_RE.search(raw):
        return False

    token = raw
    while token.startswith("./"):
        token = token[2:]
    token = re.sub(r"/+", "/", token.strip("/"))
    if not token or token in {".", "*", "**"}:
        return False
    parts = [part for part in token.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return False

    filename = parts[-1]
    if token in _PM_SCOPE_FILENAMES or filename in _PM_SCOPE_FILENAMES:
        return True
    if os.path.splitext(filename)[1].lower() in _PM_SCOPE_SUFFIXES:
        return True
    if parts[0] in _PM_SCOPE_ROOTS and (len(parts) > 1 or raw.endswith("/")):
        return True
    return token.rstrip("/") in _PM_SCOPE_ROOTS


def _workspace_prefix(workspace_full: Any) -> str:
    token = str(workspace_full or "").strip()
    if not token:
        return ""
    try:
        return os.path.normcase(os.path.abspath(token))
    except (OSError, ValueError):
        return ""


def _path_parts_contain_parent(candidate: str) -> bool:
    token = candidate.replace("\\", "/")
    return any(part == ".." for part in token.split("/"))


def _workspace_relative_path(candidate: Any, workspace_full: Any) -> str:
    raw = _strip_wrapping_quotes(str(candidate or "").strip())
    if not raw or raw.startswith("~") or _path_parts_contain_parent(raw):
        return ""

    workspace_prefix = _workspace_prefix(workspace_full)
    if not workspace_prefix:
        if os.path.isabs(raw):
            return ""
        relative = raw.replace("\\", "/")
        while relative.startswith("./"):
            relative = relative[2:]
        return re.sub(r"/+", "/", relative.strip("/")).lower()

    try:
        resolved = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(workspace_prefix, raw))
        common = os.path.commonpath([workspace_prefix, os.path.normcase(resolved)])
    except (OSError, ValueError):
        return ""
    if os.path.normcase(common) != workspace_prefix:
        return ""
    try:
        relative = os.path.relpath(resolved, workspace_prefix)
    except (OSError, ValueError):
        return ""
    if relative == ".":
        return ""
    return re.sub(r"/+", "/", relative.replace("\\", "/").strip("/")).lower()


def _is_workspace_bound_concrete_path(candidate: Any, workspace_full: Any) -> bool:
    relative = _workspace_relative_path(candidate, workspace_full)
    if not relative:
        return False
    return _is_concrete_pm_scope_path(relative)


def _coerce_pm_path_to_workspace_relative(candidate: Any, workspace_full: Any) -> tuple[str, str]:
    """Coerce an LLM-provided PM path into a workspace-relative path.

    PM contracts are executable handoffs. Absolute paths are not acceptable in
    those contracts because Director write gates are scoped to the active
    workspace. If a path is already inside the workspace, keep its true relative
    form. If an LLM names a throwaway external project root such as
    ``C:/Temp/roguelike-ts/src/app.ts``, preserve only the project-internal
    suffix (``src/app.ts``). Parent traversal and non-concrete path text still
    fail closed.
    """
    raw = _strip_wrapping_quotes(str(candidate or "").strip())
    if not raw or raw.startswith("~") or _path_parts_contain_parent(raw):
        return "", ""

    relative = _workspace_relative_path(raw, workspace_full)
    if relative and _is_concrete_pm_scope_path(relative):
        return relative, ""

    normalized_raw = raw.replace("\\", "/")
    is_absolute = os.path.isabs(raw) or bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or normalized_raw.startswith("/")
    if not is_absolute:
        token = _normalize_path(raw)
        return (token, "") if token and _is_concrete_pm_scope_path(token) else ("", "")

    without_drive = re.sub(r"^[A-Za-z]:/", "", normalized_raw).strip("/")
    parts = [part for part in without_drive.split("/") if part]
    if not parts:
        return "", ""

    suffix_candidates: list[tuple[list[str], str]] = []
    for index, part in enumerate(parts):
        if part.lower() in _PM_SCOPE_ROOTS:
            suffix_candidates.append((parts[index:], "/".join(parts[:index])))
            break

    filename = parts[-1]
    if filename in _PM_SCOPE_FILENAMES or os.path.splitext(filename)[1].lower() in _PM_SCOPE_SUFFIXES:
        if len(parts) >= 3 and parts[0].lower() in {"temp", "tmp"}:
            suffix_candidates.append((parts[2:], "/".join(parts[:2])))
        if len(parts) >= 2:
            suffix_candidates.append((parts[-2:], "/".join(parts[:-2])))
        suffix_candidates.append(([filename], "/".join(parts[:-1])))

    for suffix_parts, stripped_root in suffix_candidates:
        token = _normalize_path("/".join(suffix_parts))
        if token and _is_concrete_pm_scope_path(token):
            suffix_text = "/".join(suffix_parts).strip("/")
            original_root = stripped_root
            if suffix_text and normalized_raw.lower().endswith("/" + suffix_text.lower()):
                original_root = normalized_raw[: -len(suffix_text)].rstrip("/")
            return token, original_root
    return "", ""


def _dedupe_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        token = str(path or "").strip()
        key = token.lower()
        if not token or key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped


def _derive_scope_from_pm_targets(target_files: list[str]) -> list[str]:
    scopes: list[str] = []
    for target in target_files:
        token = _normalize_path(target)
        if not token:
            continue
        directory = os.path.dirname(token).replace("\\", "/").strip("/")
        scope = directory or token
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes


def _replace_external_roots_in_text(value: Any, stripped_roots: set[str]) -> Any:
    if not stripped_roots:
        return value
    if isinstance(value, str):
        text = value
        for root in sorted((item for item in stripped_roots if item), key=len, reverse=True):
            root_slash = root.replace("\\", "/").strip("/")
            if not root_slash:
                continue
            drive_variants = [root_slash]
            if re.match(r"^[A-Za-z]:/", root_slash):
                drive_variants.append(root_slash.replace("/", "\\"))
            for variant in drive_variants:
                text = text.replace(variant + "/", "")
                text = text.replace(variant + "\\", "")
                text = text.replace(variant, "workspace root")
        return text
    if isinstance(value, list):
        return [_replace_external_roots_in_text(item, stripped_roots) for item in value]
    if isinstance(value, dict):
        return {key: _replace_external_roots_in_text(item, stripped_roots) for key, item in value.items()}
    return value


def _sanitize_pm_task_paths_in_place(tasks: list[dict[str, Any]], workspace_full: str) -> int:
    normalized_count = 0
    for task in tasks:
        stripped_roots: set[str] = set()
        for field in ("context_files", "target_files", "scope_paths"):
            original_paths = _normalize_path_list(task.get(field) or [])
            sanitized: list[str] = []
            for raw_path in original_paths:
                relative, stripped_root = _coerce_pm_path_to_workspace_relative(raw_path, workspace_full)
                if not relative:
                    continue
                sanitized.append(relative)
                if stripped_root:
                    stripped_roots.add(stripped_root)
            sanitized = _dedupe_paths(sanitized)
            if sanitized != original_paths:
                normalized_count += 1
            task[field] = sanitized

        if not task.get("scope_paths") and task.get("target_files"):
            task["scope_paths"] = _derive_scope_from_pm_targets(
                [str(item) for item in task.get("target_files") or [] if str(item).strip()]
            )
            normalized_count += 1

        if stripped_roots:
            for field in (
                "title",
                "goal",
                "description",
                "backlog_ref",
                "acceptance",
                "acceptance_criteria",
                "execution_checklist",
                "steps",
            ):
                if field in task:
                    task[field] = _replace_external_roots_in_text(task.get(field), stripped_roots)
    return normalized_count


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


def _drop_unknown_dependency_refs_in_place(tasks: list[dict[str, Any]]) -> int:
    known_ids = {_normalize_text(task.get("id")) for task in tasks if _normalize_text(task.get("id"))}
    normalized_count = 0
    for task in tasks:
        task_id = _normalize_text(task.get("id"))
        target_key = "depends_on"
        raw_deps = task.get(target_key)
        if not isinstance(raw_deps, list):
            target_key = "dependencies"
            raw_deps = task.get(target_key)
        deps = _normalize_dep_list(raw_deps)
        if not deps:
            continue
        kept: list[str] = []
        seen: set[str] = set()
        for dep in deps:
            if dep not in known_ids or dep == task_id or dep in seen:
                normalized_count += 1
                continue
            seen.add(dep)
            kept.append(dep)
        if kept != deps:
            task[target_key] = kept
    return normalized_count


def _collect_task_scope_paths(task: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    paths.extend(_normalize_path_list(task.get("scope_paths") or []))
    paths.extend(_normalize_path_list(task.get("target_files") or []))
    paths.extend(_normalize_path_list(task.get("context_files") or []))
    return paths


def _collect_task_delivery_paths(task: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    paths.extend(_normalize_path_list(task.get("scope_paths") or []))
    paths.extend(_normalize_path_list(task.get("target_files") or []))
    return paths


def _has_card3d_text_hints(text: str) -> bool:
    if not text:
        return False
    if _CARD3D_PM_CORE_HINT_RE.search(text) and _CARD3D_PM_STACK_HINT_RE.search(text):
        return True
    stack_hits = len({match.group(0).lower() for match in _CARD3D_PM_STACK_HINT_RE.finditer(text)})
    return stack_hits >= 2 and _CARD3D_PM_CORE_HINT_RE.search(text) is not None


def _is_card3d_pm_contract(normalized: dict[str, Any], tasks: list[Any]) -> bool:
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
    if _has_card3d_text_hints(combined_text):
        return True

    normalized_paths = {_normalize_path(path) for path in coverage_paths if _normalize_path(path)}
    covered_domains = {
        domain
        for domain in _CARD3D_PM_REQUIRED_DOMAINS
        if any(_path_matches_card3d_detection_domain(path, domain) for path in normalized_paths)
    }
    return len(covered_domains) >= 2 and bool(covered_domains & _CARD3D_PM_DETECTION_CORE_DOMAINS)


def _is_game_pm_contract(normalized: dict[str, Any], tasks: list[Any]) -> bool:
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
    if _GAME_PM_HINT_RE.search(combined_text):
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


def _last_task_id(tasks: list[dict[str, Any]]) -> str:
    for task in reversed(tasks):
        task_id = _normalize_text(task.get("id"))
        if task_id:
            return task_id
    return ""


def _unique_task_id(existing_ids: set[str], base: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "-", base.strip()).strip("-").upper()
    if not token:
        token = "PM-AUTO-TASK"
    candidate = token
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{token}-{suffix}"
        suffix += 1
    existing_ids.add(candidate)
    return candidate


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


def _append_unique_text_item(task: dict[str, Any], field: str, item: str) -> int:
    existing = task.get(field)
    if not isinstance(existing, list):
        task[field] = [item]
        return 1
    normalized_existing = {_normalize_text(value).lower() for value in existing if _normalize_text(value)}
    normalized_item = _normalize_text(item).lower()
    if normalized_item and normalized_item not in normalized_existing:
        existing.append(item)
        return 1
    return 0


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


def _contains_prompt_leakage(text: str) -> bool:
    lowered = _normalize_text(text).lower()
    if not lowered:
        return False
    if any(token in lowered for token in _PM_PROMPT_LEAK_TOKENS):
        return True
    return any(token in lowered for token in _PM_CHINESE_PROMPT_LEAK_TOKENS)


def _title_is_too_short(title: str) -> bool:
    if not title:
        return True
    cjk_count = len(_CJK_CHAR_RE.findall(title))
    if cjk_count >= 5:
        return False
    return len(title) < 10


def _has_measurable_acceptance_anchor(acceptance_items: list[str]) -> bool:
    for item in acceptance_items:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        if _PM_MEASURABLE_BACKTICK_RE.search(normalized):
            return True
        if _PM_MEASURABLE_COMMAND_RE.search(normalized):
            return True
        has_assert = bool(_PM_MEASURABLE_ASSERT_RE.search(normalized))
        has_observable = bool(_PM_MEASURABLE_RESULT_RE.search(normalized) or _PM_MEASURABLE_PATH_RE.search(normalized))
        if has_assert and has_observable:
            return True
    return False


def _has_executable_or_file_acceptance_anchor(acceptance_items: list[str]) -> bool:
    """Return True when acceptance proves an executable check or concrete file evidence.

    ``_has_measurable_acceptance_anchor`` intentionally accepts observable outcomes
    such as HTTP status codes. Director/ChiefEngineer handoff tasks need a stronger
    anchor so a PM contract cannot pass with generic "it works" style acceptance.
    """
    for item in acceptance_items:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        if _PM_MEASURABLE_COMMAND_RE.search(normalized):
            return True
        for match in _PM_EXECUTABLE_BACKTICK_RE.finditer(normalized):
            if _PM_MEASURABLE_COMMAND_RE.search(match.group(1)):
                return True
        if _PM_MEASURABLE_ASSERT_RE.search(normalized) and _PM_MEASURABLE_PATH_RE.search(normalized):
            return True
    return False


def _has_fragile_game_acceptance(acceptance_items: list[str]) -> bool:
    return any(_GAME_PM_FRAGILE_ACCEPTANCE_RE.search(_normalize_text(item)) for item in acceptance_items)


def _dedupe_text_items(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = _normalize_text(item)
        key = token.lower()
        if not token or key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped


def _primary_task_evidence_path(task: dict[str, Any]) -> str:
    for path in _collect_task_delivery_paths(task):
        normalized = _normalize_path(path)
        if normalized and _is_concrete_pm_scope_path(normalized):
            return normalized
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


def evaluate_pm_task_quality(
    normalized: dict[str, Any],
    docs_stage: dict[str, Any] | None = None,
    workspace_full: str | None = None,
) -> dict[str, Any]:
    """Evaluate PM task quality and return quality report.

    Args:
        normalized: Normalized PM task payload with 'tasks' key
        docs_stage: Optional docs stage configuration
        workspace_full: Optional current workspace root. Falls back to
            normalized["workspace"] when available.

    Returns:
        Quality report with score, issues, warnings, and summary
    """
    tasks_raw = normalized.get("tasks")
    tasks: list[Any] = tasks_raw if isinstance(tasks_raw, list) else []
    critical_issues: list[str] = []
    warnings: list[str] = []
    seen_signatures: set[str] = set()
    low_action_count = 0
    phase_count = 0
    dependency_task_count = 0
    checklist_task_count = 0
    measurable_acceptance_task_count = 0
    docs_section_task_count = 0
    backlog_trace_task_count = 0

    docs_stage_dict: dict[str, Any] = docs_stage if isinstance(docs_stage, dict) else {}
    effective_workspace = str(workspace_full or normalized.get("workspace") or "").strip()
    docs_enabled = bool(docs_stage_dict.get("enabled"))
    active_doc = _normalize_path(docs_stage_dict.get("active_doc_path", ""))
    active_dir = _normalize_path(os.path.dirname(active_doc)) if active_doc else ""
    is_card3d_contract = _is_card3d_pm_contract(normalized, tasks)
    is_game_contract = _is_game_pm_contract(normalized, tasks)

    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            critical_issues.append(f"task[{index}]: task payload is not an object")
            continue
        task_id = str(task.get("id") or f"TASK-{index}").strip()
        title = _normalize_text(task.get("title"))
        goal = _normalize_text(task.get("goal"))
        description = _normalize_text(task.get("description"))
        backlog_ref = _normalize_text(task.get("backlog_ref"))
        signature = _normalize_text(f"{title.lower()}::{goal.lower()}")
        combined_text = " ".join([title, goal, description, backlog_ref]).strip()

        if signature:
            if signature in seen_signatures:
                critical_issues.append(f"{task_id}: duplicated title/goal signature")
            seen_signatures.add(signature)

        if _title_is_too_short(title):
            warnings.append(f"{task_id}: title is too short")
        if len(goal) < 18:
            warnings.append(f"{task_id}: goal is too short")
        if _contains_prompt_leakage(combined_text):
            critical_issues.append(f"{task_id}: detected role/prompt leakage markers in task content")
        if (is_card3d_contract or is_game_contract) and _has_forbidden_game_dependency_policy(task):
            critical_issues.append(f"{task_id}: game task violates no-external-dependency policy")

        acceptance = task.get("acceptance_criteria")
        if not isinstance(acceptance, list):
            acceptance = task.get("acceptance")
        acceptance_items = [_normalize_text(item) for item in (acceptance or []) if _normalize_text(item)]
        if not acceptance_items:
            critical_issues.append(f"{task_id}: acceptance criteria is missing")
        elif _has_fragile_game_acceptance(acceptance_items):
            critical_issues.append(f"{task_id}: acceptance uses fragile random-sequence assertions")
        elif not _has_executable_or_file_acceptance_anchor(acceptance_items):
            critical_issues.append(f"{task_id}: acceptance requires executable command or file evidence")
        elif not _has_measurable_acceptance_anchor(acceptance_items):
            warnings.append(f"{task_id}: acceptance criteria lacks measurable anchors")
        else:
            measurable_acceptance_task_count += 1

        task_paths = _collect_task_scope_paths(task)
        concrete_workspace_paths = [
            path for path in task_paths if _is_workspace_bound_concrete_path(path, effective_workspace)
        ]
        invalid_scope_paths = [path for path in task_paths if path not in concrete_workspace_paths]
        if not task_paths:
            critical_issues.append(f"{task_id}: task requires explicit scope")
        elif not concrete_workspace_paths:
            critical_issues.append(f"{task_id}: task requires concrete workspace-bound scope paths")
        elif invalid_scope_paths:
            critical_issues.append(
                f"{task_id}: scope paths must stay inside workspace ({', '.join(invalid_scope_paths[:3])})"
            )

        checklist = task.get("execution_checklist")
        checklist_items = []
        if isinstance(checklist, list):
            checklist_items = [_normalize_text(item) for item in checklist if _normalize_text(item)]

        action_text = " ".join([combined_text, " ".join(checklist_items)]).strip()
        lowered_task = action_text.lower()
        if not any(token in lowered_task for token in _PM_ACTION_TOKENS):
            low_action_count += 1
            warnings.append(f"{task_id}: action signal is weak")

        phase = str(task.get("phase") or "").strip().lower()
        if phase:
            phase_count += 1

        deps = task.get("depends_on")
        if not isinstance(deps, list):
            deps = task.get("dependencies")
        if isinstance(deps, list) and any(_normalize_text(item) for item in deps):
            dependency_task_count += 1

        if checklist_items:
            checklist_task_count += 1
        else:
            warnings.append(f"{task_id}: missing execution_checklist")

        if backlog_ref:
            backlog_trace_task_count += 1

        assigned_to = str(task.get("assigned_to") or "").strip()
        assigned_key = assigned_to.lower().replace("-", "_").replace(" ", "_")
        if assigned_key in {"director", "chiefengineer", "chief_engineer"}:
            if acceptance_items and not _has_executable_or_file_acceptance_anchor(acceptance_items):
                critical_issues.append(
                    f"{task_id}: assignee {assigned_to or assigned_key} requires executable command or file evidence in acceptance"
                )
            concrete_task_paths = [path for path in task_paths if _is_concrete_pm_scope_path(path)]
            non_path_entries = [path for path in task_paths if path not in concrete_task_paths]
            if task_paths and not concrete_task_paths:
                critical_issues.append(f"{task_id}: assignee {assigned_to} requires concrete relative scope paths")
            elif non_path_entries:
                warnings.append(f"{task_id}: non-path scope entries ignored ({', '.join(non_path_entries[:2])})")
            elif docs_enabled and active_doc:
                out_of_scope: list[str] = []
                for path in concrete_task_paths:
                    normalized_path = _normalize_path(path)
                    if not normalized_path:
                        continue
                    if normalized_path == active_doc:
                        continue
                    if active_dir and normalized_path == active_dir:
                        continue
                    if active_dir and normalized_path.startswith(active_dir + "/"):
                        continue
                    out_of_scope.append(path)
                if out_of_scope:
                    critical_issues.append(f"{task_id}: docs-stage scope violation ({', '.join(out_of_scope[:3])})")

        if docs_enabled:
            metadata_raw = task.get("metadata")
            metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
            sections_raw = metadata.get("doc_sections")
            sections = sections_raw if isinstance(sections_raw, list) else []
            if sections and any(_normalize_text(item) for item in sections):
                docs_section_task_count += 1
            else:
                warnings.append(f"{task_id}: docs-stage task missing metadata.doc_sections")

    task_count = len(tasks)
    if task_count == 0:
        critical_issues.append("PM returned zero tasks")
    unique_ratio = len(seen_signatures) / float(task_count) if task_count > 0 else 1.0
    if task_count >= 2 and unique_ratio < 0.67:
        critical_issues.append(f"task list is overly repetitive (unique_signature_ratio={unique_ratio:.2f})")
    if task_count > 0 and low_action_count == task_count:
        critical_issues.append("all tasks are low-action/generic and not execution-ready")
    if task_count >= 2 and phase_count == 0:
        critical_issues.append("task list missing phase hints")
    if task_count >= 2 and checklist_task_count == 0:
        critical_issues.append("task list missing execution_checklist")
    if task_count >= 2 and dependency_task_count == 0:
        critical_issues.append("task list missing dependency chain")
    if task_count >= 2 and measurable_acceptance_task_count == 0:
        critical_issues.append("acceptance criteria are not measurable")
    if docs_enabled and task_count < 2:
        critical_issues.append("docs-stage decomposition requires at least 2 tasks")
    if docs_enabled and task_count >= 2 and docs_section_task_count == 0:
        critical_issues.append("docs-stage tasks missing metadata.doc_sections")
    if docs_enabled and task_count >= 2 and backlog_trace_task_count < max(1, task_count // 2):
        critical_issues.append("docs-stage tasks missing backlog traceability")
    if is_card3d_contract:
        if task_count < _GAME_PM_MIN_TASKS:
            critical_issues.append(f"card3d PM decomposition requires at least {_GAME_PM_MIN_TASKS} tasks")
        covered_domains = _covered_card3d_domains(tasks, effective_workspace)
        missing_domains = [domain for domain in _CARD3D_PM_REQUIRED_DOMAINS if domain not in covered_domains]
        if missing_domains:
            critical_issues.append(f"card3d PM decomposition missing domains: {', '.join(missing_domains)}")
        missing_test_targets = _missing_card3d_required_test_targets(tasks)
        if missing_test_targets:
            critical_issues.append(
                "card3d tests task must target all required test files: " + ", ".join(missing_test_targets)
            )
        if (
            "tests" not in missing_domains
            and not missing_test_targets
            and not _card3d_tests_task_has_placeholder_cleanup_contract(tasks)
        ):
            critical_issues.append(
                "card3d tests task must require replacing/removing trivial arithmetic placeholder tests"
            )
    elif is_game_contract:
        if task_count < _GAME_PM_MIN_TASKS:
            critical_issues.append(f"game PM decomposition requires at least {_GAME_PM_MIN_TASKS} tasks")
        covered_domains = _covered_game_domains(tasks, effective_workspace)
        missing_domains = [domain for domain in _GAME_PM_REQUIRED_DOMAINS if domain not in covered_domains]
        if missing_domains:
            critical_issues.append(f"game PM decomposition missing domains: {', '.join(missing_domains)}")
    if task_count >= 2:
        typed_tasks = [task for task in tasks if isinstance(task, dict)]
        critical_issues.extend(_unknown_dependency_refs(typed_tasks))
        try:
            validate_dependency_dag(typed_tasks)
        except DependencyCycleError as exc:
            critical_issues.append(f"circular dependency detected: {' -> '.join(exc.cycle)}")

    score = 100
    score -= min(60, len(critical_issues) * 12)
    score -= min(30, len(warnings) * 3)
    score = max(0, score)
    summary = (
        f"tasks={task_count}; critical={len(critical_issues)}; warnings={len(warnings)}; "
        f"unique_ratio={unique_ratio:.2f}; phase_tasks={phase_count}; dep_tasks={dependency_task_count}; "
        f"checklist_tasks={checklist_task_count}; measurable_accept_tasks={measurable_acceptance_task_count}; "
        f"doc_section_tasks={docs_section_task_count}; backlog_trace_tasks={backlog_trace_task_count}; score={score}"
    )
    return {
        "ok": len(critical_issues) == 0,
        "score": score,
        "task_count": task_count,
        "unique_signature_ratio": unique_ratio,
        "critical_issues": critical_issues,
        "warnings": warnings,
        "summary": summary,
    }


def autofix_pm_contract_for_quality(
    normalized: dict[str, Any],
    *,
    workspace_full: str,
) -> dict[str, int]:
    """Attempt to autofix PM contract quality issues.

    This function adds missing phases, checklists, dependencies, and acceptance criteria
    to tasks that lack them.

    Args:
        normalized: Normalized PM task payload
        workspace_full: Absolute path to workspace

    Returns:
        Statistics about what was added
    """
    from polaris.cells.orchestration.pm_planning.internal.shared_quality import detect_integration_verify_command

    tasks_raw = normalized.get("tasks")
    tasks: list[Any] = tasks_raw if isinstance(tasks_raw, list) else []
    stats: dict[str, int] = {
        "task_count": len(tasks) if tasks else 0,
        "phases_added": 0,
        "checklists_added": 0,
        "deps_added": 0,
        "deps_normalized": 0,
        "acceptance_added": 0,
        "acceptance_sanitized": 0,
        "descriptions_added": 0,
        "game_domain_tasks_added": 0,
        "card3d_domain_tasks_added": 0,
        "card3d_test_contract_repairs": 0,
        "game_context_attached": 0,
        "game_dependency_policy_sanitized": 0,
        "game_policy_tasks_removed": 0,
        "paths_normalized": 0,
    }
    if not tasks:
        return stats

    verify_command = detect_integration_verify_command(workspace_full)
    normalized_tasks = [task for task in tasks if isinstance(task, dict)]
    stats["paths_normalized"] += _sanitize_pm_task_paths_in_place(normalized_tasks, workspace_full)
    if _attach_workspace_game_context_if_needed(normalized, normalized_tasks, workspace_full):
        stats["game_context_attached"] += 1
    is_card3d_contract = _is_card3d_pm_contract(normalized, normalized_tasks)
    if is_card3d_contract:
        normalized.setdefault("_quality_gate_card3d_context", "card3d task_or_workspace_hints")
        stats["game_policy_tasks_removed"] += _remove_card3d_policy_incompatible_tasks_in_place(
            normalized_tasks,
            workspace_full,
        )
        if stats["game_policy_tasks_removed"]:
            stats["deps_normalized"] += _drop_unknown_dependency_refs_in_place(normalized_tasks)
        for task in normalized_tasks:
            stats["game_dependency_policy_sanitized"] += _sanitize_game_dependency_policy_in_place(task, verify_command)
    elif _is_game_pm_contract(normalized, normalized_tasks):
        stats["game_policy_tasks_removed"] += _remove_game_policy_incompatible_tasks_in_place(
            normalized_tasks,
            workspace_full,
        )
        if stats["game_policy_tasks_removed"]:
            stats["deps_normalized"] += _drop_unknown_dependency_refs_in_place(normalized_tasks)
        for task in normalized_tasks:
            stats["game_dependency_policy_sanitized"] += _sanitize_game_dependency_policy_in_place(task, verify_command)
    stats["deps_normalized"] += _normalize_dependency_refs_in_place(normalized_tasks)
    stats["card3d_domain_tasks_added"] += _append_missing_card3d_domain_tasks(
        normalized,
        normalized_tasks,
        workspace_full=workspace_full,
        verify_command=verify_command,
    )
    if is_card3d_contract:
        stats["card3d_test_contract_repairs"] += _repair_card3d_tests_task_contract(normalized_tasks)
    stats["game_domain_tasks_added"] += _append_missing_game_domain_tasks(
        normalized,
        normalized_tasks,
        workspace_full=workspace_full,
        verify_command=verify_command,
    )
    stats["task_count"] = len(normalized_tasks)
    has_dependency = False

    for index, task in enumerate(normalized_tasks, start=1):
        if not isinstance(task, dict):
            continue

        if not task.get("phase"):
            phases = ["requirements", "implementation", "verification"]
            phase = phases[(index - 1) % len(phases)]
            task["phase"] = phase
            stats["phases_added"] += 1

        if not task.get("execution_checklist"):
            task["execution_checklist"] = [
                "Read existing code and understand context",
                "Implement the required changes",
                "Run tests to verify correctness",
            ]
            stats["checklists_added"] += 1

        acceptance = task.get("acceptance_criteria")
        if not isinstance(acceptance, list):
            acceptance = task.get("acceptance")
        acceptance_items = acceptance if isinstance(acceptance, list) else []

        stats["acceptance_sanitized"] += _sanitize_fragile_game_acceptance_in_place(task, verify_command)
        acceptance = task.get("acceptance_criteria")
        if not isinstance(acceptance, list):
            acceptance = task.get("acceptance")
        acceptance_items = acceptance if isinstance(acceptance, list) else []

        if not acceptance_items:
            task_type = str(task.get("type") or task.get("assigned_to") or "").lower()
            if "docs" in task_type or "document" in task_type:
                task["acceptance_criteria"] = [
                    "Documentation compiles without errors",
                    "All sections are present and properly formatted",
                ]
            else:
                task["acceptance_criteria"] = [
                    "Code compiles successfully",
                    f"Run `{verify_command}` passes",
                ]
            stats["acceptance_added"] += 1

        description = task.get("description")
        if not description or len(str(description).strip()) < 20:
            title = str(task.get("title") or "").strip()
            task["description"] = f"Execute {title} according to acceptance criteria"
            stats["descriptions_added"] += 1

        deps = task.get("depends_on")
        if not isinstance(deps, list):
            deps = task.get("dependencies")
        if _normalize_dep_list(deps):
            has_dependency = True

    if len(normalized_tasks) > 1 and not has_dependency:
        prev_task_id = None
        for task in normalized_tasks:
            if prev_task_id:
                deps = task.get("depends_on")
                if not isinstance(deps, list):
                    deps = task.get("dependencies")
                deps = deps if isinstance(deps, list) else []
                if prev_task_id not in deps:
                    deps.append(prev_task_id)
                    if "depends_on" in task:
                        task["depends_on"] = deps
                    elif "dependencies" in task:
                        task["dependencies"] = deps
                    else:
                        task["depends_on"] = deps
                    stats["deps_added"] += 1
            prev_task_id = str(task.get("id") or "").strip()
            if not prev_task_id:
                task["id"] = f"TASK-{normalized_tasks.index(task) + 1}"
                prev_task_id = task["id"]

    normalized["tasks"] = normalized_tasks
    return stats


def check_quality_promote_candidate(
    quality_report: dict[str, Any],
    *,
    mode: str = "strict",
    min_score: int = 80,
    max_retries: int = 3,
    retry_count: int = 0,
) -> tuple[bool, str]:
    """Determine if a quality candidate should be promoted.

    Args:
        quality_report: Quality report from evaluate_pm_task_quality
        mode: Quality mode - "off", "warn", or "strict"
        min_score: Minimum score threshold
        max_retries: Maximum retry attempts
        retry_count: Current retry count

    Returns:
        Tuple of (should_promote, reason)
    """
    if mode == "off":
        return True, "quality gate disabled"

    is_ok = quality_report.get("ok", False)
    score = quality_report.get("score", 0)
    critical_count = len(quality_report.get("critical_issues", []))
    warning_count = len(quality_report.get("warnings", []))

    if mode == "strict":
        if not is_ok:
            return False, f"strict mode: {critical_count} critical issues found"
        if score < min_score:
            return False, f"strict mode: score {score} below minimum {min_score}"
        if critical_count > 0:
            return False, f"strict mode: {critical_count} critical issues"
        return True, f"strict mode: passed (score={score})"

    if mode == "warn":
        if not is_ok and retry_count < max_retries:
            return False, f"warn mode: retry needed ({critical_count} critical, {warning_count} warnings)"
        if not is_ok:
            return True, f"warn mode: forced promotion after {max_retries} retries"
        if score < min_score:
            return True, f"warn mode: score {score} below threshold but allowing"
        return True, f"warn mode: passed (score={score}, warnings={warning_count})"

    return True, f"unknown mode: {mode}"


def get_quality_gate_config() -> dict[str, Any]:
    """Get quality gate configuration from environment.

    Returns:
        Configuration dict with mode, min_score, and max_retries
    """
    import os

    mode = str(os.environ.get("KERNELONE_PM_TASK_QUALITY_MODE", "strict")).strip().lower()
    if mode not in ("off", "warn", "strict"):
        mode = "strict"

    min_score_raw = os.environ.get("KERNELONE_PM_TASK_QUALITY_MIN_SCORE", "80")
    try:
        min_score = max(0, min(100, int(min_score_raw)))
    except ValueError:
        min_score = 80

    max_retries_raw = os.environ.get("KERNELONE_PM_TASK_QUALITY_RETRIES", "3")
    try:
        max_retries = max(0, int(max_retries_raw))
    except ValueError:
        max_retries = 3

    return {
        "mode": mode,
        "min_score": min_score,
        "max_retries": max_retries,
    }

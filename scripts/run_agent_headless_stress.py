#!/usr/bin/env python3
"""Polaris AI-agent headless stress runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import textwrap
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode, urlparse, urlunparse

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx
import websockets
from core.stress_path_policy import (
    default_stress_runtime_root,
    default_stress_workspace_base,
    ensure_stress_runtime_root,
    ensure_stress_workspace_path,
    runtime_layout_policy_violations,
)
from core.workspace_policy import ensure_workspace_target_allowed


DEFAULT_REQUIRED_ROLES = ("architect", "pm", "director", "qa")
OPTIONAL_ROLES = ("chief_engineer",)
FORMAL_HTTP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"^/health$")),
    ("GET", re.compile(r"^/settings$")),
    ("POST", re.compile(r"^/settings$")),
    ("GET", re.compile(r"^/runtime/storage-layout$")),
    ("GET", re.compile(r"^/state/snapshot$")),
    ("GET", re.compile(r"^/v2/director/tasks$")),
    ("GET", re.compile(r"^/v2/role/[a-z_]+/chat/status$")),
    ("POST", re.compile(r"^/v2/factory/runs$")),
    ("GET", re.compile(r"^/v2/factory/runs/[^/]+$")),
    ("GET", re.compile(r"^/v2/factory/runs/[^/]+/events$")),
    ("GET", re.compile(r"^/v2/factory/runs/[^/]+/artifacts$")),
    ("GET", re.compile(r"^/v2/factory/runs/[^/]+/stream$")),
)
LEAKAGE_KEYWORDS = (
    "you are",
    "role",
    "system prompt",
    "no yapping",
    "提示词",
    "角色设定",
    "<thinking>",
    "<tool_call>",
)
BACKEND_DISCOVERY_TIMEOUT_SECONDS = 30.0
DEFAULT_STRESS_WORKSPACE_BASE = default_stress_workspace_base("PolarisAgentStress")
DEFAULT_STRESS_RAMDISK_ROOT = default_stress_runtime_root("PolarisAgentStressRuntime")


@dataclass(frozen=True)
class Scenario:
    name: str
    category: str
    core_capabilities: tuple[str, ...]
    enhancements: tuple[str, ...]
    stress_focus: tuple[str, ...]


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("个人记账簿（账单管理）", "crud", ("账单录入", "分类筛选", "月度汇总"), ("预算预警", "图表统计", "CSV 导入导出"), ("表单状态", "持久化", "一致性校验")),
    Scenario("待办事项清单（To-Do List）", "crud", ("新增编辑", "完成状态", "优先级与标签"), ("拖拽排序", "分组视图", "提醒或离线缓存"), ("状态流转", "过滤组合", "交互回归")),
    Scenario("简易 Markdown 编辑器", "editor", ("文本编辑", "实时预览", "文档保存"), ("目录导航", "语法高亮", "导出 HTML/PDF"), ("文本处理", "预览同步", "渲染安全")),
    Scenario("实时聊天室（WebSocket）", "realtime", ("多人消息", "在线状态", "房间切换"), ("消息历史", "输入中提示", "重连策略"), ("实时连接", "状态同步", "异常恢复")),
    Scenario("博客系统（CMS）", "cms", ("文章创建", "编辑发布", "列表展示"), ("草稿状态", "分类标签", "搜索与后台"), ("内容模型", "路由组织", "权限边界")),
    Scenario("天气预报展示器", "api_integration", ("城市查询", "天气卡片", "多日预报"), ("最近搜索", "定位能力", "缓存与错误降级"), ("第三方 API 适配", "缓存", "降级策略")),
    Scenario("个人简历生成器", "document", ("表单录入", "模板渲染", "导出"), ("多模板切换", "主题配置", "PDF 导出"), ("模板渲染", "结构化数据", "导出质量")),
    Scenario("抽奖 / 随机点名工具", "tooling", ("名单导入", "随机选择", "结果展示"), ("去重规则", "权重设置", "历史记录"), ("随机逻辑", "状态控制", "交互反馈")),
    Scenario("番茄钟（专注计时器）", "timer", ("专注倒计时", "休息阶段切换", "记录统计"), ("通知提醒", "声音提示", "周期配置"), ("计时精度", "前后台一致性", "持久化")),
    Scenario("密码管理器（加密存储）", "security", ("密码条目管理", "本地加密", "解锁查看"), ("主密码", "分类管理", "复制保护与强度提示"), ("加密边界", "敏感数据处理", "误泄漏防御")),
    Scenario("图片占位符生成器", "tooling", ("尺寸定制", "文字与背景色", "下载输出"), ("批量生成", "预设模板", "URL 参数化"), ("参数校验", "图像生成", "批处理流程")),
    Scenario("在线剪贴板（跨端传词）", "realtime", ("文本发送", "文本接收", "短期存储"), ("过期时间", "多设备同步", "历史记录"), ("同步一致性", "权限控制", "临时数据清理")),
    Scenario("聚合搜索工具（一键搜多站）", "tooling", ("统一输入", "多站点跳转", "聚合结果"), ("搜索模板管理", "快捷键", "历史记录"), ("配置化", "跳转逻辑", "结果整合")),
    Scenario("简易单位转换器（汇率 / 度量）", "calculator", ("单位换算", "双向输入", "结果展示"), ("汇率缓存", "常用组合", "最近记录"), ("计算正确性", "配置扩展", "边界值")),
    Scenario("文件断点续传器", "file_transfer", ("分片上传", "断点恢复", "进度展示"), ("校验和", "并发上传", "失败重试"), ("文件处理", "恢复逻辑", "异常注入")),
    Scenario("静态网站生成器（SSG）", "build_pipeline", ("Markdown 输入", "模板渲染", "静态输出"), ("导航生成", "分页", "标签页与构建命令"), ("内容编译链", "文件系统", "构建产物校验")),
    Scenario("RSS 阅读器", "content", ("订阅源管理", "文章列表", "已读状态"), ("抓取缓存", "关键词过滤", "收藏"), ("抓取兼容性", "解析容错", "状态持久化")),
    Scenario("自动化签到脚本", "automation", ("登录流程", "签到执行", "结果记录"), ("定时任务", "失败告警", "多站点配置"), ("自动化稳定性", "重试恢复", "凭据管理")),
    Scenario("屏幕截图 / 录屏工具", "desktop", ("截图", "区域选择", "文件保存"), ("录屏", "快捷键", "历史记录与格式配置"), ("桌面能力", "文件输出", "性能与权限")),
    Scenario("贪吃蛇 / 俄罗斯方块小游戏", "game", ("游戏循环", "得分统计", "重新开始"), ("难度配置", "排行榜", "本地存档与音效"), ("渲染刷新", "状态机", "输入响应")),
)


@dataclass
class RoundReport:
    round: int
    project_name: str
    category: str
    enhancements: list[str]
    workspace: str
    directive_path: str
    result: str = "FAIL"
    run_id: str = ""
    runtime_root: str = ""
    factory_status: str = ""
    factory_phase: str = ""
    duration_seconds: float = 0.0
    role_readiness: dict[str, dict[str, Any]] = field(default_factory=dict)
    runtime_ws: dict[str, Any] = field(default_factory=dict)
    factory_stream: dict[str, Any] = field(default_factory=dict)
    snapshot_gate_passed: bool = False
    pm_quality: dict[str, Any] = field(default_factory=dict)
    director_lineage: dict[str, Any] = field(default_factory=dict)
    qa_result: dict[str, Any] = field(default_factory=dict)
    prompt_leakage_findings: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    evidence: dict[str, list[str]] = field(default_factory=lambda: {"files": [], "endpoints": []})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_workspace_base(value: str, *, self_upgrade_mode: bool | None = None) -> Path:
    if value:
        return ensure_stress_workspace_path(
            ensure_workspace_target_allowed(
                Path(value).expanduser().resolve(),
                self_upgrade_mode=self_upgrade_mode,
            )
        )
    return ensure_stress_workspace_path(DEFAULT_STRESS_WORKSPACE_BASE)


def _build_ramdisk_root(value: str = "") -> Path:
    if value:
        return ensure_stress_runtime_root(Path(value).expanduser().resolve())
    return ensure_stress_runtime_root(DEFAULT_STRESS_RAMDISK_ROOT)


def _resolve_polaris_root(env: dict[str, str] | None = None) -> Path:
    source = env or os.environ
    root_override = str(source.get("KERNELONE_ROOT", "") or "").strip()
    if root_override:
        return Path(root_override).expanduser().resolve()

    home_override = str(source.get("KERNELONE_HOME", "") or "").strip()
    if home_override:
        expanded = Path(home_override).expanduser().resolve()
        if expanded.name.lower() == ".polaris":
            return expanded.parent if str(expanded.parent) else expanded
        return expanded

    if os.name == "nt":
        appdata = str(source.get("APPDATA", "") or "").strip()
        if appdata:
            return Path(appdata).expanduser().resolve()

    xdg_config_home = str(source.get("XDG_CONFIG_HOME", "") or "").strip()
    if xdg_config_home:
        return Path(xdg_config_home).expanduser().resolve()

    return Path.home().resolve()


def _resolve_polaris_home(env: dict[str, str] | None = None) -> Path:
    return _resolve_polaris_root(env) / ".polaris"


def _desktop_backend_info_path(env: dict[str, str] | None = None) -> Path:
    return _resolve_polaris_home(env) / "runtime" / "desktop-backend.json"


def _load_desktop_backend_info(path: Path) -> dict[str, Any]:
    payload = _read_json_utf8(path)
    if not isinstance(payload, dict):
        return {}
    backend = payload.get("backend")
    if not isinstance(backend, dict):
        backend = {}
    return {
        "path": str(path),
        "source": str(payload.get("source") or "").strip() or "desktop_backend_info",
        "state": str(payload.get("state") or "").strip().lower(),
        "ready": bool(payload.get("ready")),
        "base_url": _normalize_base_url(str(backend.get("baseUrl") or "")),
        "token": str(backend.get("token") or "").strip(),
        "pid": backend.get("pid"),
        "port": backend.get("port"),
        "updated_at": str(payload.get("updated_at") or "").strip(),
    }


async def _resolve_backend_connection(
    base_url: str,
    token: str,
    *,
    discovery_timeout_seconds: float = BACKEND_DISCOVERY_TIMEOUT_SECONDS,
) -> tuple[str, str, dict[str, Any]]:
    resolved_base_url = _normalize_base_url(base_url)
    resolved_token = str(token or "").strip()
    if resolved_base_url and resolved_token:
        return resolved_base_url, resolved_token, {"source": "cli_or_env"}

    desktop_info_path = _desktop_backend_info_path()
    deadline = time.monotonic() + max(float(discovery_timeout_seconds or 0.0), 0.0)
    latest_snapshot: dict[str, Any] = {}

    while True:
        snapshot = _load_desktop_backend_info(desktop_info_path)
        if snapshot:
            latest_snapshot = snapshot
            candidate_base_url = resolved_base_url or str(snapshot.get("base_url") or "").strip()
            candidate_token = resolved_token or str(snapshot.get("token") or "").strip()
            if candidate_base_url and candidate_token:
                state = str(snapshot.get("state") or "").strip().lower()
                if bool(snapshot.get("ready")) or state == "running" or time.monotonic() >= deadline:
                    return candidate_base_url, candidate_token, {
                        "source": "desktop_backend_info",
                        "path": str(snapshot.get("path") or desktop_info_path),
                        "state": state,
                        "ready": bool(snapshot.get("ready")),
                        "updated_at": str(snapshot.get("updated_at") or "").strip(),
                    }
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.25)

    candidate_base_url = resolved_base_url or str(latest_snapshot.get("base_url") or "").strip()
    candidate_token = resolved_token or str(latest_snapshot.get("token") or "").strip()
    if candidate_base_url and candidate_token:
        return candidate_base_url, candidate_token, {
            "source": "desktop_backend_info",
            "path": str(latest_snapshot.get("path") or desktop_info_path),
            "state": str(latest_snapshot.get("state") or "").strip().lower(),
            "ready": bool(latest_snapshot.get("ready")),
            "updated_at": str(latest_snapshot.get("updated_at") or "").strip(),
        }

    raise ValueError(
        "Unable to resolve Polaris backend info from --base-url/--token, "
        "KERNELONE_BASE_URL/KERNELONE_TOKEN, or the official desktop backend info file: "
        f"{desktop_info_path}"
    )


def _ensure_formal_http_request_allowed(method: str, path: str) -> None:
    normalized_method = str(method or "").strip().upper()
    normalized_path = str(path or "").strip()
    for allowed_method, pattern in FORMAL_HTTP_PATTERNS:
        if normalized_method == allowed_method and pattern.match(normalized_path):
            return
    raise ValueError(
        "headless stress runner may only drive Polaris through formal interfaces; "
        f"rejected request: {normalized_method} {normalized_path}"
    )


def _prepare_target_workspace(workspace: Path) -> Path:
    resolved = workspace.expanduser().resolve()
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError(f"target workspace is not a directory: {resolved}")
        if any(resolved.iterdir()):
            raise ValueError(
                "headless stress runner may not preseed or mutate target workspace content; "
                f"workspace must start empty: {resolved}"
            )
        return resolved
    resolved.mkdir(parents=True, exist_ok=False)
    return resolved


def _normalize_agent_label(value: str) -> str:
    label = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower())
    return label.strip("-") or "agent"


def _slugify(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return token.strip("-") or "scenario"


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return raw
    parsed = urlparse(raw)
    path = parsed.path.rstrip("/")
    if path.endswith("/v2"):
        path = path[: -len("/v2")]
    return urlunparse(parsed._replace(path=path, params="", query="", fragment="")).rstrip("/")


def _build_ws_url(base_url: str, *, token: str, workspace: str, roles: Iterable[str]) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = urlencode(
        {
            "token": token,
            "roles": ",".join(sorted({str(role).strip() for role in roles if str(role).strip()})),
            "workspace": workspace,
        }
    )
    return urlunparse(
        parsed._replace(
            scheme=scheme,
            path="/v2/ws/runtime",
            params="",
            query=query,
            fragment="",
        )
    )


def _read_text_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_json_utf8(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_text_utf8(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json_utf8(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _collect_string_leaves(value: Any, bucket: list[str]) -> None:
    if isinstance(value, str):
        token = value.strip()
        if token:
            bucket.append(token)
        return
    if isinstance(value, list):
        for item in value:
            _collect_string_leaves(item, bucket)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_string_leaves(item, bucket)


def _contains_role_leakage(candidate: str) -> bool:
    return bool(
        re.search(r"\brole\b\s*[:=]", candidate, flags=re.IGNORECASE)
        or re.search(r"\b(?:system|assistant|developer|user)\s+role\b", candidate, flags=re.IGNORECASE)
        or "角色设定" in candidate
    )


def _detect_prompt_leakage(text: str, evidence_path: str) -> list[dict[str, Any]]:
    candidates = [str(text or "")]
    if str(evidence_path).lower().endswith(".json"):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None
        if payload is not None:
            values: list[str] = []
            _collect_string_leaves(payload, values)
            if values:
                candidates = values

    findings: list[dict[str, Any]] = []
    for keyword in LEAKAGE_KEYWORDS:
        lowered = keyword.lower()
        hit = False
        for candidate in candidates:
            token = str(candidate or "")
            if lowered == "role":
                if _contains_role_leakage(token):
                    hit = True
                    break
            elif lowered in token.lower():
                hit = True
                break
        if hit:
            findings.append({"type": "prompt_leakage", "keyword": keyword, "evidence": f"{evidence_path}::{keyword}"})
    return findings


def _validate_snapshot_gate(snapshot: dict[str, Any]) -> bool:
    tasks = snapshot.get("tasks")
    pm_state = snapshot.get("pm_state")
    if not isinstance(tasks, list) or not isinstance(pm_state, dict):
        return False
    try:
        completed = int(pm_state.get("completed_task_count") or 0)
    except Exception:
        completed = 0
    last_director_status = str(pm_state.get("last_director_status") or "").strip()
    return len(tasks) > 0 and completed > 0 and bool(last_director_status)


def _validate_pm_contract(pm_contract: dict[str, Any]) -> dict[str, Any]:
    quality_gate_raw = pm_contract.get("quality_gate")
    quality_gate: dict[str, Any] = quality_gate_raw if isinstance(quality_gate_raw, dict) else {}
    tasks_raw = pm_contract.get("tasks")
    tasks: list[Any] = tasks_raw if isinstance(tasks_raw, list) else []
    invalid_tasks = 0
    for task in tasks:
        if not isinstance(task, dict):
            invalid_tasks += 1
            continue
        task_dict: dict[str, Any] = task
        has_goal = bool(str(task_dict.get("goal") or "").strip())
        scope_paths = task_dict.get("scope_paths")
        has_scope = isinstance(scope_paths, list) and len(scope_paths) > 0
        execution_checklist = task_dict.get("execution_checklist")
        has_steps = isinstance(execution_checklist, list) and len(execution_checklist) > 0
        acceptance = task_dict.get("acceptance_criteria")
        if not isinstance(acceptance, list):
            acceptance = task_dict.get("acceptance")
        has_acceptance = isinstance(acceptance, list) and len(acceptance) > 0
        if not (has_goal and has_scope and has_steps and has_acceptance):
            invalid_tasks += 1
    try:
        score = float(quality_gate.get("score") or 0)
    except Exception:
        score = 0.0
    try:
        critical_issue_count = int(quality_gate.get("critical_issue_count") or 0)
    except Exception:
        critical_issue_count = 0
    summary = str(quality_gate.get("summary") or "").strip()
    return {
        "score": score,
        "critical_issue_count": critical_issue_count,
        "summary": summary,
        "task_count": len(tasks),
        "invalid_task_count": invalid_tasks,
        "passed": score >= 80.0 and critical_issue_count == 0 and invalid_tasks == 0 and len(tasks) > 0,
    }


def _count_director_lineage(task_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0
    linked = 0
    for row in task_rows:
        if not isinstance(row, dict):
            continue
        total += 1
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and str(metadata.get("pm_task_id") or "").strip():
            linked += 1
    ratio = round((linked / total) * 100.0, 2) if total > 0 else 0.0
    return {"total_tasks": total, "linked_task_count": linked, "linked_ratio": ratio, "passed": total > 0 and linked > 0}


def _find_latest_runtime_events_path(runtime_root: Path) -> str:
    runs_root = runtime_root / "runs"
    if not runs_root.exists():
        return ""
    candidates: list[tuple[float, Path]] = []
    for path in runs_root.glob("*/events/runtime.events.jsonl"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except Exception:
            continue
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return str(candidates[0][1])


def _pick_scenario(round_index: int, offset: int = 0) -> Scenario:
    index = (max(0, int(round_index)) + max(0, int(offset))) % len(SCENARIOS)
    return SCENARIOS[index]


def _build_round_directive(scenario: Scenario, *, round_number: int, agent_label: str, complexity_floor_lines: int) -> str:
    core = "\n".join(f"- {item}" for item in scenario.core_capabilities)
    enhancements = "\n".join(f"- {item}" for item in scenario.enhancements)
    focus = "\n".join(f"- {item}" for item in scenario.stress_focus)
    return textwrap.dedent(
        f"""\
        # Polaris AI Agent Headless Stress Directive

        本轮目标不是做一个玩具页面，而是用 Polaris 当前正式执行链去压测 {agent_label} 这类 AI Agent 在复杂项目上的稳定性。

        ## 本轮项目

        - 轮次：{int(round_number)}
        - 目标代理：{agent_label}
        - 项目主题：{scenario.name}
        - 项目类别：{scenario.category}

        ## 核心能力

        {core}

        ## 必做增强特性

        {enhancements}

        ## 压测重点

        {focus}

        ## 复杂度硬门槛

        1. 最少 10 个文件。
        2. 前后端总代码至少 {int(complexity_floor_lines)} 行。
        3. 至少 3 个模块或服务。
        4. 至少 3 个配置/构建相关文件。
        5. 至少 2 个测试文件，并包含单元测试或集成测试。

        ## AI Agent 审计要求

        1. 必须通过 Polaris 当前正式链路完成：Architect/Court -> PM -> Director -> QA。
        2. PM 任务合同必须具备目标、作用域、执行清单、可测验收，且对外任务键只能使用 `id`。
        3. Director 执行必须留下 `metadata.pm_task_id` 血缘证据。
        4. 全过程禁止提示词泄漏，禁止出现 `you are`、`system prompt`、`<thinking>`、`<tool_call>` 等词进入交付合同。
        5. QA 目标是 `integration_qa_passed`，不得靠旧兼容层或手工改目标项目代码过关。

        ## 交付要求

        1. 项目必须可运行、可测试、可审计。
        2. README 必须给出运行方式、测试方式、模块说明。
        3. 需要真实业务逻辑，不允许空壳 CRUD 或模板占位。
        4. 若出现失败，必须通过 Polaris 自己修复后再回归。
        """
    ).strip() + "\n"


class RuntimeWsCollector:
    def __init__(self, base_url: str, token: str, workspace: str, roles: Iterable[str]) -> None:
        self._base_url = base_url
        self._token = token
        self._workspace = workspace
        self._roles = tuple(roles)
        self.stats: dict[str, Any] = {
            "connected": False,
            "total_messages": 0,
            "status": 0,
            "process_stream": 0,
            "llm_stream": 0,
            "runtime_event": 0,
            "file_edit": 0,
            "task_trace": 0,
            "ping": 0,
            "errors": [],
        }

    async def run(self, stop_event: asyncio.Event) -> None:
        ws_url = _build_ws_url(
            self._base_url,
            token=self._token,
            workspace=self._workspace,
            roles=self._roles,
        )
        try:
            async with websockets.connect(
                ws_url,
                open_timeout=20,
                ping_interval=20,
                ping_timeout=20,
                max_size=2_000_000,
            ) as websocket:
                self.stats["connected"] = True
                await websocket.send(json.dumps({"type": "GET_SNAPSHOT"}, ensure_ascii=False))
                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if not isinstance(raw, str):
                        continue
                    self._record_message(raw)
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    if str(payload.get("type") or "").upper() == "PING":
                        await websocket.send(json.dumps({"type": "PONG"}, ensure_ascii=False))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats["errors"].append(f"{type(exc).__name__}: {exc}")

    def _record_message(self, raw: str) -> None:
        self.stats["total_messages"] += 1
        try:
            payload = json.loads(raw)
        except Exception:
            return
        message_type = str(payload.get("type") or "").strip().lower()
        if message_type in self.stats and isinstance(self.stats[message_type], int):
            self.stats[message_type] += 1
        elif message_type == "ping":
            self.stats["ping"] += 1


class FactorySseCollector:
    def __init__(self, client: httpx.AsyncClient, run_id: str) -> None:
        self._client = client
        self._run_id = run_id
        self.stats: dict[str, Any] = {
            "connected": False,
            "status_events": 0,
            "audit_events": 0,
            "done_events": 0,
            "errors": [],
        }

    async def run(self) -> None:
        _ensure_formal_http_request_allowed(
            "GET",
            f"/v2/factory/runs/{self._run_id}/stream",
        )
        event_name = ""
        data_lines: list[str] = []
        try:
            async with self._client.stream(
                "GET",
                f"/v2/factory/runs/{self._run_id}/stream",
                timeout=None,
            ) as response:
                response.raise_for_status()
                self.stats["connected"] = True
                async for raw_line in response.aiter_lines():
                    line = str(raw_line or "")
                    if line == "":
                        if event_name:
                            self._record_event(event_name)
                            if event_name == "done":
                                return
                        event_name = ""
                        data_lines = []
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_name = line[len("event:"):].strip()
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[len("data:"):].lstrip())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.stats["errors"].append(f"{type(exc).__name__}: {exc}")

    def _record_event(self, event_name: str) -> None:
        normalized = str(event_name or "").strip().lower()
        if normalized == "status":
            self.stats["status_events"] += 1
        elif normalized == "event":
            self.stats["audit_events"] += 1
        elif normalized == "done":
            self.stats["done_events"] += 1
        elif normalized:
            self.stats["errors"].append(f"unexpected_sse_event:{normalized}")


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    _ensure_formal_http_request_allowed(method, path)
    response = await client.request(method, path, json=payload)
    response.raise_for_status()
    return response.json()


async def _collect_role_readiness(
    client: httpx.AsyncClient,
    roles: Iterable[str],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for role in roles:
        payload = await _request_json(client, "GET", f"/v2/role/{role}/chat/status")
        if not isinstance(payload, dict):
            payload = {"ready": False, "error": "invalid_payload"}
        results[str(role)] = payload
    return results


async def _poll_factory_completion(
    client: httpx.AsyncClient,
    run_id: str,
    timeout_seconds: int,
    poll_interval: float,
) -> dict[str, Any]:
    terminal = {"completed", "failed", "cancelled"}
    deadline = time.monotonic() + max(1, int(timeout_seconds))
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = await _request_json(client, "GET", f"/v2/factory/runs/{run_id}")
        if isinstance(payload, dict):
            last_payload = payload
            status = str(payload.get("status") or "").strip().lower()
            if status in terminal:
                return payload
        await asyncio.sleep(max(0.2, float(poll_interval)))
    timed_out = dict(last_payload)
    timed_out.setdefault("status", "timeout")
    timed_out.setdefault("phase", str(last_payload.get("phase") or "failed"))
    return timed_out


async def _run_single_round(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    workspace_base: Path,
    ramdisk_root: Path,
    report_root: Path,
    round_number: int,
    scenario_offset: int,
    agent_label: str,
    start_from: str,
    director_iterations: int,
    round_timeout_seconds: int,
    poll_interval_seconds: float,
    complexity_floor_lines: int,
) -> RoundReport:
    started_at = time.monotonic()
    scenario = _pick_scenario(round_number - 1, offset=scenario_offset)
    workspace_name = f"{agent_label}-{_slugify(scenario.name)}-round-{round_number:02d}"
    workspace = _prepare_target_workspace((workspace_base / workspace_name).resolve())

    round_dir = report_root / f"round-{round_number:02d}"
    round_dir.mkdir(parents=True, exist_ok=True)
    directive_path = round_dir / "directive.md"
    directive = _build_round_directive(
        scenario,
        round_number=round_number,
        agent_label=agent_label,
        complexity_floor_lines=complexity_floor_lines,
    )
    _write_text_utf8(directive_path, directive)

    report = RoundReport(
        round=round_number,
        project_name=scenario.name,
        category=scenario.category,
        enhancements=list(scenario.enhancements),
        workspace=str(workspace),
        directive_path=str(directive_path),
    )
    report.evidence["files"].append(str(directive_path))

    await _request_json(
        client,
        "POST",
        "/settings",
        {
            "workspace": str(workspace),
            "ramdisk_root": str(ramdisk_root),
            "pm_runs_director": True,
        },
    )
    report.evidence["endpoints"].append("POST /settings")

    layout = await _request_json(client, "GET", "/runtime/storage-layout")
    runtime_root = str(layout.get("runtime_root") or "").strip()
    report.runtime_root = runtime_root
    report.evidence["endpoints"].append("GET /runtime/storage-layout")
    layout_violations = runtime_layout_policy_violations(layout if isinstance(layout, dict) else None)
    if layout_violations:
        report.issues.extend(layout_violations)
        report.duration_seconds = round(time.monotonic() - started_at, 2)
        return report

    roles = tuple(DEFAULT_REQUIRED_ROLES) + OPTIONAL_ROLES
    report.role_readiness = await _collect_role_readiness(client, roles)
    report.evidence["endpoints"].append("GET /v2/role/{role}/chat/status")
    not_ready_roles = [
        role
        for role in DEFAULT_REQUIRED_ROLES
        if not bool((report.role_readiness.get(role) or {}).get("ready"))
    ]
    if not_ready_roles:
        report.issues.append("roles_not_ready:" + ",".join(not_ready_roles))
        report.duration_seconds = round(time.monotonic() - started_at, 2)
        return report

    ws_stop = asyncio.Event()
    ws_collector = RuntimeWsCollector(base_url, token, str(workspace), DEFAULT_REQUIRED_ROLES)
    ws_task = asyncio.create_task(ws_collector.run(ws_stop))
    sse_task: asyncio.Task[None] | None = None
    sse_collector: FactorySseCollector | None = None
    try:
        payload = await _request_json(
            client,
            "POST",
            "/v2/factory/runs",
            {
                "workspace": str(workspace),
                "start_from": start_from,
                "directive": directive,
                "run_director": True,
                "director_iterations": max(1, int(director_iterations)),
                "loop": False,
                "input_source": "directive",
            },
        )
        report.evidence["endpoints"].append("POST /v2/factory/runs")
        report.run_id = str(payload.get("run_id") or "").strip()
        report.factory_status = str(payload.get("status") or "").strip()
        report.factory_phase = str(payload.get("phase") or "").strip()
        if not report.run_id:
            report.issues.append("factory_run_id_missing")
            return report

        sse_collector = FactorySseCollector(client, report.run_id)
        sse_task = asyncio.create_task(sse_collector.run())

        final_status = await _poll_factory_completion(
            client,
            report.run_id,
            timeout_seconds=round_timeout_seconds,
            poll_interval=poll_interval_seconds,
        )
        report.evidence["endpoints"].append(f"GET /v2/factory/runs/{report.run_id}")
        report.factory_status = str(final_status.get("status") or report.factory_status).strip()
        report.factory_phase = str(final_status.get("phase") or report.factory_phase).strip()
    finally:
        ws_stop.set()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ws_task, timeout=5)
        if ws_task.done():
            with suppress(Exception):
                ws_task.result()
        else:
            ws_task.cancel()
            with suppress(Exception):
                await ws_task
        if sse_task is not None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(sse_task, timeout=5)
            if not sse_task.done():
                sse_task.cancel()
                with suppress(Exception):
                    await sse_task

    report.runtime_ws = dict(ws_collector.stats)
    report.factory_stream = dict(sse_collector.stats if sse_collector is not None else {})

    snapshot = await _request_json(client, "GET", "/state/snapshot")
    snapshot_path = round_dir / "snapshot.json"
    _write_json_utf8(snapshot_path, snapshot)
    report.evidence["files"].append(str(snapshot_path))
    report.evidence["endpoints"].append("GET /state/snapshot")
    report.snapshot_gate_passed = _validate_snapshot_gate(snapshot if isinstance(snapshot, dict) else {})
    if not report.snapshot_gate_passed:
        report.issues.append("snapshot_gate_failed")

    director_tasks_raw = await _request_json(client, "GET", "/v2/director/tasks")
    director_tasks = director_tasks_raw if isinstance(director_tasks_raw, list) else []
    director_tasks_path = round_dir / "director.tasks.json"
    _write_json_utf8(director_tasks_path, director_tasks)
    report.evidence["files"].append(str(director_tasks_path))
    report.evidence["endpoints"].append("GET /v2/director/tasks")
    report.director_lineage = _count_director_lineage([item for item in director_tasks if isinstance(item, dict)])
    if not report.director_lineage.get("passed"):
        report.issues.append("director_lineage_missing_pm_task_id")

    factory_events = await _request_json(client, "GET", f"/v2/factory/runs/{report.run_id}/events")
    factory_events_path = round_dir / "factory.events.json"
    _write_json_utf8(factory_events_path, factory_events)
    report.evidence["files"].append(str(factory_events_path))
    report.evidence["endpoints"].append(f"GET /v2/factory/runs/{report.run_id}/events")

    factory_artifacts = await _request_json(client, "GET", f"/v2/factory/runs/{report.run_id}/artifacts")
    factory_artifacts_path = round_dir / "factory.artifacts.json"
    _write_json_utf8(factory_artifacts_path, factory_artifacts)
    report.evidence["files"].append(str(factory_artifacts_path))
    report.evidence["endpoints"].append(f"GET /v2/factory/runs/{report.run_id}/artifacts")

    runtime_root_path = Path(runtime_root) if runtime_root else Path()
    pm_contract_path = runtime_root_path / "contracts" / "pm_tasks.contract.json"
    plan_path = runtime_root_path / "contracts" / "plan.md"
    qa_result_path = runtime_root_path / "results" / "integration_qa.result.json"

    pm_contract = _read_json_utf8(pm_contract_path)
    report.pm_quality = _validate_pm_contract(pm_contract)
    if not report.pm_quality.get("passed"):
        report.issues.append("pm_quality_gate_failed")
    if pm_contract_path.exists():
        report.evidence["files"].append(str(pm_contract_path))

    plan_text = _read_text_utf8(plan_path)
    if plan_path.exists():
        report.evidence["files"].append(str(plan_path))
    leakage_findings: list[dict[str, Any]] = []
    if pm_contract:
        leakage_findings.extend(_detect_prompt_leakage(json.dumps(pm_contract, ensure_ascii=False), str(pm_contract_path)))
    if plan_text:
        leakage_findings.extend(_detect_prompt_leakage(plan_text, str(plan_path)))
    report.prompt_leakage_findings = leakage_findings
    if leakage_findings:
        report.issues.append("prompt_leakage_detected")

    qa_result = _read_json_utf8(qa_result_path)
    report.qa_result = {
        "path": str(qa_result_path),
        "reason": str(qa_result.get("reason") or "").strip(),
        "passed": bool(qa_result.get("passed")) or str(qa_result.get("reason") or "").strip() == "integration_qa_passed",
    }
    if qa_result_path.exists():
        report.evidence["files"].append(str(qa_result_path))
    if not report.qa_result.get("passed"):
        report.issues.append("integration_qa_failed")

    if str(report.factory_status).lower() != "completed":
        report.issues.append(f"factory_status={report.factory_status or 'unknown'}")
    if int(report.runtime_ws.get("total_messages") or 0) <= 0:
        report.issues.append("runtime_ws_no_messages")
    if int(report.factory_stream.get("done_events") or 0) <= 0:
        report.issues.append("factory_sse_done_missing")

    latest_runtime_events = _find_latest_runtime_events_path(runtime_root_path) if runtime_root else ""
    if latest_runtime_events:
        report.evidence["files"].append(latest_runtime_events)

    report.duration_seconds = round(time.monotonic() - started_at, 2)
    report.result = "PASS" if not report.issues else "FAIL"
    round_report_path = round_dir / "round.report.json"
    _write_json_utf8(round_report_path, asdict(report))
    report.evidence["files"].append(str(round_report_path))
    return report


def _build_report(
    *,
    agent_label: str,
    base_url: str,
    workspace_base: Path,
    stable_required: int,
    requested_rounds: int,
    rounds: list[RoundReport],
) -> dict[str, Any]:
    consecutive = 0
    max_consecutive = 0
    for round_report in rounds:
        if round_report.result == "PASS":
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0
    projects_completed = sum(1 for item in rounds if item.result == "PASS")
    projects_failed = sum(1 for item in rounds if item.result != "PASS")
    categories = sorted({item.category for item in rounds})
    stable_achieved = max_consecutive >= stable_required
    next_risks: list[str] = []
    for item in rounds:
        if item.prompt_leakage_findings:
            next_risks.append(f"round_{item.round}_prompt_leakage")
        if not item.qa_result.get("passed"):
            next_risks.append(f"round_{item.round}_qa_reason_{item.qa_result.get('reason') or 'unknown'}")
        if int(item.runtime_ws.get("total_messages") or 0) <= 0:
            next_risks.append(f"round_{item.round}_runtime_ws_visibility_low")
    return {
        "schema_version": 1,
        "mode": "ai_agent_headless_stress",
        "timestamp": _now_iso(),
        "status": "PASS" if stable_achieved else "FAIL",
        "agent_label": agent_label,
        "base_url": base_url,
        "workspace_base": str(workspace_base),
        "stable_required": stable_required,
        "requested_rounds": requested_rounds,
        "executed_rounds": len(rounds),
        "stable_achieved": stable_achieved,
        "required_roles": list(DEFAULT_REQUIRED_ROLES),
        "stress_rounds": [
            {
                "round": item.round,
                "project_name": item.project_name,
                "category": item.category,
                "enhancements": item.enhancements,
                "result": item.result,
                "evidence": list(item.evidence.get("files") or []),
            }
            for item in rounds
        ],
        "coverage_summary": {
            "categories_covered": categories,
            "projects_completed": projects_completed,
            "projects_failed": projects_failed,
        },
        "rounds": [asdict(item) for item in rounds],
        "next_risks": sorted(set(next_risks)),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Polaris AI-agent headless stress validation.")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("KERNELONE_BASE_URL", ""),
        help="Optional backend base URL override; otherwise auto-discover the current Polaris backend context",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("KERNELONE_TOKEN", ""),
        help="Optional backend bearer token override; otherwise auto-discover the current Polaris backend context",
    )
    parser.add_argument("--agent-label", default="codex", help="Agent label recorded in reports, e.g. codex or claude")
    parser.add_argument(
        "--workspace-base",
        default="",
        help="Base directory for generated target workspaces (Windows stress runs must stay under C:/Temp/)",
    )
    parser.add_argument(
        "--self-upgrade-mode",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow Polaris meta-project to be used as a workspace target in general policy checks; stress-path policy still requires C:/Temp/",
    )
    parser.add_argument("--rounds", type=int, default=5, help="Maximum round count")
    parser.add_argument("--stable-required", type=int, default=2, help="Stop after N consecutive passing rounds")
    parser.add_argument("--start-from", default="auto", choices=["auto", "architect", "pm"], help="Factory run start_from mode")
    parser.add_argument("--director-iterations", type=int, default=1, help="Director iterations for each round")
    parser.add_argument("--round-timeout-seconds", type=int, default=1800, help="Timeout budget per factory round")
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0, help="Factory status poll interval in seconds")
    parser.add_argument("--scenario-offset", type=int, default=0, help="Rotate scenario selection by an offset")
    parser.add_argument("--complexity-floor-lines", type=int, default=500, help="Minimum code line target embedded in the directive")
    parser.add_argument("--report-output", default="", help="Optional final JSON report path")
    return parser.parse_args(argv)


async def _run_async(args: argparse.Namespace) -> int:
    try:
        workspace_base = _build_workspace_base(
            str(args.workspace_base or ""),
            self_upgrade_mode=args.self_upgrade_mode,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        ramdisk_root = _build_ramdisk_root("")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        base_url, token, backend_context = await _resolve_backend_connection(
            str(args.base_url or ""),
            str(args.token or ""),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    source_label = str(backend_context.get("source") or "").strip()
    if source_label == "desktop_backend_info":
        backend_path = str(backend_context.get("path") or "").strip()
        print(f"Resolved Polaris backend info from desktop backend state: {backend_path}", flush=True)
    elif source_label:
        print(f"Resolved Polaris backend info from: {source_label}", flush=True)
    workspace_base.mkdir(parents=True, exist_ok=True)
    report_root = workspace_base / "_reports" / datetime.now().strftime("%Y%m%d-%H%M%S")
    report_root.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(connect=20.0, read=60.0, write=20.0, pool=20.0)
    rounds: list[RoundReport] = []
    consecutive_passes = 0

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout) as client:
        health = await _request_json(client, "GET", "/health")
        _write_json_utf8(report_root / "health.json", health)

        for round_number in range(1, max(1, int(args.rounds)) + 1):
            report = await _run_single_round(
                client=client,
                base_url=base_url,
                token=token,
                workspace_base=workspace_base,
                ramdisk_root=ramdisk_root,
                report_root=report_root,
                round_number=round_number,
                scenario_offset=int(args.scenario_offset),
                agent_label=_normalize_agent_label(str(args.agent_label or "")),
                start_from=str(args.start_from or "auto"),
                director_iterations=max(1, int(args.director_iterations)),
                round_timeout_seconds=max(60, int(args.round_timeout_seconds)),
                poll_interval_seconds=max(0.2, float(args.poll_interval_seconds)),
                complexity_floor_lines=max(100, int(args.complexity_floor_lines)),
            )
            rounds.append(report)
            print(
                (
                    f"ROUND {report.round}: result={report.result} "
                    f"project={report.project_name} factory={report.factory_status or '-'} "
                    f"qa={report.qa_result.get('reason') or '-'} "
                    f"issues={len(report.issues)} ws={report.runtime_ws.get('total_messages', 0)} "
                    f"sse_done={report.factory_stream.get('done_events', 0)}"
                ),
                flush=True,
            )
            if report.result == "PASS":
                consecutive_passes += 1
            else:
                consecutive_passes = 0
            if consecutive_passes >= max(1, int(args.stable_required)):
                break

    final_report = _build_report(
        agent_label=_normalize_agent_label(str(args.agent_label or "")),
        base_url=base_url,
        workspace_base=workspace_base,
        stable_required=max(1, int(args.stable_required)),
        requested_rounds=max(1, int(args.rounds)),
        rounds=rounds,
    )

    output_path = (
        Path(str(args.report_output)).expanduser().resolve()
        if str(args.report_output or "").strip()
        else report_root / "agent-headless-stress.report.json"
    )
    _write_json_utf8(output_path, final_report)
    print(f"REPORT: {output_path}", flush=True)
    print(f"STATUS: {final_report['status']}", flush=True)
    return 0 if final_report["status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())

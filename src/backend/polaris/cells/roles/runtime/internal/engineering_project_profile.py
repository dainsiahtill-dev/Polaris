"""Resident AGI project awareness: deterministic profile and capability binding.

The profile is derived from repository facts only. Resident AGI consumes it to
select MCP servers, skills and prompt guidance; it never guesses from prose.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "project_profile.v1"

# Order matters: the first matching family wins as the primary type.
_SIGNATURES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("reverse", "reverse_engineering", ("*.idb", "*.i64", "*.id0", "*.id1", "*.id2", "*.nam", "*.til")),
    ("embedded", "embedded", ("platformio.ini", "CMakeLists.txt", "*.ioc")),
    ("web", "web", ("package.json", "vite.config.js", "vite.config.ts", "next.config.js")),
    ("python", "python", ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile")),
    ("rust", "rust", ("Cargo.toml",)),
    ("go", "go", ("go.mod",)),
    ("node", "node", ("package.json",)),
    ("java", "java", ("pom.xml", "build.gradle", "build.gradle.kts")),
    ("cpp", "cpp", ("CMakeLists.txt", "Makefile")),
)

_BINARY_RE = re.compile(r"^(?:.*/)?(?:bin|dist|build|target)/(?:release|debug)/[^/]+$")

_CAPABILITY_RULES: dict[str, dict[str, Any]] = {
    "web": {
        "capabilities": ["browser_observe"],
        "guidance": "使用 browser_observe 做真实浏览器验收：导航、交互、截图与 DOM 断言；不要用 Electron/Xvfb 替代。",
    },
    "reverse_engineering": {
        "capabilities": [],
        "mcp_hints": ["REA", "IDA"],
        "guidance": "逆向任务优先使用 REA/IDA 类 MCP 获取反汇编与符号；二进制证据必须绑定具体地址与函数。",
    },
    "embedded": {
        "capabilities": [],
        "guidance": "嵌入式任务区分宿主机验证与目标机验证；缺失烧录条件时如实标记 UNKNOWN。",
    },
    "python": {"capabilities": [], "guidance": "Python 任务使用项目声明的解释器与测试命令，未声明时明确说明假设。"},
    "rust": {"capabilities": [], "guidance": "Rust 任务执行 cargo build/clippy/test，并保留警告与失败原文。"},
    "go": {"capabilities": [], "guidance": "Go 任务执行 go build/vet/test，保留完整输出。"},
    "node": {"capabilities": [], "guidance": "Node 任务使用仓库锁文件对应的包管理器与脚本。"},
    "java": {"capabilities": [], "guidance": "Java 任务使用 Maven/Gradle 声明的构建入口。"},
    "cpp": {"capabilities": [], "guidance": "C/C++ 任务先确认工具链与构建系统，再执行构建与测试。"},
}


def _existing_patterns(workspace: Path, patterns: Sequence[str]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        if pattern.startswith("*"):
            if list(workspace.glob(f"**/{pattern}"))[:1]:
                found.append(pattern)
        elif (workspace / pattern).exists():
            found.append(pattern)
    return found


def _detect_binaries(workspace: Path) -> list[str]:
    matches: list[str] = []
    for directory in ("target", "build", "dist", "bin"):
        root = workspace / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and _BINARY_RE.match(path.relative_to(workspace).as_posix()):
                matches.append(path.relative_to(workspace).as_posix())
                if len(matches) >= 5:
                    return matches
    return matches


def detect_project_profile(workspace: Path) -> dict[str, Any]:
    """Derive a bounded project profile from repository facts."""

    root = Path(workspace)
    signals: list[str] = []
    project_type = "generic"
    for _name, kind, patterns in _SIGNATURES:
        matched = _existing_patterns(root, patterns)
        if matched:
            project_type = kind
            signals.extend(matched[:4])
            break
    binaries = _detect_binaries(root)
    signals.extend(binaries)
    rule = _CAPABILITY_RULES.get(project_type, {"capabilities": [], "guidance": "按仓库事实选择最小验证集。"})
    capabilities = sorted(set(rule.get("capabilities") or []))
    toolchain = [signal for signal in signals if not signal.startswith("*")][:8]
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": str(root),
        "project_type": project_type,
        "signals": signals[:12],
        "toolchain": toolchain,
        "capabilities": capabilities,
        "mcp_hints": sorted(set(rule.get("mcp_hints") or [])),
        "guidance": str(rule.get("guidance") or ""),
    }


def prompt_additions(profile: Mapping[str, Any]) -> list[dict[str, str]]:
    """Resident AGI guidance derived from the detected profile."""

    project_type = str(profile.get("project_type") or "generic")
    guidance = str(profile.get("guidance") or "")
    if not guidance:
        return []
    capabilities = ", ".join(str(item) for item in profile.get("capabilities") or []) or "无"
    mcp_hints = ", ".join(str(item) for item in profile.get("mcp_hints") or []) or "无"
    return [
        {
            "role": "system",
            "content": (
                f"Resident AGI 项目感知：类型 {project_type}；装配能力 {capabilities}；MCP 提示 {mcp_hints}。\n"
                f"指导：{guidance}"
            ),
        }
    ]


def profile_fingerprint(profile: Mapping[str, Any]) -> str:
    import hashlib

    payload = json.dumps(
        {"project_type": profile.get("project_type"), "signals": profile.get("signals")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def environment_profile() -> dict[str, Any]:
    """Expose the Resident AGI binding controls to the host."""

    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": os.environ.get("KERNELONE_RESIDENT_PROJECT_PROFILE", "1").strip().lower()
        not in {"0", "false", "off"},
    }

"""模块级常量 — 工具白名单、拒绝标记、别名映射、意图分类等。

所有业务模块仅从本模块导入常量，禁止多处重复定义。
"""

from __future__ import annotations

import re

from polaris.cells.roles.kernel.public.turn_contracts import (
    _ASYNC_TOOLS,
    _READONLY_TOOLS,
)

# ---------------------------------------------------------------------------
# LLM 拒绝标记
# ---------------------------------------------------------------------------
REFUSAL_MARKERS: tuple[str, ...] = (
    "i cannot",
    "i can't",
    "i'm sorry",
    "i am sorry",
    "i cannot assist",
    "i can't assist",
    "不能",
    "禁止",
    "不允许",
    "无法",
    "对不起",
    "抱歉",
)

# ---------------------------------------------------------------------------
# 写工具集合（突变工具）
# ---------------------------------------------------------------------------
# 这是全系统唯一手动维护的写工具列表。
# 新增写工具必须同时满足：
#   1. 在工具注册表中有真实 handler
#   2. 会修改文件系统或外部系统状态
#   3. 不能归入 _READONLY_TOOLS 或 _ASYNC_TOOLS
#
# 来源合并：
#   - constants.py 原有 8 个
#   - write_phases.py 补充 3 个（delete_file, rename_file, apply_patch）
#   - turn_decision_decoder.py 中的 bash/mkdir/mv/cp 被排除（非注册工具名）
# ---------------------------------------------------------------------------
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        # 文件写操作（有注册 handler）
        "precision_edit",
        "edit_blocks",
        "search_replace",
        "edit_file",
        "repo_apply_diff",
        "append_to_file",
        "write_file",
        "create_file",
        # 由 speculation/write_phases.py 声明的写语义工具
        # 注：delete_file / rename_file / apply_patch 目前无独立 handler，
        #     由协议层或 execute_command 代理实现；保留以维持 speculation 契约。
        "delete_file",
        "rename_file",
        "apply_patch",
        # patch_apply 是 repo_apply_diff 的别名（tool_spec_registry 注册）
        # 必须显式包含，因为 is_write_tool 的归一化逻辑不会自动映射别名
        "patch_apply",
        # apply_diff 同样是 repo_apply_diff 的别名（tool_spec_registry 中 aliases: ["apply_diff", "patch_apply"]）
        "apply_diff",
    }
)

# ---------------------------------------------------------------------------
# 读工具集合（上下文获取）
# ---------------------------------------------------------------------------
# 自动从 turn_contracts.py 的工程级真相源派生，禁止手动维护。
# 这是为了解决 constants.py 与 turn_contracts.py / turn_decision_decoder.py
# 之间 READ_TOOLS 不一致的历史债务。
# ---------------------------------------------------------------------------
READ_TOOLS: frozenset[str] = _READONLY_TOOLS

# ---------------------------------------------------------------------------
# 异步工具集合
# ---------------------------------------------------------------------------
# 自动从 turn_contracts.py 的工程级真相源派生，禁止手动维护。
# ---------------------------------------------------------------------------
ASYNC_TOOLS: frozenset[str] = _ASYNC_TOOLS

# ---------------------------------------------------------------------------
# 安全只读引导工具（bootstrap / retry 第一阶段允许）
# ---------------------------------------------------------------------------
# 业务白名单，与工程分类无关，保持独立维护。
# ---------------------------------------------------------------------------
SAFE_READ_BOOTSTRAP_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "list_directory",
        "repo_rg",
        "repo_glob",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_read_around",
        "repo_tree",
        "repo_map",
        "repo_symbols_index",
        "repo_diff",
        "file_exists",
        "glob",
        "search_code",
    }
)

# ---------------------------------------------------------------------------
# 验证工具
# ---------------------------------------------------------------------------
VERIFICATION_TOOLS: frozenset[str] = frozenset({"execute_command"})

# ---------------------------------------------------------------------------
# 工具别名（功能等价归一化）
# ---------------------------------------------------------------------------
TOOL_ALIASES: dict[str, str] = {
    "ripgrep": "repo_rg",
    "rg": "repo_rg",
}

# ---------------------------------------------------------------------------
# 必需工具等价映射（contract 守卫用）
#   key: 契约中要求的工具名
#   value: 可接受的等价工具名元组
# ---------------------------------------------------------------------------
REQUIRED_TOOL_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "search_replace": ("precision_edit", "repo_apply_diff", "edit_file"),
    "repo_rg": ("read_file", "repo_read_head", "repo_read_slice"),
}

# ===========================================================================
# 意图分类 — 覆盖完整的 SDLC 用户意图光谱
# ===========================================================================
# 优先级（从高到低）:
#   STRONG_MUTATION > DEBUG_AND_FIX > DEVOPS > WEAK_MUTATION > TESTING > PLANNING > ANALYSIS_ONLY
#
# 英文匹配使用单词边界 (\b) 避免误杀（如 credit → edit）。
# ===========================================================================

# 1. 纯分析与探索信号 (Read-only / Analysis)
ANALYSIS_ONLY_SIGNALS: tuple[str, ...] = (
    # 中文
    "建议",
    "分析",
    "总结",
    "评估",
    "意见",
    "解释",
    "说明",
    "梳理",
    "盘点",
    "看看",
    "阅读",
    "了解",
    "熟悉",
    "讲解",
    "理清",
    "概括",
    "审查",
    "帮忙看",
    "走读",
    # 英文（含 review 等常见词）
    "suggestion",
    "advice",
    "analyze",
    "analyse",
    "summarize",
    "summary",
    "assess",
    "evaluate",
    "review",
    "explain",
    "describe",
    "understand",
    "inspect",
    "audit",
    "walkthrough",
    "explore",
    "clarify",
    "overview",
)

# 2. 强突变标记 (Strong Mutation — 明确要求改变代码库状态)
STRONG_MUTATION_CN_MARKERS: tuple[str, ...] = (
    "修改",
    "改成",
    "改为",
    "替换",
    "创建",
    "新建",
    "新增",
    "更新",
    "改动",
    "落地",
    "实现",
    "写入",
    "追加",
    "删除",
    "移除",
    "清除",
    "覆盖",
    "重写",
    "插入",
    "编写",
    "开发",
    "加上",
    "去掉",
    "拆分",
    "重构",
)
STRONG_MUTATION_EN_MARKERS: tuple[str, ...] = (
    "modify",
    "change",
    "replace",
    "create",
    "write",
    "append",
    "update",
    "delete",
    "edit",
    "patch",
    "implement",
    "remove",
    "clear",
    "overwrite",
    "rewrite",
    "insert",
    "generate",
    "build",
    "code",
    "add",
)

# 3. 弱突变/重构标记 (Weak Mutation — 模糊地带，需结合上下文)
WEAK_MUTATION_CN_MARKERS: tuple[str, ...] = (
    "重构",
    "优化",
    "完善",
    "整理",
    "格式化",
    "调整",
    "规范化",
    "清理",
    "打磨",
    "改进",
)
WEAK_MUTATION_EN_MARKERS: tuple[str, ...] = (
    "refactor",
    "optimize",
    "improve",
    "format",
    "cleanup",
    "polish",
    "restructure",
    "reorganize",
    "lint",
    "tune",
)

# 4. 调试与修复 (Debugging & Bug Fixing — 通常隐含突变)
DEBUG_AND_FIX_CN_MARKERS: tuple[str, ...] = (
    "修复",
    "修bug",
    "解决",
    "排查",
    "报错",
    "异常",
    "调试",
    "定位",
    "修一下",
    "为什么报错",
    "跑不通",
    "崩溃",
)
DEBUG_AND_FIX_EN_MARKERS: tuple[str, ...] = (
    "fix",
    "resolve",
    "debug",
    "troubleshoot",
    "error",
    "exception",
    "trace",
    "investigate",
    "issue",
    "crash",
    "bug",
    "stacktrace",
)

# 5. 测试与验证 (Testing — 通常不触发 mutation guard，但触发 verification)
TESTING_SIGNALS: tuple[str, ...] = (
    "测试",
    "运行",
    "执行",
    "跑一下",
    "验证",
    "断言",
    "跑通",
    "test",
    "run",
    "execute",
    "verify",
    "assert",
    "check",
    "pass",
    "pytest",
    "jest",
    "coverage",
)

# 6. 环境、配置与基建 (DevOps — 可能改变系统状态)
# 注意：「配置」单字被移除，因为它作为名词（如「API_HOST 配置的文件」）
# 会造成对只读查询的假阳性 DEVOPS 分类。实际 DevOps 配置变更意图
# 通常以动词（如「修改配置」「更新配置」）组合出现，已由 STRONG_MUTATION
# 和 WEAK_MUTATION 路径覆盖。
DEVOPS_CONFIG_SIGNALS: tuple[str, ...] = (
    "安装",
    "部署",
    "打包",
    "构建",
    "编译",
    "依赖",
    "install",
    "setup",
    "configure",
    "deploy",
    "package",
    "build",
    "compile",
    "dependency",
    "npm",
    "pip",
    "docker",
)

# 7. 规划与拆解 (Planning — 纯编排意图，不触发 mutation)
PLANNING_SIGNALS: tuple[str, ...] = (
    "规划",
    "拆解",
    "排期",
    "设计",
    "架构",
    "蓝图",
    "任务单",
    "plan",
    "breakdown",
    "design",
    "architecture",
    "blueprint",
    "epic",
    "story",
    "task",
    "roadmap",
)

# ---------------------------------------------------------------------------
# 聚合字典 — 供运行时按类别遍历
# ---------------------------------------------------------------------------
INTENT_MARKERS_REGISTRY: dict[str, tuple[str, ...]] = {
    "ANALYSIS_ONLY": ANALYSIS_ONLY_SIGNALS,
    "STRONG_MUTATION": STRONG_MUTATION_CN_MARKERS + STRONG_MUTATION_EN_MARKERS,
    "WEAK_MUTATION": WEAK_MUTATION_CN_MARKERS + WEAK_MUTATION_EN_MARKERS,
    "DEBUG_AND_FIX": DEBUG_AND_FIX_CN_MARKERS + DEBUG_AND_FIX_EN_MARKERS,
    "TESTING": TESTING_SIGNALS,
    "DEVOPS": DEVOPS_CONFIG_SIGNALS,
    "PLANNING": PLANNING_SIGNALS,
}

# ---------------------------------------------------------------------------
# 英文单词边界正则 (编译一次，复用多次)
# ---------------------------------------------------------------------------
_EN_ANALYSIS_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in ANALYSIS_ONLY_SIGNALS if m.isascii()) + r")\b",
    re.IGNORECASE,
)
_EN_STRONG_MUTATION_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in STRONG_MUTATION_EN_MARKERS) + r")\b",
    re.IGNORECASE,
)
_EN_WEAK_MUTATION_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in WEAK_MUTATION_EN_MARKERS) + r")\b",
    re.IGNORECASE,
)
_EN_DEBUG_FIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in DEBUG_AND_FIX_EN_MARKERS) + r")\b",
    re.IGNORECASE,
)
_EN_TESTING_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in TESTING_SIGNALS if m.isascii()) + r")\b",
    re.IGNORECASE,
)
_EN_DEVOPS_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in DEVOPS_CONFIG_SIGNALS if m.isascii()) + r")\b",
    re.IGNORECASE,
)
_EN_PLANNING_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in PLANNING_SIGNALS if m.isascii()) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 验证意图标记
# ---------------------------------------------------------------------------
VERIFICATION_CN_MARKERS: tuple[str, ...] = ("验证", "校验", "测试")
VERIFICATION_EN_PATTERN: str = r"\b(verify|validation|validate|test|pytest|check)\b"

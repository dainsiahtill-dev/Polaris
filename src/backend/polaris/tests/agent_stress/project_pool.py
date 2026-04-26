"""项目池定义 - 覆盖 20 个日常开发练手型项目。

目标是让 tests.agent_stress 与当前 Polaris 官方练习项目池保持一致：
1. 个人记账簿 (账单管理)
2. 待办事项清单 (To-Do List)
3. 简易 Markdown 编辑器
4. 实时聊天室 (WebSocket)
5. 博客系统 (CMS)
6. 天气预报展示器
7. 个人简历生成器
8. 抽奖/随机点名工具
9. 番茄钟 (专注计时器)
10. 密码管理器 (加密存储)
11. 图片占位符生成器
12. 在线剪贴板 (跨端传词)
13. 聚合搜索工具 (一键搜多站)
14. 简易单位转换器 (汇率/度量)
15. 文件断点续传器
16. 静态网站生成器 (SSG)
17. RSS 阅读器
18. 自动化签到脚本
19. 屏幕截图/录屏工具
20. 贪吃蛇/俄罗斯方块小游戏
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ProjectCategory(Enum):
    """项目类别"""

    CRUD = "crud"  # CRUD / 表单 / 数据管理
    REALTIME = "realtime"  # 实时通信 / 同步
    EDITOR = "editor"  # 编辑器 / 内容处理
    TOOL = "tool"  # 工具型应用 / 自动化
    SECURITY = "security"  # 安全 / 加密 / 文件处理
    INTERACTIVE = "interactive"  # 互动 / 游戏 / 计时类 UI


class Enhancement(Enum):
    """增强特性"""

    PERSISTENCE = "本地持久化"
    IMPORT_EXPORT = "导入导出"
    WEBSOCKET = "WebSocket / SSE"
    USER_CONFIG = "用户配置或环境变量"
    UNIT_TEST = "单元测试"
    INTEGRATION_TEST = "集成测试"
    ERROR_HANDLING = "错误处理与回退机制"
    BUILD_SCRIPT = "构建脚本 / 发布脚本"
    AUDIT_LOG = "审计日志"
    PERMISSION = "权限或加密"
    BATCH = "批处理能力"
    OFFLINE_CACHE = "离线缓存"


@dataclass
class ProjectDefinition:
    """项目定义"""

    id: str  # 项目唯一标识
    name: str  # 项目名称
    category: ProjectCategory  # 所属类别
    description: str  # 核心能力描述
    enhancements: list[Enhancement]  # 增强特性
    stress_focus: list[str]  # 压测重点
    complexity_level: int = 1  # 复杂度 1-5
    requires_backend: bool = False  # 是否需要后端
    requires_websocket: bool = False  # 是否需要 WebSocket
    requires_encryption: bool = False  # 是否需要加密

    def to_directive(self) -> str:
        """转换为 Polaris directive"""
        lines = [
            f"# {self.name}",
            "",
            "## 需求描述",
            self.description,
            "",
            "## 增强特性",
        ]
        for enh in self.enhancements:
            lines.append(f"- {enh.value}")
        lines.extend(
            [
                "",
                "## 技术要求",
                f"- 复杂度等级: {self.complexity_level}/5",
            ]
        )
        if self.requires_backend:
            lines.append("- 需要后端 API 支持")
        if self.requires_websocket:
            lines.append("- 需要 WebSocket 实时通信")
        if self.requires_encryption:
            lines.append("- 需要加密/安全处理")
        lines.extend(
            [
                "",
                "## 验收标准",
                "1. 核心功能完整可用",
                "2. 增强特性正常工作",
                "3. 代码通过基础质量检查",
            ]
        )
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 项目池定义（保持与练手项目总表同序，便于审计和比对）
# ═══════════════════════════════════════════════════════════════════════════════

PROJECT_POOL: list[ProjectDefinition] = [
    ProjectDefinition(
        id="expense-tracker",
        name="个人记账簿 (账单管理)",
        category=ProjectCategory.CRUD,
        description="账单录入、分类管理、筛选查询、月度汇总统计与预算提醒",
        enhancements=[
            Enhancement.PERSISTENCE,
            Enhancement.IMPORT_EXPORT,
            Enhancement.UNIT_TEST,
        ],
        stress_focus=["表单状态管理", "数据持久化", "一致性校验"],
        complexity_level=2,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="todo-advanced",
        name="待办事项清单 (To-Do List)",
        category=ProjectCategory.CRUD,
        description="新增、编辑、完成状态切换、优先级、标签分类",
        enhancements=[
            Enhancement.PERSISTENCE,
            Enhancement.USER_CONFIG,
            Enhancement.OFFLINE_CACHE,
            Enhancement.UNIT_TEST,
        ],
        stress_focus=["状态流转", "过滤组合", "交互回归"],
        complexity_level=2,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="markdown-editor",
        name="简易 Markdown 编辑器",
        category=ProjectCategory.EDITOR,
        description="实时编辑、实时预览、文档保存、目录导航",
        enhancements=[
            Enhancement.PERSISTENCE,
            Enhancement.IMPORT_EXPORT,
            Enhancement.USER_CONFIG,
            Enhancement.UNIT_TEST,
        ],
        stress_focus=["文本处理", "预览同步", "内容渲染安全"],
        complexity_level=3,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="chat-room",
        name="实时聊天室 (WebSocket)",
        category=ProjectCategory.REALTIME,
        description="多人在线消息、用户在线状态、房间切换与消息历史",
        enhancements=[
            Enhancement.WEBSOCKET,
            Enhancement.PERSISTENCE,
            Enhancement.ERROR_HANDLING,
        ],
        stress_focus=["实时连接", "状态同步", "异常恢复"],
        complexity_level=3,
        requires_backend=True,
        requires_websocket=True,
    ),
    ProjectDefinition(
        id="blog-cms",
        name="博客系统 (CMS)",
        category=ProjectCategory.CRUD,
        description="文章创建、编辑、发布、列表展示、草稿管理与后台搜索",
        enhancements=[
            Enhancement.PERSISTENCE,
            Enhancement.PERMISSION,
            Enhancement.AUDIT_LOG,
            Enhancement.UNIT_TEST,
            Enhancement.INTEGRATION_TEST,
        ],
        stress_focus=["内容模型", "路由管理", "权限边界"],
        complexity_level=4,
        requires_backend=True,
    ),
    ProjectDefinition(
        id="weather-dashboard",
        name="天气预报展示器",
        category=ProjectCategory.TOOL,
        description="城市查询、天气卡片展示、多日预报",
        enhancements=[
            Enhancement.OFFLINE_CACHE,
            Enhancement.ERROR_HANDLING,
            Enhancement.USER_CONFIG,
            Enhancement.UNIT_TEST,
        ],
        stress_focus=["第三方 API 适配", "缓存策略", "降级处理"],
        complexity_level=2,
        requires_backend=True,
    ),
    ProjectDefinition(
        id="resume-builder",
        name="个人简历生成器",
        category=ProjectCategory.CRUD,
        description="表单录入信息、多模板切换、实时预览、导出 PDF",
        enhancements=[
            Enhancement.IMPORT_EXPORT,
            Enhancement.USER_CONFIG,
            Enhancement.BUILD_SCRIPT,
        ],
        stress_focus=["模板渲染", "结构化数据", "导出质量"],
        complexity_level=3,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="lottery-draw",
        name="抽奖/随机点名工具",
        category=ProjectCategory.TOOL,
        description="名单导入、随机选择、结果展示、去重规则与历史记录",
        enhancements=[
            Enhancement.PERSISTENCE,
            Enhancement.USER_CONFIG,
            Enhancement.IMPORT_EXPORT,
        ],
        stress_focus=["随机算法", "状态控制", "交互动效"],
        complexity_level=2,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="pomodoro-timer",
        name="番茄钟 (专注计时器)",
        category=ProjectCategory.INTERACTIVE,
        description="专注/休息倒计时、阶段切换、记录统计与提醒",
        enhancements=[
            Enhancement.PERSISTENCE,
            Enhancement.USER_CONFIG,
            Enhancement.ERROR_HANDLING,
            Enhancement.UNIT_TEST,
        ],
        stress_focus=["计时精度", "前后台状态一致性", "持久化"],
        complexity_level=2,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="password-manager",
        name="密码管理器 (加密存储)",
        category=ProjectCategory.SECURITY,
        description="密码条目管理、本地加密、主密码解锁、分类管理",
        enhancements=[
            Enhancement.ERROR_HANDLING,
            Enhancement.AUDIT_LOG,
            Enhancement.PERMISSION,
            Enhancement.UNIT_TEST,
        ],
        stress_focus=["加密边界", "敏感数据处理", "防泄漏"],
        complexity_level=4,
        requires_encryption=True,
    ),
    ProjectDefinition(
        id="image-placeholder",
        name="图片占位符生成器",
        category=ProjectCategory.TOOL,
        description="自定义尺寸/文字/背景色、批量生成、下载输出",
        enhancements=[
            Enhancement.BATCH,
            Enhancement.USER_CONFIG,
            Enhancement.BUILD_SCRIPT,
        ],
        stress_focus=["参数验证", "图像生成", "批处理流程"],
        complexity_level=2,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="clipboard-sync",
        name="在线剪贴板 (跨端传词)",
        category=ProjectCategory.REALTIME,
        description="跨端文本发送接收、短期存储、过期清理",
        enhancements=[
            Enhancement.WEBSOCKET,
            Enhancement.PERSISTENCE,
            Enhancement.PERMISSION,
            Enhancement.ERROR_HANDLING,
        ],
        stress_focus=["同步一致性", "权限控制", "临时数据清理"],
        complexity_level=3,
        requires_backend=True,
        requires_websocket=True,
    ),
    ProjectDefinition(
        id="aggregator-search",
        name="聚合搜索工具 (一键搜多站)",
        category=ProjectCategory.TOOL,
        description="统一搜索输入、多站点跳转/聚合结果与模板管理",
        enhancements=[
            Enhancement.USER_CONFIG,
            Enhancement.PERSISTENCE,
            Enhancement.OFFLINE_CACHE,
        ],
        stress_focus=["配置化", "跳转逻辑", "结果整合"],
        complexity_level=2,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="unit-converter",
        name="简易单位转换器 (汇率/度量)",
        category=ProjectCategory.TOOL,
        description="多单位换算、双向输入、汇率/度量支持",
        enhancements=[
            Enhancement.OFFLINE_CACHE,
            Enhancement.USER_CONFIG,
            Enhancement.UNIT_TEST,
        ],
        stress_focus=["计算正确性", "配置扩展", "边界值测试"],
        complexity_level=2,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="file-resumable-upload",
        name="文件断点续传器",
        category=ProjectCategory.SECURITY,
        description="分片上传、断点恢复、进度展示、校验和验证",
        enhancements=[
            Enhancement.ERROR_HANDLING,
            Enhancement.BATCH,
            Enhancement.AUDIT_LOG,
            Enhancement.INTEGRATION_TEST,
        ],
        stress_focus=["文件处理", "恢复逻辑", "异常注入测试"],
        complexity_level=4,
        requires_backend=True,
    ),
    ProjectDefinition(
        id="static-site-generator",
        name="静态网站生成器 (SSG)",
        category=ProjectCategory.EDITOR,
        description="Markdown 输入、模板渲染、导航生成、静态输出",
        enhancements=[
            Enhancement.IMPORT_EXPORT,
            Enhancement.BATCH,
            Enhancement.BUILD_SCRIPT,
            Enhancement.INTEGRATION_TEST,
        ],
        stress_focus=["内容编译链", "文件系统", "构建产物校验"],
        complexity_level=4,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="rss-reader",
        name="RSS 阅读器",
        category=ProjectCategory.EDITOR,
        description="订阅源管理、文章列表、已读状态、关键词过滤",
        enhancements=[
            Enhancement.PERSISTENCE,
            Enhancement.OFFLINE_CACHE,
            Enhancement.ERROR_HANDLING,
            Enhancement.USER_CONFIG,
        ],
        stress_focus=["抓取兼容性", "解析容错", "状态持久化"],
        complexity_level=3,
        requires_backend=True,
    ),
    ProjectDefinition(
        id="auto-signin",
        name="自动化签到脚本",
        category=ProjectCategory.TOOL,
        description="目标站点登录、签到执行、结果记录",
        enhancements=[
            Enhancement.ERROR_HANDLING,
            Enhancement.AUDIT_LOG,
            Enhancement.PERMISSION,
        ],
        stress_focus=["自动化稳定性", "重试机制", "凭据管理"],
        complexity_level=3,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="screen-capture-recorder",
        name="屏幕截图/录屏工具",
        category=ProjectCategory.TOOL,
        description="截图、区域选择、录屏、历史记录与格式配置",
        enhancements=[
            Enhancement.IMPORT_EXPORT,
            Enhancement.USER_CONFIG,
            Enhancement.BUILD_SCRIPT,
        ],
        stress_focus=["桌面能力", "文件输出", "性能与权限"],
        complexity_level=3,
        requires_backend=False,
    ),
    ProjectDefinition(
        id="arcade-games",
        name="贪吃蛇/俄罗斯方块小游戏",
        category=ProjectCategory.INTERACTIVE,
        description="贪吃蛇与俄罗斯方块双模式、得分统计、重新开始、难度配置",
        enhancements=[
            Enhancement.PERSISTENCE,
            Enhancement.USER_CONFIG,
            Enhancement.AUDIT_LOG,
        ],
        stress_focus=["渲染刷新", "状态机", "输入响应"],
        complexity_level=4,
        requires_backend=False,
    ),
]


def get_project_by_id(project_id: str) -> ProjectDefinition | None:
    """通过 ID 获取项目定义"""
    for project in PROJECT_POOL:
        if project.id == project_id:
            return project
    return None


def get_projects_by_category(category: ProjectCategory) -> list[ProjectDefinition]:
    """获取指定类别的所有项目"""
    return [p for p in PROJECT_POOL if p.category == category]


def get_all_categories() -> list[ProjectCategory]:
    """获取所有类别"""
    return list(ProjectCategory)


def build_rotation_order(
    pool: Sequence[ProjectDefinition] | None = None,
) -> list[ProjectDefinition]:
    """构建完整一轮 rotation 顺序。

    规则：
    1. 在不打乱同类项目原始顺序的前提下，按类别轮转
    2. 当某个类别耗尽后，继续消费其他类别
    3. 对完整项目池，前 20 轮会完整覆盖 20 个项目各一次
    """

    candidates = list(PROJECT_POOL if pool is None else pool)
    if not candidates:
        return []

    by_category: dict[ProjectCategory, list[ProjectDefinition]] = {category: [] for category in get_all_categories()}
    for project in candidates:
        by_category.setdefault(project.category, []).append(project)

    indices = dict.fromkeys(by_category, 0)
    ordered: list[ProjectDefinition] = []

    while len(ordered) < len(candidates):
        progressed = False
        for category in get_all_categories():
            projects = by_category.get(category, [])
            index = indices.get(category, 0)
            if index >= len(projects):
                continue
            ordered.append(projects[index])
            indices[category] = index + 1
            progressed = True
        if not progressed:
            break

    return ordered


def select_stress_rounds(
    total_rounds: int,
    strategy: str = "rotation",
    pool: Sequence[ProjectDefinition] | None = None,
) -> list[ProjectDefinition]:
    """选择压测轮次的项目

    Args:
        total_rounds: 总轮次数
        strategy: 选择策略 (rotation=轮换, random=随机, complexity_asc=复杂度递增)
        pool: 可选的项目子集

    Returns:
        项目定义列表
    """
    candidates = list(PROJECT_POOL if pool is None else pool)
    if total_rounds <= 0 or not candidates:
        return []

    if strategy == "rotation":
        rotation_order = build_rotation_order(candidates)
        return [rotation_order[i % len(rotation_order)] for i in range(total_rounds)]

    elif strategy == "random":
        import random

        return [random.choice(candidates) for _ in range(total_rounds)]

    elif strategy == "complexity_asc":
        # 按复杂度递增
        sorted_projects = sorted(
            candidates,
            key=lambda p: (p.complexity_level, p.name, p.id),
        )
        return [sorted_projects[i % len(sorted_projects)] for i in range(total_rounds)]

    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def validate_round_sequence(rounds: list[ProjectDefinition]) -> list[dict[str, Any]]:
    """验证轮次序列是否符合规则

    Returns:
        违规项列表
    """
    violations = []

    # 规则1: 不得连续两轮同一类型的简单 CRUD
    for i in range(len(rounds) - 1):
        current = rounds[i]
        next_round = rounds[i + 1]
        if (
            current.category == ProjectCategory.CRUD
            and next_round.category == ProjectCategory.CRUD
            and current.complexity_level <= 2
            and next_round.complexity_level <= 2
        ):
            violations.append(
                {
                    "rule": "no_consecutive_simple_crud",
                    "round": i + 1,
                    "message": f"轮次 {i + 1} 和 {i + 2} 都是简单 CRUD 项目",
                }
            )

    # 规则2: 检查复杂度门槛
    for i, project in enumerate(rounds):
        enhancements_count = len(project.enhancements)
        if enhancements_count < 2:
            violations.append(
                {
                    "rule": "min_enhancements",
                    "round": i + 1,
                    "message": f"{project.name} 只有 {enhancements_count} 个增强特性，需要至少 2 个",
                }
            )

    return violations


# ═══════════════════════════════════════════════════════════════════════════════
# 项目复杂度等级基线（用于压测质量门禁）
# ═══════════════════════════════════════════════════════════════════════════════

COMPLEXITY_BASELINES = {
    "simple": {
        "min_lines": 50,
        "min_modules": 1,
        "min_test_files": 0,
        "min_config_files": 1,
    },
    "medium": {
        "min_lines": 200,
        "min_modules": 2,
        "min_test_files": 1,
        "min_config_files": 2,
    },
    "complex": {
        "min_lines": 500,
        "min_modules": 3,
        "min_test_files": 2,
        "min_config_files": 3,
    },
}


def get_complexity_baseline(complexity_level: str) -> dict:
    """获取复杂度基线

    Args:
        complexity_level: 复杂度等级 ("simple", "medium", "complex")

    Returns:
        复杂度基线配置字典
    """
    return COMPLEXITY_BASELINES.get(complexity_level, COMPLEXITY_BASELINES["simple"])

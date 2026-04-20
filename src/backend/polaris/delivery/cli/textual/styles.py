"""Textual TUI 样式定义 - Claude 风格主题

基于 Catppuccin Mocha/Latte 配色方案，支持明/暗主题切换。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# =============================================================================
# 主题枚举
# =============================================================================


class ThemeMode(Enum):
    """主题模式枚举"""

    DARK = "dark"
    LIGHT = "light"
    SYSTEM = "system"  # 跟随系统


# =============================================================================
# Catppuccin Mocha 配色方案 (暗色主题)
# =============================================================================


class CatppuccinMocha:
    """Catppuccin Mocha 主题颜色定义"""

    # 基础颜色
    BASE = "#1e1e2e"  # 极深哑光黑/深灰 - 主背景
    MANTLE = "#181825"  # 更深背景 - 代码块背景
    CRUST = "#11111b"  # 最深背景 - 边框、分割

    # 文本颜色
    TEXT = "#cdd6f4"  # 柔和白色/浅灰 - 主文本
    SUBTEXT0 = "#a6adc8"  # 次级文本
    SUBTEXT1 = "#bac2de"  # 辅助文本
    OVERLAY0 = "#6c7086"  # 淡化文本
    OVERLAY1 = "#7f849c"  # 提示文本
    OVERLAY2 = "#9399b2"  # 注释文本

    # 强调色 - Claude 深蓝/紫色系
    BLUE = "#89b4fa"  # 主蓝色 - User 消息
    LAVENDER = "#b4befe"  # 薰衣草紫
    MAUVE = "#cba6f7"  # 紫色 - Agent 消息
    PINK = "#f5c2e7"  # 粉色
    FLAMINGO = "#f2cdcd"  # 火烈鸟粉

    # 功能色
    GREEN = "#a6e3a1"  # 成功/完成
    YELLOW = "#f9e2af"  # 警告/思考
    RED = "#f38ba8"  # 错误
    PEACH = "#fab387"  # 提示

    # 表面色
    SURFACE0 = "#313244"  # User 消息面板背景
    SURFACE1 = "#45475a"  # 边框/分隔
    SURFACE2 = "#585b70"  # 悬停状态


# =============================================================================
# Catppuccin Latte 配色方案 (亮色主题)
# =============================================================================


class CatppuccinLatte:
    """Catppuccin Latte 主题颜色定义 - 亮色主题"""

    # 基础颜色
    BASE = "#eff1f5"  # 极浅灰白 - 主背景
    MANTLE = "#e6e9ef"  # 更浅背景 - 代码块背景
    CRUST = "#dce0e8"  # 最浅背景 - 边框、分割

    # 文本颜色
    TEXT = "#4c4f69"  # 深紫灰 - 主文本
    SUBTEXT0 = "#6c6f85"  # 次级文本
    SUBTEXT1 = "#7c7f93"  # 辅助文本
    OVERLAY0 = "#9ca0b0"  # 淡化文本
    OVERLAY1 = "#a6a9c2"  # 提示文本
    OVERLAY2 = "#b7b9cc"  # 注释文本

    # 强调色 - Claude 深蓝/紫色系
    BLUE = "#1e66f5"  # 主蓝色 - User 消息
    LAVENDER = "#7287fd"  # 薰衣草紫
    MAUVE = "#8839ef"  # 紫色 - Agent 消息
    PINK = "#ea76cb"  # 粉色
    FLAMINGO = "#f2cdcd"  # 火烈鸟粉

    # 功能色
    GREEN = "#40a02b"  # 成功/完成
    YELLOW = "#df8e1d"  # 警告/思考
    RED = "#d20f39"  # 错误
    PEACH = "#fe640b"  # 提示

    # 表面色
    SURFACE0 = "#ccd0da"  # User 消息面板背景
    SURFACE1 = "#bcc0cc"  # 边框/分隔
    SURFACE2 = "#acb0be"  # 悬停状态


@dataclass
class ThemeColors:
    """Claude 风格主题颜色 - 支持明/暗主题"""

    # 基础颜色
    background: str = CatppuccinMocha.BASE  # 主背景
    surface: str = CatppuccinMocha.SURFACE0  # 面板背景
    surface_dark: str = CatppuccinMocha.MANTLE  # 深色表面
    crust: str = CatppuccinMocha.CRUST  # 最深背景

    # 文本颜色
    text: str = CatppuccinMocha.TEXT  # 主文本
    text_muted: str = CatppuccinMocha.SUBTEXT0  # 淡化文本
    text_secondary: str = CatppuccinMocha.OVERLAY2  # 次级文本

    # 边框和分隔
    border: str = CatppuccinMocha.SURFACE1  # 主边框
    border_subtle: str = CatppuccinMocha.OVERLAY0  # 细边框
    divider: str = CatppuccinMocha.SURFACE1  # 分隔线

    # 强调色
    primary: str = CatppuccinMocha.MAUVE  # 主强调色 - 紫色
    accent_blue: str = CatppuccinMocha.BLUE  # 蓝色强调
    accent_mauve: str = CatppuccinMocha.MAUVE  # 紫色强调
    accent_green: str = CatppuccinMocha.GREEN  # 绿色强调
    accent_yellow: str = CatppuccinMocha.YELLOW  # 黄色强调
    accent_red: str = CatppuccinMocha.RED  # 红色强调
    accent_peach: str = CatppuccinMocha.PEACH  # 橙色强调

    # 消息类型颜色
    user_bg: str = CatppuccinMocha.SURFACE0  # User 消息背景
    user_accent: str = CatppuccinMocha.BLUE  # User 消息强调色
    agent_bg: str = CatppuccinMocha.BASE  # Agent 消息背景
    agent_accent: str = CatppuccinMocha.MAUVE  # Agent 消息强调色
    system_bg: str = CatppuccinMocha.CRUST  # 系统消息背景
    thinking: str = CatppuccinMocha.YELLOW  # 思考过程
    tool_call: str = CatppuccinMocha.BLUE  # 工具调用
    tool_result: str = CatppuccinMocha.GREEN  # 工具结果
    debug: str = CatppuccinMocha.OVERLAY0  # Debug 信息
    error: str = CatppuccinMocha.RED  # 错误
    success: str = CatppuccinMocha.GREEN  # 成功

    # 滚动条
    scrollbar: str = CatppuccinMocha.OVERLAY0  # 滚动条
    scrollbar_hover: str = CatppuccinMocha.OVERLAY2  # 滚动条悬停

    # 代码块
    code_bg: str = CatppuccinMocha.MANTLE  # 代码块背景
    code_border: str = CatppuccinMocha.SURFACE1  # 代码块边框

    # 进度条
    progress_bg: str = CatppuccinMocha.SURFACE0  # 进度条背景
    progress_fill: str = CatppuccinMocha.MAUVE  # 进度条填充

    # 输入框
    input_bg: str = CatppuccinMocha.BASE  # 输入框背景
    input_border: str = CatppuccinMocha.SURFACE1  # 输入框边框
    input_focus: str = CatppuccinMocha.MAUVE  # 输入框聚焦

    # 按钮
    button_bg: str = CatppuccinMocha.MAUVE  # 按钮背景
    button_hover: str = CatppuccinMocha.PINK  # 按钮悬停
    button_text: str = CatppuccinMocha.CRUST  # 按钮文字

    # 状态指示器
    status_connected: str = CatppuccinMocha.GREEN  # 已连接
    status_processing: str = CatppuccinMocha.YELLOW  # 处理中
    status_error: str = CatppuccinMocha.RED  # 错误
    status_idle: str = CatppuccinMocha.OVERLAY0  # 空闲

    @classmethod
    def from_dark(cls) -> ThemeColors:
        """从暗色主题创建"""
        return cls(
            background=CatppuccinMocha.BASE,
            surface=CatppuccinMocha.SURFACE0,
            surface_dark=CatppuccinMocha.MANTLE,
            crust=CatppuccinMocha.CRUST,
            text=CatppuccinMocha.TEXT,
            text_muted=CatppuccinMocha.SUBTEXT0,
            text_secondary=CatppuccinMocha.OVERLAY2,
            border=CatppuccinMocha.SURFACE1,
            border_subtle=CatppuccinMocha.OVERLAY0,
            divider=CatppuccinMocha.SURFACE1,
            primary=CatppuccinMocha.MAUVE,
            accent_blue=CatppuccinMocha.BLUE,
            accent_mauve=CatppuccinMocha.MAUVE,
            accent_green=CatppuccinMocha.GREEN,
            accent_yellow=CatppuccinMocha.YELLOW,
            accent_red=CatppuccinMocha.RED,
            accent_peach=CatppuccinMocha.PEACH,
            user_bg=CatppuccinMocha.SURFACE0,
            user_accent=CatppuccinMocha.BLUE,
            agent_bg=CatppuccinMocha.BASE,
            agent_accent=CatppuccinMocha.MAUVE,
            system_bg=CatppuccinMocha.CRUST,
            thinking=CatppuccinMocha.YELLOW,
            tool_call=CatppuccinMocha.BLUE,
            tool_result=CatppuccinMocha.GREEN,
            debug=CatppuccinMocha.OVERLAY0,
            error=CatppuccinMocha.RED,
            success=CatppuccinMocha.GREEN,
            scrollbar=CatppuccinMocha.OVERLAY0,
            scrollbar_hover=CatppuccinMocha.OVERLAY2,
            code_bg=CatppuccinMocha.MANTLE,
            code_border=CatppuccinMocha.SURFACE1,
            progress_bg=CatppuccinMocha.SURFACE0,
            progress_fill=CatppuccinMocha.MAUVE,
            input_bg=CatppuccinMocha.BASE,
            input_border=CatppuccinMocha.SURFACE1,
            input_focus=CatppuccinMocha.MAUVE,
            button_bg=CatppuccinMocha.MAUVE,
            button_hover=CatppuccinMocha.PINK,
            button_text=CatppuccinMocha.CRUST,
            status_connected=CatppuccinMocha.GREEN,
            status_processing=CatppuccinMocha.YELLOW,
            status_error=CatppuccinMocha.RED,
            status_idle=CatppuccinMocha.OVERLAY0,
        )

    @classmethod
    def from_light(cls) -> ThemeColors:
        """从亮色主题创建"""
        return cls(
            background=CatppuccinLatte.BASE,
            surface=CatppuccinLatte.SURFACE0,
            surface_dark=CatppuccinLatte.MANTLE,
            crust=CatppuccinLatte.CRUST,
            text=CatppuccinLatte.TEXT,
            text_muted=CatppuccinLatte.SUBTEXT0,
            text_secondary=CatppuccinLatte.OVERLAY2,
            border=CatppuccinLatte.SURFACE1,
            border_subtle=CatppuccinLatte.OVERLAY0,
            divider=CatppuccinLatte.SURFACE1,
            primary=CatppuccinLatte.MAUVE,
            accent_blue=CatppuccinLatte.BLUE,
            accent_mauve=CatppuccinLatte.MAUVE,
            accent_green=CatppuccinLatte.GREEN,
            accent_yellow=CatppuccinLatte.YELLOW,
            accent_red=CatppuccinLatte.RED,
            accent_peach=CatppuccinLatte.PEACH,
            user_bg=CatppuccinLatte.SURFACE0,
            user_accent=CatppuccinLatte.BLUE,
            agent_bg=CatppuccinLatte.BASE,
            agent_accent=CatppuccinLatte.MAUVE,
            system_bg=CatppuccinLatte.CRUST,
            thinking=CatppuccinLatte.YELLOW,
            tool_call=CatppuccinLatte.BLUE,
            tool_result=CatppuccinLatte.GREEN,
            debug=CatppuccinLatte.OVERLAY0,
            error=CatppuccinLatte.RED,
            success=CatppuccinLatte.GREEN,
            scrollbar=CatppuccinLatte.OVERLAY0,
            scrollbar_hover=CatppuccinLatte.OVERLAY2,
            code_bg=CatppuccinLatte.MANTLE,
            code_border=CatppuccinLatte.SURFACE1,
            progress_bg=CatppuccinLatte.SURFACE0,
            progress_fill=CatppuccinLatte.MAUVE,
            input_bg=CatppuccinLatte.BASE,
            input_border=CatppuccinLatte.SURFACE1,
            input_focus=CatppuccinLatte.MAUVE,
            button_bg=CatppuccinLatte.MAUVE,
            button_hover=CatppuccinLatte.PINK,
            button_text=CatppuccinLatte.BASE,
            status_connected=CatppuccinLatte.GREEN,
            status_processing=CatppuccinLatte.YELLOW,
            status_error=CatppuccinLatte.RED,
            status_idle=CatppuccinLatte.OVERLAY0,
        )


# =============================================================================
# 主题管理器
# =============================================================================


class ThemeManager:
    """主题管理器 - 支持明/暗主题切换和持久化"""

    _instance: ThemeManager | None = None

    def __init__(self) -> None:
        self._current_mode: ThemeMode = ThemeMode.DARK
        self._dark_colors = ThemeColors.from_dark()
        self._light_colors = ThemeColors.from_light()
        self._preferences_path: str | None = None

    @classmethod
    def get_instance(cls) -> ThemeManager:
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_testing(cls) -> None:
        """重置单例用于测试隔离"""
        cls._instance = None

    @property
    def current_mode(self) -> ThemeMode:
        """获取当前主题模式"""
        return self._current_mode

    @property
    def colors(self) -> ThemeColors:
        """获取当前主题颜色"""
        if self._current_mode == ThemeMode.LIGHT:
            return self._light_colors
        return self._dark_colors

    @property
    def is_dark(self) -> bool:
        """是否暗色主题"""
        return self._current_mode == ThemeMode.DARK

    @property
    def is_light(self) -> bool:
        """是否亮色主题"""
        return self._current_mode == ThemeMode.LIGHT

    def set_mode(self, mode: ThemeMode) -> None:
        """设置主题模式"""
        self._current_mode = mode
        self._save_preferences()

    def toggle(self) -> ThemeMode:
        """切换主题"""
        if self._current_mode == ThemeMode.DARK:
            self._current_mode = ThemeMode.LIGHT
        else:
            self._current_mode = ThemeMode.DARK
        self._save_preferences()
        return self._current_mode

    def load_preferences(self, path: str) -> None:
        """加载偏好设置"""
        import json

        self._preferences_path = path
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    prefs = json.load(f)
                    mode_str = prefs.get("theme_mode", "dark")
                    try:
                        self._current_mode = ThemeMode(mode_str)
                    except ValueError:
                        self._current_mode = ThemeMode.DARK
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to load preferences from %s: %s", path, e)

    def _save_preferences(self) -> None:
        """保存偏好设置"""
        import json

        if self._preferences_path:
            try:
                os.makedirs(os.path.dirname(self._preferences_path), exist_ok=True)
                with open(self._preferences_path, "w", encoding="utf-8") as f:
                    json.dump({"theme_mode": self._current_mode.value}, f)
            except (RuntimeError, ValueError) as e:
                logger.debug("Failed to save preferences to %s: %s", self._preferences_path, e)

    def generate_css_variables(self) -> str:
        """生成 CSS 变量"""
        c = self.colors
        return f"""
$catppuccin-base: {c.background};
$catppuccin-mantle: {c.surface_dark};
$catppuccin-crust: {c.crust};

$catppuccin-text: {c.text};
$catppuccin-subtext0: {c.text_muted};
$catppuccin-subtext1: {c.text_secondary};
$catppuccin-overlay0: {c.border_subtle};
$catppuccin-overlay1: {c.border_subtle};
$catppuccin-overlay2: {c.text_secondary};

$catppuccin-blue: {c.accent_blue};
$catppuccin-lavender: {c.primary};
$catppuccin-mauve: {c.accent_mauve};
$catppuccin-pink: {c.button_hover};
$catppuccin-flamingo: {c.accent_peach};

$catppuccin-green: {c.accent_green};
$catppuccin-yellow: {c.accent_yellow};
$catppuccin-red: {c.accent_red};
$catppuccin-peach: {c.accent_peach};

$catppuccin-surface0: {c.surface};
$catppuccin-surface1: {c.border};
$catppuccin-surface2: {c.surface};
"""


# 全局主题管理器实例
_theme_manager = ThemeManager.get_instance()


# Claude 风格主题
CLAUDE_THEME = ThemeColors.from_dark()

# Catppuccin Mocha CSS 变量映射
CATPPUCCIN_CSS = """
/* Catppuccin Mocha 配色变量 */
$catppuccin-base: #1e1e2e;
$catppuccin-mantle: #181825;
$catppuccin-crust: #11111b;

$catppuccin-text: #cdd6f4;
$catppuccin-subtext0: #a6adc8;
$catppuccin-subtext1: #bac2de;
$catppuccin-overlay0: #6c7086;
$catppuccin-overlay1: #7f849c;
$catppuccin-overlay2: #9399b2;

$catppuccin-blue: #89b4fa;
$catppuccin-lavender: #b4befe;
$catppuccin-mauve: #cba6f7;
$catppuccin-pink: #f5c2e7;
$catppuccin-flamingo: #f2cdcd;

$catppuccin-green: #a6e3a1;
$catppuccin-yellow: #f9e2af;
$catppuccin-red: #f38ba8;
$catppuccin-peach: #fab387;

$catppuccin-surface0: #313244;
$catppuccin-surface1: #45475a;
$catppuccin-surface2: #585b70;
"""

# 主 CSS 样式
CLAUDE_CONSOLE_CSS = """
/* 全局样式 */
Screen {
    background: $catppuccin-base;
    color: $catppuccin-text;
}

/* 静态头部 */
#header {
    height: 3;
    background: $catppuccin-crust;
    color: $catppuccin-text;
    border-bottom: solid $catppuccin-surface1;
    padding: 0 2;
}

#header-title {
    color: $catppuccin-mauve;
    text-style: bold;
    content-align: left middle;
}

#header-status {
    color: $catppuccin-green;
    content-align: center middle;
}

#header-stats {
    color: $catppuccin-subtext0;
    content-align: right middle;
}

/* 主内容区域 */
#main-content {
    height: 1fr;
    layout: grid;
    grid-size: 2;
    grid-columns: 1fr 35;
    grid-rows: 1fr;
}

/* 对话历史窗口 */
#conversation-area {
    height: 1fr;
    background: $catppuccin-base;
    border-right: solid $catppuccin-surface1;
}

#messages-container {
    height: 1fr;
    padding: 1 2;
    overflow-y: scroll;
}

/* 侧边栏 */
#sidebar {
    height: 1fr;
    background: $catppuccin-crust;
    width: 35;
    padding: 1;
}

#sidebar-title {
    color: $catppuccin-mauve;
    text-style: bold;
    padding: 0 0 1 0;
}

.sidebar-section-title {
    color: $catppuccin-blue;
    text-style: bold;
    padding: 1 0 0 0;
}

.sidebar-item {
    color: $catppuccin-text;
    padding: 0 0 0 1;
}

.sidebar-item.tool-name {
    color: $catppuccin-yellow;
}

#sidebar-content {
    height: 1fr;
    overflow-y: scroll;
}

#sidebar-content {
    height: 1fr;
    overflow-y: scroll;
}

/* 可折叠消息组件 */
.message-panel {
    width: 100%;
    height: auto;
    margin: 1 0;
    padding: 0;
}

.message-panel-user {
    background: $catppuccin-surface0;
    border-left: solid $catppuccin-blue;
}

.message-panel-agent {
    background: $catppuccin-base;
    border-left: solid $catppuccin-mauve;
}

.message-panel-system {
    background: $catppuccin-crust;
    border-left: solid $catppuccin-overlay0;
}

.message-panel-tool {
    background: $catppuccin-mantle;
    border-left: solid $catppuccin-blue;
}

.message-header {
    height: 3;
    padding: 0 2;
    content-align: left middle;
}

.message-header.clickable:hover {
    background: $catppuccin-surface1;
}

.message-content-wrapper {
    height: auto;
}

.message-header-user {
    color: $catppuccin-blue;
    text-style: bold;
}

.message-header-agent {
    color: $catppuccin-mauve;
    text-style: bold;
}

.message-header-system {
    color: $catppuccin-overlay2;
}

.message-header-tool {
    color: $catppuccin-blue;
}

.message-content {
    padding: 0 2 1 2;
    color: $catppuccin-text;
}

.message-content-collapsed {
    display: none;
}

/* 折叠指示器 */
.toggle-button {
    color: $catppuccin-overlay1;
    text-style: bold;
    content-align: center middle;
    width: 3;
}

.toggle-button:hover {
    color: $catppuccin-mauve;
    background: $catppuccin-surface1;
}

/* 代码块 */
.code-block {
    background: $catppuccin-mantle;
    border: solid $catppuccin-surface1;
    padding: 1;
    margin: 1 0;
}

.code-block-header {
    height: 1;
    background: $catppuccin-surface1;
    color: $catppuccin-subtext0;
    padding: 0 1;
    content-align: left middle;
}

.code-block-content {
    padding: 1;
    color: $catppuccin-text;
}

/* 底部输入区域 */
#input-section {
    height: 5;
    background: #11111b;
    border-top: solid #45475a;
    dock: bottom;
}

/* 拖拽手柄 */
#resize-handle {
    height: 1;
    background: #45475a;
    content-align: center middle;
    color: #6c7086;
}

#resize-handle:hover {
    background: #585b70;
    color: #cba6f7;
}

#resize-handle.dragging {
    background: #cba6f7;
}

/* 输入框区域 */
#input-area {
    height: 4;
    padding: 0 1;
}

#input-textarea {
    width: 1fr;
    height: 100%;
    background: #1e1e2e;
    border: solid #45475a;
    color: #cdd6f4;
}

#input-textarea:focus {
    border: solid $catppuccin-mauve;
}

/* 发送按钮 */
#send-button {
    width: 8;
    height: 3;
    background: $catppuccin-mauve;
    color: $catppuccin-crust;
    content-align: center middle;
    text-style: bold;
    margin: 0 0 0 1;
}

#send-button:hover {
    background: $catppuccin-pink;
}

#send-button:disabled {
    background: $catppuccin-surface1;
    color: $catppuccin-overlay0;
}

/* 快捷键提示 */
#input-hints {
    height: 1;
    padding: 0 2;
    color: $catppuccin-overlay1;
    content-align: right middle;
}

/* 状态栏 */
#status-bar {
    height: 1;
    background: $catppuccin-crust;
    color: $catppuccin-overlay1;
    padding: 0 2;
    border-top: solid $catppuccin-surface1;
}

#status-bar-left {
    content-align: left middle;
}

#status-bar-right {
    content-align: right middle;
}

/* 滚动条样式 */
Scrollbar {
    background: transparent;
    color: $catppuccin-overlay0;
}

Scrollbar:hover {
    color: $catppuccin-overlay2;
}

/* 工具调用样式 */
.tool-call-panel {
    background: $catppuccin-mantle;
    border: solid $catppuccin-blue;
    margin: 1 0;
    padding: 1;
}

.tool-call-header {
    color: $catppuccin-blue;
    text-style: bold;
}

.tool-call-content {
    color: $catppuccin-text;
    margin-top: 1;
}

/* Debug 面板 */
.debug-panel {
    background: $catppuccin-crust;
    border-left: solid $catppuccin-overlay0;
    margin: 1 0;
}

.debug-header {
    color: $catppuccin-overlay2;
    padding: 0 2;
}

.debug-content {
    color: $catppuccin-subtext0;
    padding: 0 2 1 4;
}

/* 加载动画 */
.loading-indicator {
    color: $catppuccin-mauve;
    text-style: bold;
}

/* 分隔线 */
.divider {
    background: $catppuccin-surface1;
    height: 1;
}

/* 工具提示 */
.tooltip {
    background: $catppuccin-surface0;
    border: solid $catppuccin-surface1;
    color: $catppuccin-text;
    padding: 1;
}

/* ========== Enhanced Styles ========== */

/* 头部增强样式 */
#header-time {
    color: $catppuccin-overlay1;
    content-align: center middle;
    padding: 0 1;
}

#header-theme {
    color: $catppuccin-yellow;
    content-align: right middle;
    padding: 0 0 0 1;
}

/* 状态指示器颜色 */
.status-connected {
    color: $catppuccin-green;
}

.status-processing {
    color: $catppuccin-yellow;
}

.status-error {
    color: $catppuccin-red;
}

.status-idle {
    color: $catppuccin-overlay0;
}

/* 进度条 */
.progress-container {
    width: 100%;
    height: 2;
    background: $catppuccin-surface0;
}

.progress-bar {
    height: 100%;
    background: $catppuccin-mauve;
}

/* 流式输出动画 */
.streaming-indicator {
    color: $catppuccin-mauve;
    text-style: bold;
}

/* 命令面板 */
.command-palette {
    background: $catppuccin-crust;
    border: solid $catppuccin-mauve;
    width: 60%;
    height: auto;
}

/* 搜索高亮 */
.search-highlight {
    background: $catppuccin-yellow;
    color: $catppuccin-crust;
}

/* 消息计数 */
.message-count {
    color: $catppuccin-overlay1;
    font-size: 80%;
}

/* 时间戳 */
.timestamp {
    color: $catppuccin-overlay0;
    font-size: 80%;
}

/* 快捷键标签 */
.kbd {
    background: $catppuccin-surface1;
    border: solid $catppuccin-surface1;
    border-radius: 3;
    padding: 0 4;
    color: $catppuccin-text;
}

/* 聚焦样式 */
.focus-ring:focus {
    border: solid $catppuccin-mauve;
}

/* 悬停效果增强 */
.hover-highlight:hover {
    background: $catppuccin-surface0;
}

/* 平滑过渡 */
.transition-all {
    transition: all 0.2s ease;
}
"""

# 完整的 CSS 组合
CONSOLE_CSS = CATPPUCCIN_CSS + "\n" + CLAUDE_CONSOLE_CSS


def get_console_css(theme: ThemeMode | None = None) -> str:
    """获取控制台 CSS 样式

    Args:
        theme: 主题模式，默认使用 ThemeManager 当前设置

    Returns:
        CSS 样式字符串
    """
    manager = ThemeManager.get_instance()
    if theme is not None:
        current_mode = manager.current_mode
        # 临时切换以生成 CSS
        manager._current_mode = theme
        css_vars = manager.generate_css_variables()
        manager._current_mode = current_mode
    else:
        css_vars = manager.generate_css_variables()

    return css_vars + "\n" + CLAUDE_CONSOLE_CSS


def get_theme_colors() -> ThemeColors:
    """获取主题颜色配置"""
    return ThemeManager.get_instance().colors


def get_theme_manager() -> ThemeManager:
    """获取主题管理器"""
    return ThemeManager.get_instance()


def reload_css() -> str:
    """重新加载 CSS（用于主题切换）"""
    return get_console_css()

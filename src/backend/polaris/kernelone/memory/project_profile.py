"""Project Profile - 项目画像引擎

基于计划中"项目画像：技术栈偏好、协作习惯、决策模式沉淀"：

1. 技术栈偏好画像
   - 记录：语言版本、包管理器、框架偏好、配置文件模式

2. 协作习惯画像
   - 记录：代码提交频率、PR 大小偏好、测试覆盖率偏好
   - 记录：常用命令、构建工具偏好

3. 决策模式画像
   - 记录：架构决策历史
   - 记录：技术选型偏好
   - 记录：错误处理模式
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Schema 定义
# ═══════════════════════════════════════════════════════════════════════════


class TechStackProfile(BaseModel):
    """技术栈偏好画像"""

    # 编程语言
    languages: dict[str, bool] = Field(default_factory=dict)  # {"python": True, "node": False}
    primary_language: str | None = None

    # 包管理器
    package_manager: str | None = None  # "npm", "pnpm", "yarn", "pip", "poetry", "cargo"

    # 框架偏好
    frameworks: list[str] = Field(default_factory=list)  # ["react", "fastapi", "express"]

    # 配置文件模式
    config_files: dict[str, bool] = Field(default_factory=dict)  # {"tsconfig.json": True}

    # 语言版本
    language_versions: dict[str, str] = Field(default_factory=dict)  # {"python": "3.11", "node": "18"}

    # 构建工具
    build_tools: list[str] = Field(default_factory=list)  # ["webpack", "vite", "rollup"]


class CollaborationProfile(BaseModel):
    """协作习惯画像"""

    # 代码提交频率（按文件类型统计）
    commit_frequency: str | None = None  # "high", "medium", "low"

    # 平均 PR 大小
    avg_pr_size: str | None = None  # "small", "medium", "large"

    # 测试覆盖率偏好
    test_coverage_preference: str | None = None  # "strict", "moderate", "minimal"

    # 常用命令
    common_commands: list[str] = Field(default_factory=list)

    # 构建工具偏好
    build_tool_preference: str | None = None

    # 代码审查偏好
    code_review_style: str | None = None  # "strict", "flexible"

    # 文档习惯
    documentation_style: str | None = None  # "inline", "separate", "minimal"


class DecisionPattern(BaseModel):
    """单条决策记录"""

    id: str
    timestamp: datetime
    category: str  # "architecture", "tech_stack", "error_handling", "testing"
    title: str
    decision: str
    rationale: str
    alternatives_considered: list[str] = Field(default_factory=list)
    outcome: str | None = None  # "success", "reverted", "ongoing"


class DecisionProfile(BaseModel):
    """决策模式画像"""

    # 架构决策历史
    architecture_decisions: list[DecisionPattern] = Field(default_factory=list)

    # 技术选型偏好
    tech_preferences: dict[str, str] = Field(default_factory=dict)  # {"database": "postgresql", "framework": "react"}

    # 错误处理模式
    error_handling_style: str | None = None  # "explicit", "silent", "graceful"

    # 代码组织偏好
    code_organization: str | None = None  # "monorepo", "polyrepo", "modular"


class ProjectProfile(BaseModel):
    """完整项目画像"""

    workspace: str

    # 元数据
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    version: str = "1.0.0"

    # 子画像
    tech_stack: TechStackProfile = Field(default_factory=TechStackProfile)
    collaboration: CollaborationProfile = Field(default_factory=CollaborationProfile)
    decisions: DecisionProfile = Field(default_factory=DecisionProfile)

    # 统计信息
    total_detected_files: int = 0
    last_analysis_at: datetime | None = None


# ═══════════════════════════════════════════════════════════════════════════
# 项目画像引擎
# ═══════════════════════════════════════════════════════════════════════════


class ProjectProfileEngine:
    """项目画像引擎"""

    # 配置文件映射到语言/框架
    CONFIG_LANGUAGE_MAP: dict[str, str] = {
        "pyproject.toml": "python",
        "requirements.txt": "python",
        "setup.py": "python",
        "setup.cfg": "python",
        "Pipfile": "python",
        "poetry.lock": "python",
        "package.json": "node",
        "pnpm-lock.yaml": "pnpm",
        "yarn.lock": "yarn",
        "npm-shrinkwrap.json": "npm",
        "go.mod": "go",
        "Cargo.toml": "rust",
        "pom.xml": "java",
        "build.gradle": "java",
        "Gemfile": "ruby",
        "composer.json": "php",
    }

    # 配置文件到框架映射
    CONFIG_FRAMEWORK_MAP: dict[str, list[str]] = {
        "pyproject.toml": ["fastapi", "django", "flask"],
        "requirements.txt": ["fastapi", "django", "flask"],
        "package.json": ["react", "vue", "angular", "express", "next"],
        "go.mod": ["gin", "echo", "fiber"],
        "Cargo.toml": ["actix", "tokio", "warp"],
    }

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.profile: ProjectProfile | None = None

    def detect_tech_stack(self) -> TechStackProfile:
        """检测技术栈"""
        tech = TechStackProfile()
        if not self.workspace or not os.path.isdir(self.workspace):
            return tech

        # 1. 检测语言和配置文件
        config_files: set[str] = set()

        try:
            entries = os.listdir(self.workspace)
        except (PermissionError, OSError):
            return tech

        for entry in entries:
            if not os.path.isfile(os.path.join(self.workspace, entry)):
                continue

            # 配置文件检测
            if entry in self.CONFIG_LANGUAGE_MAP:
                config_files.add(entry)
                lang = self.CONFIG_LANGUAGE_MAP[entry]
                tech.languages[lang] = True

                # 检测包管理器
                if lang == "node":
                    if entry == "pnpm-lock.yaml":
                        tech.package_manager = "pnpm"
                    elif entry == "yarn.lock":
                        tech.package_manager = "yarn"
                    else:
                        tech.package_manager = "npm"
                elif lang == "python":
                    if os.path.exists(os.path.join(self.workspace, "poetry.lock")):
                        tech.package_manager = "poetry"
                    else:
                        tech.package_manager = "pip"
                elif lang == "rust":
                    tech.package_manager = "cargo"
                elif lang == "go":
                    tech.package_manager = "go"

            # 配置文件映射
            tech.config_files[entry] = True

            # 框架检测
            if entry in self.CONFIG_FRAMEWORK_MAP:
                possible_frameworks = self.CONFIG_FRAMEWORK_MAP[entry]
                # 简单检测：检查相关目录或更多配置文件
                for fw in possible_frameworks:
                    if self._has_framework_indicator(fw) and fw not in tech.frameworks:
                        tech.frameworks.append(fw)

        # 2. 检测主语言（基于配置文件数量）
        if tech.languages:
            primary = max(tech.languages.items(), key=lambda x: 1 if x[1] else 0)
            if primary[1]:
                tech.primary_language = primary[0]

        # 3. 检测构建工具
        tech.build_tools = self._detect_build_tools(tech)

        # 4. 检测语言版本
        tech.language_versions = self._detect_language_versions()

        return tech

    def _has_framework_indicator(self, framework: str) -> bool:
        """检测框架标识"""
        fw_indicators = {
            "react": ["src/App.tsx", "src/App.jsx", "src/components", "tsconfig.json"],
            "vue": ["src/App.vue", "src/components", "vite.config.ts"],
            "angular": ["angular.json", "src/app/app.component.ts"],
            "express": ["src/index.js", "src/app.js", "routes/"],
            "fastapi": ["src/fastapi_entrypoint.py", "src/api/", "alembic.ini"],
            "django": ["manage.py", "settings.py", "urls.py"],
            "flask": ["app.py", "src/app.py"],
            "gin": ["main.go", "go.sum"],
            "actix": ["src/main.rs", "Cargo.toml"],
            "tokio": ["src/main.rs", "Cargo.toml"],
        }

        indicators = fw_indicators.get(framework.lower(), [])
        for indicator in indicators:
            path = os.path.join(self.workspace, indicator)
            if os.path.exists(path):
                return True
        return False

    def _detect_build_tools(self, tech: TechStackProfile) -> list[str]:
        """检测构建工具"""
        build_tools: list[str] = []

        # 前端构建工具
        if tech.languages.get("node"):
            if os.path.exists(os.path.join(self.workspace, "vite.config.ts")):
                build_tools.append("vite")
            elif os.path.exists(os.path.join(self.workspace, "webpack.config.js")):
                build_tools.append("webpack")
            elif os.path.exists(os.path.join(self.workspace, "rollup.config.js")):
                build_tools.append("rollup")
            elif os.path.exists(os.path.join(self.workspace, "next.config.js")):
                build_tools.append("next")

        # Python 构建工具
        if tech.languages.get("python"):
            if os.path.exists(os.path.join(self.workspace, "poetry.lock")):
                build_tools.append("poetry")
            elif os.path.exists(os.path.join(self.workspace, "setup.py")) or os.path.exists(
                os.path.join(self.workspace, "pyproject.toml")
            ):
                build_tools.append("setuptools")

        # Go 构建工具
        if tech.languages.get("go"):
            build_tools.append("go")

        # Rust 构建工具
        if tech.languages.get("rust"):
            build_tools.append("cargo")

        return build_tools

    def _detect_language_versions(self) -> dict[str, str]:
        """检测语言版本"""
        versions: dict[str, str] = {}

        # Python 版本
        pyproject = os.path.join(self.workspace, "pyproject.toml")
        if os.path.exists(pyproject):
            try:
                with open(pyproject, encoding="utf-8") as f:
                    content = f.read()
                    match = re.search(r'python\s*=\s*["\']?(\d+\.\d+)', content)
                    if match:
                        versions["python"] = match.group(1)
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Project profile: unable to parse Python version from %s: %s", pyproject, exc)

        # Node 版本
        package_json = os.path.join(self.workspace, "package.json")
        if os.path.exists(package_json):
            try:
                with open(package_json, encoding="utf-8") as f:
                    data = json.load(f)
                    engines = data.get("engines", {})
                    if "node" in engines:
                        versions["node"] = engines["node"]
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.debug("Project profile: unable to parse Node version from %s: %s", package_json, exc)

        # Go 版本
        go_mod = os.path.join(self.workspace, "go.mod")
        if os.path.exists(go_mod):
            try:
                with open(go_mod, encoding="utf-8") as f:
                    content = f.read()
                    match = re.search(r"go\s+(\d+\.\d+)", content)
                    if match:
                        versions["go"] = match.group(1)
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Project profile: unable to parse Go version from %s: %s", go_mod, exc)

        return versions

    def detect_collaboration(self) -> CollaborationProfile:
        """检测协作习惯"""
        collab = CollaborationProfile()

        if not self.workspace or not os.path.isdir(self.workspace):
            return collab

        # 1. 检测 Git 仓库
        git_dir = os.path.join(self.workspace, ".git")
        if not os.path.exists(git_dir):
            return collab

        # 2. 分析提交历史
        try:
            result = subprocess.run(
                ["git", "log", "--format=%ai", "-n", "30"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                dates = []
                for line in result.stdout.strip().split("\n"):
                    if line:
                        try:
                            dt = datetime.fromisoformat(line.strip())
                            dates.append(dt)
                        except ValueError as exc:
                            logger.debug("Project profile: skipping unparseable git timestamp '%s': %s", line, exc)

                if len(dates) >= 2:
                    # 计算提交频率
                    time_span = (dates[0] - dates[-1]).days
                    if time_span > 0:
                        commits_per_day = len(dates) / time_span
                        if commits_per_day >= 1:
                            collab.commit_frequency = "high"
                        elif commits_per_day >= 0.2:
                            collab.commit_frequency = "medium"
                        else:
                            collab.commit_frequency = "low"
        except (subprocess.SubprocessError, FileNotFoundError) as exc:
            logger.debug("Project profile: git history analysis unavailable: %s", exc)

        # 3. 检测测试框架
        collab.test_coverage_preference = self._detect_test_preference()

        # 4. 检测常用命令
        collab.common_commands = self._detect_common_commands()

        # 5. 检测文档风格
        collab.documentation_style = self._detect_documentation_style()

        return collab

    def _detect_test_preference(self) -> str | None:
        """检测测试偏好"""
        test_indicators = {
            "strict": ["pytest.ini", "setup.cfg", ".coveragerc"],
            "moderate": ["jest.config.js", "vitest.config.ts"],
            "minimal": [],
        }

        for style, files in test_indicators.items():
            for f in files:
                if os.path.exists(os.path.join(self.workspace, f)):
                    return style

        # 默认基于项目类型推断
        if os.path.exists(os.path.join(self.workspace, "pyproject.toml")):
            return "strict"
        if os.path.exists(os.path.join(self.workspace, "package.json")):
            return "moderate"

        return None

    def _detect_common_commands(self) -> list[str]:
        """检测常用命令"""
        commands: list[str] = []

        # 从 package.json 检测 npm scripts
        package_json = os.path.join(self.workspace, "package.json")
        if os.path.exists(package_json):
            try:
                with open(package_json, encoding="utf-8") as f:
                    data = json.load(f)
                    scripts = data.get("scripts", {})
                    common = ["start", "dev", "build", "test", "lint"]
                    for cmd in common:
                        if cmd in scripts:
                            commands.append(f"npm {cmd}")
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.debug("Project profile: unable to parse npm scripts from %s: %s", package_json, exc)

        # 从 Makefile 检测
        makefile = os.path.join(self.workspace, "Makefile")
        if os.path.exists(makefile):
            try:
                with open(makefile, encoding="utf-8") as f:
                    for line in f:
                        if line.strip() and not line.startswith("#"):
                            match = re.match(r"^([a-zA-Z0-9_-]+):", line)
                            if match:
                                commands.append(f"make {match.group(1)}")
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Project profile: unable to parse Makefile %s: %s", makefile, exc)

        return commands[:10]  # 限制数量

    def _detect_documentation_style(self) -> str | None:
        """检测文档风格"""
        doc_score = {"inline": 0, "separate": 0, "minimal": 0}

        # 统计文档文件
        try:
            for root, _, files in os.walk(self.workspace):
                # 跳过隐藏目录和特定目录
                if "/." in root or "/node_modules" in root or "/__pycache__" in root:
                    continue
                for f in files:
                    if f.endswith(".md"):
                        doc_score["inline"] += 1
                    if f in ["tui_runtime.md", "CONTRIBUTING.md", "ARCHITECTURE.md"]:
                        doc_score["separate"] += 1
        except (PermissionError, OSError) as exc:
            logger.debug("Project profile: documentation scan skipped due to fs error: %s", exc)

        max_score = max(doc_score.values())
        if max_score == 0:
            return "minimal"

        for style, score in doc_score.items():
            if score == max_score:
                return style

        return None

    def detect_decisions(self) -> DecisionProfile:
        """检测决策模式"""
        decisions = DecisionProfile()

        if not self.workspace or not os.path.isdir(self.workspace):
            return decisions

        # 1. 从 ADR 目录检测架构决策
        adr_dir = os.path.join(self.workspace, "docs", "product")
        if os.path.isdir(adr_dir):
            adr_files = []
            try:
                adr_files = [f for f in os.listdir(adr_dir) if f.startswith("adr") and f.endswith(".md")]
            except OSError as exc:
                logger.debug("Project profile: unable to list ADR directory %s: %s", adr_dir, exc)

            for adr_file in adr_files:
                adr_path = os.path.join(adr_dir, adr_file)
                try:
                    with open(adr_path, encoding="utf-8") as f:
                        content = f.read()
                        # 简单解析 ADR
                        decision = self._parse_adr_content(adr_file, content)
                        if decision:
                            decisions.architecture_decisions.append(decision)
                except (OSError, UnicodeDecodeError) as exc:
                    logger.debug("Project profile: unable to parse ADR file %s: %s", adr_path, exc)

        # 2. 检测技术选型偏好
        decisions.tech_preferences = self._detect_tech_preferences()

        # 3. 检测错误处理风格
        decisions.error_handling_style = self._detect_error_handling_style()

        # 4. 检测代码组织方式
        decisions.code_organization = self._detect_code_organization()

        return decisions

    def _parse_adr_content(self, filename: str, content: str) -> DecisionPattern | None:
        """解析 ADR 内容"""
        # 简单解析：提取标题和决策
        title_match = re.search(r"#?\s*(?:ADR-\d+\s+)?(.+)", content)
        title = title_match.group(1).strip() if title_match else filename

        # 提取决策内容
        decision_match = re.search(r"[Dd]ecision[:\s]+(.+?)(?:\n|$)", content)
        decision = decision_match.group(1).strip() if decision_match else "See ADR content"

        rationale_match = re.search(r"[Rr]ationale[:\s]+(.+?)(?:\n|$)", content)
        rationale = rationale_match.group(1).strip() if rationale_match else ""

        return DecisionPattern(
            id=filename.replace(".md", ""),
            timestamp=datetime.now(),
            category="architecture",
            title=title[:100],
            decision=decision[:500],
            rationale=rationale[:500],
        )

    def _detect_tech_preferences(self) -> dict[str, str]:
        """检测技术选型偏好"""
        prefs: dict[str, str] = {}

        # 检测数据库偏好
        db_indicators = {
            "postgresql": ["postgresql", "postgres"],
            "mysql": ["mysql"],
            "mongodb": ["mongodb", "mongoose"],
            "sqlite": ["sqlite"],
            "redis": ["redis"],
        }

        try:
            for root, _, files in os.walk(self.workspace):
                if "/.git" in root or "/node_modules" in root:
                    continue
                for f in files:
                    if f.endswith((".py", ".js", ".ts", ".json", ".yaml", ".yml")):
                        try:
                            path = os.path.join(root, f)
                            with open(path, encoding="utf-8") as file:
                                content = file.read().lower()
                                for db, keywords in db_indicators.items():
                                    if any(kw in content for kw in keywords):
                                        prefs["database"] = db
                                        break
                        except (OSError, UnicodeDecodeError):
                            continue
        except (PermissionError, OSError) as exc:
            logger.debug("Project profile: tech preference scan skipped due to fs error: %s", exc)

        return prefs

    def _detect_error_handling_style(self) -> str | None:
        """检测错误处理风格"""
        # 通过检查代码模式推断
        try:
            for root, _, files in os.walk(self.workspace):
                if "/.git" in root or "/node_modules" in root or "/__pycache__" in root:
                    continue
                for f in files:
                    if f.endswith((".py", ".js", ".ts")):
                        path = os.path.join(root, f)
                        try:
                            with open(path, encoding="utf-8") as file:
                                content = file.read()
                                # 显式错误处理: try-except, throw new Error
                                explicit = (
                                    content.count("try:") + content.count("except") + content.count("throw new Error")
                                )
                                # 静默处理: console.log, pass
                                silent = content.count("console.log(") + content.count("pass\n")

                                if explicit > silent * 2:
                                    return "explicit"
                                elif silent > explicit * 2:
                                    return "silent"
                        except (OSError, UnicodeDecodeError):
                            continue
        except (PermissionError, OSError) as exc:
            logger.debug("Project profile: error-handling style scan skipped due to fs error: %s", exc)

        return None

    def _detect_code_organization(self) -> str | None:
        """检测代码组织方式"""
        # Monorepo indicators
        monorepo_indicators = ["packages/", "apps/", "modules/", "services/"]
        # Polyrepo indicators (multiple package.json at root level of subdirs)

        has_monorepo = False
        try:
            for root, dirs, _ in os.walk(self.workspace):
                if "/.git" in root or "/node_modules" in root:
                    continue
                for d in dirs:
                    if d in monorepo_indicators:
                        has_monorepo = True
                        break
        except (PermissionError, OSError) as exc:
            logger.debug("Project profile: code organization scan skipped due to fs error: %s", exc)

        if has_monorepo:
            return "monorepo"

        # Check for modular structure
        src_dir = os.path.join(self.workspace, "src")
        if os.path.isdir(src_dir):
            try:
                subdirs = os.listdir(src_dir)
                if len(subdirs) > 3:
                    return "modular"
            except OSError as exc:
                logger.debug("Project profile: unable to inspect src directory %s: %s", src_dir, exc)

        return "standard"

    def analyze(self) -> ProjectProfile:
        """完整分析项目画像"""
        profile = ProjectProfile(workspace=self.workspace)

        # 统计文件数
        try:
            count = sum(1 for _ in Path(self.workspace).rglob("*") if _.is_file())
            profile.total_detected_files = count
        except (PermissionError, OSError):
            profile.total_detected_files = 0

        # 检测各维度
        profile.tech_stack = self.detect_tech_stack()
        profile.collaboration = self.detect_collaboration()
        profile.decisions = self.detect_decisions()

        profile.last_analysis_at = datetime.now()
        profile.updated_at = datetime.now()

        self.profile = profile
        return profile

    def save(self, base_dir: str) -> str:
        """保存画像到磁盘"""
        if not self.profile:
            self.analyze()
        assert self.profile is not None  # mypy: ensure non-None after analyze()

        # 使用 brain 目录
        profile_path = os.path.join(base_dir, "workspace", "brain", "project_profile.json")
        os.makedirs(os.path.dirname(profile_path), exist_ok=True)

        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(self.profile.model_dump_json(indent=2))

        return profile_path

    def load(self, base_dir: str) -> ProjectProfile | None:
        """从磁盘加载画像"""
        profile_path = os.path.join(base_dir, "workspace", "brain", "project_profile.json")

        if not os.path.exists(profile_path):
            return None

        try:
            with open(profile_path, encoding="utf-8") as f:
                data = json.load(f)
                self.profile = ProjectProfile(**data)
                return self.profile
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def add_decision(self, decision: DecisionPattern) -> None:
        """添加决策记录"""
        if not self.profile:
            self.analyze()
        assert self.profile is not None  # mypy: ensure non-None after analyze()

        self.profile.decisions.architecture_decisions.append(decision)
        self.profile.updated_at = datetime.now()

    def update_tech_preference(self, key: str, value: str) -> None:
        """更新技术偏好"""
        if not self.profile:
            self.analyze()
        assert self.profile is not None  # mypy: ensure non-None after analyze()

        self.profile.decisions.tech_preferences[key] = value
        self.profile.updated_at = datetime.now()


# ═══════════════════════════════════════════════════════════════════════════
# 全局实例管理
# ═══════════════════════════════════════════════════════════════════════════

_PROJECT_PROFILE_ENGINES: dict[str, ProjectProfileEngine] = {}


def get_project_profile_engine(workspace: str) -> ProjectProfileEngine:
    """获取项目画像引擎实例"""
    if workspace not in _PROJECT_PROFILE_ENGINES:
        _PROJECT_PROFILE_ENGINES[workspace] = ProjectProfileEngine(workspace)
    return _PROJECT_PROFILE_ENGINES[workspace]


def analyze_project_profile(workspace: str, save_to: str | None = None) -> ProjectProfile:
    """便捷函数：分析并返回项目画像"""
    engine = get_project_profile_engine(workspace)
    profile = engine.analyze()

    if save_to:
        engine.save(save_to)

    return profile


def get_or_load_profile(workspace: str, base_dir: str) -> ProjectProfile:
    """获取或加载项目画像"""
    engine = get_project_profile_engine(workspace)

    # 尝试加载
    profile = engine.load(base_dir)
    if profile:
        return profile

    # 分析并保存
    profile = engine.analyze()
    engine.save(base_dir)
    return profile

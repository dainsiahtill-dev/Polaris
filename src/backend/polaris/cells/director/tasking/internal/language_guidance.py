"""Composable prompt-guidance profiles for Director code generation.

This module is intentionally a pure prompt-construction helper. It owns no
runtime state, performs no workspace writes, and does not call LLM providers.

The guidance architecture is layered so it can grow over time without turning
``PromptBuilder`` into a long list of language-specific branches:

1. language best-practice profile: expert identity and idiomatic coding rules
2. framework best-practice profile: stack-specific constraints
3. task-type best-practice profile: write, refactor, review, bugfix, test, etc.
4. file-role best-practice profile: source, test, config, script, schema, style
5. universal production best practices: minimal changes, verifiability, quality

All text operations MUST explicitly use UTF-8 encoding when file I/O is involved.
This module performs no file I/O.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.kernelone.role.language_identity import (
    get_language_professional_identity,
    normalize_language_token,
)


@dataclass(frozen=True)
class LanguageProfile:
    """Language-level expert identity and best practices."""

    code: str
    display_name: str
    identity: str
    standards: tuple[str, ...]
    framework_guidance: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def best_practices(self) -> tuple[str, ...]:
        """Return language best practices using the canonical taxonomy term."""
        return self.standards


@dataclass(frozen=True)
class TaskFocusProfile:
    """Task-type best practices selected from subject, description, and metadata."""

    code: str
    label: str
    guidance: tuple[str, ...]

    @property
    def best_practices(self) -> tuple[str, ...]:
        """Return task-type best practices using the canonical taxonomy term."""
        return self.guidance


@dataclass(frozen=True)
class FileRoleProfile:
    """Best practices for a concrete role a file plays in a codebase."""

    code: str
    label: str
    guidance: tuple[str, ...]

    @property
    def best_practices(self) -> tuple[str, ...]:
        """Return file-role best practices using the canonical taxonomy term."""
        return self.guidance


@dataclass(frozen=True)
class LanguagePromptContext:
    """Structured context used to compose prompt-guidance layers."""

    target_files: tuple[str, ...]
    scope_paths: tuple[str, ...] = ()
    workspace: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    subject: str = ""
    description: str = ""


@dataclass(frozen=True)
class GuidanceSelection:
    """Resolved prompt-guidance axes shared by prompt and execution profiles."""

    language: str
    language_display_name: str
    framework: str
    framework_display_name: str
    task_foci: tuple[str, ...]
    task_focus_labels: tuple[str, ...]
    file_roles: tuple[str, ...]
    file_role_labels: tuple[str, ...]
    role_identity: str


_EXT_TO_LANG: dict[str, str] = {
    ".c": "c",
    ".cc": "cpp",
    ".cjs": "javascript",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".mjs": "javascript",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "typescript",
}

_LANG_ALIASES: dict[str, str] = {
    "bash": "shell",
    "c++": "cpp",
    "c#": "csharp",
    "golang": "go",
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "py": "python",
    "python3": "python",
    "shellscript": "shell",
    "ts": "typescript",
}

_LANGUAGE_TEXT_PATTERNS: dict[str, tuple[str, ...]] = {
    "python": (
        r"\bpython\b",
        r"\bfastapi\b",
        r"\bflask\b",
        r"\bdjango\b",
        r"\bpytest\b",
        r"requirements\.txt",
        r"\.py\b",
    ),
    "typescript": (
        r"\btypescript\b",
        r"\bts-node\b",
        r"tsconfig\.json",
        r"\bts\b(?=\s+(project|service|app|api|module|code|conventions))",
        r"\.tsx?\b",
    ),
    "javascript": (
        r"\bjavascript\b",
        r"\bnode\.?js\b",
        r"\bnode\b",
        r"\bexpress\b",
        r"\bjs\b(?=\s+(project|service|app|api|module|code|conventions))",
        r"\.jsx?\b",
    ),
    "go": (
        r"\bgolang\b",
        r"主语言\s*[:：=-]\s*go\b",
        r"主要语言\s*[:：=-]\s*go\b",
        r"\b(?:primary|main|programming)\s+language\s*[:：=-]\s*go\b",
        r"\bgo_compile\b",
        r"source_target_coverage:[^\n]*\.go\b",
        r"\*\*/\*\.go\b",
        r"用\s+go\s+实现",
        r"\bgo\b(?=\s+(project|service|app|api|module|code|conventions|test|build))",
        r"go\.mod",
        r"\.go\b",
        r"\bgin\b",
        r"\bfiber\b",
    ),
    "rust": (
        r"\brust\b",
        r"\bcargo\b",
        r"cargo\.toml",
        r"\.rs\b",
    ),
    "java": (
        r"\bjava\b",
        r"\bspring\b",
        r"\bgradle\b",
        r"pom\.xml",
    ),
    "shell": (
        r"\bbash\b",
        r"\bshell\b",
        r"\bsh\b(?=\s+(script|command|automation))",
    ),
    "sql": (
        r"\bsql\b",
        r"\bpostgres\b",
        r"\bmysql\b",
        r"\bsqlite\b",
    ),
}

_LANGUAGE_PROFILES: dict[str, LanguageProfile] = {
    "go": LanguageProfile(
        code="go",
        display_name="Go (Golang)",
        identity=(
            "你是一位精通 Go (Golang) 语言的资深软件架构师，严格遵守官方 Effective Go、"
            "Go Code Review Comments 和工业级高性能服务端工程规范。"
        ),
        standards=(
            "代码风格与地道命名 (Idioms): 严格符合 gofmt/goimports；import 按标准库、第三方、本地模块分组；短作用域使用 ctx、mu、err、i 等短名，长作用域才使用描述性名称；所有导出的 type/interface/function/const 必须有标准 Go 注释。",
            '显式错误处理 (Error Handling): 严禁用 _ 忽略 error；使用 return-early guard clauses 降低嵌套；向上返回错误必须用 fmt.Errorf("具体上下文: %w", err) 包装；运行时错误通过 error 返回，除启动期不可恢复失败外不使用 panic。',
            "并发安全与上下文 (Concurrency & Context): 启动 goroutine 前必须明确生命周期、退出条件和资源释放；channel 用于传递所有权，sync.Mutex/RWMutex 用于保护内部状态；网络、数据库、RPC 和长耗时任务必须把 context.Context 作为第一个参数并检查 ctx.Done()。",
            "接口与架构设计 (Design): 遵循 accept interfaces, return structs；接口保持最小化，通常 1-2 个方法；拒绝全局变量和隐藏全局状态，通过 NewXxx 构造函数显式依赖注入。",
            "内存与性能优化 (Performance): 已知容量时使用 make([]T, 0, capacity) 或 make(map[K]V, capacity) 预分配；小结构体传值，大结构体或可变状态传指针；包含 sync.Mutex 的结构体绝不能值复制。",
            "表格驱动测试 (Testing): 复杂核心逻辑必须使用 table-driven tests 并通过 t.Run 执行子测试；覆盖极端边界条件、错误返回路径、context cancel/timeout 和并发安全场景。",
        ),
        framework_guidance={
            "gin": (
                "HTTP handler 保持薄层：参数绑定、校验、调用 service、映射响应，不把业务逻辑塞进 handler。",
                "中间件只处理横切关注点，例如 request id、日志、认证、恢复和超时。",
            ),
            "fiber": (
                "Fiber handler 中显式处理 ctx 生命周期，避免在 goroutine 中持有请求上下文。",
                "将请求 DTO、业务 service、响应 mapper 分离，便于测试。",
            ),
        },
    ),
    "python": LanguageProfile(
        code="python",
        display_name="Python",
        identity="你是一位资深 Python 架构师，严格遵守 PEP 8、现代类型提示、pytest 实践和可维护服务端工程规范。",
        standards=(
            "公共函数、方法和复杂局部数据必须有明确类型提示；优先 dataclass、Protocol 和清晰领域类型。",
            "import 位于文件顶部并按标准库、第三方、本地分组；避免隐藏副作用和导入期 I/O。",
            "异常处理必须捕获具体异常；禁止裸 except、禁止吞异常，错误信息保留可诊断上下文。",
            "异步代码不得混入阻塞调用；需要阻塞 I/O 时显式隔离到 executor 或同步边界。",
            "pytest 覆盖 happy path、edge cases、exceptions 和 regression；不要用 mock 掩盖核心逻辑。",
        ),
        framework_guidance={
            "django": (
                "保持 view/controller 薄层，业务逻辑放入 service/domain 层；ORM 查询集中且避免 N+1。",
                "迁移、模型约束、serializer/form 校验需要与业务不变量一致。",
            ),
            "fastapi": (
                "使用 Pydantic model 表达请求/响应契约，endpoint 保持薄层并显式声明状态码。",
                "依赖注入用 Depends 或构造函数边界；异步 endpoint 中避免阻塞 I/O。",
            ),
            "flask": (
                "使用 blueprint 或 application factory 组织边界；request parsing、service、response mapping 分离。",
                "注册错误处理器并返回结构化错误，不在 route 中散落异常格式化逻辑。",
            ),
            "pytest": (
                "fixture 表达测试前置条件，parametrize 覆盖边界，不让测试依赖执行顺序。",
                "断言应验证行为和副作用证据，而不是只验证实现细节。",
            ),
        },
    ),
    "typescript": LanguageProfile(
        code="typescript",
        display_name="TypeScript",
        identity=(
            "你是一位资深 TypeScript 前端/全栈架构师，遵循 TypeScript Handbook、strict mode、"
            "ESLint/Prettier 和 React/Node 工程化规范。"
        ),
        standards=(
            "以 strict: true 为基线；禁止 any、as any、@ts-ignore，未知输入先用 unknown 再收窄。",
            "用 discriminated union、readonly、精确接口和泛型约束表达状态，不用宽松对象袋。",
            "async/await 优先；并发用 Promise.all/allSettled 并显式处理取消、超时和部分失败。",
            "模块保持单一职责；副作用隔离在边界，核心逻辑写成可测试纯函数或小服务。",
            "测试使用 Vitest/Jest 组织行为断言；必要时补类型层回归，防止契约漂移。",
        ),
        framework_guidance={
            "express": (
                "Express route 保持薄层：schema 校验、调用 service、集中错误处理中间件。",
                "异步 route 必须把错误交给统一 error handler，避免未处理 Promise rejection。",
            ),
            "next": (
                "明确 server/client component 边界；数据获取、缓存和 mutation 不要混在展示组件内。",
                "API route/server action 必须校验输入并返回稳定错误形状。",
            ),
            "react": (
                "组件以 props/state 派生 UI；复杂状态拆出 hook 或 reducer，避免 effects 承载业务流程。",
                "可访问性、受控输入、loading/error/empty 状态是组件实现的一部分。",
            ),
            "vue": (
                "Composition API 中将响应式状态、computed、watch 的职责分清；避免深层隐式副作用。",
                "props/emits 明确类型，跨组件状态放入可测试 composable/store。",
            ),
        },
    ),
    "javascript": LanguageProfile(
        code="javascript",
        display_name="JavaScript",
        identity="你是一位资深 JavaScript/Node.js 工程师，遵循 Airbnb/StandardJS 风格、ES2022+、现代模块化和 Node/Web 工程实践。",
        standards=(
            "const 优先、必要时 let、禁止 var；ESM import/export 优先，除非项目现有规范是 CommonJS。",
            "Promise 必须有错误路径；async/await 优先，避免回调地狱和静默 rejection。",
            "未知输入显式校验；对象解构和默认值不要掩盖缺失必填字段。",
            "核心逻辑保持纯函数或小模块，I/O、网络、DOM、进程调用隔离在边界。",
            "测试覆盖异步成功、失败、超时和边界输入。",
        ),
        framework_guidance={
            "express": (
                "route handler 保持薄层并使用统一错误中间件；请求校验不要散落在业务函数里。",
                "响应结构保持稳定，错误对象包含 code/message/context。",
            ),
            "react": (
                "组件拆分以状态所有权为边界；避免在 render 中创建高成本对象或隐藏副作用。",
                "处理 loading、error、empty 和可访问性状态。",
            ),
        },
    ),
    "rust": LanguageProfile(
        code="rust",
        display_name="Rust",
        identity="你是一位精通 Rust 的系统工程师，严格遵守 Rust API Guidelines、所有权模型和并发安全实践。",
        standards=(
            "rustfmt/clippy 友好；公共 API 有 /// 文档，模块边界清晰。",
            "优先借用而非不必要 clone；用 Result<T, E> 和 ? 传播错误，禁止生产路径 unwrap。",
            "错误类型应可诊断，必要时用 thiserror/anyhow 区分库边界和应用边界。",
            "并发共享状态使用 Arc/Mutex/RwLock 或 channel，明确 Send/Sync 约束。",
            "测试覆盖 Result 错误分支、边界输入和所有权相关状态转换。",
            "const fn 限制：const 函数内只能调用其他 const fn 和 const 操作；禁止调用 .to_string()、.collect()、format!() 等非 const 方法；如需字符串构造，使用 &str 字面量或 const 兼容方式。",
            "Cargo 项目结构：必须有 Cargo.toml（含 [package] name/version/edition）和 src/main.rs 或 src/lib.rs；模块通过 mod 声明和 pub use 导出。",
        ),
    ),
    "ruby": LanguageProfile(
        code="ruby",
        display_name="Ruby",
        identity="你是一位资深 Ruby 应用架构师，遵循 Ruby Style Guide、RuboCop、清晰对象边界和可测试服务对象实践。",
        standards=(
            "优先清晰命名、小方法和单一职责；避免 monkey patch 和隐式全局状态。",
            "异常只用于异常情况，业务失败使用明确结果对象或可预期返回值。",
            "依赖通过构造函数或参数注入，方便 RSpec 单测。",
            "集合操作优先可读性，避免过度链式调用隐藏复杂性。",
        ),
    ),
    "java": LanguageProfile(
        code="java",
        display_name="Java",
        identity="你是一位精通 Java 的企业级架构师，遵循 Google Java Style、清晰分层和类型安全实践。",
        standards=(
            "类职责单一，public API 稳定；依赖通过构造函数注入，避免静态全局状态。",
            "异常层次清晰；不要吞 InterruptedException，资源用 try-with-resources。",
            "不可变值对象优先，集合返回防御性视图或明确所有权。",
            "测试使用 JUnit/AssertJ/Mockito 时聚焦行为，不 mock 被测核心逻辑。",
        ),
        framework_guidance={
            "spring": (
                "Controller 保持薄层，service 承载用例，repository 承载持久化；事务边界显式。",
                "DTO、domain、entity 不要混用，校验和错误响应集中处理。",
            ),
        },
    ),
    "kotlin": LanguageProfile(
        code="kotlin",
        display_name="Kotlin",
        identity="你是一位资深 Kotlin 应用架构师，遵循 Kotlin Coding Conventions、空安全、协程和表达式化建模实践。",
        standards=(
            "优先不可变 val、data class、sealed class 和非空类型，避免 !!。",
            "协程必须明确 scope、dispatcher 和取消语义；不要在协程中阻塞。",
            "扩展函数用于领域表达，不要滥用到隐藏依赖。",
            "测试覆盖空值边界、sealed 分支和协程取消路径。",
        ),
    ),
    "swift": LanguageProfile(
        code="swift",
        display_name="Swift",
        identity="你是一位精通 Swift 的资深 Apple 平台工程师，遵循 Swift API Design Guidelines 和值语义实践。",
        standards=(
            "API 命名读起来像自然语言；优先 struct/value semantics，class 只用于身份或共享可变状态。",
            "可选值显式解包，避免强制 unwrap；错误使用 throws/Result 表达。",
            "并发使用 async/await、Task 和 actor 保护共享状态。",
            "UI 状态、业务逻辑和副作用保持分层，便于单元测试。",
        ),
    ),
    "c": LanguageProfile(
        code="c",
        display_name="C",
        identity="你是一位精通 C 的系统工程师，遵循 CERT C、显式内存所有权和可移植性实践。",
        standards=(
            "明确每个指针的所有权、生命周期和可空性；检查所有分配和 I/O 返回值。",
            "避免未定义行为、越界、格式化字符串风险和隐式整数溢出。",
            "资源释放路径集中且可审计；错误路径不能泄漏内存或文件描述符。",
            "头文件只暴露稳定 API，内部细节放在 .c 文件。",
        ),
    ),
    "cpp": LanguageProfile(
        code="cpp",
        display_name="C++",
        identity="你是一位精通 C++17/20 的系统工程师，遵循 C++ Core Guidelines、RAII 和现代类型安全实践。",
        standards=(
            "资源管理使用 RAII 和智能指针；避免裸 new/delete 和悬垂引用。",
            "const 正确性、move 语义和 noexcept 边界要清晰，不做不必要拷贝。",
            "模板和泛型只在能提升类型安全或复用时使用，避免炫技。",
            "错误处理用异常或 expected/Result 风格时保持项目一致。",
        ),
    ),
    "csharp": LanguageProfile(
        code="csharp",
        display_name="C#",
        identity="你是一位资深 C#/.NET 架构师，遵循 Microsoft C# Coding Conventions、异步、nullable reference types 和清晰服务边界实践。",
        standards=(
            "启用 nullable 思维，显式处理 null；public API 使用清晰 DTO/record。",
            "异步 I/O 使用 async/await 并传递 CancellationToken，避免 sync-over-async。",
            "依赖注入和生命周期与容器一致；不要在服务内隐藏全局状态。",
            "测试覆盖异步成功、取消、异常和边界输入。",
        ),
    ),
    "php": LanguageProfile(
        code="php",
        display_name="PHP",
        identity="你是一位精通 PHP 8+ 的资深工程师，遵循 PSR-12、Composer 和现代类型声明实践。",
        standards=(
            "使用 strict_types、参数/返回类型和只读属性表达契约。",
            "业务逻辑从 controller 分离到 service/domain；依赖通过构造函数注入。",
            "异常和错误响应集中处理，不在业务路径 echo/exit。",
            "测试覆盖成功、校验失败和持久化边界。",
        ),
    ),
    "shell": LanguageProfile(
        code="shell",
        display_name="Shell/Bash",
        identity="你是一位精通 Shell/Bash 的 DevOps 工程师，遵循 Google Shell Style Guide 和安全脚本实践。",
        standards=(
            "脚本顶部使用 set -euo pipefail；IFS、trap、cleanup 和临时目录要显式。",
            "所有变量展开加双引号；数组用于参数列表，避免 eval 和未校验命令拼接。",
            "命令存在性、输入路径、权限和退出码必须检查；错误消息写 stderr。",
            "函数小而清晰，main 负责流程，支持 --help 或清晰用法提示。",
        ),
    ),
    "sql": LanguageProfile(
        code="sql",
        display_name="SQL",
        identity="你是一位资深 SQL/数据库迁移架构师，遵循项目 SQL style guide、约束优先、索引、事务和可回滚变更实践。",
        standards=(
            "DDL/DML 必须考虑事务、锁、回滚和幂等性；迁移脚本命名和顺序保持项目一致。",
            "约束、索引和外键表达真实业务不变量，不用应用层假设替代数据库约束。",
            "查询显式列名，避免 SELECT *；关注执行计划、索引选择和 N+1 风险。",
            "数据修复脚本必须可审计，避免无 WHERE 更新/删除。",
        ),
    ),
    "html": LanguageProfile(
        code="html",
        display_name="HTML",
        identity="你是一位资深语义化 HTML 和 WCAG 可访问性前端工程师，重视结构、表单语义和渐进增强。",
        standards=(
            "使用语义标签、正确 heading 层级、label/form 关联和可访问名称。",
            "脚本和样式只在必要处接入，避免把结构语义藏在 div 堆叠中。",
            "交互元素使用原生 button/input/a 语义，键盘和屏幕阅读器路径必须成立。",
        ),
    ),
    "css": LanguageProfile(
        code="css",
        display_name="CSS",
        identity="你是一位资深 CSS 架构和 UI 用户体验工程师，遵循 CSS Guidelines、响应式、可维护选择器和布局稳定性实践。",
        standards=(
            "使用清晰命名和低特异性选择器；避免全局泄漏和 !important。",
            "布局优先 grid/flex，尺寸使用稳定约束，避免内容变化造成布局跳动。",
            "响应式规则按组件真实断点组织，保证文本不溢出、不遮挡。",
        ),
    ),
}

_ROLE_IDENTITIES: dict[str, str] = {code: profile.identity for code, profile in _LANGUAGE_PROFILES.items()}
_ROLE_IDENTITIES["generic"] = (
    "你是一位资深代码架构师、严格代码审查者和生产维护者，负责在未知或混合技术栈中按任务证据收敛实现方案。"
)
_LANG_NAMES: dict[str, str] = {code: profile.display_name for code, profile in _LANGUAGE_PROFILES.items()}

_FRAMEWORK_IDENTITY_FRAGMENTS: dict[str, str] = {
    "django": "后端 API 架构师",
    "express": "Node.js 后端 API 工程师",
    "fastapi": "Python API 架构师",
    "fiber": "Go 后端服务工程师",
    "flask": "Python Web 服务架构师",
    "gin": "Go 后端服务架构师",
    "next": "TypeScript 前端工程师 + UI 用户体验工程师",
    "pytest": "Python 测试架构师",
    "react": "TypeScript 前端工程师 + UI 用户体验工程师",
    "spring": "Java 企业服务架构师",
    "vue": "TypeScript 前端工程师 + UI 用户体验工程师",
}

_TASK_IDENTITY_FRAGMENTS: dict[str, str] = {
    "api": "后端 API 架构师",
    "bugfix": "生产故障排查专家",
    "cli": "CLI/自动化工具工程师",
    "code_review": "严格代码审查者",
    "concurrency": "并发系统专家",
    "config": "构建配置维护者",
    "database": "数据库架构师",
    "devops": "DevOps/SRE 工程师",
    "docs": "技术文档架构师",
    "frontend": "前端工程师 + UI 用户体验工程师",
    "integration": "系统集成架构师",
    "library": "SDK/API 设计架构师",
    "observability": "可观测性工程师",
    "performance": "性能优化专家",
    "refactor": "遗留系统重构专家",
    "security": "应用安全架构师",
    "service": "服务端架构师",
    "tests": "测试架构师",
    "validation": "输入校验与解析专家",
    "write_code": "生产级实现架构师",
}

_FILE_ROLE_IDENTITY_FRAGMENTS: dict[str, str] = {
    "config": "构建配置维护者",
    "docs": "技术文档架构师",
    "schema": "数据库迁移专家",
    "script": "自动化脚本工程师",
    "style": "UI 用户体验工程师",
    "test": "测试架构师",
}

_FRAMEWORK_ALIASES: dict[str, str] = {
    "next.js": "next",
    "nextjs": "next",
    "react.js": "react",
    "spring boot": "spring",
    "vue.js": "vue",
}

_FRAMEWORK_DISPLAY_NAMES: dict[str, str] = {
    "django": "Django",
    "express": "Express",
    "fastapi": "FastAPI",
    "fiber": "Fiber",
    "flask": "Flask",
    "gin": "Gin",
    "next": "Next.js",
    "pytest": "pytest",
    "react": "React",
    "spring": "Spring",
    "vue": "Vue",
}

_FRAMEWORK_PATTERNS: dict[str, str] = {
    "django": r"\bdjango\b",
    "express": r"\bexpress\b",
    "fastapi": r"\bfastapi\b",
    "fiber": r"\bfiber\b",
    "flask": r"\bflask\b",
    "gin": r"\bgin\b",
    "next": r"\bnext(?:\.js|js)?\b",
    "pytest": r"\bpytest\b",
    "react": r"\breact\b|\btsx\b",
    "spring": r"\bspring(?:\s+boot)?\b",
    "vue": r"\bvue\b",
}

_TASK_FOCUS_PROFILES: dict[str, TaskFocusProfile] = {
    "write_code": TaskFocusProfile(
        code="write_code",
        label="Write code / implement feature",
        guidance=(
            "Write production-oriented code, not a demo: clear semantic names, single-purpose functions, explicit boundaries, and no cleverness that reduces maintainability.",
            "Separate core logic from I/O, transport, persistence, configuration, and framework glue so the core can be unit tested.",
            "Validate inputs at the boundary, handle empty/invalid data, and make error messages actionable for operators or developers.",
            "Avoid duplicate logic, hidden side effects, magic constants, broad rewrites, and unnecessary abstractions.",
            "Keep generated output focused on complete source-file changes that can compile or parse under the target language.",
        ),
    ),
    "refactor": TaskFocusProfile(
        code="refactor",
        label="Refactor code",
        guidance=(
            "Preserve external behavior and public contracts unless the task explicitly changes them.",
            "Prioritize naming, responsibility boundaries, duplicated logic, boundary handling, exception/error handling, and coupling.",
            "Do not introduce abstractions unless they remove real duplication, clarify ownership, or match existing architecture.",
            "Make changes small and reversible; keep compatibility shims only where callers or contracts require them.",
            "Separate style-only improvements from substantive quality improvements in code structure and tests when the task requires evidence.",
        ),
    ),
    "code_review": TaskFocusProfile(
        code="code_review",
        label="Code review / audit",
        guidance=(
            "Review for real bug risk first: incorrect state, missing validation, swallowed errors, unsafe concurrency, data loss, security gaps, and brittle tests.",
            "Classify findings by priority and make each finding actionable: issue, why it matters, concrete fix, and severity.",
            "Do not stop at formatting or naming; surface design debt that increases maintenance cost or test fragility.",
            "If the task asks for a written audit artifact, keep review output precise, evidence-based, and tied to exact code paths.",
        ),
    ),
    "bugfix": TaskFocusProfile(
        code="bugfix",
        label="Bug fix / production repair",
        guidance=(
            "Find and patch the root cause, not only the observed symptom; preserve unrelated behavior.",
            "Consider null/empty input, type mismatch, out-of-range access, uninitialized state, repeated calls, race conditions, and swallowed exceptions/errors.",
            "Prefer the smallest safe fix first; when deeper design debt exists, keep the immediate fix minimal and make the follow-up risk explicit.",
            "Add or update regression coverage that would fail before the fix and pass after it.",
            "Explain or encode the impact boundary so the repair does not silently change adjacent logic.",
        ),
    ),
    "tests": TaskFocusProfile(
        code="tests",
        label="Write tests / verification",
        guidance=(
            "Test behavior and contracts, not private implementation details unless the task is a narrow regression.",
            "Cover happy path, boundary cases, invalid input, exception/error paths, and the specific regression risk.",
            "Use clear test names and arrange/act/assert structure; parameterize representative cases instead of duplicating bulky setup.",
            "Mock only external boundaries; do not mock the core logic being validated.",
            "Use minimal but realistic fixtures so failures explain the product risk.",
        ),
    ),
    "api": TaskFocusProfile(
        code="api",
        label="API/backend service",
        guidance=(
            "Keep transport handlers thin: parse and validate input, call application/service logic, then map a stable response.",
            "Define status codes, error shape, auth/permission boundary, idempotency, pagination, and request/response schemas explicitly.",
            "Do not mix persistence, protocol parsing, and business rules in the same function or handler.",
            "Test success, validation failure, unauthorized/forbidden, not found, conflict, and downstream failure paths.",
        ),
    ),
    "cli": TaskFocusProfile(
        code="cli",
        label="CLI/tooling",
        guidance=(
            "Separate argument parsing, command orchestration, and core logic so the core can be tested without a shell.",
            "Return deterministic exit codes and actionable stderr messages; keep stdout machine-readable when appropriate.",
            "Handle paths, environment variables, missing dependencies, permissions, and partial failures defensively.",
            "Support dry-run or guarded execution for destructive operations when the surrounding project pattern supports it.",
        ),
    ),
    "concurrency": TaskFocusProfile(
        code="concurrency",
        label="Concurrency/async",
        guidance=(
            "Define cancellation, timeout, ownership, and cleanup semantics before starting concurrent work.",
            "Avoid shared mutable state unless guarded by the language's standard synchronization primitive.",
            "Test both success and cancellation/error races.",
        ),
    ),
    "config": TaskFocusProfile(
        code="config",
        label="Configuration/build",
        guidance=(
            "Keep configuration deterministic, minimal, and consistent with existing package/build conventions.",
            "Do not remove existing scripts, aliases, or compiler/linter options unless the task explicitly asks.",
            "Validate structured config syntax and avoid comments in formats that do not support them.",
            "Preserve local development, CI, test, and production expectations separately when the config format supports environments.",
        ),
    ),
    "database": TaskFocusProfile(
        code="database",
        label="Database / data model",
        guidance=(
            "Represent business invariants with schema constraints, indexes, foreign keys, and transactions where appropriate.",
            "Avoid unbounded destructive writes; data migrations must be ordered, idempotent where possible, and operationally auditable.",
            "Prevent N+1 queries and uncontrolled scans; choose indexes based on query shape and cardinality.",
            "Keep persistence DTO/entity concerns separate from domain/service contracts.",
        ),
    ),
    "devops": TaskFocusProfile(
        code="devops",
        label="DevOps / automation",
        guidance=(
            "Automations must be deterministic, idempotent where possible, and explicit about environment, credentials, paths, and destructive actions.",
            "Fail fast with actionable diagnostics; do not hide command failures or convert failures into success.",
            "Keep local, CI, and production assumptions separate; avoid hard-coded machine-specific paths.",
            "Log enough evidence to debug without exposing secrets.",
        ),
    ),
    "docs": TaskFocusProfile(
        code="docs",
        label="Documentation / developer guidance",
        guidance=(
            "Document current truth, commands, constraints, ownership, and verification steps; do not write target-state plans as completed fact.",
            "Prefer concrete examples and exact paths over vague prose.",
            "Keep docs aligned with code behavior and public contracts.",
        ),
    ),
    "frontend": TaskFocusProfile(
        code="frontend",
        label="Frontend/UI",
        guidance=(
            "Implement complete loading, empty, error, disabled, and success states as part of the component behavior.",
            "Respect accessibility: semantic controls, labels, keyboard support, focus states, and readable contrast.",
            "Keep view state close to the component and move reusable business logic into hooks/services.",
            "Prevent layout shifts and text overflow; preserve existing design-system conventions before introducing new patterns.",
        ),
    ),
    "integration": TaskFocusProfile(
        code="integration",
        label="Integration / external dependency",
        guidance=(
            "Put third-party SDK, network, filesystem, or subprocess calls behind narrow adapters with timeout and error taxonomy.",
            "Normalize external data at the boundary; do not leak provider-specific objects into domain logic.",
            "Test success, timeout, malformed response, retryable failure, and permanent failure paths.",
        ),
    ),
    "library": TaskFocusProfile(
        code="library",
        label="Library/SDK",
        guidance=(
            "Design a small stable public API; keep internal helpers private and replaceable.",
            "Document inputs, outputs, errors, and compatibility assumptions.",
            "Avoid hidden global state and make dependency boundaries injectable.",
        ),
    ),
    "observability": TaskFocusProfile(
        code="observability",
        label="Observability / diagnostics",
        guidance=(
            "Logs, metrics, traces, and evidence should explain state transitions and failures without becoming control flow.",
            "Include stable identifiers and bounded context; never log secrets, tokens, credentials, or raw PII.",
            "Make failure modes distinguishable: validation, permission, dependency, timeout, invariant, and unexpected error.",
        ),
    ),
    "performance": TaskFocusProfile(
        code="performance",
        label="Performance",
        guidance=(
            "Optimize the measured hot path only; keep correctness and readability ahead of speculative tuning.",
            "Control allocations, repeated parsing, N+1 I/O, and unnecessary serialization.",
            "Preserve or add a benchmark/test signal where the task contract expects measurable improvement.",
        ),
    ),
    "security": TaskFocusProfile(
        code="security",
        label="Security/auth",
        guidance=(
            "Validate and normalize untrusted input at the boundary; fail closed on ambiguous authorization state.",
            "Do not log secrets, tokens, credentials, or raw PII; avoid timing and injection vulnerabilities.",
            "Keep authentication, authorization, and auditing responsibilities separate and test denied paths.",
            "Use allowlists and structured parsers for paths, commands, SQL, URLs, and serialized data whenever possible.",
        ),
    ),
    "service": TaskFocusProfile(
        code="service",
        label="Service/integration",
        guidance=(
            "Make external calls through a narrow adapter boundary with timeout, retry, and error taxonomy.",
            "Keep domain decisions independent from network/database SDK types.",
            "Emit useful evidence/logging without turning logs into control flow.",
        ),
    ),
    "validation": TaskFocusProfile(
        code="validation",
        label="Validation / parsing",
        guidance=(
            "Treat all external input as untrusted; parse with structured APIs and return precise, actionable validation errors.",
            "Separate validation from business execution so invalid input cannot partially mutate state.",
            "Cover missing fields, wrong types, malformed data, boundary values, and incompatible combinations.",
        ),
    ),
}

_FILE_ROLE_PROFILES: dict[str, FileRoleProfile] = {
    "config": FileRoleProfile(
        code="config",
        label="Config/manifest file",
        guidance=(
            "Preserve existing keys, comments where supported, script names, and dependency intent.",
            "Use valid syntax for the exact format; JSON cannot contain comments or trailing commas.",
        ),
    ),
    "docs": FileRoleProfile(
        code="docs",
        label="Documentation",
        guidance=(
            "Write docs as operational truth: exact commands, constraints, ownership, and verification evidence.",
            "Do not claim future/target architecture as current fact.",
        ),
    ),
    "schema": FileRoleProfile(
        code="schema",
        label="Schema/migration/data",
        guidance=(
            "Make schema changes explicit, ordered, and reversible where the project migration system supports it.",
            "Encode invariants with constraints/indexes when appropriate; avoid unbounded destructive changes.",
        ),
    ),
    "script": FileRoleProfile(
        code="script",
        label="Script/automation",
        guidance=(
            "Scripts must validate inputs, quote paths, handle missing tools, and return meaningful exit codes.",
            "Keep destructive actions guarded by explicit path checks or user-confirmed inputs.",
        ),
    ),
    "source": FileRoleProfile(
        code="source",
        label="Source module",
        guidance=(
            "Keep the module cohesive and respect existing import/layer boundaries.",
            "Expose only the smallest API needed by callers; keep helpers local unless reuse is real.",
        ),
    ),
    "style": FileRoleProfile(
        code="style",
        label="Stylesheet/UI styling",
        guidance=(
            "Use stable layout constraints and responsive rules; avoid text overflow and interaction-driven layout shifts.",
            "Keep selectors scoped and avoid accidental global style changes.",
        ),
    ),
    "test": FileRoleProfile(
        code="test",
        label="Test/spec file",
        guidance=(
            "Arrange/act/assert clearly; fixtures should describe real preconditions rather than hide setup magic.",
            "Regression tests should fail before the production fix and pass after it.",
        ),
    ),
}

_UNIVERSAL_GUIDANCE: tuple[str, ...] = (
    "Honor existing project architecture, naming, formatter, linter, and test conventions before introducing new patterns.",
    "Prefer minimal, reviewable changes that satisfy the task contract; do not rewrite unrelated code.",
    "Make edge cases explicit: invalid input, missing files, empty data, permission failures, timeouts, and partial failures.",
    "Keep generated code complete and syntactically valid; include imports and local helpers needed by the changed files.",
)

_PROJECT_TYPE_TO_FOCUS: dict[str, str] = {
    "api": "api",
    "cli": "cli",
    "database": "database",
    "db": "database",
    "devops": "devops",
    "docs": "docs",
    "documentation": "docs",
    "frontend": "frontend",
    "library": "library",
    "microservice": "service",
    "service": "service",
    "test": "tests",
    "tests": "tests",
    "web": "frontend",
}

_TASK_TYPE_TO_FOCUS: dict[str, str] = {
    "audit": "code_review",
    "bug": "bugfix",
    "bugfix": "bugfix",
    "build": "write_code",
    "codegeneration": "write_code",
    "codereview": "code_review",
    "config": "config",
    "create": "write_code",
    "database": "database",
    "devops": "devops",
    "docs": "docs",
    "documentation": "docs",
    "feature": "write_code",
    "fix": "bugfix",
    "implementation": "write_code",
    "implement": "write_code",
    "integration": "integration",
    "observability": "observability",
    "refactor": "refactor",
    "repair": "bugfix",
    "review": "code_review",
    "security": "security",
    "test": "tests",
    "testing": "tests",
    "validation": "validation",
}

_TEXT_FOCUS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("code_review", r"\b(review|audit|reviewer|审查|评审|代码审查)\b"),
    ("tests", r"\b(test|tests|testing|pytest|jest|vitest|spec|unit|回归测试|测试)\b"),
    ("bugfix", r"\b(fix|bug|defect|repair|regression|failure|failed|error|exception|broken|修复|缺陷|故障)\b"),
    ("refactor", r"\b(refactor|migrate|migration|extract|decompose|cleanup|rename|重构|迁移|拆分)\b"),
    ("api", r"\b(api|endpoint|route|controller|handler|rest|graphql|http|接口|端点)\b"),
    ("frontend", r"\b(ui|frontend|component|react|vue|screen|page|form|accessibility|前端|组件|页面)\b"),
    ("cli", r"\b(cli|command|terminal|argparse|cobra|commander|click|脚本|命令行)\b"),
    (
        "security",
        r"\b(auth|authorization|authentication|permission|token|secret|csrf|xss|sql injection|权限|认证|授权|安全)\b",
    ),
    ("performance", r"\b(performance|latency|throughput|memory|allocation|optimi[sz]e|benchmark)\b"),
    ("concurrency", r"\b(async|await|concurrency|parallel|thread|goroutine|channel|race|lock|mutex|并发|异步)\b"),
    ("config", r"\b(config|configuration|build|lint|format|tsconfig|package\.json|pyproject|docker)\b"),
    ("database", r"\b(database|db|sql|migration|schema|index|transaction|orm|数据库|迁移)\b"),
    ("devops", r"\b(devops|ci|cd|pipeline|docker|kubernetes|deploy|release|workflow)\b"),
    ("docs", r"\b(docs|documentation|readme|guide|manual|文档)\b"),
    ("observability", r"\b(log|logging|metric|trace|observability|telemetry|diagnostic|日志|指标|追踪)\b"),
    ("validation", r"\b(validate|validation|parse|parser|schema|sanitize|校验|解析)\b"),
    ("integration", r"\b(integration|adapter|third[- ]party|sdk|provider|webhook|external)\b"),
    ("library", r"\b(library|sdk|package|public api|client)\b"),
    ("service", r"\b(service|adapter|integration|database|queue|worker|consumer|producer)\b"),
)


def _normalize_token(value: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _normalize_language(value: Any) -> str:
    token = normalize_language_token(value)
    if not token:
        return ""
    return _LANG_ALIASES.get(token, token)


def _normalize_framework(value: Any) -> str:
    token = str(value or "").strip().lower().replace("_", "-")
    if not token:
        return ""
    return _FRAMEWORK_ALIASES.get(token, token)


def _framework_label(framework: str) -> str:
    return _FRAMEWORK_DISPLAY_NAMES.get(framework, framework.title())


def _coerce_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return dict(metadata) if isinstance(metadata, dict) else {}


def _metadata_text(metadata: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "detected_framework",
        "detected_language",
        "framework",
        "intent",
        "language",
        "main_language",
        "phase",
        "primary_language",
        "programming_language",
        "project_type",
        "task_kind",
        "task_type",
        "acceptance_criteria",
        "constraints",
        "quality_gates",
        "verification_commands",
    ):
        raw = metadata.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list | tuple):
            values.extend(str(item) for item in raw)
    tech_stack = metadata.get("tech_stack")
    if isinstance(tech_stack, dict):
        values.extend(str(value) for value in tech_stack.values())
    return " ".join(values)


def _metadata_language(metadata: dict[str, Any]) -> str:
    tech_stack = metadata.get("tech_stack")
    if isinstance(tech_stack, dict):
        language = _normalize_language(tech_stack.get("language"))
        if language:
            return language
    for key in ("detected_language", "language", "main_language", "primary_language", "programming_language"):
        language = _normalize_language(metadata.get(key))
        if language:
            return language
    return ""


def _metadata_framework(metadata: dict[str, Any]) -> str:
    tech_stack = metadata.get("tech_stack")
    if isinstance(tech_stack, dict):
        framework = _normalize_framework(tech_stack.get("framework"))
        if framework:
            return framework
    for key in ("detected_framework", "framework", "primary_framework"):
        framework = _normalize_framework(metadata.get(key))
        if framework:
            return framework
    return ""


def _language_from_paths(paths: tuple[str, ...]) -> str:
    lang_counts: dict[str, int] = {}
    for raw_path in paths:
        ext = Path(str(raw_path or "")).suffix.lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
    if not lang_counts:
        return ""
    return max(lang_counts, key=lambda lang: (lang_counts[lang], lang))


def _language_from_workspace(workspace: str) -> str:
    if not workspace:
        return ""
    ws = Path(workspace)
    if not ws.is_dir():
        return ""

    lang_counts: dict[str, int] = {}
    for ext, lang in _EXT_TO_LANG.items():
        count = 0
        for _ in ws.rglob(f"*{ext}"):
            count += 1
            if count >= 25:
                break
        if count:
            lang_counts[lang] = lang_counts.get(lang, 0) + count
    if not lang_counts:
        return ""
    return max(lang_counts, key=lambda lang: (lang_counts[lang], lang))


def _language_from_text(text: str) -> str:
    language_scores: dict[str, int] = {}
    for language, patterns in _LANGUAGE_TEXT_PATTERNS.items():
        score = sum(1 for pattern in patterns if re.search(pattern, text))
        if score > 0:
            language_scores[language] = score
    if not language_scores:
        return ""
    return max(language_scores, key=lambda lang: (language_scores[lang], lang))


def _contract_language_from_text(text: str) -> str:
    """Return a language only from explicit contract fields or hard checks."""

    normalized = str(text or "").lower()
    explicit_patterns = (
        r"(?:主语言|主要语言|primary\s+language|main\s+language|programming\s+language|language)\s*[:：=-]\s*([a-z0-9+#._-]+)",
        r"(?:detected_language|primary_language|main_language|programming_language)\s*[:：=-]\s*([a-z0-9+#._-]+)",
    )
    for pattern in explicit_patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if not match:
            continue
        language = _normalize_language(match.group(1))
        if language in _LANGUAGE_PROFILES:
            return language

    checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("go", (r"\bgo_compile\b", r"source_target_coverage:[^\n]*\.go\b", r"\*\*/\*\.go\b")),
        ("cpp", (r"\bcpp_compile\b", r"source_target_coverage:[^\n]*\.(?:cpp|hpp|cc|hh|cxx|hxx)\b")),
        ("rust", (r"\brust_compile\b", r"source_target_coverage:[^\n]*\.rs\b")),
        ("python", (r"\bpytest\b", r"source_target_coverage:[^\n]*\.py\b")),
        ("typescript", (r"\btsc\b", r"source_target_coverage:[^\n]*\.tsx?\b")),
        ("javascript", (r"\bjs_syntax\b", r"source_target_coverage:[^\n]*\.jsx?\b")),
        ("java", (r"\bjava_compile\b", r"source_target_coverage:[^\n]*\.java\b")),
    )
    for language, patterns in checks:
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns):
            return language
    return ""


def _combined_text(context: LanguagePromptContext) -> str:
    return " ".join(
        part
        for part in (
            context.subject,
            context.description,
            " ".join(context.target_files),
            " ".join(context.scope_paths),
            _metadata_text(context.metadata),
        )
        if str(part or "").strip()
    ).lower()


def _detect_framework(context: LanguagePromptContext) -> str:
    metadata_framework = _metadata_framework(context.metadata)
    if metadata_framework:
        return metadata_framework
    text = _combined_text(context)
    for framework, pattern in _FRAMEWORK_PATTERNS.items():
        if re.search(pattern, text):
            return framework
    return ""


def _detect_task_foci(context: LanguagePromptContext) -> tuple[str, ...]:
    focus_codes: list[str] = ["write_code"]

    for key in ("task_type", "task_kind", "intent", "phase"):
        mapped_task_focus = _TASK_TYPE_TO_FOCUS.get(_normalize_token(context.metadata.get(key)))
        if mapped_task_focus:
            focus_codes.append(mapped_task_focus)

    project_type = _normalize_token(context.metadata.get("project_type"))
    mapped_project_focus = _PROJECT_TYPE_TO_FOCUS.get(project_type)
    if mapped_project_focus:
        focus_codes.append(mapped_project_focus)

    text = _combined_text(context)
    for code, pattern in _TEXT_FOCUS_PATTERNS:
        if re.search(pattern, text):
            focus_codes.append(code)

    for role in _detect_file_roles(context.target_files):
        if role == "test":
            focus_codes.append("tests")
        elif role == "config":
            focus_codes.append("config")
        elif role == "script":
            focus_codes.append("cli")
        elif role == "schema":
            focus_codes.append("database")
        elif role == "docs":
            focus_codes.append("docs")

    return _dedupe_known_codes(focus_codes, _TASK_FOCUS_PROFILES, limit=6)


def _detect_file_roles(paths: tuple[str, ...]) -> tuple[str, ...]:
    roles: list[str] = []
    for raw_path in paths:
        normalized = str(raw_path or "").strip().replace("\\", "/").lower()
        if not normalized:
            continue
        basename = os.path.basename(normalized)
        ext = Path(normalized).suffix.lower()

        if re.search(r"(^|/)(tests?|specs?|__tests__)/", normalized) or re.search(
            r"(\.test|\.spec|_test|test_)",
            basename,
        ):
            roles.append("test")
        elif basename in {
            "package.json",
            "tsconfig.json",
            "pyproject.toml",
            "ruff.toml",
            "mypy.ini",
            "go.mod",
            "cargo.toml",
            "dockerfile",
        } or ext in {".json", ".yaml", ".yml", ".toml", ".ini", ".env"}:
            roles.append("config")
        elif ext in {".sh", ".bash"} or "/scripts/" in normalized or normalized.startswith("scripts/"):
            roles.append("script")
        elif ext in {".sql"} or "migration" in normalized or "schema" in basename:
            roles.append("schema")
        elif ext in {".css", ".scss", ".sass", ".less"}:
            roles.append("style")
        elif ext in {".md", ".mdx", ".rst"} or "/docs/" in normalized or normalized.startswith("docs/"):
            roles.append("docs")
        elif ext:
            roles.append("source")
    return _dedupe_known_codes(roles, _FILE_ROLE_PROFILES, limit=5)


def _dedupe_known_codes(values: list[str], registry: dict[str, Any], *, limit: int) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        code = str(value or "").strip().lower()
        if code in registry and code not in seen:
            seen.add(code)
            selected.append(code)
        if len(selected) >= limit:
            break
    return tuple(selected)


def _format_bullets(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _build_context_summary(
    *,
    language_profile: LanguageProfile | None,
    framework: str,
    task_foci: tuple[str, ...],
    file_roles: tuple[str, ...],
) -> str:
    lines: list[str] = []
    if language_profile:
        lines.append(f"- Primary language: {language_profile.display_name}")
    else:
        lines.append("- Primary language: generic/unknown")
    if framework:
        lines.append(f"- Framework/library signal: {_framework_label(framework)}")
    if task_foci:
        labels = ", ".join(_TASK_FOCUS_PROFILES[code].label for code in task_foci)
        lines.append(f"- Task focus: {labels}")
    if file_roles:
        labels = ", ".join(_FILE_ROLE_PROFILES[code].label for code in file_roles)
        lines.append(f"- File roles: {labels}")
    return "\n".join(lines)


def detect_primary_language(
    target_files: list[str],
    workspace: str | Path = "",
    *,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Detect the primary programming language from metadata, paths, or workspace."""

    normalized_metadata = _coerce_metadata(metadata)
    metadata_language = _metadata_language(normalized_metadata)
    if metadata_language in _LANGUAGE_PROFILES:
        return metadata_language

    paths_language = _language_from_paths(tuple(str(path or "") for path in target_files))
    if paths_language:
        return paths_language

    workspace_language = _language_from_workspace(str(workspace or ""))
    if workspace_language:
        return workspace_language

    if metadata_language:
        return metadata_language
    return "generic"


def get_language_guidance(language: str) -> str:
    """Return language-specific coding guidance for prompt profile callers."""

    profile = _LANGUAGE_PROFILES.get(_normalize_language(language))
    if profile is None:
        return ""
    return f"【{profile.display_name} Language Best Practices】\n" + _format_bullets(profile.best_practices)


def get_role_identity(language: str) -> str:
    """Get the Director role identity for a normalized language code."""

    identity = get_language_professional_identity(language)
    if identity is not None:
        return identity.identity
    return _ROLE_IDENTITIES["generic"]


def _dedupe_identity_fragments(values: tuple[str, ...], *, limit: int = 4) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in str(value or "").split("+"):
            token = part.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            selected.append(token)
            if len(selected) >= limit:
                return tuple(selected)
    return tuple(selected)


def _compose_role_identity(
    *,
    language: str,
    framework: str,
    task_foci: tuple[str, ...],
    file_roles: tuple[str, ...],
) -> str:
    """Compose a concrete role identity from language, stack, task, and file roles."""

    base_identity = get_role_identity(language)
    task_identity_codes = tuple(code for code in task_foci if code != "write_code") or task_foci
    fragments = _dedupe_identity_fragments(
        (
            *([_FRAMEWORK_IDENTITY_FRAGMENTS[framework]] if framework in _FRAMEWORK_IDENTITY_FRAGMENTS else []),
            *(_TASK_IDENTITY_FRAGMENTS[code] for code in task_identity_codes if code in _TASK_IDENTITY_FRAGMENTS),
            *(_FILE_ROLE_IDENTITY_FRAGMENTS[code] for code in file_roles if code in _FILE_ROLE_IDENTITY_FRAGMENTS),
        )
    )
    if not fragments:
        return base_identity
    composite = " + ".join(fragments)
    return (
        f"{base_identity} 本任务请同时以 {composite} 的复合身份工作，"
        "所有判断都必须贴合当前语言、框架、任务类型和文件角色。"
    )


def select_guidance(context: LanguagePromptContext) -> GuidanceSelection:
    """Resolve canonical prompt-guidance axes for a Director task."""

    normalized_context = LanguagePromptContext(
        target_files=tuple(str(path or "").strip().replace("\\", "/") for path in context.target_files),
        scope_paths=tuple(str(path or "").strip().replace("\\", "/") for path in context.scope_paths),
        workspace=str(context.workspace or ""),
        metadata=_coerce_metadata(context.metadata),
        subject=str(context.subject or ""),
        description=str(context.description or ""),
    )
    contract_language = _contract_language_from_text(_combined_text(normalized_context))
    language = contract_language or detect_primary_language(
        list(normalized_context.target_files),
        "",
        metadata=normalized_context.metadata,
    )
    if language == "generic":
        language = _language_from_text(_combined_text(normalized_context)) or language
    if language == "generic" and normalized_context.workspace:
        language = _language_from_workspace(normalized_context.workspace) or language
    language_profile = _LANGUAGE_PROFILES.get(language)
    framework = _detect_framework(normalized_context)
    task_foci = _detect_task_foci(normalized_context)
    file_roles = _detect_file_roles(normalized_context.target_files)
    return GuidanceSelection(
        language=language,
        language_display_name=language_profile.display_name if language_profile else "generic/unknown",
        framework=framework,
        framework_display_name=_framework_label(framework) if framework else "",
        task_foci=task_foci,
        task_focus_labels=tuple(_TASK_FOCUS_PROFILES[code].label for code in task_foci),
        file_roles=file_roles,
        file_role_labels=tuple(_FILE_ROLE_PROFILES[code].label for code in file_roles),
        role_identity=_compose_role_identity(
            language=language,
            framework=framework,
            task_foci=task_foci,
            file_roles=file_roles,
        ),
    )


def build_language_section(
    target_files: list[str],
    workspace: str | Path = "",
    *,
    metadata: dict[str, Any] | None = None,
    subject: str = "",
    description: str = "",
    scope_paths: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, str]:
    """Build composable language/task guidance and a role identity.

    The positional parameters remain the stable call shape for existing callers.
    Keyword-only context lets ``PromptBuilder`` produce task-aware guidance
    without hard-coding language rules in the prompt assembly path.
    """

    context = LanguagePromptContext(
        target_files=tuple(str(path or "").strip().replace("\\", "/") for path in target_files if str(path or "")),
        scope_paths=tuple(str(path or "").strip().replace("\\", "/") for path in scope_paths or () if str(path or "")),
        workspace=str(workspace or ""),
        metadata=_coerce_metadata(metadata),
        subject=str(subject or ""),
        description=str(description or ""),
    )

    selection = select_guidance(context)
    language_profile = _LANGUAGE_PROFILES.get(selection.language)
    framework = selection.framework
    task_foci = selection.task_foci
    file_roles = selection.file_roles

    identity = selection.role_identity
    sections: list[str] = [
        "=== Prompt Guidance Context ===",
        _build_context_summary(
            language_profile=language_profile,
            framework=framework,
            task_foci=task_foci,
            file_roles=file_roles,
        ),
    ]

    if language_profile is not None:
        sections.extend(
            [
                f"=== {language_profile.display_name} Language Best Practices ===",
                _format_bullets(language_profile.best_practices),
            ]
        )
        framework_rules = language_profile.framework_guidance.get(framework)
        if framework_rules:
            sections.extend(
                [
                    f"=== {_framework_label(framework)} Framework Best Practices ===",
                    _format_bullets(framework_rules),
                ]
            )

    if task_foci:
        task_rules: list[str] = []
        for code in task_foci:
            task_profile = _TASK_FOCUS_PROFILES[code]
            task_rules.append(f"{task_profile.label}:")
            task_rules.extend(f"  - {item}" for item in task_profile.best_practices)
        sections.extend(["=== Task Type Best Practices ===", "\n".join(task_rules)])

    if file_roles:
        role_rules: list[str] = []
        for code in file_roles:
            file_profile = _FILE_ROLE_PROFILES[code]
            role_rules.append(f"{file_profile.label}:")
            role_rules.extend(f"  - {item}" for item in file_profile.best_practices)
        sections.extend(["=== File Role Best Practices ===", "\n".join(role_rules)])

    sections.extend(["=== Universal Production Best Practices ===", _format_bullets(_UNIVERSAL_GUIDANCE)])
    return identity, "\n" + "\n".join(section for section in sections if section.strip()) + "\n"

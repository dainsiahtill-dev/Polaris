"""Language-specific expert coding guidance for Director code generation.

Provides industry-best-practice coding standards for each programming language,
injected into the Director's code generation prompt. Each language block covers:
- Code style & idioms
- Error handling
- Concurrency/async patterns
- Architecture/design
- Performance optimization
- Testing best practices

This ensures the Director generates syntactically correct, idiomatic, production-grade
code instead of guessing conventions.
"""

from __future__ import annotations

from pathlib import Path

# Language detection by file extension
_EXT_TO_LANG: dict[str, str] = {
    ".go": "go",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
}

# Per-language expert coding guidance (industry best practices)
_LANGUAGE_GUIDANCE: dict[str, str] = {
    "go": (
        "【Go (Golang) 专家编码规范 — Effective Go + Go Code Review Comments】\n"
        "\n"
        "1. 代码风格与地道命名\n"
        "- 严格符合 gofmt 和 goimports。import 必须分组：标准库一组，第三方一组，中间空行\n"
        "- import (...) 块内每行一个带引号的包路径，禁止在块内重复 import 关键字\n"
        "- 变量命名：短作用域用短名（ctx, mu, err, i），长作用域用描述性名称\n"
        "- 所有导出的结构体/接口/函数/常量必须有注释（// X represents...）\n"
        "- 类型(type)、常量(const)、变量(var) 各自只定义一次，禁止同文件重复声明\n"
        "- go.mod 的 module 名必须与所有 import 路径前缀一致\n"
        "\n"
        "2. 显式错误处理\n"
        "- 严禁用 _ 忽略 error\n"
        '- 用卫语句（Guard Clauses）：if err != nil { return fmt.Errorf("context: %w", err) }\n'
        "- 绝不滥用 panic，仅用于启动阶段不可恢复错误\n"
        "\n"
        "3. 并发安全\n"
        "- 启动 goroutine 前明确退出机制，防泄漏\n"
        "- Channel 传递所有权，sync.Mutex 保护状态\n"
        "- 所有 I/O 操作第一个参数传 context.Context\n"
        "\n"
        "4. 设计原则\n"
        "- 接受接口，返回结构体\n"
        "- 小接口（1-2个方法），如 io.Writer\n"
        "- 禁止全局状态，用构造函数 NewXxx() 做依赖注入\n"
        "\n"
        "5. 性能\n"
        "- 已知容量时 make([]T, 0, cap) 预分配\n"
        "- 小结构体传值，大结构体或需修改的传指针\n"
        "\n"
        "6. 测试\n"
        "- 表格驱动测试 + t.Run 子测试\n"
        "- 覆盖边界条件和错误路径"
    ),
    "python": (
        "【Python 专家编码规范 — PEP 8 + Google Python Style Guide】\n"
        "\n"
        "1. 代码风格\n"
        "- 严格遵循 PEP 8：4空格缩进，snake_case 变量/函数，PascalCase 类名\n"
        "- 所有公共函数必须有类型注解（def foo(x: int) -> str:）和 docstring\n"
        "- import 在文件顶部，顺序：标准库 → 第三方 → 本地，各组间空行\n"
        "- 使用 f-string 格式化，禁止 .format() 和 % 格式化\n"
        "- 行长度不超过 120 字符\n"
        "\n"
        "2. 错误处理\n"
        "- try/except 必须指定具体异常类型（except ValueError, TypeError:）\n"
        "- 禁止裸 except: 和 except Exception:\n"
        "- 使用 logging 模块记录错误，禁止 print 错误信息\n"
        "- 自定义异常继承合适的内置异常基类\n"
        "\n"
        "3. 异步编程\n"
        "- 异步代码用 async/await，不要混用回调和线程\n"
        "- asyncio 事件循环中禁止阻塞调用（用 run_in_executor）\n"
        "- 用 asyncio.gather 并发执行独立任务\n"
        "\n"
        "4. 设计原则\n"
        "- 优先组合而非继承\n"
        "- 使用 dataclass 或 attrs 定义数据容器\n"
        "- 依赖注入通过构造函数参数，禁止全局变量\n"
        "- 用 Protocol 定义接口契约\n"
        "\n"
        "5. 性能\n"
        "- 大数据集用生成器（yield）而非列表\n"
        "- 频繁查找用 set/dict 而非 list\n"
        "- 用 __slots__ 优化内存占用大的类\n"
        "\n"
        "6. 测试\n"
        "- 使用 pytest 框架，fixture 管理测试数据\n"
        "- 参数化测试覆盖边界：@pytest.mark.parametrize\n"
        "- Mock 外部依赖，测试纯逻辑"
    ),
    "typescript": (
        "【TypeScript 专家编码规范 — TypeScript Handbook + Google TS Style】\n"
        "\n"
        "1. 代码风格\n"
        "- 严格模式（strict: true）：禁止 any，所有类型显式注解\n"
        "- 命名：PascalCase 类/接口/类型，camelCase 变量/函数，UPPER_SNAKE_CASE 常量\n"
        "- import/export 使用 ESM（import { X } from './x'），禁止 require\n"
        "- 优先使用 interface 定义对象类型，type 仅用于联合/交叉/映射类型\n"
        "\n"
        "2. 类型安全\n"
        "- 禁止 as any 和 @ts-ignore\n"
        "- 用 unknown 替代 any 处理未知类型，配合类型守卫（typeof, in, instanceof）\n"
        "- 可选属性用 ?，只读属性用 readonly\n"
        "- 泛型约束用 extends（<T extends BaseType>）\n"
        "- 联合类型用 | 区分变体，配合判别属性（discriminated unions）\n"
        "\n"
        "3. 错误处理\n"
        "- async 函数用 try/catch/finally\n"
        "- 自定义 Error 类继承 Error，包含 code 和 context\n"
        "- 永不吞异常（空 catch 块），至少 log\n"
        "- Result 模式：返回 { ok: true, data } | { ok: false, error }\n"
        "\n"
        "4. 异步编程\n"
        "- async/await 替代 .then() 链\n"
        "- Promise.all 并发独立任务，Promise.allSettled 容忍部分失败\n"
        "- AbortController 控制异步取消\n"
        "\n"
        "5. 设计原则\n"
        "- 单一职责，组合优于继承\n"
        "- 依赖注入通过构造函数\n"
        "- 不可变数据：readonly, Object.freeze, as const\n"
        "- 纯函数优先，副作用隔离到边界\n"
        "\n"
        "6. 测试\n"
        "- Vitest 或 Jest，describe/it 组织\n"
        "- 类型测试用 tsd 或 expectType\n"
        "- Mock 用 vi.mock / jest.mock"
    ),
    "javascript": (
        "【JavaScript 专家编码规范 — Airbnb JS Style Guide】\n"
        "\n"
        "1. 代码风格\n"
        "- const 优先，必要时 let，禁止 var\n"
        "- 箭头函数用于回调，普通函数用于顶层声明\n"
        "- 解构赋值提取对象/数组值\n"
        "- 模板字符串（反引号）替代字符串拼接\n"
        "- ESM import/export，禁止 require/module.exports\n"
        "\n"
        "2. 错误处理\n"
        "- try/catch/finally 处理异常\n"
        "- Promise 链必须有 .catch()\n"
        "- 自定义 Error 类携带 code 属性\n"
        "- 永不吞异常\n"
        "\n"
        "3. 异步编程\n"
        "- async/await 替代回调和 .then()\n"
        "- Promise.all 并发独立任务\n"
        "- 避免回调地狱，必要时用 util.promisify\n"
        "\n"
        "4. 设计原则\n"
        "- 纯函数优先\n"
        "- 闭包封装私有状态\n"
        "- 工厂函数创建对象\n"
        "- 事件驱动解耦模块\n"
        "\n"
        "5. 性能\n"
        "- Map/Set 替代对象用于动态键\n"
        "- 避免在循环中创建闭包\n"
        "- 大数据用流（Stream）处理"
    ),
    "rust": (
        "【Rust 专家编码规范 — The Rust Book + API Guidelines】\n"
        "\n"
        "1. 代码风格\n"
        "- 遵循 rustfmt 格式化\n"
        "- 命名：snake_case 函数/变量，PascalCase 类型/Trait，SCREAMING_SNAKE_CASE 常量\n"
        "- use 声明在文件顶部，按模块分组\n"
        "- 所有公共 API 必须有 /// 文档注释（含示例）\n"
        "\n"
        "2. 所有权与生命周期\n"
        "- 优先借用（&T）而非转移所有权\n"
        "- 生命周期标注清晰，避免不必要的 clone\n"
        "- 用 Cow<'_, str> 处理可能需要所有权的字符串\n"
        "- 避免 Rc/RefCell，优先用生命周期和借用检查器\n"
        "\n"
        "3. 错误处理\n"
        "- 用 Result<T, E> 和 ? 运算符传播错误\n"
        "- 自定义错误类型实现 std::error::Error\n"
        '- 禁止 unwrap()（测试除外），用 expect("reason") 或 match\n'
        "- 用 thiserror 库简化错误类型定义\n"
        "\n"
        "4. 并发安全\n"
        "- 用 Arc<Mutex<T>> 共享可变状态\n"
        "- 优先用 channel (crossbeam/mpsc) 传递消息\n"
        "- async 代码用 tokio runtime\n"
        "- Send + Sync 标记确保线程安全\n"
        "\n"
        "5. 设计原则\n"
        "- Trait 定义接口，impl 实现行为\n"
        "- 零成本抽象：编译器优化掉不用的泛型\n"
        "- 类型状态模式（Typestate）编码状态机\n"
        "- 用 Builder 模式构造复杂对象\n"
        "\n"
        "6. 测试\n"
        "- #[cfg(test)] mod tests 内联单元测试\n"
        "- #[test] + assert_eq! / assert!\n"
        "- proptest 属性测试覆盖边界"
    ),
}

# Role-specific identity for Director (language + domain aware)
_ROLE_IDENTITIES: dict[str, str] = {
    "go": "你是一位精通 Go (Golang) 的资深后端架构师，严格遵守 Effective Go 和 Go Code Review Comments 规范。你的代码必须具备工业级强度、高性能、高可读性。",
    "python": "你是一位精通 Python 的资深软件架构师，严格遵守 PEP 8 和 Google Python Style Guide。你的代码必须具备生产级质量、类型安全、可维护性。",
    "typescript": "你是一位精通 TypeScript 的资深全栈工程师，严格遵守 TypeScript Handbook 和严格模式。你的代码必须类型安全、无 any、架构清晰。",
    "javascript": "你是一位精通 JavaScript (ES2022+) 的资深工程师，遵循 Airbnb JS Style Guide。你的代码必须现代、高效、可维护。",
    "rust": "你是一位精通 Rust 的系统级工程师，严格遵守 The Rust Book 和 Rust API Guidelines。你的代码必须内存安全、零成本抽象、高并发安全。",
    "ruby": "你是一位精通 Ruby 的资深工程师，遵循 Ruby Style Guide 和社区惯例。你的代码必须优雅、可读、DRY。",
    "java": "你是一位精通 Java 的资深企业级架构师，遵循 Google Java Style Guide。你的代码必须面向对象、设计模式合理、高性能。",
    "kotlin": "你是一位精通 Kotlin 的资深工程师，遵循 Kotlin 官方编码规范。你的代码必须简洁、空安全、函数式优先。",
    "swift": "你是一位精通 Swift 的资深 iOS/macOS 工程师，遵循 Swift API Design Guidelines。你的代码必须值语义优先、协议驱动。",
    "c": "你是一位精通 C 的系统级工程师，遵循 CERT C Coding Standard。你的代码必须内存安全、无未定义行为、高性能。",
    "cpp": "你是一位精通 C++17/20 的资深系统工程师，遵循 C++ Core Guidelines。你的代码必须 RAII、智能指针优先、移动语义。",
    "csharp": "你是一位精通 C# 的资深 .NET 工程师，遵循 Microsoft C# 编码规范。你的代码必须异步优先、LINQ 合理使用、null 安全。",
    "php": "你是一位精通 PHP 8+ 的资深工程师，遵循 PSR-12 编码规范。你的代码必须类型声明完整、面向对象、Composer 管理依赖。",
    "shell": "你是一位精通 Shell/Bash 的 DevOps 工程师，遵循 Google Shell Style Guide。你的脚本必须 set -euo pipefail、变量引号、错误处理。",
    "generic": "你是一位经验丰富的软件工程师，正在实现一个任务。",
}

# Language display names
_LANG_NAMES: dict[str, str] = {
    "go": "Go (Golang)",
    "python": "Python",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "rust": "Rust",
    "ruby": "Ruby",
    "java": "Java",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "c": "C",
    "cpp": "C++",
    "csharp": "C#",
    "php": "PHP",
    "shell": "Shell/Bash",
}


def detect_primary_language(target_files: list[str], workspace: str | Path = "") -> str:
    """Detect the primary programming language from target file extensions.

    Returns the language code (e.g., 'go', 'python', 'typescript') or
    'generic' if no language can be determined.
    """
    lang_counts: dict[str, int] = {}
    for f in target_files:
        ext = Path(f).suffix.lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

    if not lang_counts and workspace:
        # Fallback: scan workspace for source files
        ws = Path(workspace)
        if ws.is_dir():
            for ext, lang in _EXT_TO_LANG.items():
                count = len(list(ws.rglob(f"*{ext}")))
                if count > 0:
                    lang_counts[lang] = lang_counts.get(lang, 0) + count

    if not lang_counts:
        return "generic"

    return max(lang_counts, key=lang_counts.get)  # type: ignore[arg-type]


def get_language_guidance(language: str) -> str:
    """Get language-specific coding guidance.

    Returns the guidance string for the given language, or an empty string
    if no guidance is available.
    """
    return _LANGUAGE_GUIDANCE.get(language, "")


def get_role_identity(language: str) -> str:
    """Get a specific role identity for the Director based on detected language.

    Specific identities (e.g., 'Go backend architect') activate more relevant
    parameters in the LLM than generic 'software developer', producing higher
    quality, more idiomatic code.
    """
    return _ROLE_IDENTITIES.get(language, _ROLE_IDENTITIES["generic"])


def build_language_section(target_files: list[str], workspace: str | Path = "") -> tuple[str, str]:
    """Build language guidance + role identity for the Director prompt.

    Returns:
        Tuple of (role_identity, language_guidance_section).
        role_identity is always non-empty (falls back to generic).
        language_guidance_section may be empty if no guidance available.
    """
    lang = detect_primary_language(target_files, workspace)
    identity = get_role_identity(lang)
    guidance = get_language_guidance(lang)
    if guidance:
        lang_name = _LANG_NAMES.get(lang, lang.title())
        section = f"\n=== {lang_name} Expert Guidance ===\n{guidance}\n"
    else:
        section = ""
    return identity, section

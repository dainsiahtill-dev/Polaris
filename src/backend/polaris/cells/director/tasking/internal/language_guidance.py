"""Language-specific coding guidance for Director code generation.

Provides expert-level coding standards and idioms for each programming language,
injected into the Director's code generation prompt. This ensures the Director
generates syntactically correct, idiomatic code instead of guessing conventions.

Each language guidance block is concise (~300 chars) to avoid bloating the prompt
while providing critical syntax and convention reminders.
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

# Per-language expert coding guidance (concise, high-signal)
_LANGUAGE_GUIDANCE: dict[str, str] = {
    "go": (
        "【Go 编码规范】\n"
        "- import 必须在 package 声明之后、代码之前，用 import (...) 块组织\n"
        "- import 块内每行一个包名（带引号），禁止在块内重复 import 关键字\n"
        "- 类型(type)、常量(const)、变量(var) 各自只定义一次，禁止同文件重复声明\n"
        "- 错误处理用 if err != nil { return err }，禁止 panic\n"
        "- 导出名大写开头，非导出名小写开头\n"
        "- go.mod 的 module 名必须与 import 路径前缀一致"
    ),
    "python": (
        "【Python 编码规范】\n"
        "- 遵循 PEP 8，类型注解优先\n"
        "- import 在文件顶部，标准库 → 第三方 → 本地\n"
        "- 异常处理用 try/except 指定具体异常类型，禁止裸 except\n"
        "- 使用 f-string 格式化字符串\n"
        "- 异步代码用 async/await，不要混用回调"
    ),
    "typescript": (
        "【TypeScript 编码规范】\n"
        "- 严格模式：禁止 any，显式类型注解\n"
        "- import/export 使用 ESM 语法（import { X } from './x'）\n"
        "- 接口优于类型别名（interface > type）\n"
        "- 可选属性用 ?，非空断言用 !（谨慎使用）\n"
        "- async 函数返回 Promise<T>，用 try/catch 处理错误"
    ),
    "javascript": (
        "【JavaScript 编码规范】\n"
        "- 优先使用 const，必要时 let，禁止 var\n"
        "- 使用 ESM import/export\n"
        "- async/await 处理异步，避免回调地狱\n"
        "- 错误处理用 try/catch\n"
        "- 模板字符串用反引号"
    ),
    "rust": (
        "【Rust 编码规范】\n"
        "- 所有权和生命周期必须显式标注\n"
        "- 错误处理用 Result<T, E> 和 ? 运算符\n"
        "- 使用 Cargo 管理依赖，use 声明在文件顶部\n"
        "- 公共 API 必须有 /// 文档注释\n"
        "- 避免 unwrap()，用 expect() 或 match 处理"
    ),
}

# Language display names for the prompt header
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


def build_language_section(target_files: list[str], workspace: str | Path = "") -> str:
    """Build a complete language guidance section for the Director prompt.

    Returns a formatted section string, or empty string if no guidance available.
    """
    lang = detect_primary_language(target_files, workspace)
    guidance = get_language_guidance(lang)
    if not guidance:
        return ""
    lang_name = _LANG_NAMES.get(lang, lang.title())
    return f"\n=== {lang_name} Expert Guidance ===\n{guidance}\n"

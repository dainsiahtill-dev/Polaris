"""Independent artifact quality: the frozen deterministic checks of one project.

The catalog freezes a structural floor per project: a real source layout, a
compilable source set, and the domain vocabulary inside the real source.
Platform acceptance and artifact quality are different verdicts -- the
platform proves the run's own verification, this module proves the frozen
floor against the delivered workspace.

The bench harness owns this evaluation.  It reports product quality; it never
becomes the platform's execution or acceptance authority.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

#: Directories that never hold the delivered product source.
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".polaris",
        ".venv",
        "__pycache__",
        "node_modules",
        "target",
        "vendor",
    }
)

#: File suffixes that count as real product source.
_SOURCE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".mjs",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".swift",
        ".ts",
        ".tsx",
    }
)

#: The language compile checks the frozen floor can declare.
_COMPILE_CHECK_NAMES = frozenset(
    {
        "cpp_compile",
        "go_compile",
        "java_compile",
        "js_syntax",
        "py_compile",
        "rust_compile",
        "ts_syntax",
    }
)

#: The language compile check each source suffix family satisfies.
_COMPILE_CHECK_BY_SUFFIX = {
    ".c": "cpp_compile",
    ".cc": "cpp_compile",
    ".cpp": "cpp_compile",
    ".go": "go_compile",
    ".h": "cpp_compile",
    ".hpp": "cpp_compile",
    ".java": "java_compile",
    ".js": "js_syntax",
    ".jsx": "js_syntax",
    ".mjs": "js_syntax",
    ".py": "py_compile",
    ".rs": "rust_compile",
    ".ts": "ts_syntax",
    ".tsx": "ts_syntax",
}


def _is_excluded(path: Path, workspace: Path) -> bool:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        return True
    return any(part in _EXCLUDED_DIR_NAMES for part in relative.parts[:-1])


def _product_files(workspace: Path) -> list[Path]:
    files: list[Path] = []
    for path in workspace.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if _is_excluded(path, workspace):
            continue
        files.append(path)
    return files


def _source_files(workspace: Path) -> list[Path]:
    return [path for path in _product_files(workspace) if path.suffix.lower() in _SOURCE_SUFFIXES]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _min_files_check(workspace: Path, required: int) -> tuple[bool, str]:
    files = _product_files(workspace)
    return len(files) >= required, f"{len(files)} delivered files (need {required})"


def _content_any_check(workspace: Path, words: list[str]) -> tuple[bool, str]:
    # The vocabulary must appear in the real source, never in the README, a
    # comment or a keyword-stuffed placeholder.
    haystack = "\n".join(_read_text(path) for path in _source_files(workspace)).lower()
    hits = [word for word in words if word.lower() in haystack]
    return bool(hits), f"domain vocabulary in real source: {hits or 'none'}"


def _source_target_coverage_check(workspace: Path, pattern: str) -> tuple[bool, str]:
    # ``PurePath.match`` does not expand ``**`` recursively, so the frozen
    # pattern is resolved with glob semantics against the workspace root.
    try:
        candidates = glob.glob(pattern, root_dir=str(workspace), recursive=True)
    except (OSError, ValueError) as exc:
        return False, f"pattern {pattern!r} is not resolvable: {exc}"
    matches = [Path(candidate) for candidate in candidates if not _is_excluded(workspace / candidate, workspace)]
    return bool(matches), f"{len(matches)} source files match {pattern}"


def _package_scripts_check(workspace: Path) -> tuple[bool, str]:
    package_path = workspace / "package.json"
    if not package_path.is_file():
        return False, "package.json is missing"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"package.json is unreadable: {exc}"
    scripts = package.get("scripts") if isinstance(package, dict) else None
    if not isinstance(scripts, dict) or not scripts:
        return False, "package.json declares no scripts"
    placeholders = []
    for name, command in scripts.items():
        text = str(command or "")
        if "package.json" in text and not re.search(r"\b(node|npm|python3?|tsc|jest|vitest)\b", text):
            placeholders.append(str(name))
    if placeholders:
        return False, f"manifest-only placeholder scripts: {placeholders}"
    return True, f"scripts: {sorted(str(name) for name in scripts)}"


def _compile_check_by_suffix(workspace: Path) -> dict[str, bool]:
    """Run the language compile/syntax check for every delivered source family.

    The frozen floor asks whether the delivered source compiles, so the
    delivered workspace is compiled here rather than inferred from the run's
    own output.
    """
    verdicts: dict[str, bool] = {}
    sources = _source_files(workspace)
    families = {
        "js_syntax": [path for path in sources if path.suffix.lower() in {".js", ".jsx", ".mjs"}],
        "py_compile": [path for path in sources if path.suffix.lower() == ".py"],
        "ts_syntax": [path for path in sources if path.suffix.lower() in {".ts", ".tsx"}],
        "go_compile": [path for path in sources if path.suffix.lower() == ".go"],
        "java_compile": [path for path in sources if path.suffix.lower() == ".java"],
        "rust_compile": [path for path in sources if path.suffix.lower() == ".rs"],
        "cpp_compile": [path for path in sources if path.suffix.lower() in {".c", ".cc", ".cpp"}],
    }
    if families["js_syntax"]:
        verdicts["js_syntax"] = _run_syntax_check("node", ["--check"], families["js_syntax"])
    if families["py_compile"]:
        verdicts["py_compile"] = _run_syntax_check(sys.executable, ["-m", "py_compile"], families["py_compile"])
    if families["ts_syntax"]:
        verdicts["ts_syntax"] = _run_typescript_check(workspace)
    if families["go_compile"]:
        verdicts["go_compile"] = _run_project_build(workspace, ["go", "build", "./..."])
    if families["java_compile"]:
        verdicts["java_compile"] = _run_java_check(workspace, families["java_compile"])
    if families["rust_compile"]:
        verdicts["rust_compile"] = _run_rust_check(workspace)
    if families["cpp_compile"]:
        verdicts["cpp_compile"] = _run_cpp_check(workspace, families["cpp_compile"])
    return verdicts


def _toolchain_executable(name: str) -> str | None:
    """The host toolchain binary, when this host provides one."""
    return shutil.which(name)


def _run_rust_check(workspace: Path, *, timeout_s: int = 900) -> bool:
    """Build the delivered Rust crate with cargo."""
    if (workspace / "Cargo.toml").is_file():
        return _run_project_build(workspace, ["cargo", "build", "--offline"], timeout_s=timeout_s)
    cargo = _toolchain_executable("cargo")
    if cargo is None:
        return False
    return _run_project_build(workspace, [cargo, "build", "--offline"], timeout_s=timeout_s)


def _run_cpp_check(workspace: Path, sources: list[Path], *, timeout_s: int = 600) -> bool:
    """Compile the delivered C/C++ sources with the host compiler.

    A delivered Makefile project is compiled with its own build entry point;
    a bare source set is compiled one translation unit at a time, because the
    project may not have a linkable main.  The include roots of the project
    are honoured, so a source that includes its own headers relative to the
    project layout compiles exactly as the project intends.
    """
    compiler = _toolchain_executable("g++") or _toolchain_executable("c++")
    if compiler is None:
        return False
    makefile = workspace / "Makefile"
    if makefile.is_file():
        return _run_project_build(workspace, ["make"], timeout_s=timeout_s)
    for source in sources:
        include_flags: list[str] = []
        for parent in source.parents:
            try:
                relative = parent.relative_to(workspace)
            except ValueError:
                break
            include_flags.append(f"-I{parent}")
            if relative == Path("."):
                break
        # The project's own include roots: a source in tests/ includes the
        # headers of the project root the same way the project does.
        for root in ("src", "include"):
            candidate = workspace / root
            if candidate.is_dir():
                include_flags.append(f"-I{candidate}")
        completed = subprocess.run(
            [compiler, "-fsyntax-only", *include_flags, str(source)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if completed.returncode != 0:
            return False
    return True


def _run_syntax_check(
    executable: str,
    prefix: list[str],
    paths: list[Path],
    *,
    timeout_s: int = 180,
) -> bool:
    """Check every file with the language's own parser."""
    for path in paths:
        try:
            completed = subprocess.run(
                [executable, *prefix, str(path)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if completed.returncode != 0:
            return False
    return True


def _run_typescript_check(workspace: Path, *, timeout_s: int = 300) -> bool:
    """Type-check the delivered TypeScript sources when a toolchain exists."""
    tsc = workspace / "node_modules" / ".bin" / "tsc"
    if tsc.is_file():
        try:
            completed = subprocess.run(
                [str(tsc), "--noEmit"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0
    package = workspace / "package.json"
    if package.is_file():
        try:
            payload = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        scripts = payload.get("scripts") if isinstance(payload, dict) else None
        if isinstance(scripts, dict) and "build" in scripts:
            return _run_project_build(workspace, ["npm", "run", "build"])
    return False


def _run_command(command: list[str], *, cwd: Path, timeout_s: int = 600) -> bool:
    """Run one command and report whether it succeeded."""
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _run_project_build(workspace: Path, command: list[str], *, timeout_s: int = 600) -> bool:
    return _run_command(command, cwd=workspace, timeout_s=timeout_s)


def _run_java_check(workspace: Path, sources: list[Path], *, timeout_s: int = 600) -> bool:
    """Compile the delivered Java production sources.

    The frozen floor asks whether the delivered Java source compiles.  The
    production sources are the ones under the project's main source root; the
    test sources belong to the project's own test framework, which the
    project resolves through its build.
    """
    javac = _toolchain_executable("javac")
    if javac is None:
        return False
    main_root = workspace / "src" / "main" / "java"
    if main_root.is_dir():
        production = [path for path in sources if path.is_relative_to(main_root)]
        if production:
            sources = production
    # Java compiles as a unit: one translation unit alone cannot resolve the
    # other classes of the project.
    return _run_command([javac, *[str(path) for path in sources]], cwd=workspace, timeout_s=timeout_s)


def evaluate_catalog_checks(
    project: Mapping[str, Any],
    workspace: Path,
    *,
    verification_checks: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate the frozen deterministic checks of one project.

    Returns a report with the per-check verdicts, the failing checks and the
    overall quality verdict.  The verdict is independent product quality: it
    never claims platform acceptance.
    """
    raw_checks = project.get("checks")
    if not isinstance(raw_checks, list):
        return {"verdict": "UNKNOWN", "checks": {}, "failed": [], "reason": "project declares no checks"}
    workspace = Path(workspace)
    compile_verdicts = _compile_check_by_suffix(workspace)
    results: dict[str, bool] = {}
    details: dict[str, str] = {}
    for raw in raw_checks:
        check = str(raw or "").strip()
        if not check:
            continue
        name, _, argument = check.partition(":")
        name = name.strip()
        if name == "min_files":
            try:
                required = int(argument)
            except ValueError:
                results[check] = False
                details[check] = "malformed min_files check"
                continue
            passed, detail = _min_files_check(workspace, required)
        elif name == "content_any":
            words = [word for word in re.split(r"[|,\s]+", argument) if word]
            passed, detail = _content_any_check(workspace, words)
        elif name == "source_target_coverage":
            passed, detail = _source_target_coverage_check(workspace, argument.strip())
        elif name == "package_scripts":
            passed, detail = _package_scripts_check(workspace)
        elif name in _COMPILE_CHECK_NAMES:
            passed = compile_verdicts.get(name, False)
            detail = (
                f"the delivered {name} source compiles" if passed else f"the delivered {name} source does not compile"
            )
        else:
            results[check] = False
            details[check] = f"unsupported deterministic check: {name}"
            continue
        results[check] = passed
        details[check] = detail
    failed = sorted(check for check, passed in results.items() if not passed)
    return {
        "verdict": "PASS" if not failed else "FAIL",
        "checks": results,
        "details": details,
        "failed": failed,
    }


def catalog_quality_report(
    project: Mapping[str, Any],
    workspace: Path,
    *,
    verification_checks: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """The bench-facing artifact quality block (independent of platform acceptance)."""
    evaluation = evaluate_catalog_checks(project, workspace, verification_checks=verification_checks)
    return {
        "catalog_quality": evaluation["verdict"],
        "catalog_quality_detail": {
            "checks": evaluation.get("checks", {}),
            "details": evaluation.get("details", {}),
            "failed": evaluation.get("failed", []),
        },
    }


def workspace_from_env(workspace: str) -> Path:
    """Resolve a project workspace path from the harness environment."""
    return Path(os.path.expanduser(str(workspace or ".")))


#: The structural floor the platform verification must enforce.  The compile
#: checks stay with the project's own build: the frozen requirement states
#: them explicitly, and the project owns its toolchain.
_STRUCTURAL_CHECK_NAMES = frozenset({"min_files", "content_any", "source_target_coverage", "package_scripts"})

_STRUCTURAL_CHECK_SCRIPT = """
import glob
import json
import sys
from pathlib import Path

root = Path(".")
excluded = {".git", ".polaris", ".venv", "__pycache__", "node_modules", "target", "vendor"}
source_suffixes = {".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js", ".jsx", ".kt",
                   ".mjs", ".php", ".py", ".rb", ".rs", ".swift", ".ts", ".tsx"}
checks = __CHECKS_JSON__


def _files():
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in excluded for part in path.parts[:-1]):
            continue
        found.append(path)
    return found


def _sources():
    return [path for path in _files() if path.suffix.lower() in source_suffixes]


problems = []
for check in checks:
    name, _, argument = check.partition(":")
    if name == "min_files":
        delivered = len(_files())
        if delivered < int(argument):
            problems.append(f"min_files: {delivered} delivered files, need {argument}")
    elif name == "content_any":
        words = [word for word in argument.replace(",", "|").split("|") if word]
        haystack = "\\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in _sources()).lower()
        if not any(word.lower() in haystack for word in words):
            problems.append("content_any: the domain vocabulary is absent from the real source")
    elif name == "source_target_coverage":
        hits = [
            candidate
            for candidate in glob.glob(argument, root_dir=".", recursive=True)
            if not any(part in excluded for part in Path(candidate).parts[:-1])
        ]
        if not hits:
            problems.append(f"source_target_coverage: no source file matches {argument}")
    elif name == "package_scripts":
        try:
            package = json.loads(Path("package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            package = {}
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if not isinstance(scripts, dict) or not scripts:
            problems.append("package_scripts: package.json declares no scripts")

if problems:
    print("STRUCTURAL_FLOOR_FAILED: " + "; ".join(problems))
    sys.exit(1)
print("STRUCTURAL_FLOOR_OK")
"""


def structural_check_command(project: Mapping[str, Any]) -> str:
    """Build the required verification command for the frozen structural floor.

    The frozen requirement states that the deterministic checks are the floor
    of the delivered artifact.  The platform verification owns the gate, so
    the floor becomes a required check: a run whose layout or vocabulary does
    not hold cannot be accepted.
    """
    raw_checks = project.get("checks")
    if not isinstance(raw_checks, list):
        return ""
    selected = [str(check) for check in raw_checks if str(check).partition(":")[0] in _STRUCTURAL_CHECK_NAMES]
    if not selected:
        return ""
    script = _STRUCTURAL_CHECK_SCRIPT.replace("__CHECKS_JSON__", repr(selected))
    return f"python3 -c {shlex.quote(script)}"

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

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Mapping

from polaris.kernelone.process import ProcessTreeDrainError, ProcessTreeRunControl, run_process_tree_safe
from polaris.kernelone.process.sandbox import build_workspace_sandbox_argv

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
        if not pattern or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            return False, "source target must be a relative project pattern"
        delivered = set(_product_files(workspace))
        matches = delivered.intersection(workspace.glob(pattern))
    except (OSError, ValueError) as exc:
        return False, f"pattern {pattern!r} is not resolvable: {exc}"
    return bool(matches), f"{len(matches)} source files match {pattern}"


def _package_scripts_check(workspace: Path) -> tuple[bool, str]:
    package_path = workspace / "package.json"
    if package_path.is_symlink() or not package_path.is_file():
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


class _CheckUnavailableError(RuntimeError):
    """The harness could not obtain a verdict from the requested checker."""


class _CompilerFailureError(RuntimeError):
    """A running checker rejected the candidate."""


class _UnsettledVerifierError(RuntimeError):
    def __init__(self, directory: Path) -> None:
        super().__init__("isolated verifier process settlement unknown")
        self.directory = directory


def _compile_in_scratch(workspace: Path, name: str) -> bool:
    """Give each requested compiler a disposable candidate and no network."""
    base = Path.home() / ".polaris" / "tmp"
    if base.resolve().is_relative_to(workspace.resolve()):
        raise _CheckUnavailableError("scratch directory overlaps the delivered workspace")
    base.mkdir(parents=True, exist_ok=True)
    with ExitStack() as cleanup:
        directory = Path(tempfile.mkdtemp(prefix="catalog-check-", dir=base))
        cleanup.callback(shutil.rmtree, directory)
        candidate = directory / "candidate"
        shutil.copytree(workspace, candidate, symlinks=True, ignore=shutil.ignore_patterns(".git", ".polaris"))
        try:
            return _compile_requested_sources(candidate, name)
        except ProcessTreeDrainError as error:
            cleanup.pop_all()
            raise _UnsettledVerifierError(directory) from error


def _compile_requested_sources(candidate: Path, name: str) -> bool:
    sources = [p for p in _source_files(candidate) if _COMPILE_CHECK_BY_SUFFIX.get(p.suffix.lower()) == name]
    if not sources:
        return False
    if name == "js_syntax":
        return all(_run_command(["node", "--check", str(p)], cwd=candidate) for p in sources)
    if name == "py_compile":
        return _run_command(["python3", "-I", "-m", "py_compile", *map(str, sources)], cwd=candidate)
    if name == "ts_syntax":
        return _run_typescript_check(candidate)
    if name == "go_compile":
        return _run_command(["go", "build", "./..."], cwd=candidate)
    if name == "java_compile":
        return _run_java_check(candidate, sources)
    if name == "rust_compile":
        return _run_command(["cargo", "build", "--offline"], cwd=candidate, timeout_s=900)
    return _run_cpp_check(candidate, sources)


def _run_cpp_check(workspace: Path, sources: list[Path], *, timeout_s: int = 600) -> bool:
    """Compile the delivered C/C++ sources with the host compiler.

    A delivered Makefile project is compiled with its own build entry point;
    a bare source set is compiled one translation unit at a time, because the
    project may not have a linkable main.  The include roots of the project
    are honoured, so a source that includes its own headers relative to the
    project layout compiles exactly as the project intends.
    """
    compiler = "c++"
    makefile = workspace / "Makefile"
    if makefile.is_file():
        return _run_command(["make"], cwd=workspace, timeout_s=timeout_s)
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
        if not _run_command(
            [compiler, "-fsyntax-only", *include_flags, str(source)], cwd=workspace, timeout_s=timeout_s
        ):
            return False
    return True


def _run_typescript_check(workspace: Path, *, timeout_s: int = 300) -> bool:
    """Type-check the delivered TypeScript sources when a toolchain exists."""
    tsc = workspace / "node_modules" / ".bin" / "tsc"
    if tsc.is_file():
        return _run_command([str(tsc), "--noEmit"], cwd=workspace, timeout_s=timeout_s)
    package = workspace / "package.json"
    if package.is_file() and not package.is_symlink():
        try:
            payload = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        scripts = payload.get("scripts") if isinstance(payload, dict) else None
        if isinstance(scripts, dict) and "build" in scripts:
            return _run_command(["npm", "run", "build"], cwd=workspace, timeout_s=timeout_s)
    raise _CheckUnavailableError("TypeScript toolchain and build recipe unavailable")


def _toolchain_bin_dirs() -> list[Path]:
    """宿主配置的工具链目录（缺 PATH 时也能找到 javac/go/cargo 等）。

    实测 L2-16（Java）：javac 在 ~/.local/jdk/bin，不在 PATH ——
    shutil.which 找不到，catalog 质检报 "toolchain unavailable" → 整个
    bench_gate=UNKNOWN。host-config 里明明配了 toolchain_bin_dirs。
    """

    import json
    import os

    raw = os.environ.get("POLARIS_HOST_CONFIG") or "/home/dains/.polaris/bench_runs/host-config-toolchains.json"
    try:
        with open(raw, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        return []
    dirs: list[Path] = []
    for profile in (config.get("permission_profiles") or {}).values():
        for item in profile.get("toolchain_bin_dirs") or []:
            path = Path(item).expanduser()
            if path.is_dir() and path not in dirs:
                dirs.append(path)
    return dirs


def _run_command(command: list[str], *, cwd: Path, timeout_s: int = 600) -> bool:
    """Run a compiler inside KernelOne confinement with only the scratch copy writable."""
    try:
        # 把**宿主自己选中的那个工具**所在目录交给沙箱。
        # 旧版只认 /usr 下的工具链，于是 host 配置里明明配了的
        # /home/<user>/.local/go/bin 被丢掉 —— 沙箱里没有 go，returncode 127
        # 被报成 "toolchain unavailable"，整个 Go 项目的 catalog_quality 变
        # UNKNOWN（实测 L1-04：平台侧 completed/accepted/passed 全绿，
        # 唯独 bench_gate=UNKNOWN，只因为 catalog 质检跑不了 go_compile）。
        system_tool = shutil.which(command[0])
        if system_tool is None:
            for directory in _toolchain_bin_dirs():
                candidate = directory / command[0]
                if candidate.exists():
                    system_tool = str(candidate)
                    break
        tool_dirs: list[Path] = []
        if system_tool:
            tool_dir = Path(system_tool).resolve().parent
            # 工具本身在 workspace 内（典型：node_modules/.bin/tsc →
            # node_modules/typescript/bin/tsc，是符号链接）时，它已经被
            # read/write root 覆盖，绝不能再作为 toolchain dir 传进去 ——
            # sandbox 会拒绝 "toolchain bin directory overlaps a writable
            # mount"，于是每个 TS 项目的 catalog 质检都变 UNKNOWN
            # （实测 L1-08：平台 accepted/approved 全绿，唯 bench_gate=UNKNOWN）。
            if not tool_dir.is_relative_to(Path(cwd).resolve()):
                tool_dirs.append(tool_dir)
        # 工具链目录必须**同时**是只读挂载：sandbox 会拒绝
        # "toolchain bin directory is outside existing read mounts"。
        # 旧代码只传 toolchain_bin_dirs，靠 /usr 恰好是默认只读挂载才没暴露；
        # 一旦工具链装在 ~/.local（Go/Rust/JDK 的常见位置）就整条 catalog
        # 质检变成 UNKNOWN。
        # 免安装版 Go（~/.local/go/bin/go）是 trimmed 二进制，必须知道 GOROOT；
        # 沙箱的 toolchain_env 就是为"工具链自带的配置环境"准备的。
        toolchain_env: dict[str, str] = {}
        goroot = None
        rustup_home = None
        if command[0] == "go" and tool_dirs:
            goroot = tool_dirs[0].parent
            toolchain_env["GOROOT"] = str(goroot)
        if command[0] in {"cargo", "rustc"}:
            # ~/.cargo/bin/cargo 是 rustup shim，必须知道 RUSTUP_HOME 才能找到
            # 已安装的 toolchain；否则报 "rustup could not choose a version of
            # cargo to run"（实测 L1-05 catalog 质检 FAIL，而宿主上 default
            # toolchain 明明是配好的）。
            candidate = Path.home() / ".rustup"
            if candidate.is_dir():
                rustup_home = candidate
                toolchain_env["RUSTUP_HOME"] = str(candidate)
                toolchain_env.setdefault("CARGO_HOME", str(Path.home() / ".cargo"))
        # toolchain_env 的值同样必须在已授予的只读根内。
        env_roots = [
            root
            for root in (
                goroot,
                rustup_home,
                Path(toolchain_env["CARGO_HOME"]) if "CARGO_HOME" in toolchain_env else None,
            )
            if root is not None
        ]
        argv = build_workspace_sandbox_argv(
            command,
            workspace=cwd,
            cwd=cwd,
            read_roots=[cwd, *tool_dirs, *env_roots],
            write_roots=[cwd],
            network_allowed=False,
            toolchain_bin_dirs=tool_dirs,
            toolchain_env=toolchain_env or None,
        )
        completed = run_process_tree_safe(argv, timeout=timeout_s, cancel_control=ProcessTreeRunControl())
    except ProcessTreeDrainError:
        raise
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        raise _CheckUnavailableError(f"isolated verifier unavailable: {type(exc).__name__}") from exc
    if completed.stderr.startswith("bwrap:") or completed.returncode in {126, 127}:
        raise _CheckUnavailableError("isolated verifier or toolchain unavailable")
    if completed.returncode:
        diagnostic = (completed.stderr or completed.stdout).strip()[-1500:]
        raise _CompilerFailureError(f"{Path(command[0]).name} exited {completed.returncode}: {diagnostic}")
    return True


def _run_java_check(workspace: Path, sources: list[Path], *, timeout_s: int = 600) -> bool:
    """Compile the delivered Java production sources.

    The frozen floor asks whether the delivered Java source compiles.  The
    production sources are the ones under the project's main source root; the
    test sources belong to the project's own test framework, which the
    project resolves through its build.
    """
    main_root = workspace / "src" / "main" / "java"
    if main_root.is_dir():
        production = [path for path in sources if path.is_relative_to(main_root)]
        if production:
            sources = production
    # Java compiles as a unit: one translation unit alone cannot resolve the
    # other classes of the project.
    return _run_command(["javac", *[str(path) for path in sources]], cwd=workspace, timeout_s=timeout_s)


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
    if not workspace.is_dir() or not any(str(item or "").strip() for item in raw_checks):
        return {"verdict": "UNKNOWN", "checks": {}, "failed": [], "reason": "no candidate or declared checks"}
    results: dict[str, bool | None] = {}
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
            try:
                passed = _compile_in_scratch(workspace, name)
            except _UnsettledVerifierError as error:
                results[check] = None
                details[check] = str(error)
                return {
                    "verdict": "UNKNOWN",
                    "checks": results,
                    "details": details,
                    "failed": [key for key, value in results.items() if value is False],
                    "unknown": [check],
                    "verifier_resources_closed": False,
                    "retained_scratch": str(error.directory),
                }
            except _CompilerFailureError as error:
                results[check] = False
                details[check] = str(error)
                continue
            except (OSError, _CheckUnavailableError) as error:
                results[check] = None
                details[check] = f"could not evaluate {name}: {error}"
                continue
            detail = (
                f"the delivered {name} source compiles" if passed else f"the delivered {name} source does not compile"
            )
        else:
            results[check] = None
            details[check] = f"unsupported deterministic check: {name}"
            continue
        results[check] = passed
        details[check] = detail
    failed = sorted(check for check, passed in results.items() if passed is False)
    unknown = sorted(check for check, passed in results.items() if passed is None)
    return {
        "verdict": "FAIL" if failed else "UNKNOWN" if unknown else "PASS",
        "unknown": unknown,
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
        "verifier_resources_closed": evaluation.get("verifier_resources_closed", True),
        "catalog_quality_detail": {
            "checks": evaluation.get("checks", {}),
            "details": evaluation.get("details", {}),
            "failed": evaluation.get("failed", []),
            "unknown": evaluation.get("unknown", []),
            "reason": evaluation.get("reason"),
            "retained_scratch": evaluation.get("retained_scratch"),
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
        hits = set()
        if argument and not Path(argument).is_absolute() and ".." not in Path(argument).parts:
            hits = set(_files()).intersection(root.glob(argument))
        if not hits:
            problems.append(f"source_target_coverage: no source file matches {argument}")
    elif name == "package_scripts":
        try:
            manifest = Path("package.json")
            package = {} if manifest.is_symlink() else json.loads(manifest.read_text(encoding="utf-8"))
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

"""Declarative verifier toolchain profiles.

Profiles authorize proof-producing command shapes. They do not execute
commands, mint launch capabilities, or infer success.
"""

from __future__ import annotations

import re
from pathlib import PurePath

_NON_PROVING_ARGUMENTS = frozenset(
    {
        "--collect-only",
        "--collectonly",
        "--co",
        "--help",
        "--list",
        "--list-tests",
        "--version",
        "-h",
        "help",
        "version",
        "--watch",
        "--watchall",
        "--fix",
        "--fix-dry-run",
        "--no-run",
        "--unsafe-fixes",
        "-run",
        "-list",
        "-count",
    }
)


def _argument_name(value: str) -> str:
    """Normalize ``--flag=value`` before policy comparison."""

    return value.casefold().split("=", 1)[0]


def _contains_non_proving_argument(argv: tuple[str, ...]) -> bool:
    return any(_argument_name(argument) in _NON_PROVING_ARGUMENTS for argument in argv[1:])


def _is_nonmutating_lint(argv: tuple[str, ...]) -> bool:
    executable = _exe(argv)
    args = tuple(item.casefold() for item in argv[1:])
    argument_names = {_argument_name(item) for item in argv[1:]}
    if {"--fix", "--fix-dry-run", "--unsafe-fixes"}.intersection(argument_names):
        return False
    if executable == "ruff" and args and args[0] == "format":
        return "--check" in args
    if executable == "cargo" and args and args[0] == "fmt":
        return "--check" in args and "--" in args
    if executable == "go" and args and args[0] == "fmt":
        return False
    if executable == "dotnet" and args and args[0] == "format":
        return "--verify-no-changes" in args
    if executable == "dart" and args and args[0] == "format":
        return "--output=none" in args and "--set-exit-if-changed" in args
    if executable == "mix" and args and args[0] == "format":
        return "--check-formatted" in args
    return True


def _exe(argv: tuple[str, ...]) -> str:
    return PurePath(argv[0].replace("\\", "/")).name.casefold()


def _subcommand(argv: tuple[str, ...]) -> str:
    return argv[1].casefold() if len(argv) > 1 else ""


def _python_module(argv: tuple[str, ...], module: str) -> bool:
    return _exe(argv) in {"python", "python3", "py"} and len(argv) >= 3 and argv[1] == "-m" and argv[2] == module


def _script_name(argv: tuple[str, ...]) -> str:
    executable = _exe(argv)
    if executable == "npm":
        if len(argv) >= 2 and argv[1] in {"test", "start"}:
            return argv[1]
        return argv[2] if len(argv) >= 3 and argv[1] in {"run", "run-script"} else ""
    if executable in {"pnpm", "yarn", "bun"}:
        if len(argv) >= 2 and argv[1] in {"test", "start", "build", "lint", "dev"}:
            return argv[1]
        return argv[2] if len(argv) >= 3 and argv[1] in {"run", "run-script"} else ""
    return ""


def _node_profile(modality: str, argv: tuple[str, ...]) -> str:
    executable = _exe(argv)
    if executable not in {"npm", "pnpm", "yarn", "bun"}:
        return ""
    subcommand = _subcommand(argv)
    if modality == "environment_prep" and subcommand in {"ci", "install", "i"}:
        return "node.package_install"
    script = _script_name(argv).casefold()
    allowed = {
        "build": {"build", "compile", "typecheck", "type-check"},
        "test": {"test", "test:ci", "check:test"},
        "lint": {"lint", "check", "format:check"},
        "entrypoint": {"start", "dev", "serve"},
    }
    return f"node.script_{modality}" if script in allowed.get(modality, set()) else ""


def _python_profile(modality: str, argv: tuple[str, ...]) -> str:
    executable = _exe(argv)
    if modality == "environment_prep":
        pip_args = argv[3:] if _python_module(argv, "pip") else argv[1:] if executable in {"pip", "pip3"} else ()
        if (
            pip_args
            and pip_args[0] == "install"
            and tuple(pip_args[1:])
            in {
                (".",),
                ("-e", "."),
                ("-r", "requirements.txt"),
                ("-r", "requirements-dev.txt"),
            }
        ):
            return "python.package_install"
    if modality == "test" and (executable in {"pytest", "py.test"} or _python_module(argv, "pytest")):
        return "python.pytest"
    if modality == "lint":
        if executable == "ruff" and len(argv) >= 2 and argv[1] in {"check", "format"}:
            return f"python.ruff_{argv[1]}"
        if _python_module(argv, "ruff") and len(argv) >= 4 and argv[3] in {"check", "format"}:
            return f"python.ruff_{argv[3]}"
        if executable in {"mypy", "pyright", "flake8", "pylint"}:
            return f"python.{executable}"
    if modality == "build" and _python_module(argv, "compileall"):
        return "python.compileall"
    if modality == "entrypoint" and executable in {"python", "python3", "py"} and len(argv) >= 2:
        if argv[1] not in {"-c", "-m"} and argv[1].endswith(".py"):
            return "python.script_entrypoint"
        if len(argv) >= 3 and argv[1] == "-m" and argv[2] not in {"pip", "pytest", "ruff", "compileall"}:
            return "python.module_entrypoint"
    return ""


def _compiled_toolchain_profile(modality: str, argv: tuple[str, ...]) -> str:
    executable = _exe(argv)
    subcommand = _subcommand(argv)
    exact: dict[str, dict[str, set[str]]] = {
        "cargo": {
            "environment_prep": {"fetch"},
            "build": {"build", "check"},
            "test": {"test"},
            "lint": {"clippy", "fmt"},
            "entrypoint": {"run"},
        },
        "go": {
            "environment_prep": {"mod"},
            "build": {"build"},
            "test": {"test"},
            "lint": {"vet", "fmt"},
            "entrypoint": {"run"},
        },
        "dotnet": {
            "environment_prep": {"restore"},
            "build": {"build", "publish"},
            "test": {"test"},
            "lint": {"format"},
            "entrypoint": {"run"},
        },
        "swift": {
            "environment_prep": {"package"},
            "build": {"build"},
            "test": {"test"},
            "entrypoint": {"run"},
        },
        "dart": {
            "environment_prep": {"pub"},
            "build": {"compile"},
            "test": {"test"},
            "lint": {"analyze", "format"},
            "entrypoint": {"run"},
        },
        "flutter": {
            "environment_prep": {"pub"},
            "build": {"build"},
            "test": {"test"},
            "lint": {"analyze"},
            "entrypoint": {"run"},
        },
        "zig": {"build": {"build"}, "test": {"test"}, "entrypoint": {"run"}},
        "mix": {"environment_prep": {"deps.get"}, "build": {"compile"}, "test": {"test"}, "lint": {"format"}},
        "rebar3": {"environment_prep": {"get-deps"}, "build": {"compile"}, "test": {"eunit", "ct"}},
        "cabal": {"environment_prep": {"update"}, "build": {"build"}, "test": {"test"}, "entrypoint": {"run"}},
        "stack": {"environment_prep": {"setup"}, "build": {"build"}, "test": {"test"}, "entrypoint": {"run"}},
        "dune": {"build": {"build"}, "test": {"runtest"}, "entrypoint": {"exec"}},
    }
    if subcommand in exact.get(executable, {}).get(modality, set()):
        if executable == "go" and modality == "environment_prep" and tuple(argv[1:3]) != ("mod", "download"):
            return ""
        if executable == "swift" and modality == "environment_prep" and tuple(argv[1:3]) != ("package", "resolve"):
            return ""
        if executable == "dart" and modality == "environment_prep" and tuple(argv[1:3]) != ("pub", "get"):
            return ""
        if executable == "flutter" and modality == "environment_prep" and tuple(argv[1:3]) != ("pub", "get"):
            return ""
        ecosystem = {
            "cargo": "rust.cargo",
            "go": "go",
            "dotnet": "dotnet",
            "swift": "swift",
            "dart": "dart",
            "flutter": "flutter",
            "zig": "zig",
            "mix": "elixir.mix",
            "rebar3": "erlang.rebar3",
            "cabal": "haskell.cabal",
            "stack": "haskell.stack",
            "dune": "ocaml.dune",
        }[executable]
        return f"{ecosystem}.{subcommand.replace('.', '_').replace('-', '_')}"
    return ""


def _jvm_native_profile(modality: str, argv: tuple[str, ...]) -> str:
    executable = _exe(argv)
    args = tuple(item.casefold() for item in argv[1:])
    if executable in {"mvn", "mvnw"}:
        if modality == "environment_prep" and "dependency:go-offline" in args:
            return "jvm.maven_dependencies"
        goals = {
            "build": {"compile", "package", "verify"},
            "test": {"test", "verify"},
            "lint": {"checkstyle:check", "spotbugs:check"},
            "entrypoint": {"exec:java", "spring-boot:run"},
        }
        if any(goal in args for goal in goals.get(modality, set())):
            return f"jvm.maven_{modality}"
    if executable in {"gradle", "gradlew"}:
        tasks = {
            "build": {"build", "assemble"},
            "test": {"test", "check"},
            "lint": {"check", "lint"},
            "entrypoint": {"run", "bootrun"},
        }
        if any(task in args for task in tasks.get(modality, set())):
            return f"jvm.gradle_{modality}"
    if executable == "javac" and modality == "build" and any(item.endswith(".java") for item in argv[1:]):
        return "jvm.javac_build"
    if executable == "java" and modality == "entrypoint" and len(argv) >= 2:
        return "jvm.java_entrypoint"
    if executable in {"gcc", "g++", "clang", "clang++", "cc", "c++", "rustc"} and modality == "build":
        source_suffixes = (".c", ".cc", ".cpp", ".cxx", ".rs")
        if any(item.endswith(source_suffixes) for item in argv[1:]) and "-o" in argv:
            return f"native.{executable.replace('+', 'p')}_build"
    if executable == "cmake" and modality == "build" and "--build" in argv[1:]:
        return "native.cmake_build"
    if executable == "ctest" and modality == "test":
        return "native.ctest"
    if executable in {"make", "gmake"}:
        target = next((item.casefold() for item in argv[1:] if not item.startswith("-")), "")
        targets = {"build": {"all", "build"}, "test": {"test", "check"}, "lint": {"lint"}, "entrypoint": {"run"}}
        if target in targets.get(modality, set()):
            return f"native.make_{modality}"
    return ""


def _dynamic_toolchain_profile(modality: str, argv: tuple[str, ...]) -> str:
    executable = _exe(argv)
    args = tuple(item.casefold() for item in argv[1:])
    if executable in {"phpunit", "vendor/bin/phpunit"} and modality == "test":
        return "php.phpunit"
    if executable == "composer":
        if modality == "environment_prep" and _subcommand(argv) == "install":
            return "php.composer_install"
        if len(argv) >= 3 and _subcommand(argv) in {"run", "run-script"} and argv[2].casefold() == modality:
            return f"php.composer_{modality}"
    if executable in {"bundle", "bundler"}:
        if modality == "environment_prep" and _subcommand(argv) == "install":
            return "ruby.bundle_install"
        if modality == "test" and tuple(args[:2]) == ("exec", "rspec"):
            return "ruby.rspec"
        if len(args) >= 3 and args[:2] == ("exec", "rake") and args[2] in {"test", "spec"} and modality == "test":
            return "ruby.rake_test"
    if executable in {"rake", "rspec"} and modality == "test":
        return f"ruby.{executable}"
    return ""


def resolve_builtin_profile(modality: str, argv: tuple[str, ...]) -> tuple[str, str]:
    """Return ``(profile_id, error_code)`` for one exact argv proposal."""

    if _contains_non_proving_argument(argv):
        return "", "non_proving_verifier_command"
    if modality == "lint" and not _is_nonmutating_lint(argv):
        return "", "non_proving_verifier_command"
    resolvers = (
        _node_profile,
        _python_profile,
        _compiled_toolchain_profile,
        _jvm_native_profile,
        _dynamic_toolchain_profile,
    )
    for resolver in resolvers:
        profile_id = resolver(modality, argv)
        if profile_id:
            return profile_id, ""
    for other_modality in ("environment_prep", "build", "test", "lint", "entrypoint"):
        if other_modality == modality:
            continue
        if any(resolver(other_modality, argv) for resolver in resolvers):
            return "", "verifier_modality_mismatch"
    return "", "untrusted_verifier_command"


def evaluate_builtin_proof(
    profile_id: str,
    modality: str,
    exit_code: int | None,
    timed_out: bool,
    output_bytes: bytes,
) -> bool:
    """Validate physical output for profiles where exit zero alone is insufficient."""

    if timed_out or exit_code != 0:
        return False
    if modality != "test":
        return True
    output = output_bytes.decode("utf-8", errors="replace")
    normalized_profile = profile_id.casefold()
    if normalized_profile in {"python.pytest", "pytest"}:
        matches = re.findall(r"(?:^|\s)(\d+)\s+passed(?:\s|,|$)", output, flags=re.IGNORECASE)
        return any(int(value) > 0 for value in matches) and "no tests ran" not in output.casefold()
    if normalized_profile in {"rust.cargo.test", "cargo.test"}:
        matches = re.findall(r"test result:\s*ok\.\s*(\d+)\s+passed", output, flags=re.IGNORECASE)
        return any(int(value) > 0 for value in matches)
    if normalized_profile == "go.test":
        lowered = output.casefold()
        return "[no test files]" not in lowered and bool(re.search(r"(?m)^ok\s+\S+", output))
    if normalized_profile == "node.script_test":
        patterns = (
            r"(?im)^\s*tests?\s*:?\s*(\d+)\s+passed",
            r"(?im)^\s*(\d+)\s+passing",
            r"(?im)^#\s*pass\s+(\d+)",
        )
        return any(int(value) > 0 for pattern in patterns for value in re.findall(pattern, output))
    # A test receipt without a profile-specific parser is not authoritative.
    return False


__all__ = ["evaluate_builtin_proof", "resolve_builtin_profile"]

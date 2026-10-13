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


_NODE_TEST_PROFILE_ID = "node.test_runner"
_NODE_TEST_FLAG = "--test"
_NODE_TEST_REPORTER_FORMS = (("--test-reporter=tap",), ("--test-reporter", "tap"))
_NODE_TEST_PATH_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"})
_NODE_PACKAGE_MANAGERS = ("npm", "pnpm", "yarn", "bun")
_NODE_INSTALL_SUBCOMMANDS = ("ci", "install", "i")
_NODE_SCRIPT_NAMES = {
    "build": frozenset({"build", "compile", "typecheck", "type-check"}),
    "test": frozenset({"test", "test:ci", "check:test"}),
    "lint": frozenset({"lint", "check", "format:check"}),
    "entrypoint": frozenset({"start", "dev", "serve"}),
}
_BUILTIN_COMMAND_MODALITIES = ("environment_prep", "build", "test", "lint", "entrypoint")


def _is_node_test_runner_argv(argv: tuple[str, ...]) -> bool:
    return _exe(argv) == "node" and len(argv) >= 2 and argv[1] == _NODE_TEST_FLAG


def _node_test_argument_rejection(argv: tuple[str, ...]) -> tuple[int | None, str]:
    """One existing grammar feeds both classification and readonly explanation."""
    if not _is_node_test_runner_argv(argv):
        if len(argv) > 1 and argv[1].startswith("-") and all(argv[1] != form[0] for form in _NODE_TEST_REPORTER_FORMS):
            return 1, "unsupported_argument"
        return 1, "test_flag_position"
    index = 2
    while index < len(argv):
        matched = next((form for form in _NODE_TEST_REPORTER_FORMS if argv[index : index + len(form)] == form), None)
        if matched is not None:
            index += len(matched)
            continue
        operand = argv[index]
        if operand.startswith("-"):
            return index, "unsupported_argument"
        test_path = PurePath(operand.replace("\\", "/"))
        if test_path.is_absolute() or ".." in test_path.parts or any(character in operand for character in ":;$&|`\\"):
            return index, "unsafe_test_path"
        if test_path.suffix.casefold() not in _NODE_TEST_PATH_SUFFIXES:
            return index, "unsupported_test_path_suffix"
        index += 1
    return None, ""


def _node_test_accepted_shape() -> str:
    forms = " | ".join(" ".join(form) for form in _NODE_TEST_REPORTER_FORMS)
    return (
        f"node {_NODE_TEST_FLAG} [{forms}] [workspace-relative test file/glob ...]; "
        f"suffixes: {', '.join(sorted(_NODE_TEST_PATH_SUFFIXES))}"
    )


def _node_profile_candidates(modality: str) -> list[dict[str, object]]:
    """Project examples from existing Node grammar; resolver remains authoritative."""
    shapes: list[tuple[str, list[tuple[str, ...]]]] = []
    if modality == "environment_prep":
        shapes.append(
            (
                f"{' | '.join(_NODE_PACKAGE_MANAGERS)} <{' | '.join(_NODE_INSTALL_SUBCOMMANDS)}>",
                [(manager, action) for manager in _NODE_PACKAGE_MANAGERS for action in _NODE_INSTALL_SUBCOMMANDS],
            )
        )
    scripts = _NODE_SCRIPT_NAMES.get(modality, frozenset())
    if scripts:
        shapes.append(
            (
                f"{' | '.join(_NODE_PACKAGE_MANAGERS)} <run | run-script> <{' | '.join(sorted(scripts))}>",
                [
                    (manager, action, script)
                    for manager in _NODE_PACKAGE_MANAGERS
                    for action in ("run", "run-script")
                    for script in sorted(scripts)
                ]
                + [(manager, script) for manager in _NODE_PACKAGE_MANAGERS for script in sorted(scripts)],
            )
        )
    if modality == "test":
        shapes.append(
            (
                _node_test_accepted_shape(),
                [("node", _NODE_TEST_FLAG)]
                + [("node", _NODE_TEST_FLAG, *form, "tests/example.test.js") for form in _NODE_TEST_REPORTER_FORMS],
            )
        )
    if modality in {"build", "lint"}:
        compiler_flags = ("--noEmit",) if modality == "lint" else ()
        shapes.append(
            (
                "npx tsc --noEmit [-p workspace-relative tsconfig]"
                if modality == "lint"
                else "npx tsc [-p workspace-relative tsconfig]; without --noEmit",
                [("npx", "tsc", *compiler_flags), ("npx", "tsc", *compiler_flags, "-p", "tsconfig.json")],
            )
        )
    if modality == "entrypoint":
        shapes.append(
            (
                "node <workspace-relative .js | .mjs | .cjs file> [application arguments ...]",
                [("node", f"src/cli{suffix}") for suffix in (".js", ".mjs", ".cjs")],
            )
        )
    candidates: list[dict[str, object]] = []
    for shape, examples in shapes:
        profile_id, _error = resolve_builtin_profile(modality, examples[0])
        accepted_examples = [
            list(example) for example in examples if resolve_builtin_profile(modality, example) == (profile_id, "")
        ]
        if profile_id and accepted_examples:
            candidates.append(
                {
                    "candidate_profile_id": profile_id,
                    "expected_modality": modality,
                    "accepted_shape": shape,
                    "argv_examples": accepted_examples,
                }
            )
    return candidates


def describe_builtin_command_profile(modality: str, argv: tuple[str, ...]) -> dict[str, object]:
    """Describe existing alternatives; never rewrite a proposal or grant permission."""
    candidates = _node_profile_candidates(modality)
    for expected_modality in _BUILTIN_COMMAND_MODALITIES:
        if expected_modality == modality:
            continue
        profile_id, _error = resolve_builtin_profile(expected_modality, argv)
        if profile_id:
            candidates.insert(
                0,
                {
                    "candidate_profile_id": profile_id,
                    "expected_modality": expected_modality,
                    "accepted_shape": " ".join(argv),
                    "argv_examples": [list(argv)],
                },
            )
            break
    feedback: dict[str, object] = {"candidate_profiles": candidates}
    if candidates:
        feedback.update(candidates[0])
        feedback["materialization_note"] = (
            "Examples describe planning syntax only. Declare and materialize the selected workspace files, "
            "package scripts and dependencies; runtime still validates their contents, identity and proof."
        )
    if not _is_node_test_runner_argv(argv) and not (
        modality == "test"
        and _exe(argv) == "node"
        and len(argv) >= 3
        and argv[1].startswith("-")
        and _NODE_TEST_FLAG in argv[2:]
    ):
        return feedback
    if modality != "test":
        candidates.extend(_node_profile_candidates("test"))
    feedback.update(
        next(candidate for candidate in candidates if candidate["candidate_profile_id"] == _NODE_TEST_PROFILE_ID)
    )
    index, reason_code = _node_test_argument_rejection(argv)
    if index is not None:
        feedback.update(
            {
                "argument_index": index,
                "rejected_argument": argv[index],
                "reason_code": reason_code,
                "reason": f"Argument {index} {argv[index]!r} is outside the current {_NODE_TEST_PROFILE_ID} grammar "
                f"({reason_code}).",
                "materialization_note": "If tests need transpilation, explicitly declare compilation and test artifacts "
                "or authored JavaScript tests. Do not invent output paths; actual execution still validates inputs and proof.",
            }
        )
    elif modality != "test":
        feedback["reason"] = f"The declared runner is a test profile, not the requested {modality} modality."
    return feedback


def _node_profile(modality: str, argv: tuple[str, ...]) -> str:
    executable = _exe(argv)
    if modality == "test" and _is_node_test_runner_argv(argv):
        index, _reason = _node_test_argument_rejection(argv)
        return _NODE_TEST_PROFILE_ID if index is None else ""
    if executable == "node" and modality == "entrypoint" and len(argv) >= 2:
        entrypoint_path = PurePath(argv[1].replace("\\", "/"))
        if (
            not argv[1].startswith("-")
            and not entrypoint_path.is_absolute()
            and ".." not in entrypoint_path.parts
            and entrypoint_path.suffix.casefold() in {".js", ".mjs", ".cjs"}
        ):
            return "node.file_entrypoint"
    if executable == "npx" and len(argv) >= 2 and argv[1] == "tsc":
        compiler_options = {item.casefold().split("=", 1)[0] for item in argv[2:]}
        if modality == "lint" and "--noemit" in compiler_options:
            return "node.typescript_check"
        if modality == "build" and "--noemit" not in compiler_options:
            return "node.typescript_build"
    if executable not in _NODE_PACKAGE_MANAGERS:
        return ""
    subcommand = _subcommand(argv)
    if modality == "environment_prep" and subcommand in _NODE_INSTALL_SUBCOMMANDS:
        return "node.package_install"
    script = _script_name(argv).casefold()
    return f"node.script_{modality}" if script in _NODE_SCRIPT_NAMES.get(modality, frozenset()) else ""


def _python_profile(modality: str, argv: tuple[str, ...]) -> str:
    executable = _exe(argv)
    if modality == "environment_prep":
        if _python_module(argv, "venv") and tuple(argv[3:]) == (".venv",):
            return "python.venv_create"
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
    if modality == "test" and _python_module(argv, "unittest"):
        args = tuple(argv[3:])
        if not args:
            return ""
        if args[0] == "discover":
            allowed_flags = {"-b", "--buffer", "-c", "--catch", "-f", "--failfast", "-q", "--quiet", "-v", "--verbose"}
            value_flags = {"-p", "--pattern", "-s", "--start-directory", "-t", "--top-level-directory"}
            index = 1
            while index < len(args):
                token = args[index]
                if token in allowed_flags:
                    index += 1
                    continue
                if token not in value_flags or index + 1 >= len(args):
                    return ""
                value = args[index + 1]
                path = PurePath(value.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or not value.strip():
                    return ""
                index += 2
            return "python.unittest_discover"
        if all(not item.startswith("-") and re.fullmatch(r"[A-Za-z0-9_.]+", item) for item in args):
            return "python.unittest"
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
        if (
            len(argv) >= 3
            and argv[1] == "-m"
            and argv[2]
            not in {
                "compileall",
                "pip",
                "pytest",
                "ruff",
                "unittest",
                "venv",
            }
        ):
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
    for other_modality in _BUILTIN_COMMAND_MODALITIES:
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
    if normalized_profile in {"python.unittest", "python.unittest_discover"}:
        matches = re.findall(r"(?m)^Ran\s+(\d+)\s+tests?\s+in\s+", output)
        return (
            any(int(value) > 0 for value in matches)
            and bool(re.search(r"(?m)^OK(?:\s|$)", output))
            and not bool(re.search(r"(?m)^FAILED(?:\s|$)", output))
        )
    if normalized_profile in {"rust.cargo.test", "cargo.test"}:
        matches = re.findall(r"test result:\s*ok\.\s*(\d+)\s+passed", output, flags=re.IGNORECASE)
        return any(int(value) > 0 for value in matches)
    if normalized_profile == "go.test":
        # ``go test ./...`` legitimately reports ``[no test files]`` for
        # auxiliary packages (for example ``cmd/<entrypoint>``) while other
        # packages in the same authoritative run execute real tests.  Reject
        # only an all-empty run; one successful package line proves that the
        # command exercised at least one package test suite.
        return bool(re.search(r"(?m)^ok\s+\S+", output))
    if normalized_profile == "node.test_runner":
        counts = {name: re.findall(rf"(?m)^# {name} (\d+)\s*$", output) for name in ("tests", "pass", "fail")}
        return (
            all(len(values) == 1 for values in counts.values())
            and int(counts["tests"][0]) > 0
            and int(counts["pass"][0]) > 0
            and int(counts["fail"][0]) == 0
            and bool(re.search(r"(?m)^TAP version 13\s*$", output))
            and bool(re.search(r"(?m)^ok \d+ - \S", output))
            and bool(re.search(r"(?m)^1\.\.[1-9]\d*\s*$", output))
            and not bool(re.search(r"(?m)^not ok \d+", output))
        )
    if normalized_profile == "node.script_test":
        patterns = (
            r"(?im)^\s*tests?\s*:?\s*(\d+)\s+passed",
            r"(?im)^\s*(\d+)\s+passing",
            r"(?im)^#\s*pass\s+(\d+)",
        )
        counted = any(int(value) > 0 for pattern in patterns for value in re.findall(pattern, output))
        deterministic_harness = (
            "[fail]" not in output.casefold()
            and "verify pass" in output.casefold()
            and bool(re.search(r"(?im)^\s*\[pass\]\s+\S+", output))
        )
        return counted or deterministic_harness
    # A test receipt without a profile-specific parser is not authoritative.
    return False


__all__ = ["evaluate_builtin_proof", "resolve_builtin_profile"]

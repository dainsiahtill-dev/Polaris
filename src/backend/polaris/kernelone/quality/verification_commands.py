"""Pure rejection of commands that cannot provide verifier work evidence.

This is admission policy, never an execution result or success verdict.
"""

from __future__ import annotations

from pathlib import PurePosixPath

_NON_PROOF_EXECUTABLES = frozenset(
    {
        ":",
        "echo",
        "false",
        "noop",
        "no-op",
        "printf",
        "true",
        "pwd",
        "which",
        "where",
        "whereis",
    }
)
_INTROSPECTION_ARGUMENTS = frozenset({"--help", "-h", "help", "--version", "-V", "version"})
_INTROSPECTION_TOOL_EXECUTABLES = frozenset(
    {
        "bun",
        "cargo",
        "cmake",
        "deno",
        "dotnet",
        "go",
        "java",
        "javac",
        "mvn",
        "node",
        "npm",
        "npx",
        "php",
        "pip",
        "pip3",
        "pnpm",
        "pytest",
        "python",
        "python3",
        "ruby",
        "ruff",
        "rustc",
        "tsc",
        "yarn",
    }
)
_SHORT_VERSION_EXECUTABLES = frozenset(
    {"bun", "deno", "go", "java", "node", "npm", "php", "python", "python3", "ruby", "rustc"}
)
_INLINE_INTERPRETER_FLAGS = frozenset({"-c", "-e", "--eval"})
_NOOP_INLINE_PROGRAMS = frozenset(
    {
        "0",
        "pass",
        "print('ok')",
        'print("ok")',
        "exit(0)",
        "sys.exit(0)",
        "process.exit(0)",
        "console.log('ok')",
        'console.log("ok")',
    }
)


def normalize_verification_command_modality(modality: str, argv: tuple[str, ...]) -> str:
    """A compiler typecheck is lint evidence even when called a test."""

    if modality != "test" or not argv:
        return modality
    executable = PurePosixPath(argv[0].replace("\\", "/")).name.casefold()
    compiler = executable == "tsc" or (executable == "npx" and len(argv) > 1 and argv[1] == "tsc")
    if compiler and "--noemit" in {item.casefold() for item in argv[1:]}:
        return "lint"
    return modality


def require_verification_command_proof(argv: tuple[str, ...]) -> None:
    """Reject commands that can succeed without proving any delivery work."""

    if not argv:
        raise ValueError("verification command argv must be non-empty")

    executable = PurePosixPath(argv[0].replace("\\", "/")).name.casefold()
    if executable in _NON_PROOF_EXECUTABLES:
        raise ValueError("verification command must provide proof-of-work, not a no-op")
    introspection_requested = any(argument in _INTROSPECTION_ARGUMENTS for argument in argv[1:])
    if introspection_requested and (len(argv) == 2 or executable in _INTROSPECTION_TOOL_EXECUTABLES):
        raise ValueError("verification command must provide proof-of-work, not help/version introspection")
    if len(argv) == 2 and argv[1] == "-v" and executable in _SHORT_VERSION_EXECUTABLES:
        raise ValueError("verification command must provide proof-of-work, not version introspection")
    if len(argv) >= 3 and argv[1] in _INLINE_INTERPRETER_FLAGS:
        inline_program = " ".join(argv[2:]).strip().casefold().rstrip(";")
        if inline_program in _NOOP_INLINE_PROGRAMS:
            raise ValueError("verification command must provide proof-of-work, not an inline no-op")


__all__ = ["normalize_verification_command_modality", "require_verification_command_proof"]

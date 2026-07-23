"""Tests for the pure command capability validation boundary."""

from __future__ import annotations

import ast
import asyncio
import builtins
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.llm.toolkit.executor.command_capability import (
    _ALLOWED_COMMANDS_HASH_DOMAIN,
    _EVIDENCE_HASH_DOMAIN,
    CommandCapabilityValidationInputV1,
    _hash_payload,
    validate_command_capability,
)
from polaris.kernelone.llm.toolkit.executor.handlers.command import (
    _capability_allowed_commands,
    _validate_command_capability,
)

_TOKEN_HASH = "a" * 64
_HASH_FRAME = b"polaris.kernelone.command-capability.hash.v1\0"


def test_command_capability_module_imports_only_pure_stdlib_dependencies() -> None:
    """Static fence: capability decisions cannot acquire I/O or process authority."""
    module_path = Path(__file__).resolve().parents[1] / "command_capability.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    forbidden_prefixes = (
        "multiprocessing",
        "polaris.cells",
        "polaris.kernelone.fs",
        "polaris.kernelone.process",
        "polaris.kernelone.single_agent",
        "subprocess",
    )
    assert [
        module
        for module in imported
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    ] == []


class _CapabilityExecutor:
    """Minimal handler-compatible token holder without executor construction."""

    def __init__(self, capability_token: dict[str, Any]) -> None:
        self._capability_token = capability_token


def _input(*, allowed_commands: tuple[str, ...], command: str) -> CommandCapabilityValidationInputV1:
    return CommandCapabilityValidationInputV1(
        capability_token_id="job-1",
        capability_token_hash=_TOKEN_HASH,
        allowed_commands=allowed_commands,
        canonical_command=command,
    )


def test_pure_validator_has_handler_parity_without_constructor_or_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pure validation preserves handler decisions without constructing effect services."""
    from multiprocessing.process import BaseProcess

    from polaris.kernelone.fs import KernelFileSystem
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
    from polaris.kernelone.process.command_executor import CommandExecutionService

    side_effect_calls: list[str] = []
    patched_entrypoints: set[str] = set()

    def patch_entrypoint(owner: object, name: str, label: str) -> None:
        if not hasattr(owner, name):
            return

        def fail_side_effect(*args: object, **kwargs: object) -> None:
            del args, kwargs
            side_effect_calls.append(label)
            raise AssertionError(f"command capability validation called side effect: {label}")

        monkeypatch.setattr(owner, name, fail_side_effect)
        patched_entrypoints.add(label)

    patch_entrypoint(builtins, "open", "builtins.open")
    for name in ("open", "read", "write", "system", "popen"):
        patch_entrypoint(os, name, f"os.{name}")
    for name in ("execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe"):
        patch_entrypoint(os, name, f"os.{name}")
    dynamic_process_names = (
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
    )
    expected_dynamic_process_spies = {f"os.{name}" for name in dynamic_process_names if hasattr(os, name)}
    for name in dynamic_process_names:
        patch_entrypoint(os, name, f"os.{name}")
    for name in ("open", "read_text", "read_bytes", "write_text", "write_bytes"):
        patch_entrypoint(Path, name, f"Path.{name}")
    patch_entrypoint(KernelFileSystem, "__init__", "KernelFileSystem.__init__")
    patch_entrypoint(CommandExecutionService, "__init__", "CommandExecutionService.__init__")
    patch_entrypoint(AgentAccelToolExecutor, "__init__", "AgentAccelToolExecutor.__init__")
    patch_entrypoint(subprocess, "Popen", "subprocess.Popen")
    patch_entrypoint(subprocess, "run", "subprocess.run")
    patch_entrypoint(subprocess, "call", "subprocess.call")
    patch_entrypoint(subprocess, "check_call", "subprocess.check_call")
    patch_entrypoint(subprocess, "check_output", "subprocess.check_output")
    patch_entrypoint(asyncio, "create_subprocess_exec", "asyncio.create_subprocess_exec")
    patch_entrypoint(asyncio, "create_subprocess_shell", "asyncio.create_subprocess_shell")
    patch_entrypoint(multiprocessing, "Process", "multiprocessing.Process")
    patch_entrypoint(BaseProcess, "start", "multiprocessing.process.BaseProcess.start")

    def fail_skill_install(*args: object, **kwargs: object) -> None:
        del args, kwargs
        side_effect_calls.append("install_default_skills")
        raise AssertionError("command capability validation called side effect: install_default_skills")

    monkeypatch.setattr("polaris.kernelone.single_agent.skill_system.install_default_skills", fail_skill_install)
    patched_entrypoints.add("install_default_skills")

    cases: tuple[tuple[dict[str, Any], tuple[str, ...], str, bool], ...] = (
        ({}, (), "python --version", True),
        ({"allowed_commands": ["python --version"]}, ("python --version",), "python --version", True),
        ({"allowed_commands": ["python"]}, ("python",), "python -m pytest", True),
        ({"allowed_commands": ("python", "python")}, ("python", "python"), "python --version", True),
        ({"allowed_commands": ["python"]}, ("python",), "python --version | cat", False),
        (
            {"allowed_commands": ["python --version | cat"]},
            ("python --version | cat",),
            "python --version | cat",
            True,
        ),
        ({"allowed_commands": ["python --version"]}, ("python --version",), "echo hello", False),
    )

    for token, allowed_commands, command, expected_allowed in cases:
        if allowed_commands:
            token = {
                "token_id": "job-1",
                "execution_envelope_hash": _TOKEN_HASH,
                **token,
            }
        result = validate_command_capability(_input(allowed_commands=allowed_commands, command=command))
        legacy = _validate_command_capability(_CapabilityExecutor(token), command)  # type: ignore[arg-type]

        assert result.allowed is expected_allowed
        assert (legacy is None) is expected_allowed
        if not expected_allowed:
            assert result.status == "denied"
            assert result.reason == "command_outside_allowed_commands"
            assert legacy == {
                "ok": False,
                "error": "Command blocked by capability token: command is outside allowed_commands",
                "blocked": True,
                "command": command,
                "error_type": "command_capability_denied",
                "allowed_commands": list(allowed_commands),
            }

    assert {
        "os.system",
        "os.popen",
        "subprocess.Popen",
        "multiprocessing.process.BaseProcess.start",
        "install_default_skills",
    } <= patched_entrypoints
    assert expected_dynamic_process_spies <= patched_entrypoints
    assert side_effect_calls == []


def test_input_normalizes_duplicates_and_rejects_invalid_types_or_tokens() -> None:
    """Input construction canonicalizes legal duplicates and rejects malformed evidence."""
    duplicate_input = _input(allowed_commands=("python", "python", "ruff check"), command=" python --version ")

    assert duplicate_input.allowed_commands == ("python", "ruff check")
    assert duplicate_input.canonical_command == "python --version"

    base = _input(allowed_commands=("python",), command="python --version")
    invalid_mutations = (
        {"capability_token_id": 1},
        {"capability_token_hash": 1},
        {"allowed_commands": ["python"]},
        {"allowed_commands": ("python", 1)},
        {"allowed_commands": ("   ",)},
        {"canonical_command": ""},
    )
    for mutation in invalid_mutations:
        with pytest.raises(ValueError):
            replace(base, **mutation)


def test_input_accepts_opaque_legacy_token_evidence_and_partial_missing_values() -> None:
    """Capability evidence is opaque correlation data, not a Job Token format gate."""
    variants = (
        ("legacy-token", "env-hash"),
        ("legacy-token", ""),
        ("", "env-hash"),
        ("", ""),
    )
    for token_id, token_hash in variants:
        validation_input = CommandCapabilityValidationInputV1(
            capability_token_id=token_id,
            capability_token_hash=token_hash,
            allowed_commands=(),
            canonical_command="python --version",
        )

        result = validate_command_capability(validation_input)

        assert result.allowed is True
        assert result.status == "allowed"
        assert result.reason is None
        assert result.capability_token_id == token_id
        assert result.capability_token_hash == token_hash


def test_result_contract_rejects_forged_or_cross_field_inconsistent_states() -> None:
    """Result construction accepts only canonical, hash-bound validator semantics."""
    valid = validate_command_capability(_input(allowed_commands=("python",), command="python --version"))

    assert not hasattr(valid, "__dict__")
    assert valid.allowed_commands_hash.islower()
    assert len(valid.allowed_commands_hash) == 64
    assert valid.evidence_hash.islower()
    assert len(valid.evidence_hash) == 64
    with pytest.raises(FrozenInstanceError):
        valid.allowed = False  # type: ignore[misc]

    invalid_mutations = (
        {"allowed": 1},
        {"status": "open"},
        {"reason": "not_closed"},
        {"allowed_commands": ["python"]},
        {"allowed_commands": ("ruff check", "python")},
        {"canonical_command": " python --version"},
        {"allowed_commands_hash": "0" * 64},
        {"evidence_hash": "0" * 64},
        {"allowed": False, "status": "denied", "reason": "command_outside_allowed_commands"},
    )
    for mutation in invalid_mutations:
        with pytest.raises(ValueError):
            replace(valid, **mutation)


def test_container_order_and_duplicates_have_one_canonical_authorization_result() -> None:
    """Capability list containers project to one sorted set and one evidence identity."""
    variants: tuple[object, ...] = (
        ["ruff check", "python", "python"],
        ("python", "ruff check"),
        {"ruff check", "python"},
    )
    results = []
    canonical_inputs = []
    for variant in variants:
        extracted = _capability_allowed_commands(
            _CapabilityExecutor({"allowed_commands": variant})  # type: ignore[arg-type]
        )
        validation_input = _input(allowed_commands=tuple(extracted), command="python --version")
        canonical_inputs.append(validation_input.allowed_commands)
        results.append(validate_command_capability(validation_input))

    assert canonical_inputs == [("python", "ruff check")] * len(variants)
    assert all(result.allowed for result in results)
    assert len({result.allowed_commands_hash for result in results}) == 1
    assert len({result.evidence_hash for result in results}) == 1


@pytest.mark.parametrize(
    ("token", "command", "expected_allowed_commands", "expected_denial"),
    (
        (
            {"token_id": "partial-token", "allowed_commands": []},
            "echo unrestricted",
            [],
            None,
        ),
        (
            {"execution_envelope_hash": "env-hash", "allowed_commands": " python "},
            "python --version",
            ["python"],
            None,
        ),
        (
            {
                "token_id": "legacy-token",
                "execution_envelope_hash": "not-a-64-hex-hash",
                "allowed_commands": [" python ", ("ruff check", ["pytest", "   ", 17])],
            },
            "ruff check .",
            ["python", "ruff check", "pytest"],
            None,
        ),
        (
            {"allowed_commands": "   ", "authorized_commands": " python "},
            "python --version",
            ["python"],
            None,
        ),
        (
            {
                "token_id": "legacy-token",
                "execution_envelope_hash": "env-hash",
                "allowed_commands": [" ruff check ", ("python", "python"), [" pytest "]],
            },
            "echo denied",
            ["ruff check", "python", "python", "pytest"],
            {
                "ok": False,
                "error": "Command blocked by capability token: command is outside allowed_commands",
                "blocked": True,
                "command": "echo denied",
                "error_type": "command_capability_denied",
                "allowed_commands": ["ruff check", "python", "python", "pytest"],
            },
        ),
    ),
)
def test_head_legacy_extraction_match_and_denial_payload_matrix(
    token: dict[str, Any],
    command: str,
    expected_allowed_commands: list[str],
    expected_denial: dict[str, Any] | None,
) -> None:
    """Compatibility projection preserves HEAD extraction, match, and payload behavior."""
    executor = _CapabilityExecutor(token)

    assert _capability_allowed_commands(executor) == expected_allowed_commands  # type: ignore[arg-type]
    assert _validate_command_capability(executor, command) == expected_denial  # type: ignore[arg-type]


def test_hashes_are_stable_across_python_hash_seeds() -> None:
    """Set iteration order cannot alter canonical commands or evidence hashes."""
    script = f"""
import json
from polaris.kernelone.llm.toolkit.executor.command_capability import CommandCapabilityValidationInputV1, validate_command_capability
commands = tuple({{"ruff check", "python", "pytest", "mypy", "git status"}})
validation_input = CommandCapabilityValidationInputV1(
    capability_token_id="job-1",
    capability_token_hash={_TOKEN_HASH!r},
    allowed_commands=commands,
    canonical_command="python --version",
)
result = validate_command_capability(validation_input)
print(json.dumps([validation_input.allowed_commands, result.allowed_commands_hash, result.evidence_hash]))
"""
    outputs = []
    for seed in ("1", "2", "17", "101"):
        child_env = dict(os.environ)
        child_env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=child_env,
            text=True,
        )
        outputs.append(json.loads(completed.stdout))

    assert outputs == [outputs[0]] * len(outputs)


def test_pure_validator_hashes_are_deterministic_and_domain_separated() -> None:
    """Fixed domain framing separates recomputable hashes for the same payload."""
    same_payload: dict[str, object] = {"allowed_commands": ["python", "ruff check"]}
    canonical_payload = json.dumps(same_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    expected_allowed_hash = hashlib.sha256(
        _HASH_FRAME + _ALLOWED_COMMANDS_HASH_DOMAIN.encode() + b"\0" + canonical_payload
    ).hexdigest()
    expected_evidence_hash = hashlib.sha256(
        _HASH_FRAME + _EVIDENCE_HASH_DOMAIN.encode() + b"\0" + canonical_payload
    ).hexdigest()

    assert _hash_payload(_ALLOWED_COMMANDS_HASH_DOMAIN, same_payload) == expected_allowed_hash
    assert _hash_payload(_EVIDENCE_HASH_DOMAIN, same_payload) == expected_evidence_hash
    assert expected_allowed_hash != expected_evidence_hash

    command = "python --version"
    first = validate_command_capability(_input(allowed_commands=(command,), command=command))
    second = validate_command_capability(_input(allowed_commands=(command,), command=command))

    assert first == second
    assert first.allowed_commands == (command,)
    assert first.allowed_commands_hash
    assert first.evidence_hash
    assert first.allowed_commands_hash != first.evidence_hash

"""Pure command capability validation for the KernelOne executor boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

CommandCapabilityDenialReasonV1 = Literal["command_outside_allowed_commands"]
CommandCapabilityValidationStatusV1 = Literal["allowed", "denied"]

_ALLOWED_COMMANDS_HASH_DOMAIN = "polaris.kernelone.command-capability.allowed-commands.v1"
_EVIDENCE_HASH_DOMAIN = "polaris.kernelone.command-capability.evidence.v1"
_HASH_FRAME = b"polaris.kernelone.command-capability.hash.v1\0"
_SHELL_OPERATORS = ("|", "&&", "||", ";", ">", "<")
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class CommandCapabilityValidationInputV1:
    """Detached inputs for one command capability decision."""

    capability_token_id: str
    capability_token_hash: str
    allowed_commands: tuple[str, ...]
    canonical_command: str

    def __post_init__(self) -> None:
        if type(self.canonical_command) is not str:
            raise ValueError("canonical_command must be exact str")
        canonical_command = _canonicalize_command(self.canonical_command)
        if not canonical_command:
            raise ValueError("canonical_command must not be empty")
        allowed_commands = _canonicalize_allowed_commands(self.allowed_commands)
        _validate_capability_token_binding(
            capability_token_id=self.capability_token_id,
            capability_token_hash=self.capability_token_hash,
            commands_are_scoped=bool(allowed_commands),
        )
        object.__setattr__(self, "canonical_command", canonical_command)
        object.__setattr__(self, "allowed_commands", allowed_commands)


@dataclass(frozen=True, slots=True)
class CommandCapabilityValidationResultV1:
    """Immutable result of one command capability decision."""

    capability_token_id: str
    capability_token_hash: str
    allowed: bool
    status: CommandCapabilityValidationStatusV1
    reason: CommandCapabilityDenialReasonV1 | None
    canonical_command: str
    allowed_commands: tuple[str, ...]
    allowed_commands_hash: str
    evidence_hash: str

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise ValueError("allowed must be exact bool")
        if type(self.status) is not str or self.status not in ("allowed", "denied"):
            raise ValueError("status must be a closed command capability status")
        if self.reason is not None and (
            type(self.reason) is not str or self.reason != "command_outside_allowed_commands"
        ):
            raise ValueError("reason must be the closed command capability denial reason")
        if type(self.canonical_command) is not str or not self.canonical_command:
            raise ValueError("canonical_command must be a non-empty exact str")
        if _canonicalize_command(self.canonical_command) != self.canonical_command:
            raise ValueError("canonical_command must already be canonical")

        allowed_commands = _canonicalize_allowed_commands(self.allowed_commands)
        if allowed_commands != self.allowed_commands:
            raise ValueError("allowed_commands must already be sorted and unique")
        _validate_capability_token_binding(
            capability_token_id=self.capability_token_id,
            capability_token_hash=self.capability_token_hash,
            commands_are_scoped=bool(allowed_commands),
        )
        _validate_sha256(self.allowed_commands_hash, field_name="allowed_commands_hash")
        _validate_sha256(self.evidence_hash, field_name="evidence_hash")

        expected_allowed = _command_is_allowed(self.canonical_command, allowed_commands)
        expected_status: CommandCapabilityValidationStatusV1 = "allowed" if expected_allowed else "denied"
        expected_reason: CommandCapabilityDenialReasonV1 | None = (
            None if expected_allowed else "command_outside_allowed_commands"
        )
        if self.allowed is not expected_allowed:
            raise ValueError("allowed does not match command capability semantics")
        if self.status != expected_status:
            raise ValueError("status does not match allowed")
        if self.reason != expected_reason:
            raise ValueError("reason does not match allowed status")

        expected_allowed_commands_hash = _allowed_commands_hash(allowed_commands)
        if self.allowed_commands_hash != expected_allowed_commands_hash:
            raise ValueError("allowed_commands_hash does not bind allowed_commands")
        expected_evidence_hash = _validation_evidence_hash(
            capability_token_id=self.capability_token_id,
            capability_token_hash=self.capability_token_hash,
            allowed=self.allowed,
            status=self.status,
            reason=self.reason,
            canonical_command=self.canonical_command,
            allowed_commands=allowed_commands,
            allowed_commands_hash=self.allowed_commands_hash,
        )
        if self.evidence_hash != expected_evidence_hash:
            raise ValueError("evidence_hash does not bind the validation result")


def _canonicalize_command(command: str) -> str:
    token = command.strip()
    if not token:
        return ""
    token = re.sub(r"\*\*(.*?)\*\*", r"\1", token, flags=re.DOTALL)
    token = token.replace("`", "")
    token = token.replace("\r\n", "\n").replace("\r", "\n")
    token = re.sub(r"\n+\*+\s*$", "", token).strip()
    token = re.sub(r"\s+\*+\s*$", "", token).strip()
    token = token.replace("\n", " ").strip()
    return re.sub(r"\s{2,}", " ", token)


def _canonicalize_allowed_commands(allowed_commands: tuple[str, ...]) -> tuple[str, ...]:
    if type(allowed_commands) is not tuple:
        raise ValueError("allowed_commands must be exact tuple[str, ...]")
    canonical_commands: list[str] = []
    for command in allowed_commands:
        if type(command) is not str:
            raise ValueError("allowed command tokens must be exact str")
        canonical_command = _canonicalize_command(command)
        if not canonical_command or "\x00" in canonical_command:
            raise ValueError("allowed command tokens must be non-empty canonical text")
        canonical_commands.append(canonical_command)
    return tuple(sorted(set(canonical_commands)))


def _validate_capability_token_binding(
    *,
    capability_token_id: str,
    capability_token_hash: str,
    commands_are_scoped: bool,
) -> None:
    if type(capability_token_id) is not str:
        raise ValueError("capability_token_id must be exact str")
    if type(capability_token_hash) is not str:
        raise ValueError("capability_token_hash must be exact str")
    del commands_are_scoped


def _validate_sha256(value: str, *, field_name: str) -> None:
    if type(value) is not str or _SHA256_HEX_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase 64-character SHA256 hex")


def _contains_shell_operators(command: str) -> bool:
    return any(marker in command for marker in _SHELL_OPERATORS)


def _hash_payload(domain: str, payload: dict[str, object]) -> str:
    canonical_payload = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(_HASH_FRAME + domain.encode() + b"\0" + canonical_payload.encode()).hexdigest()


def _allowed_commands_hash(allowed_commands: tuple[str, ...]) -> str:
    return _hash_payload(
        _ALLOWED_COMMANDS_HASH_DOMAIN,
        {"allowed_commands": allowed_commands},
    )


def _validation_evidence_hash(
    *,
    capability_token_id: str,
    capability_token_hash: str,
    allowed: bool,
    status: CommandCapabilityValidationStatusV1,
    reason: CommandCapabilityDenialReasonV1 | None,
    canonical_command: str,
    allowed_commands: tuple[str, ...],
    allowed_commands_hash: str,
) -> str:
    return _hash_payload(
        _EVIDENCE_HASH_DOMAIN,
        {
            "allowed": allowed,
            "allowed_commands": allowed_commands,
            "allowed_commands_hash": allowed_commands_hash,
            "canonical_command": canonical_command,
            "capability_token_hash": capability_token_hash,
            "capability_token_id": capability_token_id,
            "reason": reason,
            "status": status,
        },
    )


def _command_is_allowed(canonical_command: str, allowed_commands: tuple[str, ...]) -> bool:
    if not allowed_commands:
        return True
    has_shell_operator = _contains_shell_operators(canonical_command)
    return any(
        canonical_command == allowed_command
        or (not has_shell_operator and canonical_command.startswith(f"{allowed_command} "))
        for allowed_command in allowed_commands
    )


def validate_command_capability(
    validation_input: CommandCapabilityValidationInputV1,
) -> CommandCapabilityValidationResultV1:
    """Return a deterministic, side-effect-free command capability decision."""
    if type(validation_input) is not CommandCapabilityValidationInputV1:
        raise ValueError("validation_input must be CommandCapabilityValidationInputV1")
    canonical_command = validation_input.canonical_command
    allowed_commands = validation_input.allowed_commands
    allowed_commands_hash = _allowed_commands_hash(allowed_commands)
    command_is_allowed = _command_is_allowed(canonical_command, allowed_commands)
    status: CommandCapabilityValidationStatusV1 = "allowed" if command_is_allowed else "denied"
    reason: CommandCapabilityDenialReasonV1 | None = None if command_is_allowed else "command_outside_allowed_commands"
    evidence_hash = _validation_evidence_hash(
        capability_token_id=validation_input.capability_token_id,
        capability_token_hash=validation_input.capability_token_hash,
        allowed=command_is_allowed,
        status=status,
        reason=reason,
        canonical_command=canonical_command,
        allowed_commands=allowed_commands,
        allowed_commands_hash=allowed_commands_hash,
    )
    return CommandCapabilityValidationResultV1(
        capability_token_id=validation_input.capability_token_id,
        capability_token_hash=validation_input.capability_token_hash,
        allowed=command_is_allowed,
        status=status,
        reason=reason,
        canonical_command=canonical_command,
        allowed_commands=allowed_commands,
        allowed_commands_hash=allowed_commands_hash,
        evidence_hash=evidence_hash,
    )

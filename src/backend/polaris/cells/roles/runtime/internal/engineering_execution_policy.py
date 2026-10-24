"""Host-owned shell selection; no model argument can select execution policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandExecutionPolicy:
    """One immutable launcher supplies both actual argv and its attempt evidence."""

    name: str
    argv_prefix: tuple[str, ...]

    def argv(self, command: str) -> list[str]:
        return [*self.argv_prefix, command]

    def binding(self, commands: list[str]) -> dict[str, object]:
        return {"name": self.name, "argv": [self.argv(command) for command in commands]}


def resolve_execution_policy(name: str | None) -> CommandExecutionPolicy:
    """Missing historical policy explicitly retains POSIX; unknown fails closed."""
    if name is None or name == "posix_sh_v1":
        return CommandExecutionPolicy("posix_sh_v1", ("/bin/sh", "-c"))
    if name == "bash_pipefail_v1":
        return CommandExecutionPolicy(name, ("/bin/bash", "--noprofile", "--norc", "-o", "pipefail", "-c"))
    raise ValueError(f"unsupported verification execution policy: {name!r}")

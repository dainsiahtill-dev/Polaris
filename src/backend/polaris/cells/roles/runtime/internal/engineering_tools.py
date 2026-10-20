"""Small engineering tool surface over KFS and physically owned command execution."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits, ToolObservation
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments_from_snapshot
from polaris.kernelone.process.process_tree import (
    ProcessTreeCancelledError,
    ProcessTreeDrainError,
    ProcessTreeRunControl,
    run_process_tree_safe,
)
from polaris.kernelone.process.sandbox import WorkspaceSandboxUnavailableError, build_workspace_sandbox_argv
from polaris.kernelone.tool_execution.contracts import frozen_node_to_value
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

ENGINEERING_TOOLS = frozenset({"read_file", "repo_tree", "repo_rg", "write_file", "edit_file", "execute_command"})


def bounded_output(content: str, maximum: int) -> str:
    """Bound UTF-8 output and preserve explicit evidence that bytes were omitted."""
    encoded = content.encode("utf-8")
    if len(encoded) <= maximum:
        return content
    return (
        encoded[:maximum].decode("utf-8", errors="ignore")
        + f"\n[output truncated: {len(encoded) - maximum} bytes omitted]"
    )


class EngineeringTools:
    """Session authority is explicit; arguments and model text cannot expand it."""

    def __init__(
        self,
        workspace: Path,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
        *,
        consult: Callable[[str, str], Awaitable[str]] | None = None,
        review: Callable[[], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.scope = scope
        self.limits = limits
        self.cancel = cancel
        self.consult = consult
        self.review = review
        self.filesystem = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())
        self.enabled = frozenset(ToolSpecRegistry.get_canonical(name) for name in scope.enabled_tools) & (
            ENGINEERING_TOOLS
            | ({"consult_role"} if consult is not None else set())
            | ({"report_review"} if review is not None else set())
        )
        if limits.max_output_bytes <= 0 or limits.max_tool_output_bytes <= 0:
            raise ValueError("engineering output limits must be positive")
        if not self.workspace.is_dir():
            raise ValueError("engineering workspace must be a directory")
        for root in (*scope.read_roots, *scope.write_roots):
            candidate = Path(root)
            # Writes are workspace-local. Explicit external dependency reads
            # are supported by the command sandbox, not by workspace file tools.
            if candidate.is_relative_to(self.workspace):
                self.filesystem.resolve_workspace_path(str(candidate))
            if candidate.resolve().is_relative_to(self.workspace / ".polaris"):
                raise ValueError("engineering grants cannot enter the control root")
        if any(not Path(root).resolve().is_relative_to(self.workspace) for root in scope.write_roots):
            raise ValueError("engineering write roots must stay inside workspace")

    def schemas(self) -> list[dict[str, Any]]:
        """Derive schemas from the registry, narrowing edits to exact replacement."""
        schemas = []
        for name in sorted(self.enabled):
            schema = ToolSpecRegistry.get_llm_schema(name)
            if schema is None:
                raise ValueError(f"missing engineering tool schema: {name}")
            if name == "edit_file":
                function = schema["function"]
                parameters = function["parameters"]
                parameters["properties"] = {
                    key: value
                    for key, value in parameters["properties"].items()
                    if key in {"file", "search", "replace"}
                }
                parameters["required"] = ["file", "search", "replace"]
                parameters.pop("anyOf", None)
                function["description"] = "Replace one unique exact occurrence of search with replace in a UTF-8 file."
            schemas.append(schema)
        return schemas

    def normalize(self, tool: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Normalize one native call through the sole registry owner."""
        snapshot = ToolSpecRegistry.capture_effective_spec(tool)
        if not snapshot.registered:
            raise ValueError(f"unknown engineering tool: {tool}")
        name = snapshot.canonical_tool_name
        specification = frozen_node_to_value(snapshot.canonical_effective_spec)
        aliases = specification.get("arg_aliases", {}) if isinstance(specification, dict) else {}
        # A transport synonym cannot choose between two different destinations
        # or payloads. Edit dialect disambiguation belongs to the K1 normalizer.
        if name != "edit_file" and isinstance(aliases, dict):
            operands: dict[str, list[Any]] = {}
            for key, value in arguments.items():
                canonical = aliases.get(key, key)
                operands.setdefault(canonical, []).append(value)
            if any(any(value != values[0] for value in values[1:]) for values in operands.values()):
                raise ValueError("conflicting engineering tool argument aliases")
        normalized = dict(normalize_tool_arguments_from_snapshot(snapshot, arguments))
        if name not in self.enabled:
            raise ValueError(f"engineering tool disabled: {name}")
        return name, normalized

    async def execute(self, call_id: str, tool: str, arguments: dict[str, Any]) -> ToolObservation:
        """Execute one call in source order; errors remain explicit observations."""
        name = tool
        try:
            name, args = self.normalize(tool, arguments)
            if self.cancel.is_set():
                return ToolObservation(call_id, name, False, "Execution cancelled before dispatch.", None, True)
            self._remaining()
            if name == "report_review":
                self._keys(args, {"approved", "feedback", "major_findings"})
                if self.review is None or type(args.get("approved")) is not bool:
                    raise ValueError("report_review requires an actual QA frame and boolean approved")
                feedback = self._text(args, "feedback")
                findings = args.get("major_findings", [])
                if not isinstance(findings, list) or any(
                    not isinstance(item, str) or not item.strip() for item in findings
                ):
                    raise ValueError("major_findings must contain nonempty text")
                self.review()
                return self._observation(
                    call_id,
                    name,
                    True,
                    json.dumps(
                        {"approved": args["approved"], "feedback": feedback, "major_findings": findings},
                        ensure_ascii=False,
                    ),
                )
            if name == "consult_role":
                self._keys(args, {"role", "question"})
                role, question = self._text(args, "role"), self._text(args, "question")
                if role not in {"pm", "chief_engineer"} or self.consult is None:
                    raise ValueError("consult_role requires PM or Chief Engineer and an active session owner")
                return self._observation(call_id, name, True, await self.consult(role, question))
            if name == "execute_command":
                self._keys(args, {"command", "timeout"})
                command = self._text(args, "command")
                timeout = self._integer(args, "timeout", 30, minimum=1, maximum=120)
                completed = await self._process(
                    ["/bin/sh", "-c", command], timeout=timeout, writes=self.scope.write_roots
                )
                content = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
                return self._observation(call_id, name, completed.returncode == 0, content, completed.returncode)
            if name == "repo_rg":
                self._keys(args, {"pattern", "paths", "path", "max_results", "glob", "context_lines", "case_sensitive"})
                pattern = self._text(args, "pattern")
                maximum = self._integer(args, "max_results", 50, minimum=1, maximum=10000)
                context = self._integer(args, "context_lines", 0, minimum=0, maximum=100)
                paths = args.get("paths", [args.get("path", ".")])
                if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
                    raise ValueError("repo_rg paths must be a nonempty string list")
                argv = [
                    "rg",
                    "--line-number",
                    "--no-heading",
                    "--color",
                    "never",
                    "--max-count",
                    str(maximum),
                    "--context",
                    str(context),
                ]
                if args.get("case_sensitive", False) is False:
                    argv.append("--ignore-case")
                elif args["case_sensitive"] is not True:
                    raise ValueError("case_sensitive must be boolean")
                if "glob" in args:
                    argv.extend(("--glob", self._text(args, "glob")))
                argv.extend(("--", pattern, *(str(self._path(path, write=False)) for path in paths)))
                completed = await self._process(argv, timeout=30, writes=())
                content = completed.stdout.replace(str(self.workspace) + "/", "")
                if completed.stderr:
                    content += "\nstderr:\n" + completed.stderr
                return self._observation(call_id, name, completed.returncode in {0, 1}, content, completed.returncode)
            if name == "repo_tree":
                self._keys(args, {"path", "depth", "max_entries", "recursive", "include_hidden"})
                root = self._path(self._text(args, "path", "."), write=False)
                depth = self._integer(args, "depth", 3, minimum=0, maximum=30)
                maximum = self._integer(args, "max_entries", 1000, minimum=1, maximum=10000)
                if (
                    type(args.get("include_hidden", False)) is not bool
                    or type(args.get("recursive", False)) is not bool
                ):
                    raise ValueError("tree options must be boolean")
                include_hidden = args.get("include_hidden", False)
                entries: list[str] = []
                for current, directories, files in os.walk(root, followlinks=False):
                    directories[:] = sorted(
                        entry
                        for entry in directories
                        if entry != ".polaris"
                        and not Path(current, entry).is_symlink()
                        and (include_hidden or not entry.startswith("."))
                    )
                    relative = Path(current).relative_to(root)
                    if len(relative.parts) >= depth:
                        directories[:] = []
                    for entry in sorted([*directories, *files]):
                        target = Path(current, entry)
                        if entry == ".polaris" or target.is_symlink() or (not include_hidden and entry.startswith(".")):
                            continue
                        self._path(str(target), write=False)
                        entries.append(str(target.relative_to(root)) + ("/" if target.is_dir() else ""))
                        if len(entries) >= maximum:
                            return self._observation(
                                call_id, name, True, "\n".join(entries) + "\n[tree truncated: max_entries reached]"
                            )
                return self._observation(call_id, name, True, "\n".join(entries))
            if name == "read_file":
                self._keys(args, {"file", "max_bytes", "start_line", "end_line", "range_required"})
                target = self._path(self._text(args, "file"), write=False)
                content = self.filesystem.workspace_read_text(str(target), encoding="utf-8")
                if args.get("range_required") and not {"start_line", "end_line"}.issubset(args):
                    raise ValueError("read_file requires a start_line and end_line range")
                start = self._integer(args, "start_line", 1, minimum=1)
                end = self._integer(args, "end_line", max(1, len(content.splitlines())), minimum=start)
                content = "".join(content.splitlines(keepends=True)[start - 1 : end])
                maximum = self._integer(args, "max_bytes", self.limits.max_tool_output_bytes, minimum=1)
                return self._observation(
                    call_id, name, True, bounded_output(content, min(maximum, self.limits.max_tool_output_bytes))
                )
            if name == "write_file":
                self._keys(args, {"file", "content"})
                target = self._path(self._text(args, "file"), write=True)
                receipt = self.filesystem.workspace_write_text_atomic(
                    str(target), self._text(args, "content", allow_empty=True), encoding="utf-8"
                )
                return self._observation(
                    call_id,
                    name,
                    True,
                    f"Wrote {receipt.bytes_written} UTF-8 bytes to {target.relative_to(self.workspace)}.",
                )
            if name == "edit_file":
                self._keys(args, {"file", "search", "replace", "regex", "replace_all"})
                if args.get("regex", False) is not False or args.get("replace_all", False) is not False:
                    raise ValueError("edit_file supports one unique exact replacement only")
                target = self._path(self._text(args, "file"), write=True)
                self._path(str(target), write=False)
                old, new = self._text(args, "search"), self._text(args, "replace", allow_empty=True)
                content = self.filesystem.workspace_read_text(str(target), encoding="utf-8")
                count = content.count(old)
                if count != 1:
                    raise ValueError(f"edit_file requires one exact search occurrence; observed {count}")
                self.filesystem.workspace_write_text_atomic(str(target), content.replace(old, new, 1), encoding="utf-8")
                return self._observation(
                    call_id, name, True, f"Replaced one exact occurrence in {target.relative_to(self.workspace)}."
                )
            raise ValueError(f"unsupported engineering tool: {name}")
        except ProcessTreeDrainError:
            # A missing drain proof must escape to the execution owner. It is
            # not an ordinary failed command or authority to release a writer.
            raise
        except ProcessTreeCancelledError as exc:
            return self._observation(call_id, name, False, str(exc), interrupted=True)
        except subprocess.TimeoutExpired as exc:
            return self._observation(
                call_id, name, False, f"Command timed out after physical cleanup: {exc}", interrupted=True
            )
        except (ValueError, OSError, UnicodeError, PathSecurityError, WorkspaceSandboxUnavailableError) as exc:
            return self._observation(call_id, name, False, f"{type(exc).__name__}: {exc}")

    async def _process(
        self, argv: list[str], *, timeout: float, writes: tuple[Path, ...]
    ) -> subprocess.CompletedProcess[str]:
        prepared = build_workspace_sandbox_argv(
            argv,
            workspace=self.workspace,
            cwd=self.workspace,
            read_roots=self.scope.read_roots,
            write_roots=writes,
            network_allowed=self.scope.network_allowed,
        )
        control = ProcessTreeRunControl()
        worker = asyncio.create_task(
            asyncio.to_thread(
                run_process_tree_safe,
                prepared,
                cwd=self.workspace,
                timeout=min(timeout, self._remaining()),
                encoding="utf-8",
                cancel_control=control,
            )
        )
        cancellation = asyncio.create_task(self.cancel.wait())
        try:
            done, _pending = await asyncio.wait({worker, cancellation}, return_when=asyncio.FIRST_COMPLETED)
            if cancellation in done:
                control.cancel()
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            self.cancel.set()
            control.cancel()
            with contextlib.suppress(ProcessTreeCancelledError, subprocess.TimeoutExpired):
                await asyncio.shield(worker)
            raise
        finally:
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)

    def _path(self, value: str, *, write: bool) -> Path:
        target = self.filesystem.resolve_workspace_path(value)
        control = self.workspace / ".polaris"
        if (write and target == self.workspace) or target.is_relative_to(control):
            raise ValueError("engineering tools cannot modify workspace root or access control files")
        roots = self.scope.write_roots if write else self.scope.read_roots
        if not any(target.is_relative_to(Path(root).resolve()) for root in roots):
            raise ValueError(f"engineering {'write' if write else 'read'} path outside permission scope: {value}")
        return target

    def _remaining(self) -> float:
        remaining = self.limits.deadline - time.time() if self.limits.deadline is not None else 120.0
        if remaining <= 0:
            raise ValueError("engineering run deadline exceeded")
        return remaining

    def _observation(
        self, call_id: str, name: str, ok: bool, content: str, exit_code: int | None = None, interrupted: bool = False
    ) -> ToolObservation:
        return ToolObservation(
            call_id, name, ok, bounded_output(content, self.limits.max_tool_output_bytes), exit_code, interrupted
        )

    @staticmethod
    def _keys(arguments: dict[str, Any], allowed: set[str]) -> None:
        unknown = arguments.keys() - allowed
        if unknown:
            raise ValueError(f"unsupported engineering tool arguments: {', '.join(sorted(unknown))}")

    @staticmethod
    def _text(arguments: dict[str, Any], name: str, default: str | None = None, *, allow_empty: bool = False) -> str:
        value = arguments.get(name, default)
        if not isinstance(value, str) or "\0" in value or (not allow_empty and not value.strip()):
            raise ValueError(f"{name} must be a {'possibly empty ' if allow_empty else 'nonempty '}string without NUL")
        return value

    @staticmethod
    def _integer(
        arguments: dict[str, Any], name: str, default: int, *, minimum: int, maximum: int | None = None
    ) -> int:
        value = arguments.get(name, default)
        if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
            raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
        return value

"""Small engineering tool surface over KFS and physically owned command execution."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import mimetypes
import os
import re
import shlex
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_acceptance import (
    ClauseFinding,
    ClauseMatchResult,
    match_clause_findings,
    normalize_clause_findings,
)
from polaris.cells.roles.runtime.internal.engineering_execution_policy import (
    CommandExecutionPolicy,
    resolve_execution_policy,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits, ToolObservation
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs import GuardedRegularFileSnapshotError
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments_from_snapshot
from polaris.kernelone.process import (
    CommandCacheUnavailableError,
    CommandOutputCapture,
    resolve_command_cache,
    resolve_toolchain_bin_dirs,
)
from polaris.kernelone.process.process_tree import (
    ProcessTreeCancelledError,
    ProcessTreeDrainError,
    ProcessTreeRunControl,
    run_process_tree_safe,
)
from polaris.kernelone.process.sandbox import WorkspaceSandboxUnavailableError, build_workspace_sandbox_argv
from polaris.kernelone.tool_execution.contracts import frozen_node_to_value
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

ENGINEERING_TOOLS = frozenset(
    {"read_file", "read_command_result", "repo_tree", "repo_rg", "write_file", "edit_file", "execute_command"}
)
COMMAND_TIMEOUT_DEFAULT = 30
COMMAND_TIMEOUT_MINIMUM = 1
COMMAND_TIMEOUT_MAXIMUM = 120
_REPO_RG_KEYS = frozenset({"pattern", "paths", "path", "max_results", "glob", "context_lines", "case_sensitive"})
_EXIT_MASKING_SUFFIX = re.compile(r"(?:;|\|\|)\s*(?:true|/bin/true|echo\b.*|printf\b.*|exit\s+0)\s*$")
# A decomposed goal is ruled on through its clause matrix, so a partial matrix
# is sent back. Real runs showed the small reviewer being rejected 86% of the
# time and looping until the QA budget ran out; two corrective attempts are
# enough to teach the numbered form, and a later partial matrix is then graded
# honestly as unknown instead of burning the whole frame.
_MAX_COVERAGE_REJECTS = 2


_PLACEHOLDER_EVIDENCE = {
    "review not started",
    "review not yet performed",
    "placeholder",
    "placeholder - review not yet performed",
    "not reviewed",
    "pending review",
    "pending source review",
    "todo",
    "n/a",
    "none",
    "unknown",
}


def _is_placeholder_evidence(text: str) -> bool:
    """A stock placeholder is not an observation.

    Real runs show the small reviewer handing back ``Review not started.`` for
    every clause after having read the source and run the checks, which ends the
    run on a canned rejection instead of a finding.
    """

    stripped = text.strip()
    lowered = stripped.lower().rstrip(".")
    if lowered in _PLACEHOLDER_EVIDENCE:
        return True
    # A real receipt is evidence regardless of length.
    if re.fullmatch(r"(?:call_|verif(?:y|ication)-|command:|filecard:|event-)[A-Za-z0-9_.:-]+", stripped):
        return False
    ideographs = sum(1 for char in stripped if ord(char) > 0x2E80)
    letters = sum(1 for char in stripped if char.isascii() and char.isalpha())
    if ideographs < 4 and letters < 8:
        return True
    # Short text that merely opens with a stock marker is still a placeholder.
    return len(stripped.split()) <= 4 and any(lowered.startswith(item) for item in _PLACEHOLDER_EVIDENCE)


def bounded_output(content: str, maximum: int) -> str:
    """Bound UTF-8 output and preserve explicit evidence that bytes were omitted."""
    encoded = content.encode("utf-8")
    if len(encoded) <= maximum:
        return content
    return (
        encoded[:maximum].decode("utf-8", errors="ignore")
        + f"\n[output truncated: {len(encoded) - maximum} bytes omitted]"
    )


def _shell_file_read(command: str) -> dict[str, Any] | None:
    """Normalize only unambiguous read-only cat/sed forms into FileCard reads."""

    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    while len(parts) >= 4 and parts[0] in {"cd", "echo"} and parts[2] in {"&&", ";"}:
        parts = parts[3:]
    if (
        not parts
        or any(token in parts for token in (">", ">>", "<", "`", "$("))
        or any("$" in token for token in parts)
    ):
        return None
    if len(parts) == 2 and parts[0] == "cat":
        return {"file": parts[1], "_normalized_from": "execute_command"}
    if len(parts) == 3 and parts[:2] == ["cat", "-n"]:
        return {"file": parts[2], "_normalized_from": "execute_command"}
    if len(parts) == 4 and parts[:2] == ["sed", "-n"]:
        match = re.fullmatch(r"(\d+),(\d+)p", parts[2])
        if match:
            return {
                "file": parts[3],
                "start_line": int(match.group(1)),
                "end_line": int(match.group(2)),
                "_normalized_from": "execute_command",
            }
    if len(parts) == 5 and parts[0] == "cat" and parts[2] == "|" and parts[3] == "head" and parts[4].isdigit():
        return {"file": parts[1], "end_line": int(parts[4]), "_normalized_from": "execute_command"}
    return None


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
        review: Callable[[bool, list[ClauseFinding]], None] | None = None,
        verify: Callable[[tuple[str, ...]], Awaitable[ToolObservation]] | None = None,
        observe: Callable[[dict[str, Any]], Awaitable[ToolObservation]] | None = None,
        authorize: Callable[[], None] | None = None,
        artifact_binding: Callable[[], dict[str, Any]] | None = None,
        review_clauses: tuple[str, ...] = (),
        run_id: str | None = None,
        file_card_resolver: Callable[[str], dict[str, Any]] | None = None,
        revision_provider: Callable[[], int] | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        # Acceptance clauses of the original goal, when this session is a QA
        # review. They make the structured clause matrix a dispatch-boundary
        # requirement for multi-clause goals instead of an optional argument a
        # weak model can silently omit.
        self.review_clauses = tuple(review_clauses)
        self._coverage_rejections = 0
        self.scope = replace(
            scope,
            toolchain_bin_dirs=resolve_toolchain_bin_dirs(
                scope.toolchain_bin_dirs,
                workspace=self.workspace,
                read_roots=scope.read_roots,
                write_roots=scope.write_roots,
            ),
        )
        self.limits = limits
        self.cancel = cancel
        self.consult = consult
        self.review = review
        self.verify = verify
        self.observe = observe
        self.authorize = authorize
        self.artifact_binding = artifact_binding
        self.file_card_resolver = file_card_resolver
        self.revision_provider = revision_provider
        safe_run_id = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id or "shared")[:96]
        self.run_output_root = self.workspace / ".polaris" / "runtime" / "run-output" / safe_run_id
        self.filesystem = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())
        self.enabled = frozenset(ToolSpecRegistry.get_canonical(name) for name in scope.enabled_tools) & (
            ENGINEERING_TOOLS
            | ({"consult_role"} if consult is not None else set())
            | ({"report_review"} if review is not None else set())
            | ({"browser_observe"} if observe is not None and scope.browser is not None else set())
            | (
                {"verify_project"}
                if verify is not None
                and "execute_command" in {ToolSpecRegistry.get_canonical(name) for name in scope.enabled_tools}
                else set()
            )
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
        """Derive session-specific contracts without mutating the registry."""
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
            if name == "execute_command":
                function = schema["function"]
                if self.artifact_binding is None:
                    function["parameters"]["properties"].pop("artifacts", None)
                persistence = (
                    (
                        "For persistence, save files only within authorized workspace write roots: "
                        if self.scope.command_cache == "disabled"
                        else "Workspace files may persist only within authorized workspace write roots: "
                    )
                    + json.dumps([str(root) for root in self.scope.write_roots], ensure_ascii=False)
                    + ". Path/security checks still apply; .polaris control files are inaccessible. "
                    if self.scope.write_roots
                    else "No workspace writes are authorized. "
                )
                function["description"] = (
                    "Prefer read_file with start_line/end_line for source slices and repo_rg for search; "
                    "use execute_command for builds, tests and shell operations rather than cat/sed file reads. "
                    f"Run /bin/sh -c with initial cwd {self.workspace}. Each call starts a fresh shell and sandbox. "
                    "/tmp, HOME, cache (XDG_CACHE_HOME), and output (POLARIS_SANDBOX_OUTPUT) are private and "
                    "discarded after that call. Shell variables, cd, and files in those private directories do not "
                    "persist across calls. POLARIS_RUN_OUTPUT is writable scratch shared by calls in this run; "
                    "use it for build/test intermediates instead of /tmp. "
                    + persistence
                    + (
                        "Host persistent command cache is available at POLARIS_COMMAND_CACHE "
                        "(/polaris-command-cache); its contents persist across calls. "
                        + (
                            "Cache writes are authorized. "
                            if self.scope.command_cache == "read_write"
                            else "Cache is read-only. "
                        )
                        if self.scope.command_cache != "disabled"
                        else ""
                    )
                    + (
                        "Network access is allowed by host. "
                        if self.scope.network_allowed
                        else "Network access is disabled. "
                    )
                    + "Command execution is also bounded by the remaining run deadline."
                    + (
                        " Select artifacts as relative .svg/.html/.png/.jpg file names generated under this call's "
                        "POLARIS_SANDBOX_OUTPUT for later browser_observe(artifact_ref). Do not select workspace or /tmp "
                        "files; private files from earlier calls no longer exist."
                        if self.artifact_binding is not None
                        else ""
                    )
                )
                function["parameters"]["properties"]["timeout"].update(
                    default=COMMAND_TIMEOUT_DEFAULT,
                    minimum=COMMAND_TIMEOUT_MINIMUM,
                    maximum=COMMAND_TIMEOUT_MAXIMUM,
                )
            if name == "write_file":
                function = schema["function"]
                function["description"] = (
                    str(function.get("description") or "")
                    + " file is one relative path only; put the complete document body in content, never in file."
                )
            if name == "report_review" and len(self.review_clauses) >= 2:
                # The clause matrix is the fidelity measurement for a
                # decomposed goal. Expose it as required so a function-calling
                # model is told the argument exists and must be filled, and so
                # the dispatch boundary can reject an incomplete report.
                function = schema["function"]
                parameters = function["parameters"]
                properties = parameters.setdefault("properties", {})
                clause_property = properties.get("clause_findings")
                if isinstance(clause_property, dict):
                    clause_property["description"] = (
                        "Required for this multi-clause goal: one entry per acceptance clause "
                        f"({len(self.review_clauses)} clauses), in the same order as the numbered "
                        "acceptance-clause list in the current QA instruction. Set clause to that clause's "
                        "1-based number (a range or list such as 1-5 or 1,2,3 covers several at once); copying "
                        "the clause text verbatim also works. Each entry needs clause, status "
                        "pass|fail|unknown, and evidence. For pass, use the exact call ID of a current check or "
                        "earlier successful QA observation."
                    )
                required = [item for item in (parameters.get("required") or []) if item != "clause_findings"]
                required.append("clause_findings")
                parameters["required"] = required
            schemas.append(schema)
        return schemas

    def normalize(
        self, tool: str, arguments: dict[str, Any], *, ignored: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Normalize one native call through the sole registry owner."""
        snapshot = ToolSpecRegistry.capture_effective_spec(tool)
        if not snapshot.registered:
            raise ValueError(f"unknown engineering tool: {tool}")
        name = snapshot.canonical_tool_name
        specification = frozen_node_to_value(snapshot.canonical_effective_spec)
        aliases = specification.get("arg_aliases", {}) if isinstance(specification, dict) else {}
        if name == "repo_rg":
            # Read-only search can safely ignore hallucinated metadata fields
            # (observed context_labels) without changing the query semantics.
            allowed_keys = _REPO_RG_KEYS | set(aliases)
            unknown = sorted(arguments.keys() - allowed_keys)
            if ignored is not None:
                ignored.extend(unknown)
            arguments = {key: value for key, value in arguments.items() if key in allowed_keys}
        # A transport synonym cannot choose between two different destinations
        # or payloads. Edit dialect disambiguation belongs to the K1 normalizer.
        if name != "edit_file" and isinstance(aliases, dict):
            operands: dict[str, list[Any]] = {}
            for key, value in arguments.items():
                canonical = aliases.get(key, key)
                operands.setdefault(canonical, []).append(value)
            if any(any(value != values[0] for value in values[1:]) for values in operands.values()):
                raise ValueError("conflicting engineering tool argument aliases")
        # The schema normalizer coerces a bare string into a one-element list,
        # which would turn ``commands="true"`` into a real check and let a
        # malformed request overwrite a completed verification. Refuse the
        # non-list shape here, before coercion, so the raw request stays invalid.
        if (
            name == "verify_project"
            and "commands" in arguments
            and not isinstance(arguments.get("commands"), (list, tuple))
        ):
            raise ValueError("verify_project commands must be a list of shell commands")
        normalized = dict(normalize_tool_arguments_from_snapshot(snapshot, arguments))
        timeout = normalized.get("timeout")
        if name == "execute_command" and type(timeout) is int:
            # Weak providers sometimes ignore the advertised maximum (observed
            # timeout=180 in a real QA frame). Clamping to the host ceiling is
            # semantics-preserving; lower bounds and non-integers still fail.
            normalized["timeout"] = min(timeout, COMMAND_TIMEOUT_MAXIMUM)
        if name == "execute_command" and "read_file" in self.enabled:
            read_request = _shell_file_read(str(normalized.get("command") or ""))
            if read_request is not None:
                name = "read_file"
                normalized = read_request
        if name not in self.enabled:
            raise ValueError(f"engineering tool disabled: {name}")
        return name, normalized

    async def execute(
        self,
        call_id: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        execution_policy: CommandExecutionPolicy | None = None,
        output_capture: CommandOutputCapture | None = None,
    ) -> ToolObservation:
        """Execute one call in source order; errors remain explicit observations."""
        name = tool
        ignored_arguments: list[str] = []
        try:
            name, args = self.normalize(tool, arguments, ignored=ignored_arguments)
            normalized_from = args.pop("_normalized_from", None)
            if self.authorize is not None:
                self.authorize()
            if self.cancel.is_set():
                return ToolObservation(call_id, name, False, "Execution cancelled before dispatch.", None, True)
            self._remaining()
            if name == "browser_observe":
                if self.observe is None:
                    raise ValueError("browser_observe requires the active session owner")
                return await self.observe(args)
            if name == "verify_project":
                self._keys(args, {"commands"})
                commands = args.get("commands")
                if not isinstance(commands, list) or any(
                    not isinstance(command, str) or not command.strip() or "\0" in command for command in commands
                ):
                    raise ValueError("verify_project commands must be a list of nonempty command strings without NUL")
                if not commands:
                    from polaris.cells.roles.runtime.internal.engineering_verification_plan import (
                        default_verification_commands,
                    )

                    commands = list(default_verification_commands(self.workspace))
                    if not commands:
                        raise ValueError(
                            "verify_project received no commands and the host could not detect a supported toolchain"
                        )
                masked = [command for command in commands if _EXIT_MASKING_SUFFIX.search(command.strip())]
                if masked:
                    cleaned = [_EXIT_MASKING_SUFFIX.sub("", command.strip()).strip(" ;&|") for command in masked]
                    raise ValueError(
                        "verify_project rejects exit-status masking (for example '; true', '|| true', '; echo' or '|| exit 0'). "
                        "Remove the masking suffix; the exit code must reflect the real check. Suggested command(s): "
                        + json.dumps([item for item in cleaned if item], ensure_ascii=False)
                    )
                if self.verify is None:
                    raise ValueError("verify_project requires the active Director session owner")
                return await self.verify(tuple(commands))
            if name == "report_review":
                self._keys(args, {"approved", "feedback", "major_findings", "clause_findings"})
                if self.review is None or type(args.get("approved")) is not bool:
                    raise ValueError("report_review requires an actual QA frame and boolean approved")
                feedback = self._text(args, "feedback")
                findings = args.get("major_findings", [])
                if not isinstance(findings, list) or any(
                    not isinstance(item, str) or not item.strip() for item in findings
                ):
                    raise ValueError("major_findings must contain nonempty text")
                clause_findings = normalize_clause_findings(args.get("clause_findings"))
                approved = args["approved"]
                if approved and any(f.status == "fail" for f in clause_findings):
                    # A fail finding means the candidate is not acceptable, so the
                    # model's approved=true contradicts itself. Treat it as a
                    # rejection instead of rejecting the call and burning a QA turn
                    # on a retry (measured 17 such retries in real runs).
                    approved = False
                match = match_clause_findings(self.review_clauses, clause_findings)
                gap = (
                    [clause for clause in self.review_clauses if clause not in match.matched]
                    if len(self.review_clauses) >= 2
                    else []
                )
                if gap and self._coverage_rejections < _MAX_COVERAGE_REJECTS:
                    # Dispatch-boundary gate: a decomposed goal cannot be ruled
                    # on by a partial matrix. The same coverage helper grades the
                    # acceptance ledger, so the corrective message and the final
                    # verdict cannot drift apart. Bounded, so a reviewer that
                    # cannot enumerate the clauses is graded rather than looped.
                    self._coverage_rejections += 1
                    remaining = _MAX_COVERAGE_REJECTS - self._coverage_rejections + 1
                    raise ValueError(
                        "report_review clause_findings must cover every acceptance clause. "
                        f"Missing {len(gap)} of {len(self.review_clauses)} clauses: "
                        + json.dumps(gap, ensure_ascii=False)
                        + f". Set clause to each clause's 1-based number from the list ({remaining} attempt(s) left); "
                        "after that a best-effort matrix is accepted and the missing clauses stay unknown."
                    )
                if gap:
                    match = ClauseMatchResult(match.matched, "partial")
                if not approved and self.review_clauses:
                    # A rejection without a reviewed clause matrix is an
                    # unstarted review, not an engineering finding. Send the QA
                    # role back to inspect the current candidate instead of
                    # rejecting the run on a placeholder.
                    if not clause_findings:
                        raise ValueError(
                            "report_review cannot reject the candidate before reviewing it: "
                            "inspect the current candidate and submit clause_findings with evidence."
                        )
                    if all(not str(item.evidence or "").strip() for item in clause_findings):
                        raise ValueError(
                            "report_review rejection requires per-clause evidence from the current "
                            "candidate; continue inspecting before ruling."
                        )
                    placeholder = [
                        item.clause for item in clause_findings if _is_placeholder_evidence(str(item.evidence or ""))
                    ]
                    if placeholder:
                        # "Review not started." is an unstarted review, not an
                        # engineering finding. Without this floor the small model
                        # can end the run on a canned rejection after doing real
                        # observations.
                        raise ValueError(
                            "report_review rejection evidence must describe what was observed; "
                            "stock placeholder text is not a finding. Redo the review for: "
                            + json.dumps(placeholder, ensure_ascii=False)
                        )
                    canned = [item for item in findings if _is_placeholder_evidence(item)]
                    if canned:
                        raise ValueError(
                            "report_review major_findings must describe a concrete defect; "
                            "stock placeholder text is not a finding: " + json.dumps(canned, ensure_ascii=False)
                        )
                self.review(
                    approved and not findings and all(item.status == "pass" for item in clause_findings),
                    clause_findings,
                )
                # The structured clause matrix is part of the durable review
                # judgment, not only the call arguments, so an auditor can read
                # the ruling from the observation alone.
                return self._observation(
                    call_id,
                    name,
                    True,
                    json.dumps(
                        {
                            "approved": approved,
                            "feedback": feedback,
                            "major_findings": findings,
                            "clause_match": match.mode,
                            "clause_findings": [
                                {"clause": item.clause, "status": item.status, "evidence": item.evidence}
                                for item in clause_findings
                            ],
                        },
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
                self._keys(args, {"command", "timeout", "artifacts"})
                from polaris.cells.roles.runtime.internal.engineering_artifacts import (
                    capture_artifacts,
                    requested_artifacts,
                )

                names = requested_artifacts(args["artifacts"]) if "artifacts" in args else ()
                if names and (output_capture is None or self.artifact_binding is None):
                    raise ValueError("artifact capture requires the active host output binding")
                before = self.artifact_binding() if names and self.artifact_binding is not None else {}
                command = self._text(args, "command")
                timeout = self._integer(
                    args,
                    "timeout",
                    COMMAND_TIMEOUT_DEFAULT,
                    minimum=COMMAND_TIMEOUT_MINIMUM,
                    maximum=COMMAND_TIMEOUT_MAXIMUM,
                )
                process_parameters = inspect.signature(self._process).parameters
                supports_capture = "capture_output_root" in process_parameters or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in process_parameters.values()
                )
                completed = await self._process(
                    (execution_policy or resolve_execution_policy(None)).argv(command),
                    timeout=timeout,
                    writes=self.scope.write_roots,
                    command_cache_write=True,
                    **(
                        {"capture_output_root": output_capture.root}
                        if output_capture is not None and supports_capture
                        else {}
                    ),
                )
                from polaris.cells.roles.runtime.internal.engineering_command_cards import (
                    build_command_card,
                    persist_command_result,
                )

                snapshot_ref = persist_command_result(
                    self.workspace, command, completed.stdout, completed.stderr, completed.returncode
                )
                card = build_command_card(
                    command, completed.stdout, completed.stderr, completed.returncode, snapshot_ref
                )
                content = json.dumps({"command_card": card}, ensure_ascii=False)
                observation = self._observation(call_id, name, completed.returncode == 0, content, completed.returncode)
                from polaris.cells.roles.runtime.internal.engineering_verification import (
                    is_read_only_failure,
                    read_only_write_targets,
                )

                if is_read_only_failure(content):
                    targets = read_only_write_targets(content)
                    observation = replace(
                        observation,
                        content=observation.content
                        + "\nRead-only scope rejected the write"
                        + (f" to {', '.join(targets)}" if targets else "")
                        + ". Do not copy/build into the candidate workspace. Write only to this call's "
                        + "POLARIS_SANDBOX_OUTPUT; /tmp and HOME are discarded when the call ends, so later calls "
                        + "must recreate any files they need.",
                    )
                if output_capture is not None and self.artifact_binding is not None:
                    capture_names = names
                    if not capture_names:
                        capture_names = tuple(
                            name
                            for name in output_capture.list()
                            if mimetypes.guess_type(name)[0]
                            in {"image/svg+xml", "text/html", "image/png", "image/jpeg"}
                        )
                    try:
                        try:
                            after = self.artifact_binding()
                        except (ValueError, OSError):
                            if names:
                                raise
                            after = {}
                        artifacts = capture_artifacts(output_capture, capture_names, before, after)
                        observation = replace(observation, artifacts=artifacts)
                    except (ValueError, OSError, PathSecurityError, GuardedRegularFileSnapshotError) as error:
                        try:
                            payload = json.loads(observation.content)
                            card = payload.get("command_card")
                            if isinstance(card, dict):
                                card["artifact_error"] = f"{type(error).__name__}: {error}"
                                content = (
                                    json.dumps(payload, ensure_ascii=False)
                                    + f"\nArtifact capture failed: {type(error).__name__}: {error}"
                                )
                            else:
                                content = (
                                    observation.content + f"\nArtifact capture failed: {type(error).__name__}: {error}"
                                )
                        except json.JSONDecodeError:
                            content = (
                                observation.content + f"\nArtifact capture failed: {type(error).__name__}: {error}"
                            )
                        observation = replace(observation, ok=False, content=content)
                return observation
            if name == "read_command_result":
                self._keys(args, {"content_ref", "max_bytes"})
                from polaris.cells.roles.runtime.internal.engineering_command_cards import read_command_result

                payload = read_command_result(self.workspace, self._text(args, "content_ref"))
                maximum = self._integer(args, "max_bytes", self.limits.max_tool_output_bytes, minimum=1)
                payload["stdout"] = str(payload.get("stdout") or "")[:maximum]
                payload["stderr"] = str(payload.get("stderr") or "")[:maximum]
                return self._observation(
                    call_id, name, True, json.dumps({"command_result": payload}, ensure_ascii=False)
                )
            if name == "repo_rg":
                self._keys(args, set(_REPO_RG_KEYS))
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
                if ignored_arguments:
                    content = (
                        "Normalized read-only search: ignored unsupported arguments "
                        + ", ".join(ignored_arguments)
                        + ".\n"
                        + content
                    )
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
                args = self._alias_path(args)
                self._keys(args, {"file", "content_ref", "max_bytes", "start_line", "end_line", "range_required"})
                from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card

                reference = args.get("content_ref")
                if reference is not None:
                    if not isinstance(reference, str) or not reference.startswith("filecard:"):
                        raise ValueError("content_ref must be a filecard:<sha256> reference")
                    if self.file_card_resolver is None:
                        raise ValueError("file_card reference resolution requires the active run journal")
                    card = self.file_card_resolver(reference)
                    if "file" in args and args["file"] != card["path"]:
                        raise ValueError("file and content_ref must identify the same source path")
                    target = self._path(str(card["path"]), write=False)
                    if target.is_file():
                        current = self.filesystem.workspace_read_text(str(target), encoding="utf-8")
                        current_digest = hashlib.sha256(current.encode("utf-8")).hexdigest()
                        if current_digest != card["sha256"]:
                            card = {**card, "stale": True, "current_sha256": current_digest}
                    if (
                        "start_line" in args
                        or "end_line" in args
                        or args.get("max_bytes") not in {None, 200000, 200001}
                    ):
                        from polaris.cells.roles.runtime.internal.engineering_file_cards import read_file_snapshot

                        snapshot = read_file_snapshot(self.workspace, reference)
                        original = str(snapshot["content"])
                        start = self._integer(args, "start_line", 1, minimum=1)
                        end = self._integer(args, "end_line", max(1, len(original.splitlines())), minimum=start)
                        selected = "".join(original.splitlines(keepends=True)[start - 1 : end])
                        maximum = self._integer(
                            args, "max_bytes", min(8000, self.limits.max_tool_output_bytes), minimum=1
                        )
                        preview = bounded_output(selected, max(1, min(maximum, self.limits.max_tool_output_bytes) - 64))
                        card = build_file_card(
                            str(card["path"]),
                            original,
                            revision=card.get("revision"),
                            preview=preview,
                            truncated=len(preview.encode("utf-8")) < len(selected.encode("utf-8"))
                            or end < len(original.splitlines()),
                            start_line=start,
                            end_line=end,
                            snapshot_ref=reference,
                        )
                        card["recovered_from"] = reference
                    return self._observation(
                        call_id,
                        name,
                        True,
                        json.dumps(
                            {
                                "file_card": card,
                                **(
                                    {"normalization": {"from": normalized_from, "reason": "shell_file_read"}}
                                    if normalized_from
                                    else {}
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    )
                target = self._path(self._text(args, "file"), write=False)
                if not target.is_file():
                    return self._observation(
                        call_id,
                        name,
                        False,
                        "文件不存在（尚未创建）："
                        f"{target.relative_to(self.workspace)}。"
                        "如需创建该文件，请用 write_file 写入完整内容。",
                        None,
                    )
                content = self.filesystem.workspace_read_text(str(target), encoding="utf-8")
                if args.get("range_required") and not {"start_line", "end_line"}.issubset(args):
                    # Tolerant defaults for weak models: a partial range still
                    # means "read bounded" — default the missing bound instead
                    # of burning a turn on a pedantic error.
                    if "start_line" not in args and isinstance(args.get("end_line"), int):
                        args = {**args, "start_line": 1}
                    if "end_line" not in args and isinstance(args.get("start_line"), int):
                        args = {**args, "end_line": max(1, len(content.splitlines()))}
                start = self._integer(args, "start_line", 1, minimum=1)
                end = self._integer(args, "end_line", max(1, len(content.splitlines())), minimum=start)
                selected = "".join(content.splitlines(keepends=True)[start - 1 : end])
                default_preview = min(8000, self.limits.max_tool_output_bytes)
                maximum = (
                    default_preview
                    if args.get("max_bytes") in {200000, 200001}
                    else self._integer(args, "max_bytes", default_preview, minimum=1)
                )
                preview = bounded_output(selected, max(1, min(maximum, self.limits.max_tool_output_bytes) - 64))
                from polaris.cells.roles.runtime.internal.engineering_file_cards import persist_file_snapshot

                snapshot_ref = persist_file_snapshot(
                    self.workspace, target.relative_to(self.workspace).as_posix(), content
                )
                card = build_file_card(
                    target.relative_to(self.workspace).as_posix(),
                    content,
                    revision=self.revision_provider() if self.revision_provider is not None else None,
                    preview=preview,
                    truncated=len(preview.encode("utf-8")) < len(selected.encode("utf-8"))
                    or end < len(content.splitlines()),
                    start_line=start,
                    end_line=end,
                    snapshot_ref=snapshot_ref,
                )
                return self._observation(
                    call_id,
                    name,
                    True,
                    json.dumps(
                        {
                            "file_card": card,
                            **(
                                {"normalization": {"from": normalized_from, "reason": "shell_file_read"}}
                                if normalized_from
                                else {}
                            ),
                        },
                        ensure_ascii=False,
                    ),
                )
            if name == "write_file":
                args = self._alias_path(args)
                self._keys(args, {"file", "content"})
                raw_file = args.get("file")
                if not isinstance(raw_file, str) or not raw_file.strip():
                    raise ValueError(
                        "write_file requires a nonempty relative file path; provide the target path and full content"
                    )
                file_argument = self._text(args, "file")
                target = self._path(file_argument, write=True)
                body_omitted = "content" not in args or args.get("content") is None
                if args.get("content") is None:
                    args = {**args, "content": ""}
                if body_omitted and target.is_file():
                    raise ValueError(
                        f"write_file requires a content field with the complete file body for {file_argument}; "
                        "do not send only file. Provide full content in every write_file call."
                    )
                if "\n" in file_argument or "\r" in file_argument:
                    raise ValueError(
                        "write_file file must be one relative path; put the document body in content, not in file"
                    )
                receipt = self.filesystem.workspace_write_text_atomic(
                    str(target), self._text(args, "content", default="", allow_empty=True), encoding="utf-8"
                )
                if body_omitted:
                    return self._observation(
                        call_id,
                        name,
                        True,
                        f"Created an empty file because content was omitted for {target.relative_to(self.workspace)}; "
                        "provide the full body next time if this file is meant to be implemented.",
                    )
                return self._observation(
                    call_id,
                    name,
                    True,
                    f"Wrote {receipt.bytes_written} UTF-8 bytes to {target.relative_to(self.workspace)}.",
                )
            if name == "edit_file":
                args = self._alias_path(args)
                self._keys(args, {"file", "search", "replace", "regex", "replace_all"})
                if args.get("regex", False) is not False or args.get("replace_all", False) is not False:
                    raise ValueError("edit_file supports one unique exact replacement only")
                target = self._path(self._text(args, "file"), write=True)
                self._path(str(target), write=False)
                if not target.is_file():
                    return self._observation(
                        call_id,
                        name,
                        False,
                        "文件不存在，无法编辑："
                        f"{target.relative_to(self.workspace)}。"
                        "如需创建该文件，请用 write_file 写入完整内容。",
                        None,
                    )
                old, new = self._text(args, "search"), self._text(args, "replace", allow_empty=True)
                content = self.filesystem.workspace_read_text(str(target), encoding="utf-8")
                count = content.count(old)
                if count != 1:
                    if count == 0:
                        raise ValueError(self._edit_search_hint(content, old))
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
            stdout = exc.output.decode("utf-8", "replace") if isinstance(exc.output, bytes) else (exc.output or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return self._observation(
                call_id,
                name,
                False,
                f"Command timed out after {exc.timeout}s; physical cleanup completed. "
                "It may have partially changed allowed files. Inspect current state before deciding the next action; "
                f"the command was not replayed.\nstdout:\n{stdout}\nstderr:\n{stderr}",
                interrupted=self.cancel.is_set(),
                timed_out=True,
            )
        except CommandCacheUnavailableError as exc:
            return self._observation(
                call_id, name, False, f"{type(exc).__name__}: {exc}", interrupted=self.cancel.is_set()
            )
        except (ValueError, OSError, UnicodeError, PathSecurityError, WorkspaceSandboxUnavailableError) as exc:
            return self._observation(call_id, name, False, f"{type(exc).__name__}: {exc}")

    async def _process(
        self,
        argv: list[str],
        *,
        timeout: float,
        writes: tuple[Path, ...],
        capture_output_root: Path | None = None,
        command_cache_write: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self._remaining()
        # Fixed source-reader helpers do not consume persistent command inputs.
        # Only ordinary execute_command uses the explicit host cache grant.
        cache_mode = self.scope.command_cache if command_cache_write else "disabled"
        self.run_output_root.mkdir(parents=True, exist_ok=True)
        parameters = inspect.signature(self._process).parameters
        var_keyword = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        run_output = self.run_output_root
        capture_root = capture_output_root if "capture_output_root" in parameters or var_keyword else None
        # Validate the ordinary command's containment before allocating any
        # persistent resource. Unsupported platforms or invalid source grants
        # must leave even an explicitly granted cache absent.
        prepared = build_workspace_sandbox_argv(
            argv,
            workspace=self.workspace,
            cwd=self.workspace,
            read_roots=self.scope.read_roots,
            write_roots=writes,
            network_allowed=self.scope.network_allowed,
            toolchain_bin_dirs=self.scope.toolchain_bin_dirs,
            toolchain_env=dict(self.scope.toolchain_env),
            capture_output_root=capture_root,
            run_output_root=run_output,
        )
        command_cache = resolve_command_cache(
            self.workspace, cache_mode, create=command_cache_write and cache_mode == "read_write"
        )
        if command_cache is not None:
            prepared = build_workspace_sandbox_argv(
                argv,
                workspace=self.workspace,
                cwd=self.workspace,
                read_roots=self.scope.read_roots,
                write_roots=writes,
                network_allowed=self.scope.network_allowed,
                toolchain_bin_dirs=self.scope.toolchain_bin_dirs,
                toolchain_env=dict(self.scope.toolchain_env),
                command_cache=command_cache,
                capture_output_root=capture_root,
                run_output_root=run_output,
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
            try:
                result = await asyncio.shield(worker)
            except (ProcessTreeCancelledError, subprocess.TimeoutExpired):
                if command_cache is not None:
                    command_cache.validate()
                raise
            if command_cache is not None:
                command_cache.validate()
            return result
        except asyncio.CancelledError:
            self.cancel.set()
            control.cancel()
            with contextlib.suppress(ProcessTreeCancelledError, subprocess.TimeoutExpired):
                await asyncio.shield(worker)
            if command_cache is not None:
                command_cache.validate()
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
        self,
        call_id: str,
        name: str,
        ok: bool,
        content: str,
        exit_code: int | None = None,
        interrupted: bool = False,
        timed_out: bool = False,
    ) -> ToolObservation:
        return ToolObservation(
            call_id,
            name,
            ok,
            bounded_output(content, self.limits.max_tool_output_bytes),
            exit_code,
            interrupted,
            timed_out,
        )

    @staticmethod
    def _keys(arguments: dict[str, Any], allowed: set[str]) -> None:
        unknown = arguments.keys() - allowed
        if unknown:
            raise ValueError(f"unsupported engineering tool arguments: {', '.join(sorted(unknown))}")

    @staticmethod
    def _alias_path(arguments: dict[str, Any]) -> dict[str, Any]:
        """Fold common path-key synonyms onto the canonical ``file`` key.

        Weak providers routinely name the target ``path``/``file_path``. The
        synonym only selects a destination; it cannot widen authority, so the
        subsequent path check still applies to the canonical value.
        """

        if "file" in arguments:
            return arguments
        for key in ("path", "file_path", "filepath", "filename"):
            if key in arguments:
                return {**arguments, "file": arguments[key]}
        return arguments

    @staticmethod
    def _edit_search_hint(content: str, old: str) -> str:
        """Nearest current lines for a zero-hit edit, so the model can requote."""

        message = "edit_file requires one exact search occurrence; observed 0"
        wanted = [token for token in re.split(r"\s+", old) if len(token) >= 2]
        scored: list[tuple[int, str]] = []
        for number, line in enumerate(content.splitlines(), start=1):
            score = sum(1 for token in wanted if token in line)
            if score:
                scored.append((score, f"{number}: {line[:200]}"))
        best = sorted(scored, key=lambda item: -item[0])[:3]
        if not best:
            return message
        return (
            message
            + ". The file does not contain that text; closest current lines:\n"
            + "\n".join(line for _score, line in best)
        )

    @staticmethod
    def _text(arguments: dict[str, Any], name: str, default: str | None = None, *, allow_empty: bool = False) -> str:
        value = arguments.get(name, default)
        if isinstance(value, str) and "\0" in value:
            raise ValueError(f"{name} contains NUL; remove the NUL byte and resend valid UTF-8 text")
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
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

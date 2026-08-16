"""工具执行实现

包含文件读写、命令执行、代码搜索等工具的具体实现。
"""

from __future__ import annotations

import logging
import re
import shlex
from hashlib import sha256
from pathlib import Path
from typing import Any, Never, SupportsIndex
from weakref import WeakSet

from polaris.cells.director.runtime.public import DirectorRepairEffectV1
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.events.file_event_broadcaster import (
    broadcast_file_written,
    calculate_patch,
    replace_in_file_with_broadcast,
    write_file_with_broadcast,
)
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs import (
    GuardedRegularFileSnapshotV1,
    guarded_compare_and_create_regular_file,
    guarded_compare_and_remove_regular_file,
    guarded_compare_and_replace_regular_file,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem_guards import (
    _DESTRUCTIVE_SHRINK_MAX_ADD_RATIO,
    _DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES,
    _destructive_shrink_error,
    attach_post_write_syntax_check,
    sanitize_js_ts_write_hygiene,
)
from polaris.kernelone.llm.toolkit.tool_normalization import (
    normalize_patch_like_write_content,
)
from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._shared import (
    recover_write_body_string,
)
from polaris.kernelone.llm.toolkit.write_policy import validate_tool_write_policy

from .helpers import _MIN_FILES_PATTERN, _MIN_LINES_PATTERN, canonicalize_project_manifest_path
from .security import (
    ALLOWED_EXECUTION_COMMANDS,
    TOOLING_SECURITY_AVAILABLE,
    CommandInjectionBlocked,
    is_command_allowed,
    is_command_blocked,
)

logger = logging.getLogger(__name__)

_MAX_GUARDED_REPAIR_BYTES = 64 * 1024 * 1024
_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_AUTHORITY = object()


class DirectorToolExecutionAuthorityError(RuntimeError):
    """Fail-closed denial for unscoped physical executor construction/transport."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _guarded_repair_snapshot(
    *,
    workspace: Path,
    effect: DirectorRepairEffectV1,
) -> GuardedRegularFileSnapshotV1 | None:
    """Capture and verify exact pre-state for one repair effect."""

    if not effect.exists_before:
        return None
    snapshot = read_guarded_regular_file_snapshot(
        workspace,
        effect.target_path,
        _MAX_GUARDED_REPAIR_BYTES,
    )
    if sha256(snapshot.content).hexdigest() != effect.expected_before_hash:
        raise RuntimeError("deo_target_state_drift")
    return snapshot


def _validate_repair_effect_call(
    effect: DirectorRepairEffectV1 | None,
    *,
    tool_name: str,
    args: dict[str, Any],
) -> DirectorRepairEffectV1 | None:
    if effect is None:
        return None
    if type(effect) is not DirectorRepairEffectV1 or effect.tool_name != tool_name or dict(effect.arguments) != args:
        raise RuntimeError("deo_operation_hash_mismatch")
    return effect


def _coerce_policy_scope_list(value: Any) -> list[str]:
    """Normalize an optional scope-like tool argument into a string list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _recover_write_body_or_none(value: Any) -> str | None:
    """Recover a plain UTF-8 string from a structured tool argument body.

    R195/M03: weak Directors (e.g. MiniMax-M3) sometimes emit ``content`` /
    ``search`` / ``replace`` as a structured ``$text`` continuation map or a list
    of fragments rather than a plain string. The physical write/edit tools must
    never silently ``str()`` such a body into a file — that leaks the Python repr
    into source (L1-01 m03-r17 ``src/main.ts:111`` leaked ``{'$text': ...}`` and
    broke the build with TS1005).

    Returns:
        * the original string when ``value`` is already a ``str``;
        * ``""`` when ``value`` is ``None`` (missing arg);
        * the recovered string for a structured body (R138 ``$text`` / list);
        * ``None`` when ``value`` is a non-string body that cannot be safely
          recovered — the caller must fail-closed so the repr is never written.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return recover_write_body_string(value)


def _director_write_allowed_scope(tool_kwargs: dict[str, Any] | None) -> list[str]:
    """Extract an explicit Director write scope if the call carries one."""
    kwargs = tool_kwargs or {}
    for key in (
        "allowed_scope",
        "allowed_scope_paths",
        "scope_paths",
        "target_files",
        "pm_target_files",
        "act_files",
    ):
        scope = _coerce_policy_scope_list(kwargs.get(key))
        if scope:
            return scope
    return []


def _is_package_manifest_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").replace("\\", "/").strip("/").lower()
    return normalized == "package.json" or normalized.endswith("/package.json")


def _is_json_config_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").replace("\\", "/").strip("/").lower()
    return normalized.endswith(".json")


_SOURCE_CONTENT_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".css",
        ".go",
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

_COLLAPSED_BARE_NEWLINE_MARKER_RE = re.compile(
    r"(?<=[;{}),\]])n(?=\s{2,}|\s*(?:"
    r"public|private|protected|readonly|constructor|class|interface|type|"
    r"function|def|const|let|var|return|if|for|while|import|export"
    r")\b)"
)
_ESCAPED_NEWLINE_MARKER_RE = re.compile(r"\\n(?=\s{2,}|\s*\w)")
_SOURCE_NARRATION_LEAK_RE = re.compile(
    r"(?is)^\s*(?:"
    r"i(?:'|’)ll\s+|"
    r"i\s+will\s+|"
    r"let\s+me\s+|"
    r"here(?:'|’)s\s+|"
    r"here\s+is\s+|"
    r"below\s+is\s+|"
    r"(?:the\s+)?quality\s+repair\s+mode\s+requires\s+me\b|"
    r"the\s+(?:repair\s+)?directive\s+(?:is|says|said)\b|"
    r"the\s+override\s+(?:says|instruction)\b|"
    r"the\s+(?:task|instruction|requirement|requirements)\s+(?:is|are|says|said)\b|"
    r"the\s+(?:two\s+)?(?:problem|problems|issue|issues)\s+(?:are|is)\b|"
    r"i\s+(?:also\s+)?need\s+to\b|"
    r"for\s+[\w./-]+\.(?:py|js|ts|jsx|tsx|go|rs)\s+-\s+should\b|"
    r"this\s+file\s+(?:defines|contains|implements)\b|"
    r"我(?:会|将|来)|"
    r"让我|"
    r"下面(?:是|我)"
    r")"
)


def _source_narration_leak_error(rel_path: str, text: str) -> dict[str, Any] | None:
    suffix = Path(rel_path).suffix.lower()
    if suffix not in _SOURCE_CONTENT_EXTENSIONS:
        return None
    stripped = str(text or "").lstrip()
    if not stripped:
        return None
    first_line = stripped.splitlines()[0].strip()
    if first_line.startswith(("#", "//", "/*", "*", '"""', "'''")):
        return None
    if not _SOURCE_NARRATION_LEAK_RE.search(stripped[:500]):
        return None
    return {
        "ok": False,
        "blocked": True,
        "error_type": "source_narration_contamination",
        "retryable": True,
        "error": (
            f"Source narration contamination: write_file for {rel_path} received assistant prose instead of code. "
            "The content argument must contain only the complete UTF-8 source file body."
        ),
        "suggestion": (
            "Retry write_file for the same path with real source code only. Do not include explanations, plans, "
            "phrases like 'Let me fix', markdown, or reasoning text in code files."
        ),
        "file": rel_path,
    }


def _validate_source_write_content_shape(*, rel_path: str, content: str) -> dict[str, Any]:
    """Reject source payloads that collapsed newlines into literal markers."""
    suffix = Path(rel_path).suffix.lower()
    if suffix not in _SOURCE_CONTENT_EXTENSIONS:
        return {"ok": True}

    text = str(content or "")
    narration_error = _source_narration_leak_error(rel_path, text)
    if narration_error is not None:
        return narration_error
    if "\n" in text or "\r" in text or len(text) < 120:
        return {"ok": True}

    bare_marker_count = len(_COLLAPSED_BARE_NEWLINE_MARKER_RE.findall(text))
    escaped_marker_count = len(_ESCAPED_NEWLINE_MARKER_RE.findall(text))
    if bare_marker_count < 3 and escaped_marker_count < 3:
        return {"ok": True}

    marker = "bare 'n'" if bare_marker_count >= escaped_marker_count else "escaped '\\n'"
    return {
        "ok": False,
        "blocked": True,
        "error_type": "invalid_source_content",
        "error": (
            f"Invalid source content for {rel_path}: appears to contain {marker} newline markers "
            "instead of real UTF-8 line breaks. Retry write_file with normal multiline source text."
        ),
        "file": rel_path,
    }


def _precommit_source_syntax_guard(
    *,
    rel_path: str,
    before_content: str | None,
    after_content: str,
) -> dict[str, Any]:
    """Reject any definite parse failure before disk mutation.

    A previously broken file is not permission to commit a *different* broken
    candidate.  Live L1-04 showed that such progressive commits merely moved a
    Go parser failure from ``}na < spellCost`` to an out-of-function
    ``return``.  The same Director repair turn must therefore propose an atomic
    syntactic repair; rejected candidates leave the workspace bytes unchanged.
    """

    from polaris.kernelone.quality import check_content_syntax

    after = check_content_syntax(rel_path, after_content)
    if not after.checked or after.ok:
        return {"ok": True}
    preexisting_syntax_failure = False
    if before_content is not None:
        before = check_content_syntax(rel_path, before_content)
        preexisting_syntax_failure = before.checked and not before.ok
    error_type = "source_syntax_not_repaired" if preexisting_syntax_failure else "source_syntax_regression"
    failure_verb = (
        "does not repair the existing syntax error" if preexisting_syntax_failure else "introduces a syntax error"
    )
    return {
        "ok": False,
        "blocked": True,
        "retryable": True,
        "error_type": error_type,
        "error": (
            f"Edit rejected before commit because it {failure_verb} in {rel_path}: "
            f"{str(after.error or 'parse failure')[:400]}"
        ),
        "suggestion": (
            "Read the current file again, then issue one narrower edit whose complete replacement "
            "preserves token boundaries and parses. The workspace file was not changed."
        ),
        "file": rel_path,
        "syntax_check": "failed_precommit",
        "preexisting_syntax_failure": preexisting_syntax_failure,
    }


def _validate_or_repair_json_config_content(
    *,
    rel_path: str,
    content: str,
) -> dict[str, Any]:
    if not _is_json_config_path(rel_path):
        return {"ok": True, "content": content, "repaired": False}

    from .json_config_validation import validate_json_config_file

    result = validate_json_config_file(content, rel_path, allow_repair=True)
    if result.get("ok"):
        return {
            "ok": True,
            "content": str(result.get("content") if result.get("content") is not None else content),
            "repaired": bool(result.get("repaired")),
        }

    return {
        "ok": False,
        "blocked": True,
        "error_type": "invalid_json_content",
        "error": str(result.get("error") or f"Invalid JSON content for {rel_path}"),
        "file": rel_path,
    }


def _read_workspace_agents_policy_text(workspace: Path, rel_path: str) -> str:
    """Read root and nested AGENTS.md files that apply to a workspace-relative path."""
    normalized_rel = str(rel_path or "").replace("\\", "/").strip("/")
    candidates = ["AGENTS.md"]
    parent_parts = [part for part in normalized_rel.split("/")[:-1] if part]
    for index in range(1, len(parent_parts) + 1):
        candidates.append("/".join([*parent_parts[:index], "AGENTS.md"]))

    texts: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        target = (workspace / candidate).resolve()
        if workspace not in target.parents and target != workspace:
            continue
        try:
            if target.is_file():
                texts.append(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            continue
    return "\n".join(texts)


class DirectorToolExecutor:
    """Director 工具执行器。

    提供文件读写、命令执行、代码搜索等工具的具体实现。
    """

    available_tools = (
        "write_file",
        "read_file",
        "edit_file",
        "edit_blocks",
        "search_replace",
        "delete_file",
        "run_command",
        "execute_command",
        "search_code",
    )

    def __init__(
        self,
        workspace: str,
        *,
        message_bus: Any | None = None,
        worker_id: str = "director",
        _physical_execution_authority: object | None = None,
    ) -> None:
        if _physical_execution_authority is not _DIRECTED_EFFECT_PHYSICAL_EXECUTOR_AUTHORITY:
            raise DirectorToolExecutionAuthorityError("directed_effect_physical_executor_authority_required")
        self.workspace = workspace
        self._message_bus = message_bus
        self._worker_id = worker_id

    def _assert_physical_execution_authority(self) -> None:
        if type(self) is not DirectorToolExecutor or self not in _DIRECTED_EFFECT_PHYSICAL_EXECUTOR_INSTANCES:
            raise DirectorToolExecutionAuthorityError("directed_effect_physical_executor_authority_required")

    def __copy__(self) -> DirectorToolExecutor:
        raise DirectorToolExecutionAuthorityError("directed_effect_physical_executor_transport_forbidden")

    def __deepcopy__(self, memo: dict[int, Any]) -> DirectorToolExecutor:
        del memo
        raise DirectorToolExecutionAuthorityError("directed_effect_physical_executor_transport_forbidden")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        del protocol
        raise DirectorToolExecutionAuthorityError("directed_effect_physical_executor_transport_forbidden")

    def set_message_bus(self, message_bus: Any | None) -> None:
        self._message_bus = message_bus

    def supports_tool(self, tool_name: str) -> bool:
        return str(tool_name or "") in self.available_tools

    def _validate_director_policy_for_write(
        self,
        *,
        workspace: Path,
        rel_path: str,
        old_content: str,
        new_content: str,
        operation: str,
        tool_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate a pending write and return KernelOne-compatible policy evidence."""
        normalized_rel = str(rel_path or "").replace("\\", "/").strip("/")
        package_write = _is_package_manifest_path(normalized_rel)
        verdict = validate_tool_write_policy(
            changed_files=[normalized_rel] if normalized_rel else [],
            allowed_scope=_director_write_allowed_scope(tool_kwargs),
            agents_md=_read_workspace_agents_policy_text(workspace, normalized_rel),
            operation=operation,
            package_before=old_content if package_write else None,
            package_after=new_content if package_write else None,
            require_change=True,
        )
        evidence = verdict.to_dict()
        if verdict.allowed:
            return {"ok": True, "director_policy": evidence}

        reason = "; ".join(verdict.reasons) or "Director write policy denied the write"
        return {
            "ok": False,
            "error": f"Director write policy denied: {reason}",
            "error_type": "director_write_policy_denied",
            "blocked": True,
            "director_policy": evidence,
        }

    def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        task_id: str = "",
        repair_effect: DirectorRepairEffectV1 | None = None,
    ) -> dict[str, Any]:
        """执行指定工具"""
        self._assert_physical_execution_authority()
        workspace_path = Path(self.workspace).resolve()
        try:
            repair_effect = _validate_repair_effect_call(
                repair_effect,
                tool_name=tool_name,
                args=args,
            )
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "error_type": "directed_effect_cas_denied"}

        if tool_name == "write_file":
            return self._tool_write_file(
                args,
                workspace_path,
                task_id=task_id,
                repair_effect=repair_effect,
            )
        elif tool_name == "read_file":
            return self._tool_read_file(args, workspace_path)
        elif tool_name in {"edit_file", "search_replace"}:
            return self._tool_edit_file(
                args,
                workspace_path,
                task_id=task_id,
                repair_effect=repair_effect,
            )
        elif tool_name == "edit_blocks":
            return self._tool_edit_blocks(
                args,
                workspace_path,
                task_id=task_id,
                repair_effect=repair_effect,
            )
        elif tool_name == "delete_file":
            return self._tool_delete_file(
                args,
                workspace_path,
                task_id=task_id,
                repair_effect=repair_effect,
            )
        elif tool_name in {"run_command", "execute_command"}:
            return self._tool_run_command(args, workspace_path)
        elif tool_name == "search_code":
            return self._tool_search_code(args, workspace_path)
        else:
            return {"ok": False, "error": f"Unknown tool: {tool_name}"}

    # -------------------------------------------------------------------------
    # File Tools
    # -------------------------------------------------------------------------

    def _tool_write_file(
        self,
        args: dict[str, Any],
        workspace: Path,
        *,
        task_id: str = "",
        repair_effect: DirectorRepairEffectV1 | None = None,
    ) -> dict[str, Any]:
        """写入文件工具"""
        raw_file_path = args.get("file") or args.get("path") or args.get("filepath")
        file_path = canonicalize_project_manifest_path(str(raw_file_path or "").strip())
        content = _recover_write_body_or_none(args.get("content"))
        if content is None:
            # R195/M03: a non-string content body that cannot be recovered to text
            # must fail-closed rather than be str()-serialized into the file.
            return {
                "ok": False,
                "error": "write_file content must be a UTF-8 string or recoverable text body",
                "error_type": "invalid_source_content",
            }

        if not file_path:
            return {"ok": False, "error": "Missing file path"}
        if "\n" in file_path or "\r" in file_path:
            return {"ok": False, "error": f"Invalid file path contains newline: {file_path!r}"}
        if _MIN_FILES_PATTERN.match(file_path) or _MIN_LINES_PATTERN.match(file_path):
            return {"ok": False, "error": f"Invalid file path resembles requirement sentence: {file_path}"}
        if re.match(r"^(table|index)\s+if\s+not\s+exists\b", file_path, re.IGNORECASE):
            return {"ok": False, "error": f"Invalid file path resembles SQL statement: {file_path}"}

        target = (workspace / file_path).resolve()
        if workspace not in target.parents and target != workspace:
            return {"ok": False, "error": f"Unsafe file path outside workspace: {file_path}"}

        allowed_extensionless = {
            "makefile",
            "dockerfile",
            "readme",
            "gitignore",
            "gitattributes",
            "dockerignore",
            "env",
            "editorconfig",
            "prettierrc",
            "eslintrc",
            "bashrc",
            "zshrc",
            "profile",
            "toml",
            "ini",
        }
        suffix = target.suffix.lower()
        # Strip leading dot for comparison (e.g., ".gitignore" -> "gitignore")
        target_name_lower = target.name.lower().lstrip(".")
        if not suffix and target_name_lower not in allowed_extensionless:
            return {"ok": False, "error": f"Invalid file path missing extension: {file_path}"}

        try:
            existing_content: str | None = None
            guarded_snapshot = (
                _guarded_repair_snapshot(workspace=workspace, effect=repair_effect)
                if repair_effect is not None
                else None
            )
            if guarded_snapshot is not None:
                existing_content = guarded_snapshot.content.decode("utf-8")
            elif repair_effect is None and target.exists():
                try:
                    existing_content = target.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    existing_content = None

            rel_path = target.relative_to(workspace).as_posix()
            if existing_content is not None:
                old_lines = existing_content.count("\n") + (
                    1 if existing_content and not existing_content.endswith("\n") else 0
                )
                new_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                if (
                    old_lines >= _DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES
                    and new_lines <= old_lines * _DESTRUCTIVE_SHRINK_MAX_ADD_RATIO
                ):
                    destructive_error: dict[str, Any] = _destructive_shrink_error(
                        file_path,
                        old_lines,
                        new_lines,
                        tool_hint=(
                            "write_file replaces the WHOLE file. If the intent is a partial edit, emit a "
                            "precise range/search replacement with only the changed lines so untouched code "
                            "is preserved. If the intent is a whole-file rewrite, provide a complete file "
                            "body comparable in size to the original."
                        ),
                    )
                    return destructive_error
            normalized = normalize_patch_like_write_content(
                rel_path,
                content,
                existing_content=existing_content,
            )
            if normalized.error:
                return {"ok": False, "error": normalized.error}
            text = str(normalized.content or "")
            # R146/R147: DEO Director writes must apply shared JS/TS write hygiene
            # (block-comment globs + control-flow statement commas) before disk.
            text, write_hygiene_flags = sanitize_js_ts_write_hygiene(rel_path, text)
            json_config_result = _validate_or_repair_json_config_content(rel_path=rel_path, content=text)
            if not json_config_result.get("ok"):
                return json_config_result
            text = str(json_config_result.get("content") if json_config_result.get("content") is not None else text)
            source_shape_result = _validate_source_write_content_shape(rel_path=rel_path, content=text)
            if not source_shape_result.get("ok"):
                return source_shape_result
            syntax_guard = _precommit_source_syntax_guard(
                rel_path=rel_path,
                before_content=existing_content,
                after_content=text,
            )
            if not syntax_guard.get("ok"):
                return syntax_guard

            policy_result = self._validate_director_policy_for_write(
                workspace=workspace,
                rel_path=rel_path,
                old_content=existing_content or "",
                new_content=text,
                operation="write_file:modify" if existing_content is not None else "write_file:create",
                tool_kwargs=args,
            )
            if not policy_result.get("ok"):
                return policy_result

            if repair_effect is not None:
                if not repair_effect.exists_after or sha256(text.encode("utf-8")).hexdigest() != (
                    repair_effect.expected_after_hash
                ):
                    return {
                        "ok": False,
                        "error": "deo_target_state_drift",
                        "error_type": "directed_effect_cas_denied",
                    }
                if guarded_snapshot is None:
                    guarded_compare_and_create_regular_file(
                        workspace,
                        rel_path,
                        text.encode("utf-8"),
                        max_bytes=_MAX_GUARDED_REPAIR_BYTES,
                    )
                    operation = "create"
                else:
                    guarded_compare_and_replace_regular_file(
                        workspace,
                        guarded_snapshot,
                        text.encode("utf-8"),
                        max_bytes=_MAX_GUARDED_REPAIR_BYTES,
                    )
                    operation = "modify"
                write_result = {
                    "ok": True,
                    "bytes": len(text.encode("utf-8")),
                    "operation": operation,
                    "broadcast_ok": broadcast_file_written(
                        file_path=rel_path,
                        operation=operation,
                        content_size=len(text.encode("utf-8")),
                        task_id=task_id,
                        patch=calculate_patch(existing_content or "", text),
                        message_bus=self._message_bus,
                        worker_id=self._worker_id,
                        event_log_workspace=str(workspace),
                    ),
                }
            else:
                write_result = write_file_with_broadcast(
                    workspace=str(workspace),
                    file_path=rel_path,
                    content=text,
                    message_bus=self._message_bus,
                    worker_id=self._worker_id,
                    task_id=task_id,
                )
            if not bool(write_result.get("ok")):
                return {
                    "ok": False,
                    "error": str(write_result.get("error") or "write_file failed"),
                    "file": rel_path,
                }
            raw_bytes_written = write_result.get("bytes")
            result = {
                "ok": True,
                "file": rel_path,
                "bytes_written": (raw_bytes_written if type(raw_bytes_written) is int else len(text.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                # Content-level hashes are part of the physical mutation
                # evidence consumed by Factory.  A generic successful tool
                # receipt proves dispatch/settlement, not that bytes changed.
                "before_sha256": (
                    sha256(existing_content.encode("utf-8")).hexdigest()
                    if existing_content is not None
                    else "file_absent"
                ),
                "after_sha256": sha256(text.encode("utf-8")).hexdigest(),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": policy_result.get("director_policy"),
            }
            if normalized.normalized_patch_like:
                result["normalized_patch_like_write"] = True
            if write_hygiene_flags.get("block_comment_glob_sanitized"):
                result["block_comment_glob_sanitized"] = True
            if write_hygiene_flags.get("control_flow_comma_sanitized"):
                result["control_flow_comma_sanitized"] = True
            if json_config_result.get("repaired"):
                result["json_config_repaired"] = True
            # R147: surface TypeScript parse diagnostics in tool results so the
            # model/repair ladder can fix residual issues next turn.
            return attach_post_write_syntax_check(result, str(target))
        except (OSError, RuntimeError, TypeError, UnicodeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _tool_read_file(
        self,
        args: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        """读取文件工具"""
        file_path = args.get("file") or args.get("path") or args.get("filepath")

        if not file_path:
            return {"ok": False, "error": "Missing file path"}

        target = workspace / file_path
        try:
            if not target.exists():
                return {"ok": False, "error": f"File not found: {file_path}"}
            content = target.read_text(encoding="utf-8")
            return {"ok": True, "file": file_path, "content": content}
        except (OSError, UnicodeError) as exc:
            return {"ok": False, "error": str(exc)}

    def _tool_edit_blocks(
        self,
        args: dict[str, Any],
        workspace: Path,
        *,
        task_id: str = "",
        repair_effect: DirectorRepairEffectV1 | None = None,
    ) -> dict[str, Any]:
        """Apply SEARCH/REPLACE edit_blocks through Director policy + write path (R179)."""

        from polaris.kernelone.editing.editblock_engine import apply_edit_blocks, parse_edit_blocks

        raw_file_path = (
            args.get("file")
            or args.get("path")
            or args.get("filepath")
            or args.get("file_path")
            or args.get("filePath")
            or args.get("target_file")
            or args.get("target_path")
        )
        file_path = canonicalize_project_manifest_path(str(raw_file_path or "").strip())
        if not file_path:
            return {"ok": False, "error": "Missing file path"}
        if "\n" in file_path or "\r" in file_path:
            return {"ok": False, "error": f"Invalid file path contains newline: {file_path!r}"}

        raw_blocks = (
            args.get("blocks")
            if args.get("blocks") is not None
            else args.get("content")
            if args.get("content") is not None
            else args.get("edits")
            if args.get("edits") is not None
            else args.get("diff")
            if args.get("diff") is not None
            else ""
        )
        if isinstance(raw_blocks, (list, tuple)):
            blocks_text = "\n".join(str(item or "") for item in raw_blocks)
        else:
            blocks_text = str(raw_blocks or "")
        if not blocks_text.strip():
            return {"ok": False, "error": "edit_blocks requires non-empty blocks"}

        try:
            target = (workspace / file_path).resolve()
            if workspace not in target.parents and target != workspace:
                return {"ok": False, "error": f"Unsafe file path outside workspace: {file_path}"}
            guarded_snapshot = (
                _guarded_repair_snapshot(workspace=workspace, effect=repair_effect)
                if repair_effect is not None
                else None
            )
            if repair_effect is not None and guarded_snapshot is None:
                return {"ok": False, "error": "deo_target_state_drift"}
            if repair_effect is None and not target.exists():
                return {"ok": False, "error": f"File not found: {file_path}"}
            if not target.is_file():
                return {"ok": False, "error": f"Path is not a file: {file_path}"}

            content = (
                guarded_snapshot.content.decode("utf-8")
                if guarded_snapshot is not None
                else target.read_text(encoding="utf-8")
            )
            rel_path = target.relative_to(workspace).as_posix()
            blocks = parse_edit_blocks(blocks_text, default_filepath=rel_path)
            if not blocks:
                return {"ok": False, "error": "edit_blocks: no valid SEARCH/REPLACE blocks parsed"}
            scoped = []
            for block in blocks:
                block_path = str(block.filepath or "").replace("\\", "/").strip().lstrip("./")
                if not block_path or block_path == rel_path:
                    scoped.append(block)
            if not scoped:
                return {"ok": False, "error": f"edit_blocks: no blocks target {rel_path}"}
            applied = apply_edit_blocks({rel_path: content}, scoped, fuzzy=True)
            new_content = str(applied.get(rel_path, content))
            if new_content == content:
                return {"ok": False, "error": "edit_blocks: no content change after apply"}

            json_config_result = _validate_or_repair_json_config_content(rel_path=rel_path, content=new_content)
            if not json_config_result.get("ok"):
                return json_config_result
            final_content = str(
                json_config_result.get("content") if json_config_result.get("content") is not None else new_content
            )
            syntax_guard = _precommit_source_syntax_guard(
                rel_path=rel_path,
                before_content=content,
                after_content=final_content,
            )
            if not syntax_guard.get("ok"):
                return syntax_guard
            policy_result = self._validate_director_policy_for_write(
                workspace=workspace,
                rel_path=rel_path,
                old_content=content,
                new_content=final_content,
                operation="edit_file",
                tool_kwargs=args,
            )
            if not policy_result.get("ok"):
                return policy_result

            if repair_effect is not None:
                if (
                    not repair_effect.exists_after
                    or guarded_snapshot is None
                    or sha256(final_content.encode("utf-8")).hexdigest() != repair_effect.expected_after_hash
                ):
                    return {
                        "ok": False,
                        "error": "deo_target_state_drift",
                        "error_type": "directed_effect_cas_denied",
                    }
                guarded_compare_and_replace_regular_file(
                    workspace,
                    guarded_snapshot,
                    final_content.encode("utf-8"),
                    max_bytes=_MAX_GUARDED_REPAIR_BYTES,
                )
                write_result = {
                    "ok": True,
                    "broadcast_ok": broadcast_file_written(
                        file_path=rel_path,
                        operation="modify",
                        content_size=len(final_content.encode("utf-8")),
                        task_id=task_id,
                        patch=calculate_patch(content, final_content),
                        message_bus=self._message_bus,
                        worker_id=self._worker_id,
                        event_log_workspace=str(workspace),
                    ),
                }
            else:
                write_result = write_file_with_broadcast(
                    workspace=str(workspace),
                    file_path=rel_path,
                    content=final_content,
                    message_bus=self._message_bus,
                    worker_id=self._worker_id,
                    task_id=task_id,
                )
            if not bool(write_result.get("ok")):
                return {
                    "ok": False,
                    "error": str(write_result.get("error") or "edit_blocks failed"),
                    "file": rel_path,
                }
            return {
                "ok": True,
                "file": rel_path,
                "blocks_applied": len(scoped),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": policy_result.get("director_policy"),
            }
        except (OSError, RuntimeError, TypeError, UnicodeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _tool_edit_file(
        self,
        args: dict[str, Any],
        workspace: Path,
        *,
        task_id: str = "",
        repair_effect: DirectorRepairEffectV1 | None = None,
    ) -> dict[str, Any]:
        """编辑文件工具（搜索替换）"""
        raw_file_path = args.get("file") or args.get("path") or args.get("filepath")
        file_path = canonicalize_project_manifest_path(str(raw_file_path or "").strip())
        search = _recover_write_body_or_none(args.get("search") or args.get("old_string") or args.get("oldText"))
        replace = _recover_write_body_or_none(args.get("replace") or args.get("new_string") or args.get("newText"))

        if not file_path:
            return {"ok": False, "error": "Missing file path"}
        if "\n" in file_path or "\r" in file_path:
            return {"ok": False, "error": f"Invalid file path contains newline: {file_path!r}"}
        if search is None or replace is None:
            # R195/M03: an unrecoverable structured search/replace body must
            # fail-closed rather than be str()-serialized into the file.
            return {
                "ok": False,
                "error": "edit_file search/replace must be a UTF-8 string or recoverable text body",
                "error_type": "invalid_source_content",
            }
        if search == "":
            # R195/M03: an empty search is a recoverable arg-shape no-op, NOT a
            # control-plane integrity break. Previously this returned ok=False ->
            # deo_physical_execution_failed -> TOOL_RESULT_FAILED ->
            # run_ledger_integrity_failed -> DELIVERY_FAILED (L1-01 m03-r17: two
            # such calls killed the whole delivery). Allow it as a no-op so the
            # DEO batch is not dropped; the file is preserved (R193/R194 no-wipe),
            # the turn continues, and product-quality gates catch any genuine
            # downstream defect on a separate plane.
            return {
                "ok": True,
                "no_op": True,
                "reason": "edit_file_empty_search",
                "file": file_path,
            }

        try:
            target = (workspace / file_path).resolve()
            if workspace not in target.parents and target != workspace:
                return {"ok": False, "error": f"Unsafe file path outside workspace: {file_path}"}
            guarded_snapshot = (
                _guarded_repair_snapshot(workspace=workspace, effect=repair_effect)
                if repair_effect is not None
                else None
            )
            if repair_effect is not None and guarded_snapshot is None:
                return {"ok": False, "error": "deo_target_state_drift"}
            if repair_effect is None and not target.exists():
                return {"ok": False, "error": f"File not found: {file_path}"}
            if not target.is_file():
                return {"ok": False, "error": f"Path is not a file: {file_path}"}

            content = (
                guarded_snapshot.content.decode("utf-8")
                if guarded_snapshot is not None
                else target.read_text(encoding="utf-8")
            )
            if search not in content:
                return {"ok": False, "error": f"Search text not found in file: {search[:50]}..."}

            rel_path = target.relative_to(workspace).as_posix()
            new_content = content.replace(search, replace, 1)
            json_config_result = _validate_or_repair_json_config_content(rel_path=rel_path, content=new_content)
            if not json_config_result.get("ok"):
                return json_config_result
            final_content = str(
                json_config_result.get("content") if json_config_result.get("content") is not None else new_content
            )
            syntax_guard = _precommit_source_syntax_guard(
                rel_path=rel_path,
                before_content=content,
                after_content=final_content,
            )
            if not syntax_guard.get("ok"):
                return syntax_guard
            policy_result = self._validate_director_policy_for_write(
                workspace=workspace,
                rel_path=rel_path,
                old_content=content,
                new_content=final_content,
                operation="edit_file",
                tool_kwargs=args,
            )
            if not policy_result.get("ok"):
                return policy_result

            if repair_effect is not None:
                if (
                    not repair_effect.exists_after
                    or guarded_snapshot is None
                    or sha256(final_content.encode("utf-8")).hexdigest() != repair_effect.expected_after_hash
                ):
                    return {
                        "ok": False,
                        "error": "deo_target_state_drift",
                        "error_type": "directed_effect_cas_denied",
                    }
                guarded_compare_and_replace_regular_file(
                    workspace,
                    guarded_snapshot,
                    final_content.encode("utf-8"),
                    max_bytes=_MAX_GUARDED_REPAIR_BYTES,
                )
                replace_result = {
                    "ok": True,
                    "replacements": 1,
                    "broadcast_ok": broadcast_file_written(
                        file_path=rel_path,
                        operation="modify",
                        content_size=len(final_content.encode("utf-8")),
                        task_id=task_id,
                        patch=calculate_patch(content, final_content),
                        message_bus=self._message_bus,
                        worker_id=self._worker_id,
                        event_log_workspace=str(workspace),
                    ),
                }
            elif json_config_result.get("repaired"):
                replace_result = write_file_with_broadcast(
                    workspace=str(workspace),
                    file_path=rel_path,
                    content=final_content,
                    message_bus=self._message_bus,
                    worker_id=self._worker_id,
                    task_id=task_id,
                )
            else:
                replace_result = replace_in_file_with_broadcast(
                    workspace=str(workspace),
                    file_path=rel_path,
                    old_text=search,
                    new_text=replace,
                    count=1,
                    message_bus=self._message_bus,
                    worker_id=self._worker_id,
                    task_id=task_id,
                )
            if not bool(replace_result.get("ok")):
                return {
                    "ok": False,
                    "error": str(replace_result.get("error") or "edit_file failed"),
                    "file": rel_path,
                }
            result = {
                "ok": True,
                "file": rel_path,
                "replacements": int(replace_result.get("replacements") or 1),
                # Preserve exact physical pre/post state in the tool result so
                # the durable directed-effect receipt and downstream Factory
                # settlement can distinguish a real edit from dispatch/no-op.
                "before_sha256": sha256(content.encode("utf-8")).hexdigest(),
                "after_sha256": sha256(final_content.encode("utf-8")).hexdigest(),
                "broadcast_ok": bool(replace_result.get("broadcast_ok")),
                "director_policy": policy_result.get("director_policy"),
            }
            if json_config_result.get("repaired"):
                result["json_config_repaired"] = True
            return result
        except (OSError, RuntimeError, TypeError, UnicodeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _tool_delete_file(
        self,
        args: dict[str, Any],
        workspace: Path,
        *,
        task_id: str = "",
        repair_effect: DirectorRepairEffectV1 | None = None,
    ) -> dict[str, Any]:
        """Delete a single workspace file through Director policy gates."""
        raw_file_path = args.get("file") or args.get("path") or args.get("filepath")
        file_path = str(raw_file_path or "").strip()

        if not file_path:
            return {"ok": False, "error": "Missing file path"}
        if "\n" in file_path or "\r" in file_path:
            return {"ok": False, "error": f"Invalid file path contains newline: {file_path!r}"}
        if _MIN_FILES_PATTERN.match(file_path) or _MIN_LINES_PATTERN.match(file_path):
            return {"ok": False, "error": f"Invalid file path resembles requirement sentence: {file_path}"}
        if re.match(r"^(table|index)\s+if\s+not\s+exists\b", file_path, re.IGNORECASE):
            return {"ok": False, "error": f"Invalid file path resembles SQL statement: {file_path}"}

        try:
            fs = KernelFileSystem(str(workspace.resolve()), LocalFileSystemAdapter())
            rel_path = fs.to_workspace_relative_path(file_path)

            guarded_snapshot = (
                _guarded_repair_snapshot(workspace=workspace, effect=repair_effect)
                if repair_effect is not None
                else None
            )
            if repair_effect is not None and (guarded_snapshot is None or repair_effect.exists_after):
                return {
                    "ok": False,
                    "error": "deo_target_state_drift",
                    "error_type": "directed_effect_cas_denied",
                }

            if rel_path == ".":
                return {"ok": False, "error": f"Path is not a file: {file_path}", "file": rel_path}
            if not fs.workspace_exists(rel_path):
                return {"ok": False, "error": f"File not found: {file_path}", "file": rel_path}
            if fs.workspace_is_dir(rel_path):
                return {"ok": False, "error": f"Path is a directory: {file_path}", "file": rel_path}
            if not fs.workspace_is_file(rel_path):
                return {"ok": False, "error": f"Path is not a file: {file_path}", "file": rel_path}

            policy_result = self._validate_director_policy_for_write(
                workspace=workspace,
                rel_path=rel_path,
                old_content=(guarded_snapshot.content.decode("utf-8") if guarded_snapshot is not None else ""),
                new_content="",
                operation="delete_file",
                tool_kwargs=args,
            )
            if not policy_result.get("ok"):
                return policy_result

            if guarded_snapshot is not None:
                guarded_compare_and_remove_regular_file(
                    workspace,
                    guarded_snapshot,
                    max_bytes=_MAX_GUARDED_REPAIR_BYTES,
                )
                deleted = True
            else:
                deleted = fs.workspace_remove(rel_path, missing_ok=False)
            if not deleted:
                return {"ok": False, "error": f"Failed to delete file: {file_path}", "file": rel_path}

            broadcast_ok = broadcast_file_written(
                file_path=rel_path,
                operation="delete",
                content_size=0,
                task_id=task_id,
                patch="",
                message_bus=self._message_bus,
                worker_id=self._worker_id,
                event_log_workspace=str(workspace),
            )
            return {
                "ok": True,
                "file": rel_path,
                "path": rel_path,
                "deleted": True,
                "bytes_written": 0,
                "operation": "delete_file",
                "broadcast_ok": bool(broadcast_ok),
                "director_policy": policy_result.get("director_policy"),
            }
        except (OSError, PathSecurityError, RuntimeError, TypeError, UnicodeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    # -------------------------------------------------------------------------
    # Command Tools
    # -------------------------------------------------------------------------

    def _tool_run_command(
        self,
        args: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        """运行命令工具（安全加固版）"""
        command_raw = args.get("command") or args.get("cmd")
        command = str(command_raw or "").strip()
        if not command:
            return {"ok": False, "error": "Missing command"}
        timeout_raw = args.get("timeout", 30)
        try:
            timeout = int(timeout_raw)
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(1, min(timeout, 300))
        security = self._validate_command_security(command)
        if security:
            return security
        use_shell = bool(args.get("shell", False))
        try:
            cmd_args = shlex.split(command, posix=False) if not use_shell else command
        except ValueError as exc:
            logger.warning(
                "Command argument parsing failed; blocking shell=True fallback to prevent "
                "command injection. command=%r ValueError=%s",
                command,
                exc,
            )
            raise CommandInjectionBlocked(
                command=command,
                reason=f"shlex.split.failed:{exc}",
            ) from exc
        return self._run_command_service(cmd_args, workspace, timeout, use_shell)

    def _validate_command_security(self, command: str) -> dict[str, Any] | None:
        """验证命令安全性"""
        if TOOLING_SECURITY_AVAILABLE:
            if is_command_blocked(command):
                # Layer 2 / R195-pattern (L1-01 r15 + r22 recurring killer): a
                # command blocked for compound/restricted shell metacharacters is
                # a RECOVERABLE denial, not a control-plane integrity break. The
                # security guard is PRESERVED -- the command is never executed --
                # but returning ok=False projected as deo_physical_execution_failed
                # -> TOOL_RESULT_FAILED -> run_ledger_integrity_failed and killed
                # the whole delivery. Return a non-fatal no-op with corrective
                # feedback so the model can re-issue single commands and the
                # ledger stays clean; product-quality gates catch unverified builds.
                return {
                    "ok": True,
                    "no_op": True,
                    "blocked": True,
                    "reason": "command_blocked_compound_or_restricted",
                    "output": (
                        "Command not executed: matches a compound/restricted shell "
                        "pattern. Re-issue as separate single commands, one per call "
                        "(e.g. 'npm run build', then 'npm test')."
                    ),
                    "exit_code": 0,
                }
            if not is_command_allowed(command, ALLOWED_EXECUTION_COMMANDS):
                return {"ok": False, "error": "Command not in allowed whitelist"}
        else:
            lowered = command.lower()
            for pattern in (
                "rm -rf",
                "del /s",
                "rmdir /s",
                ";",
                "&&",
                "||",
                "|",
                "`",
                "$(",
                ">",
                "<",
            ):
                if pattern in lowered:
                    return {"ok": False, "error": f"Dangerous pattern: {pattern}"}
        return None

    def _run_command_service(
        self,
        cmd_args: Any,
        workspace: Path,
        timeout: int,
        use_shell: bool,
    ) -> dict[str, Any]:
        """通过 CommandExecutionService 执行命令"""
        from polaris.kernelone.process.command_executor import (
            CommandExecutionService,
            CommandRequest,
        )

        try:
            if use_shell or not isinstance(cmd_args, list):
                cmd_args = shlex.split(str(cmd_args)) if not use_shell else []
                if not cmd_args:
                    return {"ok": False, "error": "Cannot parse shell command safely"}
            cmd_svc = CommandExecutionService(str(workspace))
            request = CommandRequest(
                executable=cmd_args[0],
                args=cmd_args[1:] if len(cmd_args) > 1 else [],
                cwd=str(workspace),
                timeout_seconds=timeout,
            )
            result = cmd_svc.run(request)
            return {
                "ok": result.get("ok", False),
                "exit_code": result.get("returncode", -1),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    # -------------------------------------------------------------------------
    # Search Tool
    # -------------------------------------------------------------------------

    def _tool_search_code(
        self,
        args: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        """代码搜索工具（使用 ripgrep）"""
        from polaris.kernelone.process.command_executor import (
            CommandExecutionService,
            CommandRequest,
        )

        query = args.get("query") or args.get("search")
        if not query:
            return {"ok": False, "error": "Missing query"}

        try:
            # 使用 rg 进行搜索
            cmd_svc = CommandExecutionService(str(workspace))
            request = CommandRequest(
                executable="rg",
                args=[
                    "-n",
                    "-i",
                    "--type-add",
                    "code:*.{py,js,ts,jsx,tsx,java,go,rs,c,cpp}",
                    "-tcode",
                    query,
                ],
                cwd=str(workspace),
                timeout_seconds=10,
            )
            result = cmd_svc.run(request)
            stdout = result.get("stdout", "")
            ok = result.get("ok", False)
            return {
                "ok": True,
                "query": query,
                "results": stdout if ok else "",
                "count": len([ln for ln in stdout.split("\n") if ln.strip()]) if ok else 0,
            }
        except FileNotFoundError:
            # rg 未安装，返回模拟结果
            return {"ok": True, "query": query, "results": "", "count": 0, "note": "rg not installed"}
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}


_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_INSTANCES: WeakSet[DirectorToolExecutor] = WeakSet()


def _create_director_tool_executor(
    workspace: str,
    *,
    message_bus: Any | None = None,
    worker_id: str = "director",
) -> DirectorToolExecutor:
    """Create the private physical executor for the canonical DEO mutation port."""

    executor = DirectorToolExecutor(
        workspace,
        message_bus=message_bus,
        worker_id=worker_id,
        _physical_execution_authority=_DIRECTED_EFFECT_PHYSICAL_EXECUTOR_AUTHORITY,
    )
    _DIRECTED_EFFECT_PHYSICAL_EXECUTOR_INSTANCES.add(executor)
    return executor

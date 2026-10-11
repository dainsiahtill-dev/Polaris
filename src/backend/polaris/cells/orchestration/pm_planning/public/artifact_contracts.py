"""Pure PM artifact/action contracts shared by PM admission and Factory.

Declarations are promises, never effects, build success or direct write grants.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

_LANGUAGE_SOURCE_EXTENSIONS: dict[str, frozenset[str]] = {
    "javascript": frozenset({".js", ".mjs", ".cjs", ".jsx"}),
    "typescript": frozenset({".ts", ".tsx", ".mts", ".cts"}),
    "python": frozenset({".py"}),
    "rust": frozenset({".rs"}),
    "go": frozenset({".go"}),
    "java": frozenset({".java"}),
    "cpp": frozenset({".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"}),
    "csharp": frozenset({".cs"}),
    "ruby": frozenset({".rb"}),
    "swift": frozenset({".swift"}),
    "kotlin": frozenset({".kt", ".kts"}),
    "scala": frozenset({".scala"}),
}

_LANGUAGE_NEUTRAL_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".md",
        ".txt",
        ".html",
        ".css",
        ".xml",
        ".csv",
        ".lock",
        # Build/QA/run helper scripts: auxiliary automation that legitimately
        # appears in projects of ANY primary language (e.g. a Go project's
        # scripts/qa.sh QA verifier). These are not "wrong language" source
        # files, so they must not trip the language-consistency mismatch guard.
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
    }
)

_LANGUAGE_NEUTRAL_FILENAMES: frozenset[str] = frozenset(
    {
        "go.mod",
        "go.sum",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "cmakelists.txt",
        "cargo.toml",
        "cargo.lock",
    }
)


def _pm_artifact_relative_path(value: Any, *, allow_root: bool = False) -> str:
    """Resolve only unambiguous workspace-relative plan paths, without touching disk."""
    if not isinstance(value, str) or not value or value != value.strip():
        return ""
    if any(ord(char) < 32 or char in ':~$*?"<>|;' for char in value):
        return ""
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized == ".":
        return "." if allow_root else ""
    if not normalized or normalized.startswith("/"):
        return ""
    if any(part in {"", ".", ".."} or part != part.strip() for part in normalized.split("/")):
        return ""
    return normalized


def _pm_primary_source_targets(task: dict[str, Any], extensions: frozenset[str]) -> set[str]:
    """Distinguish product inputs from test targets and declaration-only TypeScript."""
    if task.get("phase") != "implementation" or str(task.get("assigned_to") or "").lower() != "director":
        return set()
    targets = task.get("target_files")
    if not isinstance(targets, list):
        return set()
    sources: set[str] = set()
    for value in targets:
        path = _pm_artifact_relative_path(value)
        if not path or Path(path).suffix.lower() not in extensions:
            continue
        lowered = path.lower()
        if set(lowered.split("/")[:-1]) & {"test", "tests", "__tests__", "qa"}:
            continue
        if lowered.endswith((".d.ts", ".d.mts", ".d.cts", ".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")):
            continue
        sources.add(path)
    return sources


def _pm_typed_verifiers(task: dict[str, Any]) -> list[tuple[str, list[str], str]]:
    """Retain exact typed command/cwd rows; malformed rows confer no classification."""
    rows = task.get("verification_commands")
    if not isinstance(rows, list):
        return []
    verifiers: list[tuple[str, list[str], str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        argv = row.get("argv")
        cwd = _pm_artifact_relative_path(row.get("cwd"), allow_root=True)
        if not cwd or not isinstance(argv, list) or not argv:
            continue
        if not all(isinstance(arg, str) and arg and arg == arg.strip() for arg in argv):
            continue
        modality = row.get("modality")
        if isinstance(modality, str) and modality in {"build", "entrypoint", "test", "lint"}:
            verifiers.append((modality, argv, cwd))
    return verifiers


def _pm_direct_verifier_script(argv: list[str], cwd: str) -> str:
    """Read a direct interpreter operand, never eval/shell text or incidental filenames."""
    if argv[0] == "node":
        index = 2 if len(argv) > 1 and argv[1] == "--test" else 1
        suffixes = {".js", ".mjs", ".cjs"}
    elif argv[0] in {"python", "python3"}:
        index, suffixes = 1, {".py"}
    else:
        return ""
    if len(argv) <= index:
        return ""
    operand = _pm_artifact_relative_path(argv[index])
    if not operand or operand.startswith("-") or Path(operand).suffix.lower() not in suffixes:
        return ""
    return operand if cwd == "." else f"{cwd}/{operand}"


def _pm_typescript_emitted_output_kind(path: str) -> str:
    """Recognize emitted code families without treating caches as emitted code."""
    code_path = path.removesuffix(".map")
    if code_path.endswith((".d.ts", ".d.mts", ".d.cts")):
        return "declaration"
    if PurePosixPath(code_path).suffix.lower() in {".js", ".mjs", ".cjs", ".jsx"}:
        return "javascript"
    return ""


def _pm_emitting_typescript_build(argv: list[str], cwd: str, sources: set[str], output_target: str) -> bool:
    """Bind finite tsc options to the declared code/declaration/map output kind."""
    output_kind = _pm_typescript_emitted_output_kind(output_target)
    if cwd != "." and not output_target.startswith(f"{cwd}/"):
        return False
    if argv[:2] == ["npx", "tsc"]:
        args = argv[2:]
    elif argv[:3] == ["npx", "--no-install", "tsc"]:
        args = argv[3:]
    elif argv[0] == "tsc":
        args = argv[1:]
    else:
        return False
    project_dir = cwd
    project_seen = False
    output_seen = False
    explicit_sources: set[str] = set()
    index = 0
    value_options = {"--outDir", "--rootDir", "--outFile", "--target", "--module", "--moduleResolution", "--lib"}
    flag_options = {
        "--strict",
        "--esModuleInterop",
        "--skipLibCheck",
        "--declaration",
        "--sourceMap",
        "--declarationMap",
    }
    while index < len(args):
        arg = args[index]
        if arg in {"-p", "--project"}:
            if project_seen:
                return False
            project_seen = True
            index += 1
            if index >= len(args):
                return False
            project = _pm_artifact_relative_path(args[index], allow_root=True)
            if not project:
                return False
            if project.endswith(".json"):
                project = str(Path(project).parent)
            project_dir = project if cwd == "." else (cwd if project == "." else f"{cwd}/{project}")
        elif arg in value_options:
            index += 1
            if index >= len(args) or args[index].startswith("-"):
                return False
            if arg in {"--outDir", "--rootDir", "--outFile"}:
                path = _pm_artifact_relative_path(args[index], allow_root=arg != "--outFile")
                if not path:
                    return False
                if arg in {"--outDir", "--outFile"}:
                    if output_seen:
                        # Repeated or mutually exclusive output options are ambiguous.
                        return False
                    output_seen = True
                    output_path = path if cwd == "." else (cwd if path == "." else f"{cwd}/{path}")
                    if arg == "--outFile":
                        expected_output = output_path
                        if output_kind == "declaration":
                            extension = PurePosixPath(output_path).suffix
                            declaration_extension = {".mjs": ".d.mts", ".cjs": ".d.cts"}.get(extension, ".d.ts")
                            expected_output = str(PurePosixPath(output_path).with_suffix(declaration_extension))
                        if output_target.endswith(".map"):
                            expected_output += ".map"
                        if output_target != expected_output:
                            return False
                    elif output_path != "." and not output_target.startswith(f"{output_path}/"):
                        return False
        elif arg == "--emitDeclarationOnly":
            if output_kind != "declaration":
                return False
        elif arg not in flag_options:
            source = _pm_artifact_relative_path(arg)
            if not source or Path(source).suffix.lower() not in _LANGUAGE_SOURCE_EXTENSIONS["typescript"]:
                # Includes noEmit, watch, help, eval, and unknown flags.
                return False
            explicit_sources.add(source if cwd == "." else f"{cwd}/{source}")
        index += 1
    if explicit_sources:
        return not project_seen and explicit_sources <= sources
    return any(project_dir == "." or source.startswith(f"{project_dir}/") for source in sources)


def _pm_declared_auxiliary_targets(
    task: dict[str, Any], primary_language: str, *, has_product_sources: bool
) -> set[str]:
    """Classify declarations per owner; this does not prove effects or verifier success."""
    if not has_product_sources:
        return set()
    extensions = _LANGUAGE_SOURCE_EXTENSIONS[primary_language]
    sources = _pm_primary_source_targets(task, extensions)
    verifiers = _pm_typed_verifiers(task)
    if (
        task.get("phase") == "verification"
        and str(task.get("assigned_to") or "").lower() == "qa"
        and not _pm_primary_source_targets({**task, "phase": "implementation", "assigned_to": "Director"}, extensions)
    ):
        return {
            script
            for modality, argv, cwd in verifiers
            if modality == "test" and (script := _pm_direct_verifier_script(argv, cwd))
        }
    if primary_language != "typescript" or not sources:
        return set()
    builds = [(argv, cwd) for modality, argv, cwd in verifiers if modality == "build"]
    return {
        script
        for modality, argv, cwd in verifiers
        if modality == "entrypoint"
        and argv[0] == "node"
        and len(argv) > 1
        and not argv[1].startswith("-")
        and (script := _pm_direct_verifier_script(argv, cwd))
        and any(
            _pm_emitting_typescript_build(build_argv, build_cwd, sources, script) for build_argv, build_cwd in builds
        )
    }


_PM_VERIFICATION_MODALITY_ORDER = ("environment_prep", "build", "test", "lint", "entrypoint")
_PM_VERIFICATION_MODALITIES = frozenset(_PM_VERIFICATION_MODALITY_ORDER)
_PM_SHELL_LAUNCHERS = frozenset({"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"})


def _validate_pm_verification_commands(
    value: Any, *, path: str = "$.verification_commands"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return non-authoritative diagnostics without silently losing bad rows."""
    diagnostics: list[dict[str, Any]] = []

    def reject(row_path: str, code: str, message: str, **details: Any) -> None:
        diagnostics.append({"path": row_path, "code": code, "message": message, **details})

    if type(value) is not list:
        reject(path, "invalid_type", "verification_commands must be an array")
        return [], diagnostics
    normalized: list[dict[str, Any]] = []
    for index, raw_row in enumerate(value):
        row_path = f"{path}[{index}]"
        if type(raw_row) is not dict or set(raw_row) != {"modality", "argv", "cwd"}:
            reject(
                row_path,
                "invalid_row_shape",
                "Row fields must be exactly modality/argv/cwd",
                allowed=["modality", "argv", "cwd"],
            )
            continue
        modality = raw_row.get("modality")
        argv_value = raw_row.get("argv")
        cwd_value = raw_row.get("cwd")
        if type(modality) is not str or modality not in _PM_VERIFICATION_MODALITIES:
            reject(
                f"{row_path}.modality",
                "invalid_modality",
                "Unknown verifier modality",
                allowed=list(_PM_VERIFICATION_MODALITY_ORDER),
            )
            continue
        if type(argv_value) is not list or not argv_value or len(argv_value) > 128:
            reject(f"{row_path}.argv", "invalid_argv", "argv must contain 1..128 non-empty strings")
            continue
        argv: list[str] = []
        invalid_argv = False
        for item in argv_value:
            if type(item) is not str or not item or item != item.strip() or "\x00" in item:
                invalid_argv = True
                break
            argv.append(item)
        if invalid_argv:
            reject(
                f"{row_path}.argv",
                "invalid_argv_item",
                "argv items must be non-empty strings without surrounding whitespace or NUL",
            )
            continue
        executable = PurePosixPath(argv[0].replace("\\", "/")).name.lower()
        if executable in _PM_SHELL_LAUNCHERS and any(item in {"-c", "-lc", "/c"} for item in argv[1:]):
            reject(
                f"{row_path}.argv",
                "shell_wrapper_forbidden",
                "Shell command-string wrappers are not verifier authority",
            )
            continue
        if (
            type(cwd_value) is not str
            or not cwd_value
            or cwd_value != cwd_value.strip()
            or any(ord(char) < 32 for char in cwd_value)
        ):
            reject(f"{row_path}.cwd", "invalid_cwd", "cwd must be '.' or a normalized relative directory")
            continue
        if cwd_value != ".":
            windows_path = PureWindowsPath(cwd_value)
            posix_path = PurePosixPath(cwd_value)
            if (
                "\\" in cwd_value
                or windows_path.drive
                or windows_path.root
                or posix_path.is_absolute()
                or any(part in {"", ".", ".."} for part in cwd_value.split("/"))
                or posix_path.as_posix() != cwd_value
            ):
                reject(
                    f"{row_path}.cwd",
                    "invalid_cwd",
                    "cwd must stay within the workspace as a normalized relative directory",
                )
                continue
        normalized.append({"modality": modality, "argv": argv, "cwd": cwd_value})
    return ([] if diagnostics else normalized), diagnostics


validate_pm_verification_commands = _validate_pm_verification_commands


def validate_generated_outputs(
    value: Any, *, path: str = "$.generated_outputs"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate exact declaration shape without inventing or executing anything."""
    diagnostics: list[dict[str, Any]] = []

    def reject(where: str, message: str) -> None:
        diagnostics.append({"code": "generated_output_schema_invalid", "path": where, "message": message})

    if type(value) is not list:
        reject(path, "generated_outputs must be an array")
        return [], diagnostics
    result: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        where = f"{path}[{index}]"
        if type(row) is not dict or set(row) != {"path", "inputs", "build"}:
            reject(where, "Output fields must be exactly path/inputs/build")
            continue
        output = row["path"]
        if not _canonical_artifact_path(output):
            reject(where + ".path", "Output must be a canonical workspace-relative file")
        inputs = row["inputs"]
        if type(inputs) is not list or not inputs:
            reject(where + ".inputs", "Output needs authored input rows")
        else:
            seen: set[tuple[str, str]] = set()
            for offset, item in enumerate(inputs):
                item_path = f"{where}.inputs[{offset}]"
                if type(item) is not dict or set(item) != {"task_id", "path"}:
                    reject(item_path, "Input fields must be exactly task_id/path")
                    continue
                identity = item["task_id"]
                source = item["path"]
                if type(identity) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", identity) is None:
                    reject(item_path + ".task_id", "Input needs a canonical task ID")
                if not _canonical_artifact_path(source):
                    reject(item_path + ".path", "Input must be a canonical authored file")
                if isinstance(identity, str) and isinstance(source, str):
                    key = (identity, source)
                    if key in seen:
                        reject(item_path, "Duplicate input binding")
                    seen.add(key)
        build = row["build"]
        if type(build) is not dict or set(build) != {"argv", "cwd"}:
            reject(where + ".build", "Build fields must be exactly argv/cwd")
        else:
            _, errors = _validate_pm_verification_commands([{"modality": "build", **build}], path=where + ".build")
            for error in errors:
                reject(error["path"], error["message"])
        result.append(deepcopy(row))
    return ([] if diagnostics else result), diagnostics


def _canonical_artifact_path(value: Any) -> bool:
    return (
        type(value) is str
        and "\\" not in value
        and _pm_artifact_relative_path(value) == value
        and value not in {"", "."}
        and str(PurePosixPath(value)) == value
    )


@dataclass(frozen=True)
class GeneratedOutputV1:
    """Immutable expected artifact; carries no effect or write authority."""

    path: str
    inputs: tuple[tuple[str, str], ...]
    build_argv: tuple[str, ...]
    build_cwd: str

    def __post_init__(self) -> None:
        if type(self.inputs) is not tuple or any(type(item) is not tuple or len(item) != 2 for item in self.inputs):
            raise ValueError("inputs must contain immutable task/path pairs")
        if type(self.build_argv) is not tuple:
            raise ValueError("build_argv must be immutable")
        _, errors = validate_generated_outputs([self.to_dict()])
        if errors:
            raise ValueError(str(errors))

    @classmethod
    def from_dict(cls, value: Any) -> GeneratedOutputV1:
        rows, errors = validate_generated_outputs([value])
        if errors:
            raise ValueError(str(errors))
        row = rows[0]
        return cls(
            row["path"],
            tuple((i["task_id"], i["path"]) for i in row["inputs"]),
            tuple(row["build"]["argv"]),
            row["build"]["cwd"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "inputs": [{"task_id": identity, "path": path} for identity, path in self.inputs],
            "build": {"argv": list(self.build_argv), "cwd": self.build_cwd},
        }


@dataclass(frozen=True)
class PMArtifactContractResultV1:
    """Pure admission and inventory projection, never an execution verdict."""

    diagnostics: tuple[dict[str, Any], ...]
    authored_targets: tuple[str, ...]
    generated_outputs: tuple[dict[str, Any], ...]

    @property
    def ok(self) -> bool:
        return not self.diagnostics


def validate_pm_artifact_contracts(
    tasks: list[dict[str, Any]], *, primary_language: str = ""
) -> PMArtifactContractResultV1:
    """Single PM/Factory artifact, action, language and generated graph gate.

    Legacy typed-tsc/direct-test classification remains bounded to already
    authored paths. New output declarations never become authored targets.
    """
    diagnostics: list[dict[str, Any]] = []
    language = primary_language.strip().lower()
    if type(tasks) is not list or any(type(task) is not dict for task in tasks):
        return PMArtifactContractResultV1(
            ({"code": "artifact_contract_schema_invalid", "path": "$.tasks", "message": "Tasks must be object rows"},),
            (),
            (),
        )
    for index, task in enumerate(tasks):
        if "metadata" in task and type(task["metadata"]) is not dict:
            return PMArtifactContractResultV1(
                (
                    {
                        "code": "artifact_contract_schema_invalid",
                        "path": f"$.tasks[{index}].metadata",
                        "message": "Task metadata must be an object",
                    },
                ),
                (),
                (),
            )
        for field in ("target_files", "context_files", "depends_on", "dependencies"):
            if field in task and (
                type(task[field]) is not list or any(type(value) is not str for value in task[field])
            ):
                return PMArtifactContractResultV1(
                    (
                        {
                            "code": "artifact_contract_schema_invalid",
                            "path": f"$.tasks[{index}].{field}",
                            "message": "Artifact/dependency fields must be string arrays",
                        },
                    ),
                    (),
                    (),
                )
    extensions = _LANGUAGE_SOURCE_EXTENSIONS.get(language, frozenset())
    by_id = {str(t.get("id") or f"TASK-{i + 1}"): t for i, t in enumerate(tasks)}
    ancestors: dict[str, set[str]] = {}
    authored = tuple(dict.fromkeys(p for t in tasks for p in t.get("target_files", []) if isinstance(p, str)))
    authored_set = set(authored)
    records: list[dict[str, Any]] = []
    owners: dict[str, str] = {}

    def reject(index: int, field: str, code: str, message: str, *, fields: tuple[str, ...] = ()) -> None:
        task = tasks[index]
        metadata = task.get("metadata", {})
        origin = metadata.get("pm_artifact_source_path") if isinstance(metadata, dict) else None
        diagnostics.append(
            {
                "code": code,
                "path": f"{origin or f'$.tasks[{index}]'}.{field}",
                "task_id": str(task.get("id") or f"TASK-{index + 1}"),
                "message": message,
                **({"repair_fields": list(fields)} if fields else {}),
            }
        )

    def collect(identity: str, stack: set[str]) -> set[str]:
        if identity in stack:
            raise ValueError("Cyclic dependency")
        if identity in ancestors:
            return ancestors[identity]
        task = by_id[identity]
        deps = task.get("depends_on", task.get("dependencies", []))
        if not isinstance(deps, list) or any(not isinstance(d, str) or d not in by_id for d in deps):
            raise ValueError("Unknown or malformed dependency")
        result: set[str] = set()
        for dep in deps:
            result.add(dep)
            result.update(collect(dep, stack | {identity}))
        ancestors[identity] = result
        return result

    if len(by_id) != len(tasks):
        reject(0, "id", "artifact_dependency_invalid", "Task IDs must be unique")
    for index, task in enumerate(tasks):
        identity = str(task.get("id") or f"TASK-{index + 1}")
        if "verification_commands" in task:
            _, errors = _validate_pm_verification_commands(task["verification_commands"])
            for error in errors:
                field = error["path"].removeprefix("$.verification_commands")
                reject(index, "verification_commands" + field, error["code"], error["message"])
        try:
            collect(identity, set())
        except (ValueError, KeyError):
            reject(
                index, "depends_on", "artifact_dependency_invalid", "Dependencies must be a known acyclic task graph"
            )
            ancestors[identity] = set()
        if "generated_outputs" in task:
            rows, errors = validate_generated_outputs(
                task["generated_outputs"], path=f"$.tasks[{index}].generated_outputs"
            )
            for error in errors:
                field = error["path"].split(f"$.tasks[{index}].", 1)[-1]
                reject(index, field, error["code"], error["message"])
            for row in rows:
                output = row["path"]
                if output in owners or output in authored_set:
                    reject(
                        index,
                        "generated_outputs",
                        "generated_output_ownership_invalid",
                        "Generated output must have one owner and no authored-write collision",
                    )
                owners[output] = identity
                records.append({"task_id": identity, **row})
        targets = task.get("target_files", [])
        contexts = task.get("context_files", [])
        if isinstance(contexts, list) and {p for p in contexts if isinstance(p, str)} & set(targets):
            reject(
                index,
                "context_files",
                "artifact_readonly_write_collision",
                "Read-only inputs cannot be task-owned writes",
            )
        if (
            not targets
            and task.get("execution_backend", task.get("metadata", {}).get("execution_backend", "code_edit"))
            == "code_edit"
        ):
            reject(
                index, "target_files", "artifact_action_contract_invalid", "code_edit requires a useful owned artifact"
            )
        scope = str(task.get("scope", ""))
        if task.get("execution_backend", "code_edit") == "code_edit" and re.search(
            r"(?:\bno\s+(?:code\s+)?(?:changes|writes)\b|无代码变更|不修改任何文件)", scope, re.IGNORECASE
        ):
            reject(
                index,
                "scope",
                "artifact_action_contract_invalid",
                "A passive milestone cannot declare code_edit; author a useful verification artifact or use native acceptance orchestration",
                fields=(
                    "target_files",
                    "context_files",
                    "scope_paths",
                    "scope",
                    "goal",
                    "description",
                    "steps",
                    "acceptance",
                    "verification_commands",
                ),
            )

    has_sources = bool(extensions) and any(_pm_primary_source_targets(t, extensions) for t in tasks)
    for record in records:
        identity = record["task_id"]
        index = next(i for i, t in enumerate(tasks) if str(t.get("id") or f"TASK-{i + 1}") == identity)
        task = tasks[index]
        sources: set[str] = set()
        for item in record["inputs"]:
            input_owner = item["task_id"]
            if (
                input_owner not in by_id
                or input_owner not in (ancestors.get(identity, set()) | {identity})
                or item["path"] not in by_id.get(input_owner, {}).get("target_files", [])
            ):
                reject(
                    index,
                    "generated_outputs",
                    "generated_output_input_invalid",
                    "Inputs must bind self/ancestor authored targets",
                )
            elif PurePosixPath(item["path"]).suffix.lower() in (
                _LANGUAGE_SOURCE_EXTENSIONS["typescript"] if not language else extensions
            ):
                sources.add(item["path"])
        rows, errors = _validate_pm_verification_commands(task.get("verification_commands", []))
        build = record["build"]
        if errors or not any(
            r["modality"] == "build" and r["argv"] == build["argv"] and r["cwd"] == build["cwd"] for r in rows
        ):
            reject(
                index,
                "generated_outputs",
                "generated_output_build_invalid",
                "Build must equal a same-task typed build verifier",
            )
        argv = build["argv"]
        explicit_tsc = argv[0] == "tsc" or argv[:2] == ["npx", "tsc"] or argv[:3] == ["npx", "--no-install", "tsc"]
        if (language == "typescript" or explicit_tsc) and _pm_typescript_emitted_output_kind(record["path"]):
            if not sources:
                reject(
                    index,
                    "generated_outputs",
                    "generated_output_input_invalid",
                    "Compiled code needs primary-language authored input",
                )
            if explicit_tsc and not _pm_emitting_typescript_build(argv, build["cwd"], sources, record["path"]):
                reject(
                    index,
                    "generated_outputs",
                    "generated_output_build_invalid",
                    "Explicit compiler options contradict the output/input declaration",
                )
    action_fields = (
        "target_files",
        "context_files",
        "scope_paths",
        "scope",
        "goal",
        "description",
        "steps",
        "acceptance",
        "verification_commands",
    )
    output_consumers: dict[str, set[str]] = {}
    for index, task in enumerate(tasks):
        identity = str(task.get("id") or f"TASK-{index + 1}")
        accessible = ancestors.get(identity, set()) | {identity}
        auxiliaries = (
            _pm_declared_auxiliary_targets(task, language, has_product_sources=has_sources) if extensions else set()
        )
        wrong: list[str] = []
        for path in task.get("target_files", []):
            if not isinstance(path, str) or not extensions:
                continue
            normalized = path.replace("\\", "/")
            name = PurePosixPath(normalized).name.lower()
            ext = PurePosixPath(normalized).suffix.lower()
            canonical = _pm_artifact_relative_path(path)
            if name in _LANGUAGE_NEUTRAL_FILENAMES or ext in _LANGUAGE_NEUTRAL_EXTENSIONS or not ext:
                continue
            if canonical == "tests/test_product.py" and task.get("phase") != "implementation":
                continue
            if ext not in extensions and canonical not in auxiliaries:
                wrong.append(path)
        if wrong:
            reject(
                index, "target_files", "pm_plan_language_mismatch", f"Wrong primary-language authored targets: {wrong}"
            )
            if task.get("phase") == "verification":
                for path in wrong:
                    output_consumers.setdefault(path, set()).add(identity)
                reject(
                    index,
                    "target_files",
                    "artifact_action_contract_invalid",
                    "Verification code_edit must own a useful test/report; observed outputs are read-only dependencies",
                    fields=action_fields,
                )
        for path in task.get("context_files", []):
            if path in owners and owners[path] not in accessible:
                reject(
                    index,
                    "depends_on",
                    "generated_output_dependency_invalid",
                    "Generated context requires its producer dependency",
                )
    # A wrong authored verification target is a structured contradiction. Its
    # common source/build ancestors may propose outputs in the bounded revision.
    # This grants field edits only: no path/stem/prose inference, chosen owner,
    # declaration obligation, write scope or successful-build evidence.
    proposals: dict[str, set[str]] = {}
    for consumers in output_consumers.values():
        common = set.intersection(*(ancestors.get(consumer, set()) for consumer in consumers))
        candidates = {
            identity
            for identity in common
            if _pm_primary_source_targets(by_id[identity], extensions)
            and any(
                modality == "build" and not ({"--noEmit", "--emitDeclarationOnly"} & set(argv))
                for modality, argv, _ in _pm_typed_verifiers(by_id[identity])
            )
        }
        for consumer in consumers:
            proposals.setdefault(consumer, set()).update(candidates)
    for diagnostic in diagnostics:
        if diagnostic["code"] == "artifact_action_contract_invalid" and proposals.get(diagnostic["task_id"]):
            diagnostic["proposal_task_ids"] = sorted(proposals[diagnostic["task_id"]])
    return PMArtifactContractResultV1(tuple(diagnostics), authored, () if diagnostics else tuple(deepcopy(records)))


__all__ = [
    "GeneratedOutputV1",
    "PMArtifactContractResultV1",
    "validate_generated_outputs",
    "validate_pm_artifact_contracts",
    "validate_pm_verification_commands",
]

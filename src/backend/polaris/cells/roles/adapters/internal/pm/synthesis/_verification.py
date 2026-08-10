"""Verification-command attachment helpers for synthesized PM contracts."""

from __future__ import annotations

from typing import Any

from ._language import _explicit_primary_language_from_directive


def _synthesized_task_target_files(item: dict[str, Any]) -> tuple[str, ...]:
    raw_targets = item.get("target_files")
    if not isinstance(raw_targets, list):
        return ()
    return tuple(str(path or "").replace("\\", "/").strip() for path in raw_targets if str(path or "").strip())


def _synthesized_contract_language(contracts: list[dict[str, Any]], directive: str) -> str:
    targets = {path for item in contracts for path in _synthesized_task_target_files(item)}
    lower_targets = {path.lower() for path in targets}
    if "cargo.toml" in lower_targets or any(path.endswith(".rs") for path in lower_targets):
        return "rust"
    if "go.mod" in lower_targets or any(path.endswith(".go") for path in lower_targets):
        return "go"
    if "pom.xml" in lower_targets or any(path.endswith(".java") for path in lower_targets):
        return "java"
    if "cmakelists.txt" in lower_targets or any(path.endswith((".cpp", ".hpp")) for path in lower_targets):
        return "cpp"
    if any(path.endswith((".ts", ".tsx")) for path in lower_targets):
        return "typescript"
    if "package.json" in lower_targets or any(path.endswith((".js", ".jsx")) for path in lower_targets):
        return "javascript"
    if any(path.endswith(".py") for path in lower_targets):
        return "python"
    explicit = _explicit_primary_language_from_directive(directive)
    return explicit if explicit in {"rust", "go", "java", "cpp", "typescript", "javascript", "python"} else ""


def _synthesized_verifier_profile(language: str) -> dict[str, tuple[str, ...]]:
    profiles: dict[str, dict[str, tuple[str, ...]]] = {
        "typescript": {
            "environment_prep": ("npm", "install"),
            "build": ("npm", "run", "build"),
            "test": ("npm", "test"),
            "entrypoint": ("npm", "start"),
        },
        "javascript": {
            "environment_prep": ("npm", "install"),
            "build": ("npm", "run", "build"),
            "test": ("npm", "test"),
            "entrypoint": ("npm", "start"),
        },
        "python": {
            "environment_prep": ("python", "-m", "venv", ".venv"),
            "build": ("python", "-m", "compileall", "-q", "."),
            "test": ("python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"),
            "entrypoint": ("python", "-m", "src.main"),
        },
        "go": {
            "environment_prep": ("go", "mod", "download"),
            "build": ("go", "build", "./..."),
            "test": ("go", "test", "./..."),
            "entrypoint": ("go", "run", "."),
        },
        "rust": {
            "environment_prep": ("cargo", "fetch"),
            "build": ("cargo", "build"),
            "test": ("cargo", "test"),
            "entrypoint": ("cargo", "run"),
        },
        "cpp": {
            "environment_prep": ("cmake", "-S", ".", "-B", "build"),
            "build": ("cmake", "--build", "build"),
            "test": ("python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"),
            "entrypoint": ("./build/polaris_app",),
        },
        "java": {
            "environment_prep": ("mvn", "-q", "-DskipTests", "dependency:go-offline"),
            "build": ("mvn", "-q", "-DskipTests", "package"),
            "test": ("python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"),
            "entrypoint": ("java", "-cp", "target/classes", "polaris.factory.Main"),
        },
    }
    return profiles.get(language, {})


def _attach_synthesized_verification_commands(
    contracts: list[dict[str, Any]],
    *,
    directive: str,
) -> list[dict[str, Any]]:
    """Bind deterministic fallback commands to the task that owns their artifacts.

    This is used only by PM's deterministic fallback templates.  Physical
    execution policy still validates every argv later; this helper merely
    prevents free-text acceptance criteria from becoming command authority.
    """

    language = _synthesized_contract_language(contracts, directive)
    profile = _synthesized_verifier_profile(language)
    if not profile:
        return [dict(item) for item in contracts]

    manifest_names = {
        "typescript": {"package.json"},
        "javascript": {"package.json"},
        "python": {"requirements.txt", "pyproject.toml"},
        "go": {"go.mod"},
        "rust": {"cargo.toml"},
        "cpp": {"cmakelists.txt"},
        "java": {"pom.xml"},
    }.get(language, set())
    entrypoint_paths = {
        "typescript": {"src/index.ts", "src/main.ts", "src/main.tsx", "index.html"},
        "javascript": {"src/index.js", "src/main.js", "index.html"},
        "python": {"src/main.py", "main.py", "app.py"},
        "go": {"main.go"},
        "rust": {"src/main.rs"},
        "cpp": {"src/main.cpp"},
        "java": {"src/main/java/polaris/factory/main.java"},
    }.get(language, set())

    contract_target_sets = tuple({path.lower() for path in _synthesized_task_target_files(item)} for item in contracts)
    contract_set_owns_manifest = any(targets.intersection(manifest_names) for targets in contract_target_sets)

    finalized: list[dict[str, Any]] = []
    for index, raw_item in enumerate(contracts):
        item = dict(raw_item)
        if "verification_commands" in item:
            finalized.append(item)
            continue
        targets = contract_target_sets[index]
        owns_manifest = bool(targets.intersection(manifest_names))
        owns_test = any(
            path.startswith("tests/")
            or "/test/" in path
            or "/tests/" in path
            or path.endswith(("_test.go", ".test.ts", ".spec.ts", ".test.js", ".spec.js", "test_product.py"))
            for path in targets
        )
        owns_entrypoint = bool(targets.intersection(entrypoint_paths))
        owns_build_input = any(not path.endswith((".md", ".txt")) and not path.startswith("tests/") for path in targets)
        commands: list[dict[str, Any]] = []
        if owns_manifest or (index == 0 and not contract_set_owns_manifest):
            commands.append({"modality": "environment_prep", "argv": list(profile["environment_prep"]), "cwd": "."})
        if owns_build_input:
            commands.append({"modality": "build", "argv": list(profile["build"]), "cwd": "."})
        if owns_test:
            commands.append({"modality": "test", "argv": list(profile["test"]), "cwd": "."})
        if owns_entrypoint:
            commands.append({"modality": "entrypoint", "argv": list(profile["entrypoint"]), "cwd": "."})
        item["verification_commands"] = commands
        finalized.append(item)
    return finalized

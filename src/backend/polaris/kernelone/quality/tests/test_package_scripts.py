"""Tests for platform package.json script quality checks."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality import artifact_quality_issues_from_errors, check_package_scripts


def test_check_package_scripts_rejects_placeholder_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"echo \\"No tests yet\\" && exit 0"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert "placeholder" in result.detail
    assert result.issues
    issue = result.issues[0]
    assert issue.code == "npm_placeholder_script"
    assert issue.script_name == "test"
    assert issue.script_issue == "placeholder_command"
    assert issue.command == 'echo "No tests yet" && exit 0'
    assert issue.to_dict()["metadata"]["script_issue"] == "placeholder_command"
    artifact_issues = artifact_quality_issues_from_errors(result.issue_dicts())
    assert artifact_issues[0]["code"] == "npm_placeholder_script"
    assert artifact_issues[0]["metadata"]["script_name"] == "test"


def test_check_package_scripts_rejects_missing_local_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start":"node ./missing.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert "missing local entrypoint" in result.detail
    assert result.issues
    issue = result.issues[0]
    assert issue.code == "npm_script_missing_local_entrypoint"
    assert issue.script_name == "start"
    assert issue.script_issue == "missing_local_entrypoint"
    assert issue.entrypoint == "./missing.js"


def test_check_package_scripts_accepts_node_test_glob_patterns(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node --test tests/*.test.js","test:watch":"node --test --watch tests/*.test.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is True


def test_check_package_scripts_accepts_existing_node_test_directory(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "product.test.js").write_text(
        'import test from "node:test";\ntest("works", () => {});\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node --test tests","test:unit":"node --test ./tests"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is True


def test_check_package_scripts_rejects_missing_local_node_dependency(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "build.js").write_text(
        "const { validateManifest } = require(\"../src/validate\");\nvalidateManifest({ name: 'demo' });\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"node scripts/build.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert "requires missing local module: ../src/validate" in result.detail
    assert result.issues
    issue = result.issues[0]
    assert issue.code == "npm_script_missing_local_module"
    assert issue.script_name == "build"
    assert issue.script_issue == "missing_local_module"
    assert issue.entrypoint == "scripts/build.js"
    assert issue.to_dict()["metadata"]["module_ref"] == "../src/validate"


def test_check_package_scripts_accepts_existing_local_node_dependency(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts" / "build.js").write_text(
        "const { validateManifest } = require(\"../src/validate\");\nvalidateManifest({ name: 'demo' });\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "validate.js").write_text(
        "exports.validateManifest = function validateManifest() { return { ok: true, errors: [] }; };\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"node scripts/build.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is True


def test_check_package_scripts_accepts_typescript_entrypoint_local_ts_import(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        "import { run } from './index';\nrun();\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "index.ts").write_text(
        "export function run() { return true; }\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start:src":"node --import tsx ./src/main.ts"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is True


def test_check_package_scripts_accepts_nodenext_js_specifier_resolving_to_typescript(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        "import { run } from './index.js';\nrun();\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "index.ts").write_text(
        "export function run() { return true; }\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start:src":"node --import tsx ./src/main.ts"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is True


def test_check_package_scripts_plain_js_entrypoint_does_not_accept_ts_only_import(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.js").write_text(
        "import { run } from './index';\nrun();\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "index.ts").write_text(
        "export function run() { return true; }\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start":"node ./src/main.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert "requires missing local module: ./index" in result.detail


def test_check_package_scripts_accepts_build_script_before_dist_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"tsc","start":"npm run build && node dist/index.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is True


def test_check_package_scripts_accepts_start_build_output_from_separate_build_script(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"tsc -p tsconfig.json","start":"node dist/main.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is True


def test_check_package_scripts_rejects_missing_bun_and_deno_entrypoints(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"bun:start":"bun run src/main.ts","deno:start":"deno run --allow-read src/main.ts"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert [issue.entrypoint for issue in result.issues] == ["src/main.ts", "src/main.ts"]


def test_check_package_scripts_rejects_direct_recursive_npm_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"npm run build","test":"npm run build"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert "recursively invokes itself" in result.detail
    assert "build -> build" in result.detail
    assert result.issues
    issue = result.issues[0]
    assert issue.code == "npm_script_cycle"
    assert issue.script_name == "build"
    assert issue.script_issue == "recursive_invocation"
    assert issue.to_dict()["metadata"]["cycle"] == ["build", "build"]


def test_check_package_scripts_rejects_transitive_recursive_npm_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"npm run verify","verify":"npm run build"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert "recursively invokes itself" in result.detail
    assert "build -> verify -> build" in result.detail

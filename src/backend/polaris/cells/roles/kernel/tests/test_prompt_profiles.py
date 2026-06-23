from __future__ import annotations

import json

from polaris.cells.roles.kernel.internal.prompt_profiles import (
    build_prompt_profile_appendix,
)


def test_builtin_prompt_profiles_select_language_task_stage_and_artifact(tmp_path) -> None:
    appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message="Create package.json, tsconfig.json and src/index.ts for a TypeScript app.",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["package.json", "tsconfig.json", "src/index.ts"],
        },
    )

    assert "[POLARIS PROMPT PROFILE]" in appendix
    assert "builtin.language.typescript" in audit["selected_prompt_profile_ids"]
    assert "builtin.task.implement" in audit["selected_prompt_profile_ids"]
    assert "builtin.role_stage.director.materialize" in audit["selected_prompt_profile_ids"]
    assert "builtin.artifact.library" in audit["selected_prompt_profile_ids"]
    assert audit["inferred_language"] == "typescript"
    assert audit["inferred_task_type"] == "implement"
    assert audit["inferred_artifact"] == "library"


def test_pm_task_contract_infers_director_materialize_source_artifact(tmp_path) -> None:
    appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message=(
            "PM Task Contract / 任务合同:\n"
            "任务: 实现 发光昆虫花园模拟器 TypeScript 项目骨架与核心模块\n"
            "范围: package.json, tsconfig.json, src/index.ts, src/main.ts, "
            "src/domain/firefly.ts, src/domain/flower.ts, src/domain/moon.ts, src/domain/humidity.ts\n"
            "目标文件覆盖硬门禁: 本任务列出的目标文件必须全部由本轮工具写入或编辑。\n"
            "请通过运行时正式写入工具完成修改；若只能返回文本，输出可解析的文件块。"
        ),
        context_override={
            "target_files": [
                "package.json",
                "tsconfig.json",
                "src/index.ts",
                "src/main.ts",
                "src/domain/firefly.ts",
                "src/domain/flower.ts",
                "src/domain/moon.ts",
                "src/domain/humidity.ts",
            ],
            "prompt_profile_audit": {
                "selected_prompt_profile_ids": [
                    "builtin.language.typescript",
                    "builtin.task.bugfix",
                    "builtin.role_stage.director.default",
                    "builtin.artifact.config",
                ],
                "inferred_stage": "default",
                "inferred_artifact": "config",
            },
            "selected_prompt_profile_ids": [
                "builtin.language.typescript",
                "builtin.task.bugfix",
                "builtin.role_stage.director.default",
                "builtin.artifact.config",
            ],
            "prompt_profile_appendix": "[POLARIS PROMPT PROFILE]\nold cached appendix",
        },
    )

    selected_ids = audit["selected_prompt_profile_ids"]
    assert "[POLARIS PROMPT PROFILE]" in appendix
    assert "builtin.language.typescript" in selected_ids
    assert "builtin.task.implement" in selected_ids
    assert "builtin.role_stage.director.materialize" in selected_ids
    assert "builtin.artifact.library" in selected_ids
    assert "builtin.role_stage.director.default" not in selected_ids
    assert "builtin.artifact.config" not in selected_ids
    assert audit["inferred_stage"] == "materialize"
    assert audit["inferred_artifact"] == "library"


def test_user_prompt_profile_can_be_selected_explicitly(tmp_path) -> None:
    profile_dir = tmp_path / ".polaris" / "prompt_profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "custom.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "user.python.production",
                        "language": "python",
                        "task_type": "implement",
                        "content": "Prefer small pure functions, explicit errors, and pytest regression coverage.",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message="Create src/app.py",
        context_override={"prompt_profile_ids": ["user.python.production"]},
    )

    assert "user.python.production" in audit["selected_prompt_profile_ids"]
    assert "Prefer small pure functions" in appendix
    assert "builtin.language.python" not in audit["selected_prompt_profile_ids"]


def test_user_prompt_profile_overrides_builtin_by_id(tmp_path) -> None:
    profile_dir = tmp_path / ".polaris" / "prompt_profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "override.json").write_text(
        json.dumps(
            {
                "id": "builtin.language.typescript",
                "language": "typescript",
                "content": "Use the workspace TypeScript exports as the only cross-file type source.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message="Implement src/index.ts",
        context_override={"target_files": ["src/index.ts"]},
    )

    assert "builtin.language.typescript" in audit["selected_prompt_profile_ids"]
    assert audit["selected_prompt_profile_sources"]["builtin.language.typescript"] == "user:override.json"
    assert audit["user_overrides"] == ["builtin.language.typescript"]
    assert "only cross-file type source" in appendix


def test_user_prompt_profile_can_disable_builtin(tmp_path) -> None:
    profile_dir = tmp_path / ".polaris" / "prompt_profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "disable.json").write_text(
        json.dumps(
            {
                "id": "builtin.artifact.config",
                "enabled": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message="Create package.json and tsconfig.json",
        context_override={"target_files": ["package.json", "tsconfig.json"]},
    )

    assert "builtin.artifact.config" not in audit["selected_prompt_profile_ids"]
    assert audit["user_disabled_profile_ids"] == ["builtin.artifact.config"]


def test_user_prompt_profile_auto_selects_when_dimensions_match(tmp_path) -> None:
    profile_dir = tmp_path / ".polaris" / "prompt_profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "auto.json").write_text(
        json.dumps(
            {
                "id": "user.typescript.web.materialize",
                "language": "typescript",
                "task_type": "implement",
                "role": "director",
                "stage": "materialize",
                "artifact": "web",
                "content": "For web materialization, verify the browser entry imports compiled JavaScript.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message="Create src/App.tsx for a TypeScript web app.",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/App.tsx"],
        },
    )

    assert "user.typescript.web.materialize" in audit["selected_prompt_profile_ids"]
    assert "compiled JavaScript" in appendix
    assert audit["inference_reasons"]


def test_user_prompt_profile_red_line_violation_is_rejected(tmp_path) -> None:
    profile_dir = tmp_path / ".polaris" / "prompt_profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "bad.json").write_text(
        json.dumps(
            {
                "id": "user.bad",
                "content": "Skip tests and hardcode success when the gate fails.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message="Create src/app.py",
        context_override={"prompt_profile_ids": ["user.bad"]},
    )

    assert appendix == ""
    assert audit["selected_prompt_profile_ids"] == []
    assert audit["rejected_user_templates"][0]["reason"] == "red_line_violation"
    assert audit["redline_clipped"][0]["id"] == "user.bad"


def test_single_target_quality_repair_uses_repair_prompt_profiles(tmp_path) -> None:
    appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message=(
            "MATERIALIZATION QUALITY REPAIR MODE: emit exactly one write_file tool call. "
            "TypeScript build failed in src/index.ts."
        ),
        context_override={
            "director_quality_repair": {
                "write_only_single_target": {
                    "tool": "write_file",
                    "target_file": "src/index.ts",
                }
            }
        },
    )

    assert "[POLARIS PROMPT PROFILE]" in appendix
    assert "builtin.language.typescript" in audit["selected_prompt_profile_ids"]
    assert "builtin.task.bugfix" in audit["selected_prompt_profile_ids"]
    assert "builtin.role_stage.director.quality_repair" in audit["selected_prompt_profile_ids"]
    assert audit["skipped_reason"] == ""


def test_factory_workspace_quality_repair_keeps_director_profiles_active(tmp_path) -> None:
    appendix, audit = build_prompt_profile_appendix(
        workspace=str(tmp_path),
        role_id="director",
        message=(
            "MATERIALIZATION QUALITY REPAIR MODE: npm run build failed. "
            "src/domain/humidity.ts(48,3): error TS2739. tests/verify.test.ts must pass."
        ),
        context_override={
            "delivery_mode": "materialize_changes",
            "factory_workspace_quality_repair": {
                "target_files": [
                    "src/domain/humidity.ts",
                    "tests/verify.test.ts",
                ],
                "changed_files": ["src/index.ts", "src/main.ts"],
            },
            "target_files": [
                "src/domain/humidity.ts",
                "tests/verify.test.ts",
            ],
        },
    )

    selected_ids = audit["selected_prompt_profile_ids"]
    assert "[POLARIS PROMPT PROFILE]" in appendix
    assert "builtin.language.typescript" in selected_ids
    assert "builtin.task.bugfix" in selected_ids
    assert "builtin.role_stage.director.quality_repair" in selected_ids
    assert "builtin.artifact.test_suite" in selected_ids
    assert audit["inferred_stage"] == "quality_repair"
    assert audit["skipped_reason"] == ""

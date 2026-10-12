"""Semantic PM tasks survive bounded execution batching without contract fission."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.public.artifact_contracts import (
    validate_pm_artifact_contracts,
    validate_pm_verification_commands,
)
from polaris.cells.orchestration.pm_planning.public.service import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

# Exact native provider response from R22; independent of a developer's audit tree.
_R22_PROVIDER_RESPONSE = (
    '{"tasks":[{"id":"TASK-1","title":"Chief Engineer 蓝图：发光昆虫花园模拟'
    '器模块拓扑与契约","goal":"冻结 TypeScript 项目「发光昆虫花园模拟器」的目录拓扑、模块边界、公开接口'
    "与门控命令。PM 元数据：主语言=typescript；领域=science_creative；项目类型=simulat"
    "ion_toy；创意钩子=萤火虫根据花朵情绪和月相组成实时灯光舞蹈。蓝图必须命名：花田花朵情绪状态机、蜂群萤火虫调度器、"
    "月相相位计算器、湿度影响因子、可执行入口(index.html + TS bundle)、核心规则验证脚本(test/s"
    "moke)以及 README。模块名/类名/函数名必须包含关键词 firefly、flower、moon、humidit"
    'y 中至少四项；并明确 ≥3 条行为规则（萤火虫亮度随花朵情绪加权、随月相缩放、受湿度滞后影响）。","scope_pa'
    'ths":["src","tests","README.md"],"target_files":["BLUEPRINT.'
    'md"],"steps":["声明目录：src/domain/{flowers,fireflies,moon,humid'
    "ity}.ts、src/simulation/{garden,danceLoop}.ts、src/entry/{cli,"
    "web}.ts、src/types.ts、src/index.ts、tests/garden.test.ts、index"
    '.html、README.md、package.json、tsconfig.json。","列出公开契约：MoonPha'
    "seCalculator.computePhase(lunarDay):{phase,glowFactor,beatsP"
    "erMinute}；Flower情绪向量{jovial,tense,withered,fearful} -> Bloom"
    "Mood；FireflySwarm调度器接收 BloomMood 与 MoonPhase 输出亮度/脉冲阵型；Humid"
    'itySensor.modulate(brightness, humidity) 引入滞后。","定义3+行为规则：1)'
    " FireflyPulse = baseGlow * flowerMood.intensity * moonPhase."
    "glowFactor；2) 当 humidity<0.2 时脉冲节拍被强制进入低活跃分支；3) 月相=新月时萤火虫必须切"
    "换至 chaos 阵型（保证 branch_count≥3）；4) 异常 humidity=-1 触发 invalid "
    '输入分支（保证 edge_case≥2）。","点名产物门控：tsc --noEmit、npm run build、np'
    "m run test、npm run start(via index.html + http-server) 与 npm"
    " run smoke(打印一次 dance summary，断言 firefly/flower/moon/humidit"
    "y 关键词全部出现)；minimum 3 prod files / 120 prod lines / 6 behavio"
    'r symbols / 2 test assertions。","生成 BLUEPRINT.md 写明模块路径、公开方法'
    "、门控命令与样本数据 seed/garden.json，并在文件中显式列出 firefly/flower/moon/hu"
    'midity 四个关键词。"],"acceptance":["BLUEPRINT.md 含确切模块文件路径与最小公开 A'
    'PI 签名，关键词 firefly/flower/moon/humidity 全部出现。","蓝图包含至少 3 条 be'
    "havior_rules、6 个 behavior_symbols、3 个 branch、2 个 edge_case 条"
    '目。","蓝图指明 ts_syntax / package_scripts / min_files:3 / conten'
    "t_any:firefly|flower|moon|humidity / source_target_coverage:"
    'src/**/*.ts 的具体承接任务。","蓝图显式声明 entrypoint 为 index.html（web）+ '
    'npm run cli（Node 入口）。"],"verification_commands":[{"modality"'
    ':"lint","argv":["npx","--yes","markdownlint","BLUEPRINT.md"]'
    ',"cwd":"."}],"phase":"planning","depends_on":[],"assigned_to'
    '":"ChiefEngineer","execution_backend":"code_edit"},{"id":"TA'
    'SK-2","title":"Director 实现：领域规则 + 入口 + 测试的真实可运行产物","goal":"按'
    " BLUEPRINT 落地 TypeScript 实现：核心行为（flower 情绪、moon 相位、humidity "
    "因子、firefly dance）与可执行入口分离；产出 package.json（含 start/build/test"
    "/smoke）、tsconfig.json、index.html、README.md 及至少 3 个 prod .ts "
    "文件、≥1 个测试文件，业务源码合计 ≥120 行；源码中实际使用 firefly/flower/moon/humidi"
    'ty 四个关键词（不只是注释）。","scope_paths":["src/domain","src/simulatio'
    'n","src/entry","src/types.ts","src/index.ts","tests","index.'
    'html","package.json","tsconfig.json","README.md"],"target_fi'
    'les":["package.json","tsconfig.json","src/types.ts","src/dom'
    'ain/flowers.ts","src/domain/fireflies.ts","src/domain/moon.t'
    's","src/domain/humidity.ts","src/simulation/garden.ts","src/'
    'simulation/danceLoop.ts","src/entry/cli.ts","src/entry/web.t'
    's","src/index.ts","index.html","README.md","seed/garden.json'
    '","tests/garden.test.ts"],"steps":["package.json 设置 scripts:'
    " start=http-server -p 8080 . ; build=tsc ; test=tsc --noEmit"
    " && node --test tests/garden.test.ts ; smoke=node dist/entry"
    '/cli.js ; dev=tsc --noEmit ; typecheck=tsc --noEmit 。","tsco'
    "nfig.json strict:true、target ES2022、module Node16、rootDir sr"
    'c/outDir dist、include [\\"src/**/*\\",\\"tests/**/*\\"]。","src/t'
    "ypes.ts 导出 BloomMood/ MoonPhase/ FireflyPulse/ DanceStep 四类显"
    '式类型，禁止 any。","src/domain/flowers.ts 实现 BloomField.tick() 输出 '
    'BloomMood；含情绪 intensity∈[0,1]。","src/domain/moon.ts MoonPhas'
    "eCalculator.computePhase(lunarDay∈[1,30]) 返回 {phase, glowFac"
    'tor, beatsPerMinute}，月=15、新月=30 时强制 chaos 分支。","src/domain/h'
    "umidity.ts HumiditySensor.modulate(brightness, humidity) 引入滞"
    '后；humidity∈[-1,1]；负值触发 invalid 分支。","src/domain/fireflies.ts'
    " 实现 FireflySwarm.composeDance(fieldMood, phase, humidity) 返回"
    " FireflyPulse[]，公式 baseGlow * fieldMood.intensity * phase.gl"
    'owFactor；humidity<0.2 强制低活跃分支。","src/simulation/garden.ts + '
    "danceLoop.ts 编排 60 步 loop，输出 dance summary 包含 firefly/flower"
    '/moon/humidity 标签。","src/entry/cli.ts Node CLI：读 seed/garden'
    '.json 跑 30 步、打印 summary、各关键词必须出现、退出码 0。","src/entry/web.ts 暴'
    '露给 index.html 的预演脚本，index.html 含 <html><canvas id=\\"garden\\"'
    '></canvas></html> 与模块脚本，确保浏览器入口存在。","tests/garden.test.ts 使用'
    " node:test，至少 2 条断言：正常输入 firefly 脉冲在 0..1 之间 + 月相=新月触发 chaos"
    ' 分支；并覆盖 boundary humidity=0 / invalid humidity=-1 三类输入。","RE'
    "ADME.md 说明 npm install / build / start / test / smoke 各命令及入口"
    ' URL。"],"acceptance":["tsc --noEmit 成功通过（ts_syntax）。","npm r'
    'un build 成功生成 dist/；npm run test 通过且用例≥2。","产出物 src/**/*.ts '
    '文件 ≥3，prod_lines ≥120；source_target_coverage 命中。","firefly/f'
    "lower/moon/humidity 四个关键词在源码（变量名/类名/字符串）实际出现，不仅是 README 或注释。"
    '","index.html 含 <html> 标记且引用 src/entry/web 编译产物，npm run star'
    't 后可发现入口。","README.md 含运行命令说明。"],"verification_commands":[{"'
    'modality":"environment_prep","argv":["npm","install"],"cwd":'
    '"."},{"modality":"build","argv":["npm","run","build"],"cwd":'
    '"."},{"modality":"test","argv":["npm","run","test"],"cwd":".'
    '"},{"modality":"lint","argv":["npx","--yes","tsc","--noEmit"'
    '],"cwd":"."},{"modality":"entrypoint","argv":["node","dist/e'
    'ntry/cli.js"],"cwd":"."}],"phase":"implementation","depends_'
    'on":["TASK-1"],"assigned_to":"Director","execution_backend":'
    '"code_edit","generated_outputs":[{"path":"dist/domain/flower'
    's.js","inputs":[{"task_id":"TASK-2","path":"src/domain/flowe'
    'rs.ts"}],"build":{"argv":["npm","run","build"],"cwd":"."}},{'
    '"path":"dist/domain/fireflies.js","inputs":[{"task_id":"TASK'
    '-2","path":"src/domain/fireflies.ts"}],"build":{"argv":["npm'
    '","run","build"],"cwd":"."}},{"path":"dist/domain/moon.js","'
    'inputs":[{"task_id":"TASK-2","path":"src/domain/moon.ts"}],"'
    'build":{"argv":["npm","run","build"],"cwd":"."}},{"path":"di'
    'st/domain/humidity.js","inputs":[{"task_id":"TASK-2","path":'
    '"src/domain/humidity.ts"}],"build":{"argv":["npm","run","bui'
    'ld"],"cwd":"."}},{"path":"dist/simulation/garden.js","inputs'
    '":[{"task_id":"TASK-2","path":"src/simulation/garden.ts"}],"'
    'build":{"argv":["npm","run","build"],"cwd":"."}},{"path":"di'
    'st/simulation/danceLoop.js","inputs":[{"task_id":"TASK-2","p'
    'ath":"src/simulation/danceLoop.ts"}],"build":{"argv":["npm",'
    '"run","build"],"cwd":"."}},{"path":"dist/entry/cli.js","inpu'
    'ts":[{"task_id":"TASK-2","path":"src/entry/cli.ts"}],"build"'
    ':{"argv":["npm","run","build"],"cwd":"."}},{"path":"dist/ent'
    'ry/web.js","inputs":[{"task_id":"TASK-2","path":"src/entry/w'
    'eb.ts"}],"build":{"argv":["npm","run","build"],"cwd":"."}},{'
    '"path":"dist/index.js","inputs":[{"task_id":"TASK-2","path":'
    '"src/index.ts"}],"build":{"argv":["npm","run","build"],"cwd"'
    ':"."}}]},{"id":"TASK-3","title":"QA 闭环：确定性检查 + 行为审计 + 入口冒烟",'
    '"goal":"独立 QA 重新执行所有门控并产出 audit_report.md，覆盖 ts_syntax、packa'
    "ge_scripts、min_files:3、content_any:firefly|flower|moon|humid"
    "ity、source_target_coverage:src/**/*.ts，以及 Bench Level 1 的最小阈"
    '值；通过 entrypoint 冒烟确认萤火虫 flower/moon/humidity 实时灯光舞蹈真正运行。","s'
    'cope_paths":["src","tests","dist","index.html","seed","READM'
    'E.md","BLUEPRINT.md","audit_report.md"],"target_files":["aud'
    'it_report.md","tests/qa.smoke.ts"],"steps":["运行 tsc --noEmit'
    ' 记录 ts_syntax 通过证据。","运行 npm run build 与 npm run test，统计 pro'
    "d_files ≥3、prod_lines ≥120、test_assertions ≥2、test_files ≥1。"
    '","grep 源码确认 firefly/flower/moon/humidity 关键字真实使用（非仅 README/'
    '注释）。","运行 node dist/entry/cli.js 抓取 stdout，确认摘要中出现 firefly/f'
    'lower/moon/humidity 全部出现一次以上。","新增 tests/qa.smoke.ts 调用 comp'
    "oseDance 对 normal/boundary/invalid 三类输入分别断言，并 node --test 单独"
    '再跑一次。","若任一项失败则在 audit_report.md 列出 failure 与修复建议；通过则签字 impl'
    'ementation_depth 达标。"],"acceptance":["audit_report.md 列出 ts_'
    "syntax/package_scripts/min_files/content_any/source_target_c"
    'overage 全部 PASS。","Bench 阈值 prod_files/prod_lines/test_asser'
    'tions/test_files 全部满足。","entrypoint 冒烟日志中 firefly/flower/moo'
    'n/humidity 均出现。","tests/qa.smoke.ts 覆盖 normal/boundary/inval'
    'id 三分支并全部通过。"],"verification_commands":[{"modality":"test","'
    'argv":["node","--test","tests/qa.smoke.ts"],"cwd":"."},{"mod'
    'ality":"entrypoint","argv":["node","dist/entry/cli.js"],"cwd'
    '":"."},{"modality":"lint","argv":["npx","--yes","tsc","--noE'
    'mit"],"cwd":"."}],"phase":"verification","depends_on":["TASK'
    '-2"],"assigned_to":"QA","execution_backend":"code_edit"}]}'
)
_R22_PROVIDER_SHA256 = "1d0e9ba41dfb7113d0e80c814ec78941cf4a2112d5d85df1b423c22842c0ad96"


def _r22_tasks() -> list[dict[str, Any]]:
    assert hashlib.sha256(_R22_PROVIDER_RESPONSE.encode("utf-8")).hexdigest() == _R22_PROVIDER_SHA256
    parsed: dict[str, list[dict[str, Any]]] = json.loads(_R22_PROVIDER_RESPONSE)
    return parsed["tasks"]


def _semantic_rows(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Compare authority values, allowing existing empty/default and trailing-slash normalization.
    rows: list[dict[str, Any]] = []
    for task in tasks:
        rows.append(
            {
                "id": task["id"],
                "title": task["title"],
                "goal": task["goal"],
                "target_files": deepcopy(task.get("target_files", [])),
                "scope_paths": [path.rstrip("/") for path in task.get("scope_paths", [])],
                "context_files": deepcopy(task.get("context_files", [])),
                "acceptance": deepcopy(task.get("acceptance_criteria", task.get("acceptance", []))),
                "verification_commands": deepcopy(task.get("verification_commands", [])),
                "generated_outputs": deepcopy(task.get("generated_outputs", [])),
                "depends_on": deepcopy(task.get("depends_on", [])),
            }
        )
    return rows


def _generic_tasks() -> list[dict[str, Any]]:
    targets = [
        "package.json",
        "tsconfig.json",
        "src/types.ts",
        *[f"src/features/module_{number}.ts" for number in range(9)],
        "src/entry/cli.ts",
        "src/index.ts",
        "tests/behavior.test.ts",
        "README.md",
    ]
    return [
        {
            "id": "IMPLEMENT",
            "title": "Implement coupled typed feature and CLI",
            "goal": "Implement all feature modules, executable entrypoint and behavior tests as one product unit.",
            "description": "Materialize the complete typed feature under one implementation contract.",
            "assigned_to": "Director",
            "phase": "implementation",
            "depends_on": [],
            "scope_paths": targets,
            "target_files": targets,
            "steps": ["Implement canonical feature owners", "Wire the CLI", "Run build and behavior tests"],
            "execution_checklist": ["Read dependencies", "Materialize all targets", "Run every committed verifier"],
            "acceptance_criteria": [
                "npm run build passes and emits dist/entry/cli.js.",
                "npm run test passes with at least 2 behavior assertions.",
                "node dist/entry/cli.js exits successfully and README.md documents observed commands.",
            ],
            "verification_commands": [
                {"modality": "environment_prep", "argv": ["npm", "install"], "cwd": "."},
                {"modality": "build", "argv": ["npm", "run", "build"], "cwd": "."},
                {"modality": "test", "argv": ["npm", "run", "test"], "cwd": "."},
                {"modality": "lint", "argv": ["npx", "tsc", "--noEmit"], "cwd": "."},
                {"modality": "entrypoint", "argv": ["node", "dist/entry/cli.js"], "cwd": "."},
            ],
            "generated_outputs": [
                {
                    "path": "dist/entry/cli.js",
                    "inputs": [{"task_id": "IMPLEMENT", "path": "src/entry/cli.ts"}],
                    "build": {"argv": ["npm", "run", "build"], "cwd": "."},
                }
            ],
            "metadata": {"provider_response_sha256": "ab" * 32, "coupling_note": "shared feature contracts"},
        },
        {
            "id": "VERIFY",
            "title": "Verify the complete typed CLI feature",
            "goal": "Verify real implementation behavior and retain the audit report.",
            "description": "Run the independent smoke verifier after implementation and retain evidence.",
            "assigned_to": "QA",
            "phase": "verification",
            "depends_on": ["IMPLEMENT"],
            "target_files": ["audit_report.md", "tests/smoke.mjs"],
            "scope_paths": ["audit_report.md", "tests/smoke.mjs"],
            "context_files": ["dist/entry/cli.js"],
            "steps": ["Read the built CLI", "Run smoke tests", "Write evidence"],
            "execution_checklist": ["Check receipts", "Run smoke tests", "Retain evidence"],
            "acceptance_criteria": ["node --test tests/smoke.mjs passes with at least 2 assertions."],
            "verification_commands": [{"modality": "test", "argv": ["node", "--test", "tests/smoke.mjs"], "cwd": "."}],
        },
    ]


def test_native_r22_provider_tasks_keep_semantic_authority_after_autofix(tmp_path: Path) -> None:
    tasks = _r22_tasks()
    before = _semantic_rows(tasks)
    payload: dict[str, Any] = {"tasks": tasks, "language": "typescript"}
    stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

    assert [task["id"] for task in payload["tasks"]] == ["TASK-1", "TASK-2", "TASK-3"]
    after = _semantic_rows(payload["tasks"])
    assert after[:2] == before[:2]
    for field in ("id", "title", "goal", "target_files", "verification_commands", "depends_on"):
        assert after[2][field] == before[2][field]
    # Existing quality enrichment appends an artifact anchor; it must retain all authored criteria.
    assert after[2]["acceptance"][: len(before[2]["acceptance"])] == before[2]["acceptance"]
    assert stats["oversized_director_tasks_split"] == 0
    assert stats["task_boundary_tasks_added"] == 0
    assert "_quality_gate_task_fission" not in payload
    assert hashlib.sha256(_R22_PROVIDER_RESPONSE.encode("utf-8")).hexdigest() == _R22_PROVIDER_SHA256
    report = evaluate_pm_task_quality(payload, docs_stage={})
    assert report["critical_issues"] == [], report
    assert report["score"] == 100, report
    for task in payload["tasks"]:
        commands, diagnostics = validate_pm_verification_commands(task["verification_commands"])
        assert diagnostics == []
        assert list(commands) == task["verification_commands"]


def test_file_count_alone_does_not_block_a_valid_semantic_contract(tmp_path: Path) -> None:
    payload: dict[str, Any] = {"tasks": _generic_tasks(), "language": "typescript"}
    assert validate_pm_artifact_contracts(payload["tasks"], primary_language="typescript").ok

    report = evaluate_pm_task_quality(payload, docs_stage={}, workspace_full=str(tmp_path))

    assert report["critical_issues"] == [], report
    assert report["score"] == 100, report


def test_generic_coupled_task_keeps_generated_outputs_and_all_verifiers(tmp_path: Path) -> None:
    tasks = _generic_tasks()
    before = _semantic_rows(tasks)
    payload: dict[str, Any] = {"tasks": tasks, "language": "typescript"}
    assert len(tasks[0]["target_files"]) > 6
    assert validate_pm_artifact_contracts(tasks, primary_language="typescript").ok

    autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

    assert _semantic_rows(payload["tasks"]) == before
    assert payload["tasks"][0]["metadata"]["provider_response_sha256"] == "ab" * 32
    assert validate_pm_artifact_contracts(payload["tasks"], primary_language="typescript").ok
    report = evaluate_pm_task_quality(payload, docs_stage={})
    assert report["critical_issues"] == [], report
    assert report["score"] == 100, report


def test_native_r22_adapter_quality_preserves_parent_and_qa_dependency(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    tasks = [
        adapter._normalize_task_contract(task, index, "Create a TypeScript CLI")
        for index, task in enumerate(_r22_tasks(), start=1)
    ]
    before = _semantic_rows(tasks)

    normalized, quality = adapter._evaluate_contract_quality(tasks, directive="Create a TypeScript CLI")

    before[2]["acceptance"].append("verify ./audit_report.md exists")
    assert _semantic_rows(normalized) == before
    assert quality["critical_issues"] == [], quality
    assert quality["score"] == 100, quality
    assert normalized[2]["depends_on"] == ["TASK-2"]
    assert len(normalized[1]["acceptance_criteria"]) == 6
    assert len(normalized[1]["verification_commands"]) == 5
    assert validate_pm_artifact_contracts(normalized, primary_language="typescript").ok


def test_independently_authored_tasks_keep_their_own_goals_and_dependency(tmp_path: Path) -> None:
    tasks = _generic_tasks()
    tasks[0]["target_files"] = ["src/entry/cli.ts"]
    tasks[0]["scope_paths"] = ["src/entry/cli.ts"]
    tasks[0]["goal"] = "Implement the executable CLI and its owned feature behavior."
    before = _semantic_rows(tasks)
    payload: dict[str, Any] = {"tasks": tasks, "language": "typescript"}

    autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

    assert _semantic_rows(payload["tasks"]) == before
    assert evaluate_pm_task_quality(payload, docs_stage={})["critical_issues"] == []
    assert validate_pm_artifact_contracts(payload["tasks"], primary_language="typescript").ok


@pytest.mark.parametrize("defect", ["shell_command", "escaping_output", "dependency_cycle"])
def test_semantic_preservation_keeps_real_contract_errors_blocking(tmp_path: Path, defect: str) -> None:
    tasks = _generic_tasks()
    if defect == "shell_command":
        tasks[0]["verification_commands"][2]["argv"] = ["sh", "-c", "npm test; true"]
    elif defect == "escaping_output":
        tasks[0]["generated_outputs"][0]["path"] = "../escaped.js"
    else:
        tasks[0]["depends_on"] = ["VERIFY"]
    adapter = PMAdapter(workspace=str(tmp_path))

    _, quality = adapter._evaluate_contract_quality(tasks, directive="Create a TypeScript CLI")

    assert quality["critical_issues"], quality
    assert quality["score"] < 100

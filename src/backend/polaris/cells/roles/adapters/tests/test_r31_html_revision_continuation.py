"""Retained program HTML diagnosis continues through the original planning manifest."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.pm.parsing import _PMContractError
from polaris.cells.roles.adapters.internal.pm.revision import (
    PMRevisionBase,
    apply_revision,
    canonical_json,
    make_revision_base,
)
from polaris.cells.roles.adapters.public import PMAdapter
from polaris.infrastructure.llm.providers import anthropic_provider
from polaris.kernelone.llm.contracts.core import Usage
from polaris.kernelone.llm.types import InvokeResult

# Complete historical provider text, matched to its own-call response SHA.
# It is test data; no generated project file or execution grant is changed.
_R30_PRIMARY_OUTPUT = '{"tasks":[{"id":"TASK-1","title":"Chief Engineer 形成项目蓝图（模块、公开契约、门禁命令）","goal":"产出 CE 蓝图文档，确定发光昆虫花园模拟器的目录拓扑、TypeScript 业务模块、入口契约、确定性检查与 Bench Level-1 阈值映射；覆盖关键词 firefly/flower/moon/humidity 与核心行为规则；命名具名模块、公开文件、可执行入口与门禁命令，供 Director 无损执行。","scope_paths":["requirements.md"],"target_files":["docs/blueprint.md"],"steps":["1. 阅读 requirements.md，提炼产品元数据（typescript / science_creative / simulation_toy）与创意钩子：萤火虫根据花朵情绪和月相组成实时灯光舞蹈。","2. 决策目录拓扑：src/domain（Firefly/Flower/MoonPhase/Humidity 实体与规则）、src/sim（GardenSimulator 编排）、src/entry/cli.ts（Node CLI 入口）、tests/domain.rules.test.ts（断言业务规则），CE 选定路径即权威。","3. 声明公开契约：Firefly.glow(flowerEmotion, moonPhase, humidity) -> {hue, intensity, pulseHz}；GardenSimulator.step(seed) 推进 N 步并返回灯光舞蹈轨迹；moonPhase 影响萤火虫 hue/brightness 的非线性映射。","4. 映射 Bench Level-1 阈值：≥3 行为规则（情绪-亮度、月相-hue、湿度-脉冲频率）、≥6 行为符号、≥3 分支、≥2 边界用例（湿度=0/100、新月 vs 满月）、≥3 主实体、≥3 生产文件、≥120 行业务代码、≥2 测试断言、≥1 测试文件。","5. 命名门禁命令：npm install；npm run build（tsc）；npm test；npm start（CLI 入口）。"],"acceptance":["docs/blueprint.md 存在且含具名模块、公开契约、入口路径、门禁命令。","蓝图覆盖确定性检查：ts_syntax、package_scripts、min_files≥3、content_any:firefly|flower|moon|humidity、source_target_coverage:src/**/*.ts。","蓝图显式映射 Bench Level-1 全部最小值与三类用例（normal/boundary/invalid）。","蓝图声明 CE 选定的目录拓扑（src/domain、src/sim、src/entry、tests）与 firefly/flower/moon/humidity 关键词在源代码中的落点。"],"verification_commands":[],"phase":"planning","depends_on":[],"assigned_to":"ChiefEngineer","execution_backend":"doc_only"},{"id":"TASK-2","title":"Director 实现 TypeScript 业务模块与入口（domain + sim + CLI + 可执行测试）","goal":"按 CE 蓝图无损落盘真实 TypeScript 源码：domain 实体 Firefly/Flower/MoonPhase/Humidity 与核心规则、sim 编排器、Node CLI 入口、可执行测试与 package.json/tsconfig.json/README.md；满足 ≥3 文件、≥120 行业务代码、≥6 行为符号、≥3 行为规则、≥3 分支、≥2 边界用例；关键词 firefly/flower/moon/humidity 在源文件中真实使用（非注释/占位堆砌）。","scope_paths":["docs/blueprint.md","requirements.md"],"target_files":["package.json","tsconfig.json","README.md","src/domain/types.ts","src/domain/firefly.ts","src/domain/flower.ts","src/domain/moon.ts","src/sim/garden.ts","src/entry/cli.ts","tests/domain.rules.test.ts"],"steps":["1. 初始化 package.json，定义 scripts.start（node dist/entry/cli.js）、scripts.build（tsc）、scripts.test（node --test --import tsx tests/*.test.ts）；tsconfig.json 开启 strict、target ES2022、outDir dist、根包含 src 与 tests。","2. src/domain/types.ts 定义 MoonPhase（newMoon/quarter/full 等枚举）、FlowerEmotion、Humidity、FlowerKind 与 GlowResult{hue,intensity,pulseHz}；导出主实体 ≥3 个。","3. src/domain/flower.ts 实现 mood(emotion, humidity) -> brightness ∈ [0,1]，覆盖 ≥3 分支（低/中/高湿度）与 ≥2 边界（humidity=0、humidity=100），引入 invalid 输入分支（未知情绪抛 Error）。","4. src/domain/moon.ts 实现 moonInfluence(phase) -> {hueShift, brightnessMul}，覆盖新月/盈月/满月 ≥3 分支；月相与亮度/色相非线性映射。","5. src/domain/firefly.ts 实现 Firefly 类：glow(flowerEmotion, moonPhase, humidity) 组合上述两个规则产出 GlowResult；湿度影响 pulseHz；至少使用 firefly/flower/moon/humidity 四个关键词的实际命名。","6. src/sim/garden.ts 实现 GardenSimulator：seed 初始化花园（≥3 flowers + ≥3 fireflies），step(n) 推进 tick，每 tick 输出 firefly 灯光舞蹈轨迹；分离核心行为与 I/O。","7. src/entry/cli.ts 实现 Node CLI 入口：读取 seed（默认 42），运行 GardenSimulator 打印 N 步灯光轨迹 JSON，进程退出码 0。","8. tests/domain.rules.test.ts 使用 node:test，断言 ≥3 个业务结果：满月下 hueShift 提升、新月下 brightnessMul 降低、湿度=100 时 pulseHz 处于上界；边界用例 humidity=0/newMoon；invalid 用例未知情绪抛错。","9. README.md 说明安装、build、test、start 命令与示例输出片段。"],"acceptance":["npm install 与 npm run build（tsc）成功；tsc --noEmit 通过零错误。","src/domain 与 src/sim 至少 3 个生产 .ts 文件，合计业务代码 ≥120 行（排除注释/空行）。","Firefly.glow / Flower.mood / MoonPhase 至少 3 条行为规则；Firefly/Flower/MoonPhase/Humidity/FlowerKind/GlowResult 至少 6 个行为符号。","GardenSimulator.step 推进并返回可断言的灯光轨迹；firefly/flower/moon/humidity 四个关键词在源文件中以变量/类/函数/字符串真实出现，非仅注释堆砌。","tests/domain.rules.test.ts 存在并通过 node --test，含 ≥2 条断言覆盖 normal/boundary/invalid。","npm start 真实启动 CLI 并输出非占位灯光舞蹈轨迹，退出码 0。"],"verification_commands":[{"modality":"environment_prep","argv":["npm","install"],"cwd":"."},{"modality":"build","argv":["npx","tsc","-p","tsconfig.json"],"cwd":"."},{"modality":"lint","argv":["npx","tsc","--noEmit"],"cwd":"."},{"modality":"test","argv":["node","--import","tsx","--test","tests/domain.rules.test.ts"],"cwd":"."},{"modality":"entrypoint","argv":["node","dist/entry/cli.js","--seed","42","--steps","5"],"cwd":"."}],"phase":"implementation","depends_on":["TASK-1"],"assigned_to":"Director","execution_backend":"code_edit"},{"id":"TASK-3","title":"QA 验收闭环（确定性检查 + Bench Level-1 + 反空壳审计）","goal":"独立运行确定性检查（ts_syntax/package_scripts/min_files/content_any/source_target_coverage）与 Bench Level-1 阈值（行为规则、符号、分支、边界、主实体、生产文件/行、测试断言/文件），验证产物非空壳；出具 QA 报告 docs/qa-report.md，未达标项需回指 TASK-2 修复。","scope_paths":["docs/blueprint.md","src","tests","package.json","tsconfig.json","README.md"],"target_files":["docs/qa-report.md"],"steps":["1. 运行 tsc --noEmit 验证 ts_syntax；解析 package.json 确认 scripts.start/build/test 实际命令（不得为 echo/--version）。","2. 统计 src/** 与 tests/** 下 .ts 文件数 ≥3 且业务行 ≥120；grep 校验 firefly|flower|moon|humidity 至少出现在一个 .ts 源文件中（非仅 README/注释）。","3. 计数行为规则（Flower.mood、Firefly.glow、MoonPhase 影响）≥3；行为符号 ≥6；分支（if/case）≥3；边界用例 ≥2（humidity=0/100、新月 vs 满月）；主实体 ≥3。","4. 运行 node --test 确认测试断言 ≥2 通过 normal/boundary/invalid；运行 npm start 验证 CLI 入口产出非占位灯光舞蹈轨迹。","5. 反空壳审计：剔除关键词仅出现在 README/注释/字面量字符串的情形；剔除仅检查文件存在的测试；剔除 manifest-only 占位脚本。","6. 汇总 docs/qa-report.md：确定性检查结果表、Bench Level-1 阈值表、入口与测试命令实际输出摘要、遗留风险与回指 TASK-2 的修复项。"],"acceptance":["docs/qa-report.md 含确定性检查全项结果与 Bench Level-1 全部最小值达标证据。","ts_syntax、package_scripts、min_files≥3、content_any 命中、source_target_coverage 五项均通过。","测试断言 ≥2 且覆盖 normal/boundary/invalid 三类输入；CLI 入口运行退出码 0 且输出非占位。","报告列出反空壳审计结论：未出现仅文件存在/仅关键词出现/仅 manifest 脚本的伪通过。"],"verification_commands":[{"modality":"lint","argv":["npx","tsc","--noEmit"],"cwd":"."},{"modality":"test","argv":["node","--import","tsx","--test","tests/domain.rules.test.ts"],"cwd":"."},{"modality":"entrypoint","argv":["node","dist/entry/cli.js","--seed","42","--steps","5"],"cwd":"."}],"phase":"qa","depends_on":["TASK-2"],"assigned_to":"QA","execution_backend":"doc_only"}]}'
_R30_PRIMARY_SHA256 = "dbc0026a09424040cf023d5ed90dd2dd89cba549160374e8d81febc2a0671780"
_RETAINED_QUALITY: dict[str, Any] = {
    "score": 40,
    "critical_issues": [
        "verification_command_profile_unsupported",
        "verification_command_profile_unsupported",
        "factory_typescript_contract_missing:index.html",
    ],
    "contract_diagnostics": [
        {
            "code": "verification_command_profile_unsupported",
            "severity": "error",
            "path": "$.tasks[1].verification_commands[3]",
            "task_id": "TASK-2",
            "profile_id": "",
            "policy_error_code": "untrusted_verifier_command",
            "repair_fields": ["verification_commands"],
            "message": "Verifier command planning classification: untrusted_verifier_command; execution still requires current policy and actual proof.",
            "evidence": {
                "command_profile_feedback": {
                    "source": "control_plane.verifier_policy",
                    "profile_id": "",
                    "error_code": "untrusted_verifier_command",
                    "guidance_only": True,
                    "execution_authority": False,
                }
            },
        },
        {
            "code": "verification_command_profile_unsupported",
            "severity": "error",
            "path": "$.tasks[2].verification_commands[1]",
            "task_id": "TASK-3",
            "profile_id": "",
            "policy_error_code": "untrusted_verifier_command",
            "repair_fields": ["verification_commands"],
            "message": "Verifier command planning classification: untrusted_verifier_command; execution still requires current policy and actual proof.",
            "evidence": {
                "command_profile_feedback": {
                    "source": "control_plane.verifier_policy",
                    "profile_id": "",
                    "error_code": "untrusted_verifier_command",
                    "guidance_only": True,
                    "execution_authority": False,
                }
            },
        },
    ],
    "ok": False,
}


# This explicit requirement remains legitimate even when generic TS->HTML
# inference is removed in the independent entrypoint owner bucket.
_EXPLICIT_WEB_DIRECTIVE = (
    "主语言: typescript\n"
    "Deliver a real Web browser application with an executable HTML entrypoint. "
    "The required entrypoint is index.html; package.json build/start and tsconfig.json must remain usable."
)


def _base(workspace: Path, quality: dict[str, Any] | None = None) -> PMRevisionBase | None:
    assert hashlib.sha256(_R30_PRIMARY_OUTPUT.encode("utf-8")).hexdigest() == _R30_PRIMARY_SHA256
    return make_revision_base(
        _R30_PRIMARY_OUTPUT,
        workspace=str(workspace),
        run_id="pm-retained-html",
        task_id="task-0-pm",
        directive=_EXPLICIT_WEB_DIRECTIVE,
        quality=deepcopy(_RETAINED_QUALITY if quality is None else quality),
    )


def _adapter(workspace: Path) -> PMAdapter:
    constructor: Callable[..., PMAdapter] = PMAdapter
    return constructor(workspace=str(workspace))


def test_retained_three_diagnostics_enter_typed_revision_at_actual_http_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _base(tmp_path)
    assert base is not None, "Known HTML omission unnecessarily fell back to a full re-answer"
    original = json.loads(_R30_PRIMARY_OUTPUT)["tasks"]
    assert base.response == _R30_PRIMARY_OUTPUT and base.tasks == original
    assert all("index.html" not in row["target_files"] for row in base.tasks)
    manifest = deepcopy(base.view["edit_manifest"])
    assert manifest["remove_task_ids"] == []
    for row in original:
        permission = manifest["tasks"][row["id"]]["set"]
        assert permission["target_files"] == "string_array"
        assert permission["scope_paths"] == "string_array"
    prompt = _adapter(tmp_path)._build_pm_retry_message(
        directive=_EXPLICIT_WEB_DIRECTIVE,
        quality=deepcopy(_RETAINED_QUALITY),
        previous_output=_R30_PRIMARY_OUTPUT,
        validated_candidate=True,
        revision_base=base,
        output_budget_tokens=60_000,
    )
    captured: list[dict[str, Any]] = []

    def intercept(_url: str, _headers: Any, payload: dict[str, Any], *_args: Any) -> InvokeResult:
        captured.append(deepcopy(payload))
        return InvokeResult(ok=False, output="", latency_ms=0, usage=Usage.estimate("", ""), error="intercepted")

    monkeypatch.setattr(anthropic_provider, "invoke_with_retry", intercept)
    anthropic_provider.AnthropicProvider().invoke(
        prompt,
        "fixture-model",
        {
            "api_key": "fixture",
            "base_url": "https://provider.test/anthropic",
            "max_tokens": 60_000,
            "chat_messages": [
                {"role": "system", "content": "You are PM; preserve the immutable planning authority."},
                {"role": "user", "content": prompt},
            ],
        },
    )
    assert len(captured) == 1
    texts = [
        message["content"]
        if isinstance(message["content"], str)
        else "\n".join(block["text"] for block in message["content"])
        for message in captured[0]["messages"]
    ]
    actual = "\n".join(texts)
    assert "pm.task_revision.v1" in actual and base.base_hash in actual
    assert base.view_json in actual, "The actual full base/manifest changed at provider projection"
    assert "factory_typescript_contract_missing:index.html" in actual
    assert actual.count("verification_command_profile_unsupported") >= 2
    assert "$.tasks[1].verification_commands[3]" in actual
    assert "$.tasks[2].verification_commands[1]" in actual
    assert base.view["edit_manifest"] == manifest and base.tasks == original
    assert captured[0]["max_tokens"] == 60_000


def test_model_chooses_html_owner_and_must_pass_original_adapter_validation(tmp_path: Path) -> None:
    base = _base(tmp_path)
    assert base is not None
    original = base.tasks
    adapter = _adapter(tmp_path)
    _, before_quality = adapter._evaluate_contract_quality(deepcopy(original), directive=_EXPLICIT_WEB_DIRECTIVE)
    assert before_quality["ok"] is False
    candidate = deepcopy(original)
    for row in candidate:
        for command in row["verification_commands"]:
            if command["argv"][:4] == ["node", "--import", "tsx", "--test"]:
                command["argv"] = ["node", "--test", command["argv"][-1]]
    # Only the model proposal chooses this owner and these exact full arrays.
    candidate[1]["target_files"].append("index.html")
    candidate[1]["scope_paths"].append("index.html")
    proposal: dict[str, Any] = {
        "schema_version": "pm.task_revision.v1",
        "base_hash": base.base_hash,
        "updates": [
            {
                "task_id": original[1]["id"],
                "set": {
                    "target_files": candidate[1]["target_files"],
                    "scope_paths": candidate[1]["scope_paths"],
                    "verification_commands": candidate[1]["verification_commands"],
                },
            },
            {"task_id": original[2]["id"], "set": {"verification_commands": candidate[2]["verification_commands"]}},
        ],
        "add_tasks": [],
        "remove_task_ids": [],
    }
    merged = apply_revision(base, canonical_json(proposal))
    assert merged["tasks"] == candidate
    assert base.tasks == original and "index.html" not in original[1]["target_files"]
    _, quality = adapter._evaluate_contract_quality(merged["tasks"], directive=_EXPLICIT_WEB_DIRECTIVE)
    assert quality["ok"] is True, quality
    assert not quality["critical_issues"]
    assert [row["id"] for row in merged["tasks"]] == [row["id"] for row in original]
    stale = {**proposal, "base_hash": "0" * 64}
    with pytest.raises(_PMContractError):
        apply_revision(base, canonical_json(stale))
    invalid = deepcopy(proposal)
    invalid["updates"].append({"task_id": original[0]["id"], "set": {"assigned_to": "Director"}})
    with pytest.raises(_PMContractError):
        apply_revision(base, canonical_json(invalid))
    assert base.tasks == original
    assert not (tmp_path / "index.html").exists()


@pytest.mark.parametrize("kind", ["artifact", "unknown_critical", "unknown_missing"])
def test_html_permission_does_not_relax_narrowed_or_unknown_diagnoses(tmp_path: Path, kind: str) -> None:
    quality = deepcopy(_RETAINED_QUALITY)
    if kind == "artifact":
        quality["contract_diagnostics"].append(
            {
                "code": "generated_output_input_invalid",
                "path": "$.tasks[1].generated_outputs",
                "task_id": "TASK-2",
                "repair_fields": ["generated_outputs"],
            }
        )
    elif kind == "unknown_critical":
        quality["critical_issues"].append("unlocalized_unknown_program_finding")
    else:
        quality["critical_issues"][-1] = "factory_typescript_contract_missing:unrecognized-entry.html"
    before = deepcopy(quality)
    assert _base(tmp_path, quality) is None
    assert quality == before

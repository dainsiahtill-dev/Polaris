"""Illegal draft edits are rejected atomically, then retracted under the same manifest."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal import pm_adapter as module
from polaris.cells.roles.adapters.internal.pm.parsing import _PMContractError
from polaris.cells.roles.adapters.internal.pm.revision import (
    PMRevisionBase,
    apply_revision,
    canonical_json,
    make_revision_base,
)
from polaris.cells.roles.adapters.public import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import (
    _isolated_external_delivery as _isolated_external_delivery,
    _local_runtime,
)
from polaris.kernelone.llm.engine.executor import AIExecutor

# Exact native R32 model text is historical fixture data, not an execution grant.
_REPLAY = json.loads(
    '{"original_response":"{\\"tasks\\":[{\\"id\\":\\"TASK-1\\",\\"title\\":\\"Chief Engineer 蓝图:发光昆虫花园模拟器模块拓扑与门禁\\",\\"goal\\":\\"为发光昆虫花园模拟器(firefly/flower/moon/humidity)确立 CE 蓝图:命名具体模块、公开契约(类/函数/类型)、入口形式、构建/测试门禁命令,以及 ≥3 行为规则、≥3 主实体、≥2 边界用例的覆盖矩阵;并把 feature keywords 嵌入领域符号。\\",\\"scope_paths\\":[\\"requirements.md\\"],\\"target_files\\":[\\"docs/chief_engineer_blueprint.md\\"],\\"steps\\":[\\"枚举主实体: Firefly、Flower、MoonPhase、Garden,给出属性与公开方法签名\\",\\"定义核心行为规则(≥3):1) 萤火虫亮度=f(花朵情绪,月相,湿度);2) 月相按周期递增,NewMoon/FullMoon 切换;3) 萤火虫群按相邻亮度差形成同步舞蹈相位\\",\\"列出公开契约与模块拆分: src/garden/domain.ts(纯领域规则)、src/garden/sim.ts(仿真推进)、src/garden/render.ts(ASCII/控制台场景渲染,非必须 HTML)、src/garden/seed.ts(示例数据)、src/garden/cli.ts(Node CLI 入口)、tests/garden/rules.test.ts\\",\\"声明入口形态: Node CLI,package.json scripts.start = node dist/cli.js(或 ts-node),脚本实际调用仿真并打印一帧灯光舞蹈\\",\\"声明确定性门禁: tsc --noEmit、npm run build、npm test、node dist/cli.js --seed demo;以及 bench level-1 阈值: min_prod_files≥3、min_prod_lines≥120、min_test_files≥1、min_test_assertions≥2、min_behavior_symbols≥6、min_branch_count≥3、min_edge_cases≥2\\",\\"把 firefly/flower/moon/humidity 写入符号名或注释,确保源码实际引用而非仅 README\\"],\\"acceptance\\":[\\"蓝图文件 docs/chief_engineer_blueprint.md 存在并被本任务声明为 target_files\\",\\"蓝图列出 ≥3 行为规则、≥3 主实体、≥2 边界用例(如空花园/极端湿度/新月)\\",\\"蓝图给出至少 6 个 behavior 符号名(含 firefly/flower/moon/humidity 关键字)\\",\\"蓝图明确入口为 Node CLI,scripts.start 实际执行编译产物\\",\\"蓝图声明 tsc --noEmit、npm run build、npm test、entrypoint 四个门禁命令\\",\\"蓝图目录拓扑为权威,后续 Director 任务 target_files 必须落在该拓扑内\\"],\\"verification_commands\\":[],\\"phase\\":\\"planning\\",\\"depends_on\\":[],\\"assigned_to\\":\\"ChiefEngineer\\",\\"execution_backend\\":\\"doc_only\\"},{\\"id\\":\\"TASK-2\\",\\"title\\":\\"Director 实现:TypeScript 业务源码、Node CLI 入口、构建配置\\",\\"goal\\":\\"按 CE 蓝图落地完整可运行实现:TypeScript 业务源码覆盖 firefly/flower/moon/humidity 核心规则,Node CLI 入口可执行,package.json 与 tsconfig.json 指向真实源码根,产物行数与文件数满足 level-1 阈值。\\",\\"scope_paths\\":[\\"docs/chief_engineer_blueprint.md\\",\\"requirements.md\\"],\\"target_files\\":[\\"package.json\\",\\"tsconfig.json\\",\\"src/garden/domain.ts\\",\\"src/garden/sim.ts\\",\\"src/garden/render.ts\\",\\"src/garden/seed.ts\\",\\"src/garden/cli.ts\\",\\"src/garden/types.ts\\"],\\"steps\\":[\\"在 src/garden/types.ts 定义 FlowerEmotion、MoonPhase、Humidity、Firefly、Frame 等显式域类型(无 any)\\",\\"在 src/garden/domain.ts 实现亮度公式 brightness(firefly, flower, moon, humidity) 与月相周期 nextMoonPhase,导出 ≥3 行为规则对应函数\\",\\"在 src/garden/sim.ts 实现 step(garden, dt) 推进仿真,处理萤火虫群同步舞蹈相位(branch:同步/失步/收敛)\\",\\"在 src/garden/render.ts 实现 toAsciiFrame 或控制台彩色帧渲染,引用 flower/moon/humidity 输出\\",\\"在 src/garden/seed.ts 提供示例花园数据(至少 1 朵花、3 只萤火虫、月相初始值、湿度区间)\\",\\"在 src/garden/cli.ts 解析 --seed/--steps 参数,循环 step 并输出每帧,实现 process.exit 正确返回码\\",\\"在 package.json 声明 scripts.build (=tsc)、scripts.start (=node dist/cli.js)、scripts.test (=node --test dist/tests/**/*.test.js 或等价),并设置 main 指向编译产物\\",\\"在 tsconfig.json 设置 outDir=dist、rootDir=src、strict=true、target=ES2020、module=commonjs\\",\\"确保业务源码 ≥3 个 .ts 文件、总行数 ≥120,所有源码中含 firefly/flower/moon/humidity 实际使用\\"],\\"acceptance\\":[\\"npm install 成功(lockfile 由环境生成)\\",\\"npx tsc --noEmit 退出码 0,无类型错误\\",\\"npm run build 成功生成 dist/ 产物\\",\\"npm start -- --seed demo 能输出一帧包含 firefly/flower/moon 标识的灯光舞蹈帧\\",\\"源码文件树覆盖 src/garden/*.ts,符合 CE 蓝图目录拓扑\\",\\"主源码总行数 ≥120,业务 .ts 文件数 ≥3,均无 TODO/FIXME/stub 占位\\",\\"package.json scripts.start/build/test 均为实际执行命令而非 echo 占位\\"],\\"verification_commands\\":[{\\"modality\\":\\"environment_prep\\",\\"argv\\":[\\"npm\\",\\"install\\",\\"--no-audit\\",\\"--no-fund\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"lint\\",\\"argv\\":[\\"npx\\",\\"tsc\\",\\"--noEmit\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"build\\",\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"entrypoint\\",\\"argv\\":[\\"node\\",\\"dist/garden/cli.js\\",\\"--seed\\",\\"demo\\",\\"--steps\\",\\"3\\"],\\"cwd\\":\\".\\"}],\\"phase\\":\\"implementation\\",\\"depends_on\\":[\\"TASK-1\\"],\\"assigned_to\\":\\"Director\\",\\"execution_backend\\":\\"code_edit\\",\\"generated_outputs\\":[{\\"path\\":\\"dist/garden/domain.js\\",\\"inputs\\":[{\\"task_id\\":\\"TASK-2\\",\\"path\\":\\"src/garden/domain.ts\\"}],\\"build\\":{\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"}},{\\"path\\":\\"dist/garden/sim.js\\",\\"inputs\\":[{\\"task_id\\":\\"TASK-2\\",\\"path\\":\\"src/garden/sim.ts\\"}],\\"build\\":{\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"}},{\\"path\\":\\"dist/garden/render.js\\",\\"inputs\\":[{\\"task_id\\":\\"TASK-2\\",\\"path\\":\\"src/garden/render.ts\\"}],\\"build\\":{\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"}},{\\"path\\":\\"dist/garden/seed.js\\",\\"inputs\\":[{\\"task_id\\":\\"TASK-2\\",\\"path\\":\\"src/garden/seed.ts\\"}],\\"build\\":{\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"}},{\\"path\\":\\"dist/garden/cli.js\\",\\"inputs\\":[{\\"task_id\\":\\"TASK-2\\",\\"path\\":\\"src/garden/cli.js\\"}],\\"build\\":{\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"}},{\\"path\\":\\"dist/garden/types.js\\",\\"inputs\\":[{\\"task_id\\":\\"TASK-2\\",\\"path\\":\\"src/garden/types.ts\\"}],\\"build\\":{\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"}}]},{\\"id\\":\\"TASK-3\\",\\"title\\":\\"Director 验证:核心规则单元测试覆盖正常/边界/非法输入\\",\\"goal\\":\\"为仿真核心规则编写可执行测试,跨正常、边界、非法三类输入验证 firefly×flower×moon×humidity 行为,满足 level-1 的 min_test_files≥1、min_test_assertions≥2、min_branch_count≥3、min_edge_cases≥2,并保证 tsc --noEmit 与 npm test 均通过。\\",\\"scope_paths\\":[\\"docs/chief_engineer_blueprint.md\\",\\"src/garden/domain.ts\\",\\"src/garden/sim.ts\\",\\"src/garden/types.ts\\"],\\"target_files\\":[\\"tests/garden/rules.test.ts\\",\\"tests/garden/sim.test.ts\\"],\\"steps\\":[\\"tests/garden/rules.test.ts:对 brightness 与 nextMoonPhase 写正常用例(NewMoon/FullMoon 边界、湿度 0/1 边界、花朵多情绪)\\",\\"tests/garden/rules.test.ts:写非法输入用例(湿度负值、湿度>1、月相枚举外值)需抛出或被规约,显式断言\\",\\"tests/garden/sim.test.ts:对 step(garden, dt) 写萤火虫群同步/失步/收敛三类分支断言,验证 branch_count≥3\\",\\"tests/garden/sim.test.ts:边界用例 1) 空花园 step 不崩溃;2) 极端湿度 0 与 1.5 输入被规约或报错\\",\\"确保测试断言结果而非文件存在或关键字出现,断言业务数值(亮度区间、相位差、月相序号)\\",\\"在 package.json 中 scripts.test 指向 node --test 编译后的测试或 ts-node 运行,确保 npm test 实际执行断言\\"],\\"acceptance\\":[\\"tests 目录至少 1 个 .test.ts 文件,测试断言数 ≥2 且覆盖正常/边界/非法\\",\\"tsc --noEmit 退出码 0\\",\\"npm test 退出码 0,断言全部通过\\",\\"测试中显式引用 firefly/flower/moon/humidity 符号,验证业务结果(亮度数值、月相转移、相位差)而非只检查文件存在\\",\\"全部测试文件无 TODO/FIXME/stub 占位\\"],\\"verification_commands\\":[{\\"modality\\":\\"lint\\",\\"argv\\":[\\"npx\\",\\"tsc\\",\\"--noEmit\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"build\\",\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"test\\",\\"argv\\":[\\"npm\\",\\"test\\"],\\"cwd\\":\\".\\"}],\\"phase\\":\\"verification\\",\\"depends_on\\":[\\"TASK-2\\"],\\"assigned_to\\":\\"Director\\",\\"execution_backend\\":\\"code_edit\\"},{\\"id\\":\\"TASK-4\\",\\"title\\":\\"QA 闭环:README、确定性检查与 level-1 阈值自检\\",\\"goal\\":\\"交付 README 真实运行说明,并运行确定性检查与 bench level-1 自检,确认 ts_syntax、package_scripts、min_files≥3、content_any(firefly/flower/moon/humidity)、source_target_coverage(src/**/*.ts) 与 7 项最小阈值全部通过;验收脚本自动发现入口与测试。\\",\\"scope_paths\\":[\\"docs/chief_engineer_blueprint.md\\",\\"requirements.md\\",\\"package.json\\",\\"tsconfig.json\\",\\"src/garden\\",\\"tests/garden\\",\\"dist/garden/cli.js\\"],\\"target_files\\":[\\"README.md\\",\\"scripts/verify.sh\\"],\\"steps\\":[\\"README.md 写明:项目目标、目录结构、npm install / npm run build / npm test / npm start 实际命令、入口与示例种子使用方式、feature keywords 说明\\",\\"scripts/verify.sh 串行执行: npm install --no-audit --no-fund; npx tsc --noEmit; npm run build; npm test; node dist/garden/cli.js --seed demo --steps 2;并以 exit 码为唯一验收依据\\",\\"在 verify.sh 中加入 level-1 阈值自检:统计 src/garden/*.ts 文件数(≥3)、总行数(≥120)、tests/*.test.ts 文件数(≥1)、grep 断言 firefly|flower|moon|humidity 至少出现 1 次\\",\\"为 verify.sh 添加可执行位(chmod +x),并被 package.json scripts.verify 调用\\",\\"输出最终交付物清单与门禁结果到控制台,不依赖 echo-only 通过\\"],\\"acceptance\\":[\\"README.md 存在且包含运行命令示例,引用 firefly/flower/moon/humidity 至少 1 次\\",\\"scripts/verify.sh 存在并可执行,运行后 exit 0\\",\\"verify 覆盖全部 4 个门禁(类型检查、构建、测试、入口),且 entrypoint 输出含 firefly/flower/moon 标识\\",\\"level-1 7 项最小阈值全部满足(prod_files≥3、prod_lines≥120、test_files≥1、test_assertions≥2、behavior_symbols≥6、branch_count≥3、edge_cases≥2)\\",\\"content_any 命中:src 下任一 .ts 包含 firefly|flower|moon|humidity\\"],\\"verification_commands\\":[{\\"modality\\":\\"environment_prep\\",\\"argv\\":[\\"bash\\",\\"scripts/verify.sh\\"],\\"cwd\\":\\".\\"}],\\"phase\\":\\"qa\\",\\"depends_on\\":[\\"TASK-2\\",\\"TASK-3\\"],\\"assigned_to\\":\\"QA\\",\\"execution_backend\\":\\"code_edit\\"}]}","revision_response":"{\\n  \\"schema_version\\": \\"pm.task_revision.v1\\",\\n  \\"base_hash\\": \\"24616ef21e340c645dd4f1f4f94a22e9a1a39171d79fecdfcb71389517512146\\",\\n  \\"updates\\": [\\n    {\\n      \\"task_id\\": \\"TASK-4\\",\\n      \\"set\\": {\\n        \\"verification_commands\\": [\\n          {\\n            \\"modality\\": \\"test\\",\\n            \\"argv\\": [\\"npm\\", \\"test\\"],\\n            \\"cwd\\": \\".\\"\\n          },\\n          {\\n            \\"modality\\": \\"entrypoint\\",\\n            \\"argv\\": [\\"node\\", \\"dist/garden/cli.js\\", \\"--seed\\", \\"demo\\", \\"--steps\\", \\"2\\"],\\n            \\"cwd\\": \\".\\"\\n          }\\n        ]\\n      },\\n      \\"append\\": {\\n        \\"acceptance\\": [\\n          \\"QA 阶段日志中以 exit 码为唯一判定:scripts/verify.sh 内 npm test 与 node dist/garden/cli.js 退码 0 才视为达标,不依赖 echo-only\\",\\n          \\"不把 npx tsc --version、node --version 等探测行 stdout 当作交付证据;移除/不引入此类版本探测\\"\\n        ]\\n      }\\n    },\\n    {\\n      \\"task_id\\": \\"TASK-2\\",\\n      \\"set\\": {\\n        \\"generated_outputs\\": []\\n      },\\n      \\"append\\": {\\n        \\"steps\\": [\\n        ]\\n      }\\n    }\\n  ],\\n  \\"add_tasks\\": [],\\n  \\"remove_task_ids\\": []\\n}","base_hash":"24616ef21e340c645dd4f1f4f94a22e9a1a39171d79fecdfcb71389517512146","identity":{"directive_hash":"3c08d596ada51e190b56087c819c97811e2a7a802b646e030ee6e2c7d92e4ea0","original_response_hash":"32932f1744d8a92e60d49b65ca1c4042cd3cff7576e24634eee6236b7d912425","projection_hint":{},"run_id":"pm-37d5b880a4aa","task_id":"task-0-pm","workspace":"/home/dains/.polaris/bench/workspaces/l1-01-20261012T223527Z-r32/workspaces/2138531efa0d-cb5e2f3706a54001/L1-01-7bb25f6bf0f37baf/a2baa2bf9b63d9c11dfb818f"},"view":{"base_hash":"24616ef21e340c645dd4f1f4f94a22e9a1a39171d79fecdfcb71389517512146","edit_manifest":{"remove_task_ids":[],"tasks":{"TASK-2":{"append":{},"set":{"generated_outputs":"generated_outputs_array"}},"TASK-4":{"append":{},"set":{"verification_commands":"verifier_array"}}}},"tasks":[{"depends_on":[],"id":"TASK-1","target_files":["docs/chief_engineer_blueprint.md"],"verification_commands":[]},{"depends_on":["TASK-1"],"generated_outputs":[{"build":{"argv":["npm","run","build"],"cwd":"."},"inputs":[{"path":"src/garden/domain.ts","task_id":"TASK-2"}],"path":"dist/garden/domain.js"},{"build":{"argv":["npm","run","build"],"cwd":"."},"inputs":[{"path":"src/garden/sim.ts","task_id":"TASK-2"}],"path":"dist/garden/sim.js"},{"build":{"argv":["npm","run","build"],"cwd":"."},"inputs":[{"path":"src/garden/render.ts","task_id":"TASK-2"}],"path":"dist/garden/render.js"},{"build":{"argv":["npm","run","build"],"cwd":"."},"inputs":[{"path":"src/garden/seed.ts","task_id":"TASK-2"}],"path":"dist/garden/seed.js"},{"build":{"argv":["npm","run","build"],"cwd":"."},"inputs":[{"path":"src/garden/cli.js","task_id":"TASK-2"}],"path":"dist/garden/cli.js"},{"build":{"argv":["npm","run","build"],"cwd":"."},"inputs":[{"path":"src/garden/types.ts","task_id":"TASK-2"}],"path":"dist/garden/types.js"}],"id":"TASK-2","target_files":["package.json","tsconfig.json","src/garden/domain.ts","src/garden/sim.ts","src/garden/render.ts","src/garden/seed.ts","src/garden/cli.ts","src/garden/types.ts"],"verification_commands":[{"argv":["npm","install","--no-audit","--no-fund"],"cwd":".","modality":"environment_prep"},{"argv":["npx","tsc","--noEmit"],"cwd":".","modality":"lint"},{"argv":["npm","run","build"],"cwd":".","modality":"build"},{"argv":["node","dist/garden/cli.js","--seed","demo","--steps","3"],"cwd":".","modality":"entrypoint"}]},{"depends_on":["TASK-2"],"id":"TASK-3"},{"depends_on":["TASK-2","TASK-3"],"id":"TASK-4","verification_commands":[{"argv":["bash","scripts/verify.sh"],"cwd":".","modality":"environment_prep"}]}]},"quality":{"ok":false,"score":40,"critical_issues":["verification_command_profile_unsupported","generated_output_input_invalid"],"contract_diagnostics":[{"code":"verification_command_profile_unsupported","severity":"error","path":"$.tasks[3].verification_commands[0]","task_id":"TASK-4","profile_id":"","policy_error_code":"untrusted_verifier_command","repair_fields":["verification_commands"],"message":"Verifier command planning classification: untrusted_verifier_command; execution still requires current policy and actual proof.","evidence":{"command_profile_feedback":{"source":"control_plane.verifier_policy","profile_id":"","error_code":"untrusted_verifier_command","guidance_only":true,"execution_authority":false}}},{"code":"generated_output_input_invalid","path":"$.tasks[1].generated_outputs","task_id":"TASK-2","message":"Inputs must bind self/ancestor authored targets"},{"code":"generated_output_input_invalid","path":"$.tasks[1].generated_outputs","task_id":"TASK-2","message":"Compiled code needs primary-language authored input"}]}}'
)


def _base() -> PMRevisionBase:
    raw = _REPLAY["original_response"]
    assert len(raw) == 7413
    assert (
        hashlib.sha256(raw.encode("utf-8")).hexdigest()
        == "32932f1744d8a92e60d49b65ca1c4042cd3cff7576e24634eee6236b7d912425"
    )
    bad = _REPLAY["revision_response"]
    assert len(bad) == 994
    assert (
        hashlib.sha256(bad.encode("utf-8")).hexdigest()
        == "883ab3b593c811c975a9fe452c4a46f8fc3e3512acca139cc36b56cb146ea17e"
    )
    return PMRevisionBase(
        raw,
        canonical_json(json.loads(raw)["tasks"]),
        canonical_json(_REPLAY["identity"]),
        canonical_json(_REPLAY["view"]),
        _REPLAY["base_hash"],
    )


def _corrected() -> str:
    proposal = deepcopy(json.loads(_REPLAY["revision_response"]))
    for update in proposal["updates"]:
        update.pop("append", None)  # controlled model fixture explicitly retracts every unauthorized edit
        if update["task_id"] == "TASK-2":
            outputs = deepcopy(_base().tasks[1]["generated_outputs"])
            outputs[4]["inputs"][0]["path"] = "src/garden/cli.ts"
            update["set"]["generated_outputs"] = outputs
    return canonical_json(proposal)


@pytest.mark.parametrize("field", ["acceptance", "steps"])
def test_known_ungranted_draft_edit_is_recoverable_but_never_applied(field: str) -> None:
    base = _base()
    before = (base.response, base.tasks, base.view, base.identity)
    proposal = json.loads(_REPLAY["revision_response"])
    proposal["updates"] = [{"task_id": "TASK-4", "append": {field: ["extra prose"]}}]
    with pytest.raises(_PMContractError) as error:
        apply_revision(base, canonical_json(proposal))
    assert error.value.diagnostic["repairable"] is True
    assert error.value.diagnostic["authorized_operations"] == []
    assert (base.response, base.tasks, base.view, base.identity) == before


@pytest.mark.parametrize("field", ["assigned_to", "execution_backend", "metadata", "unknown_field"])
def test_routing_and_unknown_field_edits_remain_terminal(field: str) -> None:
    base = _base()
    proposal = json.loads(_REPLAY["revision_response"])
    proposal["updates"] = [{"task_id": "TASK-4", "set": {field: "director"}}]
    with pytest.raises(_PMContractError) as error:
        apply_revision(base, canonical_json(proposal))
    assert error.value.diagnostic["repairable"] is False
    assert error.value.diagnostic["authorized_operations"] == []


@pytest.mark.parametrize("kind", ["stale_base", "task_removal", "noncanonical_path"])
def test_original_identity_removal_and_path_boundaries_remain_terminal(tmp_path: Path, kind: str) -> None:
    base = _base()
    proposal = json.loads(_corrected())
    if kind == "stale_base":
        proposal["base_hash"] = "0" * 64
    elif kind == "task_removal":
        proposal["remove_task_ids"] = ["TASK-2"]
    else:
        general = make_revision_base(
            base.response,
            workspace=str(tmp_path),
            run_id="pm-path",
            task_id="pm-task",
            directive="Create a TypeScript CLI",
            quality={"critical_issues": [], "contract_diagnostics": []},
        )
        assert general is not None
        base = general
        proposal["base_hash"] = base.base_hash
        proposal["updates"] = [{"task_id": "TASK-2", "set": {"target_files": ["../outside.ts"]}}]
    with pytest.raises(_PMContractError) as error:
        apply_revision(base, canonical_json(proposal))
    assert error.value.diagnostic["repairable"] is False


@pytest.mark.parametrize("field", ["scope_paths", "target_files", "context_files"])
@pytest.mark.parametrize("operand", ["../outside.ts", "/outside.ts"])
def test_narrow_manifest_path_field_denial_remains_terminal(field: str, operand: str) -> None:
    base = _base()
    before = (base.tasks, base.view)
    proposal = json.loads(_corrected())
    proposal["updates"] = [{"task_id": "TASK-2", "set": {field: [operand]}}]
    with pytest.raises(_PMContractError) as error:
        apply_revision(base, canonical_json(proposal))
    assert error.value.diagnostic["repairable"] is False
    assert error.value.diagnostic["authorized_operations"] == []
    assert (base.tasks, base.view) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_bad", [False, True])
async def test_exact_r32_recovery_enters_real_runtime_http_then_original_quality_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_external_delivery: None, repeat_bad: bool
) -> None:
    base = _base()
    before = (base.response, base.tasks, base.view, base.identity)
    outputs = [
        {"role": "assistant", "content": text}
        for text in [
            base.response,
            _REPLAY["revision_response"],
            _REPLAY["revision_response"] if repeat_bad else _corrected(),
        ]
    ]
    runtime: Any
    runtime, freezes, wires = _local_runtime(tmp_path, monkeypatch, outputs)
    registry = runtime._kernels[str(tmp_path)].registry
    profile = registry.get_profile_or_raise("pm")
    registry.register(replace(profile, context_policy=replace(profile.context_policy, max_context_tokens=1_000_000)))
    # This historical request used 60k output policy; configure that capability
    # on the fake endpoint rather than bypassing the real output-budget guard.
    monkeypatch.setattr(
        AIExecutor,
        "_get_provider_config",
        lambda *_args: {
            "type": "openai_compat",
            "base_url": "https://local-provider.example.test/v1",
            "retries": 0,
            "max_context_tokens": 1_000_000,
            "max_output_tokens": 60000,
        },
    )
    # Replay the original immutable, unqualified base; only actual stage artifacts
    # and task records use pytest's fresh home sandbox, never the native workspace.
    monkeypatch.setattr(module, "make_revision_base", lambda *args, **kwargs: base)
    constructor: Callable[..., PMAdapter] = PMAdapter
    adapter = constructor(workspace=str(tmp_path))
    deadline = time.time() + 5400
    result = await adapter._run_pm_stage(
        base.identity["task_id"],
        "Create and verify the TypeScript CLI",
        {},
        {
            "run_id": base.identity["run_id"],
            "metadata": {
                "llm_max_tokens": 60000,
                "llm_call_timeout_seconds": 750,
                "pm_planning_deadline_epoch_seconds": deadline,
            },
        },
    )
    assert len(wires) == len(freezes) == 3
    assert len(runtime._test_commands) == 3
    assert all(command.workspace == str(tmp_path) for command in runtime._test_commands)
    text = json.dumps(wires[2], ensure_ascii=False)
    assert "$.updates[0].append.acceptance" in text
    assert "not server-authorized for append" in text
    wire_text = "\n".join(str(message.get("content", "")) for message in wires[2]["messages"])
    assert base.base_hash in wire_text and _REPLAY["revision_response"] in wire_text
    assert "generated_output_input_invalid" in json.dumps(wires[1], ensure_ascii=False)
    assert (base.response, base.tasks, base.view, base.identity) == before
    artifacts = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (tmp_path / ".polaris/runtime/signals/pm_revision").glob("*.json")
    ]
    rows = [r for r in artifacts if r["schema_version"] == "pm.task_revision_derivation.v1"]
    assert len(rows) == (1 if repeat_bad else 2)
    assert all(r["base_hash"] == base.base_hash for r in rows)
    bad = next(r for r in rows if r["revision_response"] == _REPLAY["revision_response"])
    assert bad["raw_merged_candidate"] is None and bad["raw_merged_candidate_hash"] is None
    assert bad["quality"]["contract_diagnostics"][0]["repairable"] is True
    if repeat_bad:
        assert result["success"] is False
        assert any(s["code"] == "pm.contracts.revision_no_progress" for s in result["quality_gate"]["signals"])
        assert all(r["raw_merged_candidate"] is None for r in rows)
    else:
        assert result["success"] is True, result
        assert result["quality_gate"]["score"] >= 80
        good = next(r for r in rows if r["quality"]["ok"] is True)
        assert good["revision"] == json.loads(_corrected())
        assert good["raw_merged_candidate"] == apply_revision(base, _corrected())
        for old, current in zip(base.tasks, good["raw_merged_candidate"]["tasks"], strict=True):
            for field in ["id", "assigned_to", "execution_backend", "target_files", "steps", "acceptance"]:
                assert current[field] == old[field]

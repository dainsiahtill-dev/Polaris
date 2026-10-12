"""PM retains one planning base while correcting a diagnosed proposal locally."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import (
    _isolated_external_delivery as _isolated_external_delivery,
)
from polaris.cells.roles.adapters.tests.test_pm_revision_document_verifiers import _candidate

_R20_REPLAY = json.loads(
    '{"packet":{"original_response_hash":"15d8f3d86540fa15d5c1a2fbe2930125dced9f8ff8c7198736aa210c51ad9bb2","revision_response_hash":"87975b62f5da66a2ea4c2c487911ef010c69efc3f19fbeaccf43284f16a08648","revision_response":"{\\"schema_version\\":\\"pm.task_revision.v1\\",\\"base_hash\\":\\"a63fd2bf6b67ba53806578ee50bb616c55476508c8b05014eb7af4ae705a14e5\\",\\"updates\\":[{\\"task_id\\":\\"TASK-1\\",\\"set\\":{\\"goal\\":\\"依据 Architect 蓝图，CE 命名发光昆虫花园模拟器的具体 TypeScript 模块拓扑、公开契约与门禁命令。模块必须真实承载 firefly（萤火虫）、flower（花朵情绪）、moon（月相）、humidity（湿度）四类核心实体与实时灯光舞蹈规则，覆盖 normal/boundary/invalid 三类分支；产出蓝图文档为后续 Director 实现与 QA 提供唯一权威路径；蓝图必须禁止 manifest-only / placeholder 脚本并显式要求 README.md 落地。\\",\\"title\\":\\"Chief Engineer 蓝图：发光昆虫花园模拟器模块拓扑与契约（含 README 与反 placeholder 约束）\\",\\"scope_paths\\":[\\"requirements.md\\",\\"docs/blueprint\\",\\"README.md\\"],\\"target_files\\":[\\"docs/blueprint/glowing-insect-garden.md\\"],\\"acceptance\\":[\\"docs/blueprint/glowing-insect-garden.md 存在，包含 ≥3 条行为规则、≥6 个领域符号（Firefly/Flower/MoonPhase/Humidity/LightDanceCoordinator/GardenWorld 等）、≥3 条分支、≥2 条 edge case（湿度越界、月相非法、萤火虫数量为 0）。\\",\\"蓝图明确声明：firefly 的灯光舞蹈 = f(flower.mood, moon.phase, humidity)，关键词 firefly/flower/moon/humidity 全部出现于实体或规则命名/描述。\\",\\"蓝图声明真实源码根（src/garden/**）与测试根（tests/garden/**），并指定 package.json/tsconfig.json 指向该源码根；声明 README.md 为交付物并要求由 Director 在 TASK-2 同步落地。\\",\\"蓝图门禁矩阵列出 npm install、npm run build、tsc --noEmit、npm test、npm start 五条命令，argv 与 cwd 完整。\\",\\"蓝图禁止空 manifest / placeholder 脚本，要求 build/test/start 实际驱动产品入口或核心规则验证；script 必须指向具体 .ts / dist 入口文件而非 echo/version 探测。\\",\\"蓝图标注 TypeScript 必备交付清单：package.json + tsconfig.json + index.html（含 <html>）+ README.md + src/garden/**/*.ts + tests/garden/**/*.test.ts。\\"]},\\"append\\":{\\"steps\\":[\\"读取 requirements.md 与架构上下文，确认 simulation_toy / science_creative 域、TypeScript 主语言、关键词 firefly/flower/moon/humidity 不可丢失。\\",\\"声明模块拓扑：src/garden/domain（GardenWorld、Flower、MoodState、Firefly、LunarPhase、HumiditySensor 实体）、src/garden/rules（lightDanceCoordinator 行为规则）、src/garden/seed（flowerSeedData 等示例/种子数据）、src/garden/entry（cli 入口 main.ts + 可选 index.html）、tests/garden/*.test.ts。\\",\\"为每个模块写公开契约：实体字段类型、行为规则输入输出、可注入接口；明确 firefly.dancePattern 由 flower.mood × moon.phase × humidity 三因素函数决定，并写明至少 3 条行为规则、≥6 个领域符号、≥3 条分支、≥2 条边界/无效输入处理。\\",\\"声明门禁命令：npm install、npm run build（tsc）、npm test、npm start、tsc --noEmit；写明每条命令的 argv 与 cwd。\\",\\"明确 package.json scripts.start / scripts.build 指向真实入口与 tsc 编译，禁止 manifest-only / placeholder / echo-only 脚本；声明 index.html 作为可视化入口的等价 HTML 入口路径；声明 README.md 落地要求（运行/构建/测试/入口说明）并将其归入 Director 目标文件清单。\\",\\"输出 docs/blueprint/glowing-insect-garden.md，包含产品行为规则、示例种子数据、模块清单、门禁命令矩阵、验收映射、TypeScript 必备交付清单与 README 约束。\\",\\"在验收矩阵中显式声明：script.argv 必须可被 npm 直接调用、禁止 echo / version 探测、禁止只检查 manifest 存在；index.html 必须含 <html> 根标签与 <canvas id=\\\\\\"garden\\\\\\">。\\"],\\"acceptance\\":[\\"蓝图 README 章节明确 README.md 由 Director 在 TASK-2 target_files 中落地，并要求 README 至少包含 build / test / start / entrypoint 说明。\\",\\"蓝图在 TypeScript 必备交付清单中列明 README.md 与 index.html（含 <html>）以闭合 factory_typescript_contract 缺口。\\"]}},{\\"task_id\\":\\"TASK-2\\",\\"set\\":{\\"goal\\":\\"依据 CE 蓝图无损实现发光昆虫花园模拟器：完成 src/garden 下的 firefly/flower/moon/humidity 领域模型与 lightDanceCoordinator 实时灯光舞蹈规则、Node CLI 入口、可视化 HTML 入口、示例种子数据、README.md、tsconfig 与 package.json 脚本；并落地 tests/garden 测试覆盖 normal/boundary/invalid 分支与 ≥2 条业务断言；严格禁止 placeholder / manifest-only 脚本。\\",\\"title\\":\\"Director 实现：发光昆虫花园模拟器核心域、入口、README 与测试\\",\\"scope_paths\\":[\\"docs/blueprint/glowing-insect-garden.md\\",\\"src/garden\\",\\"tests/garden\\"],\\"target_files\\":[\\"package.json\\",\\"tsconfig.json\\",\\"README.md\\",\\"index.html\\",\\"src/garden/domain/firefly.ts\\",\\"src/garden/domain/flower.ts\\",\\"src/garden/domain/lunarPhase.ts\\",\\"src/garden/domain/humiditySensor.ts\\",\\"src/garden/domain/gardenWorld.ts\\",\\"src/garden/rules/lightDanceCoordinator.ts\\",\\"src/garden/seed/flowerSeedData.ts\\",\\"src/garden/entry/main.ts\\",\\"tests/garden/lightDanceCoordinator.test.ts\\"]},\\"append\\":{\\"steps\\":[\\"初始化 package.json：name=glowing-insect-garden、type=module、scripts.start 指向编译后入口、scripts.build=tsc、scripts.test 指向 node --test 运行 tests/garden；依赖 typescript 与 @types/node；写入 keywords 包含 firefly/flower/moon/humidity。\\",\\"写 tsconfig.json：target=ES2022、module=NodeNext、moduleResolution=NodeNext、strict、outDir=dist、rootDir=src、include=[\\\\\\"src/**/*.ts\\\\\\"]、types=[\\\\\\"node\\\\\\"]。\\",\\"src/garden/domain 定义 Firefly、Flower（含 MoodState 枚举：cheerful/sad/restless/serene）、LunarPhase（含 new/waxing/full/waning 枚举 + 数值亮度因子）、HumiditySensor（含归一化湿度，越界抛 InvalidHumidityError）、GardenWorld 聚合实体列表与世界时钟。\\",\\"src/garden/rules/lightDanceCoordinator.ts：实现 lightDanceCoordinator(world, tick) → DanceStep[]，规则=f(flower.mood, moon.phase, humidity) 计算 firefly 颜色/亮度/闪烁频率，处理 emptyFireflies、unknownMoonPhase、humidityOutOfRange 三条 edge case。\\",\\"src/garden/seed/flowerSeedData.ts：导出 seedGarden() 返回示例花田与初始萤火虫群，覆盖至少 4 种 mood 与 4 种月相。\\",\\"src/garden/entry/main.ts：CLI 入口，加载 seedGarden、推进 tick、输出 JSON 摘要，可被 npm start 调用。\\",\\"index.html：含 <html>，引用 dist/main.js 编译产物并提供 <canvas id=\\\\\\"garden\\\\\\"> 渲染容器与启动按钮，调用主入口打印 DanceStep 摘要。\\",\\"tests/garden/lightDanceCoordinator.test.ts：用 node --test 写 ≥2 条业务断言：normal 全月+serene 萤火虫颜色为冷色调且闪烁频率 ≤1Hz；boundary 湿度=0 仍产出有效 DanceStep；invalid 月相=invalid 抛错。\\",\\"确保 min_prod_files≥3、prod_lines≥120、含 firefly/flower/moon/humidity 实际使用，代码逻辑而非纯注释。\\",\\"落地 README.md：项目说明、依赖安装、构建/测试/启动命令、入口说明（CLI + index.html）、关键词 firefly/flower/moon/humidity 释义、目录结构与示例输出片段，禁止以 placeholder 或 manifest-only 形式交付。\\",\\"核对 package.json scripts.start / scripts.build / scripts.test 全部指向真实 .ts / dist 入口与 node --test 测试文件，禁止 echo-only、version 探测或仅检查文件存在。\\"],\\"acceptance\\":[\\"README.md 在工作区根存在，含 build / test / start / entrypoint 说明与 firefly/flower/moon/humidity 关键词，闭合 factory_typescript_contract 缺口。\\",\\"npm install 成功。\\",\\"npm run build 成功，dist/garden/entry/main.js 存在。\\",\\"tsc --noEmit 退出码 0。\\",\\"npm test 至少 2 条业务断言通过，断言针对 lightDanceCoordinator 的输出值（颜色/频率/DanceStep 长度），不只检查文件存在。\\",\\"npm start 真实运行 CLI 入口并打印 seedGarden 一次 tick 的 DanceStep JSON，包含 firefly/flower/moon/humidity 字段。\\",\\"index.html 含 <html> 根标签与 <canvas id=\\\\\\"garden\\\\\\"> 容器，引用编译后入口。\\",\\"src/**/*.ts 至少 3 个文件、合计 ≥120 行；firefly/flower/moon/humidity 至少各 1 处真实业务引用（非注释堆叠）。\\",\\"package.json scripts.start/build/test 全部指向真实文件命令，无 shell 包装、echo-only 或仅检查 manifest 存在。\\"]}},{\\"task_id\\":\\"TASK-3\\",\\"set\\":{\\"goal\\":\\"QA 独立运行门禁矩阵并审计业务深度：验证 TypeScript 产物结构（含 README.md）、入口可运行性、核心规则（firefly 灯光舞蹈 = f(flower.mood, moon.phase, humidity)）、关键词 firefly/flower/moon/humidity 真实业务出现，以及 normal/boundary/invalid 三类分支的测试覆盖；拒绝 manifest-only / placeholder 交付；产出 reports/qa/glowing-insect-garden.md 报告。\\",\\"title\\":\\"QA 闭环：发光昆虫花园模拟器门禁矩阵、业务深度与 README 审计\\",\\"scope_paths\\":[\\"package.json\\",\\"tsconfig.json\\",\\"README.md\\",\\"index.html\\",\\"src/garden\\",\\"tests/garden\\",\\"reports/qa\\"],\\"target_files\\":[\\"reports/qa/glowing-insect-garden.md\\"],\\"acceptance\\":[\\"README.md 在工作区根存在且含 build / test / start / entrypoint 说明与关键词。\\",\\"npm install 退出码 0。\\",\\"npm run build 与 npx tsc --noEmit 退出码 0，dist/garden/entry/main.js 存在。\\",\\"npm test 退出码 0，≥2 条业务断言通过，覆盖 normal/boundary/invalid。\\",\\"npm start stdout 包含 firefly/flower/moon/humidity 字段的 DanceStep JSON 摘要。\\",\\"src/**/*.ts 中 firefly/flower/moon/humidity 各 ≥1 处真实业务引用，prod_files≥3、prod_lines≥120。\\",\\"index.html 含 <html> 根标签与 <canvas id=\\\\\\"garden\\\\\\"> 容器，引用编译后入口。\\",\\"lightDanceCoordinator 实现体现 flower.mood × moon.phase × humidity 三因素函数，含 ≥3 规则、≥3 分支、≥2 edge case。\\",\\"QA 拒绝 manifest-only / echo-only / 仅检查文件存在的脚本；确认 package.json scripts 实际驱动 tsc / node / node --test。\\",\\"reports/qa/glowing-insect-garden.md 存在并明确给出 PASS/FAIL 结论与门禁矩阵、README 审计结果。\\"]},\\"append\\":{\\"steps\\":[\\"审计 README.md：存在性、含 build/test/start/entrypoint 说明、含 firefly/flower/moon/humidity 关键词。\\",\\"审计 package.json scripts：grep 检查 start/build/test 指向真实 .ts / dist / node --test 入口，不允许 echo / version 探测或仅检查 manifest。\\"]}}],\\"add_tasks\\":[],\\"remove_task_ids\\":[]}","revision":{"add_tasks":[],"base_hash":"a63fd2bf6b67ba53806578ee50bb616c55476508c8b05014eb7af4ae705a14e5","remove_task_ids":[],"schema_version":"pm.task_revision.v1","updates":[{"append":{"acceptance":["蓝图 README 章节明确 README.md 由 Director 在 TASK-2 target_files 中落地，并要求 README 至少包含 build / test / start / entrypoint 说明。","蓝图在 TypeScript 必备交付清单中列明 README.md 与 index.html（含 <html>）以闭合 factory_typescript_contract 缺口。"],"steps":["读取 requirements.md 与架构上下文，确认 simulation_toy / science_creative 域、TypeScript 主语言、关键词 firefly/flower/moon/humidity 不可丢失。","声明模块拓扑：src/garden/domain（GardenWorld、Flower、MoodState、Firefly、LunarPhase、HumiditySensor 实体）、src/garden/rules（lightDanceCoordinator 行为规则）、src/garden/seed（flowerSeedData 等示例/种子数据）、src/garden/entry（cli 入口 main.ts + 可选 index.html）、tests/garden/*.test.ts。","为每个模块写公开契约：实体字段类型、行为规则输入输出、可注入接口；明确 firefly.dancePattern 由 flower.mood × moon.phase × humidity 三因素函数决定，并写明至少 3 条行为规则、≥6 个领域符号、≥3 条分支、≥2 条边界/无效输入处理。","声明门禁命令：npm install、npm run build（tsc）、npm test、npm start、tsc --noEmit；写明每条命令的 argv 与 cwd。","明确 package.json scripts.start / scripts.build 指向真实入口与 tsc 编译，禁止 manifest-only / placeholder / echo-only 脚本；声明 index.html 作为可视化入口的等价 HTML 入口路径；声明 README.md 落地要求（运行/构建/测试/入口说明）并将其归入 Director 目标文件清单。","输出 docs/blueprint/glowing-insect-garden.md，包含产品行为规则、示例种子数据、模块清单、门禁命令矩阵、验收映射、TypeScript 必备交付清单与 README 约束。","在验收矩阵中显式声明：script.argv 必须可被 npm 直接调用、禁止 echo / version 探测、禁止只检查 manifest 存在；index.html 必须含 <html> 根标签与 <canvas id=\\"garden\\">。"]},"set":{"acceptance":["docs/blueprint/glowing-insect-garden.md 存在，包含 ≥3 条行为规则、≥6 个领域符号（Firefly/Flower/MoonPhase/Humidity/LightDanceCoordinator/GardenWorld 等）、≥3 条分支、≥2 条 edge case（湿度越界、月相非法、萤火虫数量为 0）。","蓝图明确声明：firefly 的灯光舞蹈 = f(flower.mood, moon.phase, humidity)，关键词 firefly/flower/moon/humidity 全部出现于实体或规则命名/描述。","蓝图声明真实源码根（src/garden/**）与测试根（tests/garden/**），并指定 package.json/tsconfig.json 指向该源码根；声明 README.md 为交付物并要求由 Director 在 TASK-2 同步落地。","蓝图门禁矩阵列出 npm install、npm run build、tsc --noEmit、npm test、npm start 五条命令，argv 与 cwd 完整。","蓝图禁止空 manifest / placeholder 脚本，要求 build/test/start 实际驱动产品入口或核心规则验证；script 必须指向具体 .ts / dist 入口文件而非 echo/version 探测。","蓝图标注 TypeScript 必备交付清单：package.json + tsconfig.json + index.html（含 <html>）+ README.md + src/garden/**/*.ts + tests/garden/**/*.test.ts。"],"goal":"依据 Architect 蓝图，CE 命名发光昆虫花园模拟器的具体 TypeScript 模块拓扑、公开契约与门禁命令。模块必须真实承载 firefly（萤火虫）、flower（花朵情绪）、moon（月相）、humidity（湿度）四类核心实体与实时灯光舞蹈规则，覆盖 normal/boundary/invalid 三类分支；产出蓝图文档为后续 Director 实现与 QA 提供唯一权威路径；蓝图必须禁止 manifest-only / placeholder 脚本并显式要求 README.md 落地。","scope_paths":["requirements.md","docs/blueprint","README.md"],"target_files":["docs/blueprint/glowing-insect-garden.md"],"title":"Chief Engineer 蓝图：发光昆虫花园模拟器模块拓扑与契约（含 README 与反 placeholder 约束）"},"task_id":"TASK-1"},{"append":{"acceptance":["README.md 在工作区根存在，含 build / test / start / entrypoint 说明与 firefly/flower/moon/humidity 关键词，闭合 factory_typescript_contract 缺口。","npm install 成功。","npm run build 成功，dist/garden/entry/main.js 存在。","tsc --noEmit 退出码 0。","npm test 至少 2 条业务断言通过，断言针对 lightDanceCoordinator 的输出值（颜色/频率/DanceStep 长度），不只检查文件存在。","npm start 真实运行 CLI 入口并打印 seedGarden 一次 tick 的 DanceStep JSON，包含 firefly/flower/moon/humidity 字段。","index.html 含 <html> 根标签与 <canvas id=\\"garden\\"> 容器，引用编译后入口。","src/**/*.ts 至少 3 个文件、合计 ≥120 行；firefly/flower/moon/humidity 至少各 1 处真实业务引用（非注释堆叠）。","package.json scripts.start/build/test 全部指向真实文件命令，无 shell 包装、echo-only 或仅检查 manifest 存在。"],"steps":["初始化 package.json：name=glowing-insect-garden、type=module、scripts.start 指向编译后入口、scripts.build=tsc、scripts.test 指向 node --test 运行 tests/garden；依赖 typescript 与 @types/node；写入 keywords 包含 firefly/flower/moon/humidity。","写 tsconfig.json：target=ES2022、module=NodeNext、moduleResolution=NodeNext、strict、outDir=dist、rootDir=src、include=[\\"src/**/*.ts\\"]、types=[\\"node\\"]。","src/garden/domain 定义 Firefly、Flower（含 MoodState 枚举：cheerful/sad/restless/serene）、LunarPhase（含 new/waxing/full/waning 枚举 + 数值亮度因子）、HumiditySensor（含归一化湿度，越界抛 InvalidHumidityError）、GardenWorld 聚合实体列表与世界时钟。","src/garden/rules/lightDanceCoordinator.ts：实现 lightDanceCoordinator(world, tick) → DanceStep[]，规则=f(flower.mood, moon.phase, humidity) 计算 firefly 颜色/亮度/闪烁频率，处理 emptyFireflies、unknownMoonPhase、humidityOutOfRange 三条 edge case。","src/garden/seed/flowerSeedData.ts：导出 seedGarden() 返回示例花田与初始萤火虫群，覆盖至少 4 种 mood 与 4 种月相。","src/garden/entry/main.ts：CLI 入口，加载 seedGarden、推进 tick、输出 JSON 摘要，可被 npm start 调用。","index.html：含 <html>，引用 dist/main.js 编译产物并提供 <canvas id=\\"garden\\"> 渲染容器与启动按钮，调用主入口打印 DanceStep 摘要。","tests/garden/lightDanceCoordinator.test.ts：用 node --test 写 ≥2 条业务断言：normal 全月+serene 萤火虫颜色为冷色调且闪烁频率 ≤1Hz；boundary 湿度=0 仍产出有效 DanceStep；invalid 月相=invalid 抛错。","确保 min_prod_files≥3、prod_lines≥120、含 firefly/flower/moon/humidity 实际使用，代码逻辑而非纯注释。","落地 README.md：项目说明、依赖安装、构建/测试/启动命令、入口说明（CLI + index.html）、关键词 firefly/flower/moon/humidity 释义、目录结构与示例输出片段，禁止以 placeholder 或 manifest-only 形式交付。","核对 package.json scripts.start / scripts.build / scripts.test 全部指向真实 .ts / dist 入口与 node --test 测试文件，禁止 echo-only、version 探测或仅检查文件存在。"]},"set":{"goal":"依据 CE 蓝图无损实现发光昆虫花园模拟器：完成 src/garden 下的 firefly/flower/moon/humidity 领域模型与 lightDanceCoordinator 实时灯光舞蹈规则、Node CLI 入口、可视化 HTML 入口、示例种子数据、README.md、tsconfig 与 package.json 脚本；并落地 tests/garden 测试覆盖 normal/boundary/invalid 分支与 ≥2 条业务断言；严格禁止 placeholder / manifest-only 脚本。","scope_paths":["docs/blueprint/glowing-insect-garden.md","src/garden","tests/garden"],"target_files":["package.json","tsconfig.json","README.md","index.html","src/garden/domain/firefly.ts","src/garden/domain/flower.ts","src/garden/domain/lunarPhase.ts","src/garden/domain/humiditySensor.ts","src/garden/domain/gardenWorld.ts","src/garden/rules/lightDanceCoordinator.ts","src/garden/seed/flowerSeedData.ts","src/garden/entry/main.ts","tests/garden/lightDanceCoordinator.test.ts"],"title":"Director 实现：发光昆虫花园模拟器核心域、入口、README 与测试"},"task_id":"TASK-2"},{"append":{"steps":["审计 README.md：存在性、含 build/test/start/entrypoint 说明、含 firefly/flower/moon/humidity 关键词。","审计 package.json scripts：grep 检查 start/build/test 指向真实 .ts / dist / node --test 入口，不允许 echo / version 探测或仅检查 manifest。"]},"set":{"acceptance":["README.md 在工作区根存在且含 build / test / start / entrypoint 说明与关键词。","npm install 退出码 0。","npm run build 与 npx tsc --noEmit 退出码 0，dist/garden/entry/main.js 存在。","npm test 退出码 0，≥2 条业务断言通过，覆盖 normal/boundary/invalid。","npm start stdout 包含 firefly/flower/moon/humidity 字段的 DanceStep JSON 摘要。","src/**/*.ts 中 firefly/flower/moon/humidity 各 ≥1 处真实业务引用，prod_files≥3、prod_lines≥120。","index.html 含 <html> 根标签与 <canvas id=\\"garden\\"> 容器，引用编译后入口。","lightDanceCoordinator 实现体现 flower.mood × moon.phase × humidity 三因素函数，含 ≥3 规则、≥3 分支、≥2 edge case。","QA 拒绝 manifest-only / echo-only / 仅检查文件存在的脚本；确认 package.json scripts 实际驱动 tsc / node / node --test。","reports/qa/glowing-insect-garden.md 存在并明确给出 PASS/FAIL 结论与门禁矩阵、README 审计结果。"],"goal":"QA 独立运行门禁矩阵并审计业务深度：验证 TypeScript 产物结构（含 README.md）、入口可运行性、核心规则（firefly 灯光舞蹈 = f(flower.mood, moon.phase, humidity)）、关键词 firefly/flower/moon/humidity 真实业务出现，以及 normal/boundary/invalid 三类分支的测试覆盖；拒绝 manifest-only / placeholder 交付；产出 reports/qa/glowing-insect-garden.md 报告。","scope_paths":["package.json","tsconfig.json","README.md","index.html","src/garden","tests/garden","reports/qa"],"target_files":["reports/qa/glowing-insect-garden.md"],"title":"QA 闭环：发光昆虫花园模拟器门禁矩阵、业务深度与 README 审计"},"task_id":"TASK-3"}]},"base_hash":"a63fd2bf6b67ba53806578ee50bb616c55476508c8b05014eb7af4ae705a14e5"},"base":{"base":{"tasks":[{"acceptance":["docs/blueprint/glowing-insect-garden.md 存在，包含 ≥3 条行为规则、≥6 个领域符号（Firefly/Flower/MoonPhase/Humidity/LightDanceCoordinator/GardenWorld 等）、≥3 条分支、≥2 条 edge case（湿度越界、月相非法、萤火虫数量为 0）。","蓝图明确声明：firefly 的灯光舞蹈 = f(flower.mood, moon.phase, humidity)，关键词 firefly/flower/moon/humidity 全部出现于实体或规则命名/描述。","蓝图声明真实源码根（src/garden/**）与测试根（tests/garden/**），并指定 package.json/tsconfig.json 指向该源码根。","蓝图门禁矩阵列出 npm install、npm run build、tsc --noEmit、npm test、npm start 五条命令，argv 与 cwd 完整。","蓝图禁止空 manifest 占位脚本，要求 build/test/start 实际驱动产品入口或核心规则验证。"],"assigned_to":"ChiefEngineer","depends_on":[],"execution_backend":"code_edit","goal":"依据 Architect 蓝图，CE 命名发光昆虫花园模拟器的具体 TypeScript 模块拓扑、公开契约与门禁命令。模块必须真实承载 firefly（萤火虫）、flower（花朵情绪）、moon（月相）、humidity（湿度）四类核心实体与实时灯光舞蹈规则，覆盖 normal/boundary/invalid 三类分支；产出蓝图文档为后续 Director 实现与 QA 提供唯一权威路径。","id":"TASK-1","phase":"planning","scope_paths":["requirements.md","docs/blueprint"],"steps":["读取 requirements.md 与架构上下文，确认 simulation_toy / science_creative 域、TypeScript 主语言、关键词 firefly/flower/moon/humidity 不可丢失。","声明模块拓扑：src/garden/domain（GardenWorld、Flower、MoodState、Firefly、LunarPhase、HumiditySensor 实体）、src/garden/rules（lightDanceCoordinator 行为规则）、src/garden/seed（flowerSeedData 等示例/种子数据）、src/garden/entry（cli 入口 main.ts + 可选 index.html）、tests/garden/*.test.ts。","为每个模块写公开契约：实体字段类型、行为规则输入输出、可注入接口；明确 firefly.dancePattern 由 flower.mood × moon.phase × humidity 三因素函数决定，并写明至少 3 条行为规则、≥6 个领域符号、≥3 条分支、≥2 条边界/无效输入处理。","声明门禁命令：npm install、npm run build（tsc）、npm test、npm start、tsc --noEmit；写明每条命令的 argv 与 cwd。","明确 package.json scripts.start / scripts.build 指向真实入口与 tsc 编译，禁止 manifest-only 占位脚本；声明 index.html 作为可视化入口的等价 HTML 入口路径。","输出 docs/blueprint/glowing-insect-garden.md，包含产品行为规则、示例种子数据、模块清单、门禁命令矩阵、验收映射。"],"target_files":["docs/blueprint/glowing-insect-garden.md"],"title":"Chief Engineer 蓝图：发光昆虫花园模拟器模块拓扑与契约","verification_commands":[]},{"acceptance":["npm install 成功。","npm run build 成功，dist/garden/entry/main.js 存在。","tsc --noEmit 退出码 0。","npm test 至少 2 条业务断言通过，断言针对 lightDanceCoordinator 的输出值（颜色/频率/DanceStep 长度），不只检查文件存在。","npm start 真实运行 CLI 入口并打印 seedGarden 一次 tick 的 DanceStep JSON，包含 firefly/flower/moon/humidity 字段。","index.html 含 <html> 根标签与 <canvas id=\\"garden\\"> 容器，引用编译后入口。","src/**/*.ts 至少 3 个文件、合计 ≥120 行；firefly/flower/moon/humidity 至少各 1 处真实业务引用（非注释堆叠）。","package.json scripts.start/build/test 全部指向真实文件命令，无 shell 包装或 echo-only。"],"assigned_to":"Director","depends_on":["TASK-1"],"execution_backend":"code_edit","goal":"依据 CE 蓝图无损实现发光昆虫花园模拟器：完成 src/garden 下的 firefly/flower/moon/humidity 领域模型与 lightDanceCoordinator 实时灯光舞蹈规则、Node CLI 入口、可视化 HTML 入口、示例种子数据、tsconfig 与 package.json 脚本；并落地 tests/garden 测试覆盖 normal/boundary/invalid 分支与 ≥2 条业务断言。","id":"TASK-2","phase":"implementation","scope_paths":["docs/blueprint/glowing-insect-garden.md","src/garden","tests/garden"],"steps":["初始化 package.json：name=glowing-insect-garden、type=module、scripts.start 指向编译后入口、scripts.build=tsc、scripts.test 指向 node --test 运行 tests/garden；依赖 typescript 与 @types/node；写入 keywords 包含 firefly/flower/moon/humidity。","写 tsconfig.json：target=ES2022、module=NodeNext、moduleResolution=NodeNext、strict、outDir=dist、rootDir=src、include=[\\"src/**/*.ts\\"]、types=[\\"node\\"]。","src/garden/domain 定义 Firefly、Flower（含 MoodState 枚举：cheerful/sad/restless/serene）、LunarPhase（含 new/waxing/full/waning 枚举 + 数值亮度因子）、HumiditySensor（含归一化湿度，越界抛 InvalidHumidityError）、GardenWorld 聚合实体列表与世界时钟。","src/garden/rules/lightDanceCoordinator.ts：实现 lightDanceCoordinator(world, tick) → DanceStep[]，规则=f(flower.mood, moon.phase, humidity) 计算 firefly 颜色/亮度/闪烁频率，处理 emptyFireflies、unknownMoonPhase、humidityOutOfRange 三条 edge case。","src/garden/seed/flowerSeedData.ts：导出 seedGarden() 返回示例花田与初始萤火虫群，覆盖至少 4 种 mood 与 4 种月相。","src/garden/entry/main.ts：CLI 入口，加载 seedGarden、推进 tick、输出 JSON 摘要，可被 npm start 调用。","index.html：含 <html>，引用 dist/main.js 编译产物并提供 <canvas id=\\"garden\\"> 渲染容器与启动按钮，调用主入口打印 DanceStep 摘要。","tests/garden/lightDanceCoordinator.test.ts：用 node --test 写 ≥2 条业务断言：normal 全月+serene 萤火虫颜色为冷色调且闪烁频率 ≤1Hz；boundary 湿度=0 仍产出有效 DanceStep；invalid 月相=invalid 抛错。","确保 min_prod_files≥3、prod_lines≥120、含 firefly/flower/moon/humidity 实际使用，代码逻辑而非纯注释。"],"target_files":["package.json","tsconfig.json","index.html","src/garden/domain/firefly.ts","src/garden/domain/flower.ts","src/garden/domain/lunarPhase.ts","src/garden/domain/humiditySensor.ts","src/garden/domain/gardenWorld.ts","src/garden/rules/lightDanceCoordinator.ts","src/garden/seed/flowerSeedData.ts","src/garden/entry/main.ts","tests/garden/lightDanceCoordinator.test.ts"],"title":"Director 实现：发光昆虫花园模拟器核心域、入口与测试","verification_commands":[{"argv":["npm","install"],"cwd":".","modality":"environment_prep"},{"argv":["npm","run","build"],"cwd":".","modality":"build"},{"argv":["npx","tsc","--noEmit"],"cwd":".","modality":"build"},{"argv":["npm","test"],"cwd":".","modality":"test"},{"argv":["npm","start"],"cwd":".","modality":"entrypoint"}]},{"acceptance":["npm install 退出码 0。","npm run build 与 npx tsc --noEmit 退出码 0，dist/garden/entry/main.js 存在。","npm test 退出码 0，≥2 条业务断言通过，覆盖 normal/boundary/invalid。","npm start stdout 包含 firefly/flower/moon/humidity 字段的 DanceStep JSON 摘要。","src/**/*.ts 中 firefly/flower/moon/humidity 各 ≥1 处真实业务引用，prod_files≥3、prod_lines≥120。","index.html 含 <html> 根标签与 <canvas id=\\"garden\\"> 容器，引用编译后入口。","lightDanceCoordinator 实现体现 flower.mood × moon.phase × humidity 三因素函数，含 ≥3 规则、≥3 分支、≥2 edge case。","reports/qa/glowing-insect-garden.md 存在并明确给出 PASS/FAIL 结论与门禁矩阵。"],"assigned_to":"QA","depends_on":["TASK-2"],"execution_backend":"code_edit","goal":"QA 独立运行门禁矩阵并审计业务深度：验证 TypeScript 产物结构、入口可运行性、核心规则（firefly 灯光舞蹈 = f(flower.mood, moon.phase, humidity)）、关键词 firefly/flower/moon/humidity 真实业务出现，以及 normal/boundary/invalid 三类分支的测试覆盖；产出 reports/qa/glowing-insect-garden.md 报告。","id":"TASK-3","phase":"qa","scope_paths":["package.json","tsconfig.json","index.html","src/garden","tests/garden","reports/qa"],"steps":["执行环境准备：npm install。","执行 build：npm run build 与 npx tsc --noEmit，确认 dist/garden/entry/main.js 存在且无类型错误。","执行 test：npm test，收集断言数与通过情况，确认 ≥2 条业务断言且覆盖 normal/boundary/invalid。","执行 entrypoint：npm start，捕获 stdout 包含 DanceStep JSON 且字段含 firefly/flower/moon/humidity。","审计源码：grep 验证 firefly/flower/moon/humidity 在 src/**/*.ts 中为真实业务引用（非仅注释/字符串堆叠），统计 prod_files≥3、prod_lines≥120。","审计 index.html：含 <html> 与 <canvas id=\\"garden\\">，引用编译后入口。","审计行为规则：确认 lightDanceCoordinator 中体现 flower.mood × moon.phase × humidity 三因素函数，且包含 ≥3 条规则、≥3 分支、≥2 edge case。","输出 reports/qa/glowing-insect-garden.md 报告：门禁矩阵结果、断言摘要、入口 stdout 摘要、关键词审计表、行为规则摘要、结论 PASS/FAIL 与残留风险。"],"target_files":["reports/qa/glowing-insect-garden.md"],"title":"QA 闭环：发光昆虫花园模拟器门禁矩阵与业务深度审计","verification_commands":[{"argv":["npm","install"],"cwd":".","modality":"environment_prep"},{"argv":["npm","run","build"],"cwd":".","modality":"build"},{"argv":["npx","tsc","--noEmit"],"cwd":".","modality":"build"},{"argv":["npm","test"],"cwd":".","modality":"test"},{"argv":["npm","start"],"cwd":".","modality":"entrypoint"}]}]},"base_hash":"a63fd2bf6b67ba53806578ee50bb616c55476508c8b05014eb7af4ae705a14e5","execution_qualified":false,"identity":{"directive_hash":"8370843edc56048b0fac19374b2d1c1fdfead39f8149bb1b1b1bafe52c42ecfb","original_response_hash":"15d8f3d86540fa15d5c1a2fbe2930125dced9f8ff8c7198736aa210c51ad9bb2","projection_hint":{},"run_id":"pm-b0badd9468f1","task_id":"task-0-pm","workspace":"/home/dains/.polaris/bench/workspaces/l1-01-20261012T092438Z-r20/workspaces/24abacc3c1c8-21167a249775ac40/L1-01-7bb25f6bf0f37baf/86b01a6f16b88dd981b22e81"},"original_response":"{\\"tasks\\":[{\\"id\\":\\"TASK-1\\",\\"title\\":\\"Chief Engineer 蓝图：发光昆虫花园模拟器模块拓扑与契约\\",\\"goal\\":\\"依据 Architect 蓝图，CE 命名发光昆虫花园模拟器的具体 TypeScript 模块拓扑、公开契约与门禁命令。模块必须真实承载 firefly（萤火虫）、flower（花朵情绪）、moon（月相）、humidity（湿度）四类核心实体与实时灯光舞蹈规则，覆盖 normal/boundary/invalid 三类分支；产出蓝图文档为后续 Director 实现与 QA 提供唯一权威路径。\\",\\"scope_paths\\":[\\"requirements.md\\",\\"docs/blueprint\\"],\\"target_files\\":[\\"docs/blueprint/glowing-insect-garden.md\\"],\\"steps\\":[\\"读取 requirements.md 与架构上下文，确认 simulation_toy / science_creative 域、TypeScript 主语言、关键词 firefly/flower/moon/humidity 不可丢失。\\",\\"声明模块拓扑：src/garden/domain（GardenWorld、Flower、MoodState、Firefly、LunarPhase、HumiditySensor 实体）、src/garden/rules（lightDanceCoordinator 行为规则）、src/garden/seed（flowerSeedData 等示例/种子数据）、src/garden/entry（cli 入口 main.ts + 可选 index.html）、tests/garden/*.test.ts。\\",\\"为每个模块写公开契约：实体字段类型、行为规则输入输出、可注入接口；明确 firefly.dancePattern 由 flower.mood × moon.phase × humidity 三因素函数决定，并写明至少 3 条行为规则、≥6 个领域符号、≥3 条分支、≥2 条边界/无效输入处理。\\",\\"声明门禁命令：npm install、npm run build（tsc）、npm test、npm start、tsc --noEmit；写明每条命令的 argv 与 cwd。\\",\\"明确 package.json scripts.start / scripts.build 指向真实入口与 tsc 编译，禁止 manifest-only 占位脚本；声明 index.html 作为可视化入口的等价 HTML 入口路径。\\",\\"输出 docs/blueprint/glowing-insect-garden.md，包含产品行为规则、示例种子数据、模块清单、门禁命令矩阵、验收映射。\\"],\\"acceptance\\":[\\"docs/blueprint/glowing-insect-garden.md 存在，包含 ≥3 条行为规则、≥6 个领域符号（Firefly/Flower/MoonPhase/Humidity/LightDanceCoordinator/GardenWorld 等）、≥3 条分支、≥2 条 edge case（湿度越界、月相非法、萤火虫数量为 0）。\\",\\"蓝图明确声明：firefly 的灯光舞蹈 = f(flower.mood, moon.phase, humidity)，关键词 firefly/flower/moon/humidity 全部出现于实体或规则命名/描述。\\",\\"蓝图声明真实源码根（src/garden/**）与测试根（tests/garden/**），并指定 package.json/tsconfig.json 指向该源码根。\\",\\"蓝图门禁矩阵列出 npm install、npm run build、tsc --noEmit、npm test、npm start 五条命令，argv 与 cwd 完整。\\",\\"蓝图禁止空 manifest 占位脚本，要求 build/test/start 实际驱动产品入口或核心规则验证。\\"],\\"verification_commands\\":[],\\"phase\\":\\"planning\\",\\"depends_on\\":[],\\"assigned_to\\":\\"ChiefEngineer\\",\\"execution_backend\\":\\"code_edit\\"},{\\"id\\":\\"TASK-2\\",\\"title\\":\\"Director 实现：发光昆虫花园模拟器核心域、入口与测试\\",\\"goal\\":\\"依据 CE 蓝图无损实现发光昆虫花园模拟器：完成 src/garden 下的 firefly/flower/moon/humidity 领域模型与 lightDanceCoordinator 实时灯光舞蹈规则、Node CLI 入口、可视化 HTML 入口、示例种子数据、tsconfig 与 package.json 脚本；并落地 tests/garden 测试覆盖 normal/boundary/invalid 分支与 ≥2 条业务断言。\\",\\"scope_paths\\":[\\"docs/blueprint/glowing-insect-garden.md\\",\\"src/garden\\",\\"tests/garden\\"],\\"target_files\\":[\\"package.json\\",\\"tsconfig.json\\",\\"index.html\\",\\"src/garden/domain/firefly.ts\\",\\"src/garden/domain/flower.ts\\",\\"src/garden/domain/lunarPhase.ts\\",\\"src/garden/domain/humiditySensor.ts\\",\\"src/garden/domain/gardenWorld.ts\\",\\"src/garden/rules/lightDanceCoordinator.ts\\",\\"src/garden/seed/flowerSeedData.ts\\",\\"src/garden/entry/main.ts\\",\\"tests/garden/lightDanceCoordinator.test.ts\\"],\\"steps\\":[\\"初始化 package.json：name=glowing-insect-garden、type=module、scripts.start 指向编译后入口、scripts.build=tsc、scripts.test 指向 node --test 运行 tests/garden；依赖 typescript 与 @types/node；写入 keywords 包含 firefly/flower/moon/humidity。\\",\\"写 tsconfig.json：target=ES2022、module=NodeNext、moduleResolution=NodeNext、strict、outDir=dist、rootDir=src、include=[\\\\\\"src/**/*.ts\\\\\\"]、types=[\\\\\\"node\\\\\\"]。\\",\\"src/garden/domain 定义 Firefly、Flower（含 MoodState 枚举：cheerful/sad/restless/serene）、LunarPhase（含 new/waxing/full/waning 枚举 + 数值亮度因子）、HumiditySensor（含归一化湿度，越界抛 InvalidHumidityError）、GardenWorld 聚合实体列表与世界时钟。\\",\\"src/garden/rules/lightDanceCoordinator.ts：实现 lightDanceCoordinator(world, tick) → DanceStep[]，规则=f(flower.mood, moon.phase, humidity) 计算 firefly 颜色/亮度/闪烁频率，处理 emptyFireflies、unknownMoonPhase、humidityOutOfRange 三条 edge case。\\",\\"src/garden/seed/flowerSeedData.ts：导出 seedGarden() 返回示例花田与初始萤火虫群，覆盖至少 4 种 mood 与 4 种月相。\\",\\"src/garden/entry/main.ts：CLI 入口，加载 seedGarden、推进 tick、输出 JSON 摘要，可被 npm start 调用。\\",\\"index.html：含 <html>，引用 dist/main.js 编译产物并提供 <canvas id=\\\\\\"garden\\\\\\"> 渲染容器与启动按钮，调用主入口打印 DanceStep 摘要。\\",\\"tests/garden/lightDanceCoordinator.test.ts：用 node --test 写 ≥2 条业务断言：normal 全月+serene 萤火虫颜色为冷色调且闪烁频率 ≤1Hz；boundary 湿度=0 仍产出有效 DanceStep；invalid 月相=invalid 抛错。\\",\\"确保 min_prod_files≥3、prod_lines≥120、含 firefly/flower/moon/humidity 实际使用，代码逻辑而非纯注释。\\"],\\"acceptance\\":[\\"npm install 成功。\\",\\"npm run build 成功，dist/garden/entry/main.js 存在。\\",\\"tsc --noEmit 退出码 0。\\",\\"npm test 至少 2 条业务断言通过，断言针对 lightDanceCoordinator 的输出值（颜色/频率/DanceStep 长度），不只检查文件存在。\\",\\"npm start 真实运行 CLI 入口并打印 seedGarden 一次 tick 的 DanceStep JSON，包含 firefly/flower/moon/humidity 字段。\\",\\"index.html 含 <html> 根标签与 <canvas id=\\\\\\"garden\\\\\\"> 容器，引用编译后入口。\\",\\"src/**/*.ts 至少 3 个文件、合计 ≥120 行；firefly/flower/moon/humidity 至少各 1 处真实业务引用（非注释堆叠）。\\",\\"package.json scripts.start/build/test 全部指向真实文件命令，无 shell 包装或 echo-only。\\"],\\"verification_commands\\":[{\\"modality\\":\\"environment_prep\\",\\"argv\\":[\\"npm\\",\\"install\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"build\\",\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"build\\",\\"argv\\":[\\"npx\\",\\"tsc\\",\\"--noEmit\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"test\\",\\"argv\\":[\\"npm\\",\\"test\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"entrypoint\\",\\"argv\\":[\\"npm\\",\\"start\\"],\\"cwd\\":\\".\\"}],\\"phase\\":\\"implementation\\",\\"depends_on\\":[\\"TASK-1\\"],\\"assigned_to\\":\\"Director\\",\\"execution_backend\\":\\"code_edit\\"},{\\"id\\":\\"TASK-3\\",\\"title\\":\\"QA 闭环：发光昆虫花园模拟器门禁矩阵与业务深度审计\\",\\"goal\\":\\"QA 独立运行门禁矩阵并审计业务深度：验证 TypeScript 产物结构、入口可运行性、核心规则（firefly 灯光舞蹈 = f(flower.mood, moon.phase, humidity)）、关键词 firefly/flower/moon/humidity 真实业务出现，以及 normal/boundary/invalid 三类分支的测试覆盖；产出 reports/qa/glowing-insect-garden.md 报告。\\",\\"scope_paths\\":[\\"package.json\\",\\"tsconfig.json\\",\\"index.html\\",\\"src/garden\\",\\"tests/garden\\",\\"reports/qa\\"],\\"target_files\\":[\\"reports/qa/glowing-insect-garden.md\\"],\\"steps\\":[\\"执行环境准备：npm install。\\",\\"执行 build：npm run build 与 npx tsc --noEmit，确认 dist/garden/entry/main.js 存在且无类型错误。\\",\\"执行 test：npm test，收集断言数与通过情况，确认 ≥2 条业务断言且覆盖 normal/boundary/invalid。\\",\\"执行 entrypoint：npm start，捕获 stdout 包含 DanceStep JSON 且字段含 firefly/flower/moon/humidity。\\",\\"审计源码：grep 验证 firefly/flower/moon/humidity 在 src/**/*.ts 中为真实业务引用（非仅注释/字符串堆叠），统计 prod_files≥3、prod_lines≥120。\\",\\"审计 index.html：含 <html> 与 <canvas id=\\\\\\"garden\\\\\\">，引用编译后入口。\\",\\"审计行为规则：确认 lightDanceCoordinator 中体现 flower.mood × moon.phase × humidity 三因素函数，且包含 ≥3 条规则、≥3 分支、≥2 edge case。\\",\\"输出 reports/qa/glowing-insect-garden.md 报告：门禁矩阵结果、断言摘要、入口 stdout 摘要、关键词审计表、行为规则摘要、结论 PASS/FAIL 与残留风险。\\"],\\"acceptance\\":[\\"npm install 退出码 0。\\",\\"npm run build 与 npx tsc --noEmit 退出码 0，dist/garden/entry/main.js 存在。\\",\\"npm test 退出码 0，≥2 条业务断言通过，覆盖 normal/boundary/invalid。\\",\\"npm start stdout 包含 firefly/flower/moon/humidity 字段的 DanceStep JSON 摘要。\\",\\"src/**/*.ts 中 firefly/flower/moon/humidity 各 ≥1 处真实业务引用，prod_files≥3、prod_lines≥120。\\",\\"index.html 含 <html> 根标签与 <canvas id=\\\\\\"garden\\\\\\"> 容器，引用编译后入口。\\",\\"lightDanceCoordinator 实现体现 flower.mood × moon.phase × humidity 三因素函数，含 ≥3 规则、≥3 分支、≥2 edge case。\\",\\"reports/qa/glowing-insect-garden.md 存在并明确给出 PASS/FAIL 结论与门禁矩阵。\\"],\\"verification_commands\\":[{\\"modality\\":\\"environment_prep\\",\\"argv\\":[\\"npm\\",\\"install\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"build\\",\\"argv\\":[\\"npm\\",\\"run\\",\\"build\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"build\\",\\"argv\\":[\\"npx\\",\\"tsc\\",\\"--noEmit\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"test\\",\\"argv\\":[\\"npm\\",\\"test\\"],\\"cwd\\":\\".\\"},{\\"modality\\":\\"entrypoint\\",\\"argv\\":[\\"npm\\",\\"start\\"],\\"cwd\\":\\".\\"}],\\"phase\\":\\"qa\\",\\"depends_on\\":[\\"TASK-2\\"],\\"assigned_to\\":\\"QA\\",\\"execution_backend\\":\\"code_edit\\"}]}","original_response_hash":"15d8f3d86540fa15d5c1a2fbe2930125dced9f8ff8c7198736aa210c51ad9bb2","schema_version":"pm.task_revision_base.v1","view":{"base_hash":"a63fd2bf6b67ba53806578ee50bb616c55476508c8b05014eb7af4ae705a14e5","edit_manifest":{"remove_task_ids":[],"tasks":{"TASK-1":{"append":{"acceptance":"nonempty_string_array","steps":"nonempty_string_array"},"set":{"acceptance":"nonempty_string_array","dependencies":"string_array","depends_on":"string_array","goal":"nonempty_string","phase":"nonempty_string","scope_paths":"string_array","target_files":"string_array","title":"nonempty_string","verification_commands":"verifier_array"}},"TASK-2":{"append":{"acceptance":"nonempty_string_array","steps":"nonempty_string_array"},"set":{"dependencies":"string_array","depends_on":"string_array","goal":"nonempty_string","phase":"nonempty_string","scope_paths":"string_array","target_files":"string_array","title":"nonempty_string","verification_commands":"verifier_array"}},"TASK-3":{"append":{"acceptance":"nonempty_string_array","steps":"nonempty_string_array"},"set":{"dependencies":"string_array","depends_on":"string_array","goal":"nonempty_string","phase":"nonempty_string","scope_paths":"string_array","target_files":"string_array","title":"nonempty_string","verification_commands":"verifier_array"}}}},"tasks":[{"absent_fields":["dependencies"],"acceptance":["docs/blueprint/glowing-insect-garden.md 存在，包含 ≥3 条行为规则、≥6 个领域符号（Firefly/Flower/MoonPhase/Humidity/LightDanceCoordinator/GardenWorld 等）、≥3 条分支、≥2 条 edge case（湿度越界、月相非法、萤火虫数量为 0）。","蓝图明确声明：firefly 的灯光舞蹈 = f(flower.mood, moon.phase, humidity)，关键词 firefly/flower/moon/humidity 全部出现于实体或规则命名/描述。","蓝图声明真实源码根（src/garden/**）与测试根（tests/garden/**），并指定 package.json/tsconfig.json 指向该源码根。","蓝图门禁矩阵列出 npm install、npm run build、tsc --noEmit、npm test、npm start 五条命令，argv 与 cwd 完整。","蓝图禁止空 manifest 占位脚本，要求 build/test/start 实际驱动产品入口或核心规则验证。"],"assigned_to":"ChiefEngineer","depends_on":[],"execution_backend":"code_edit","goal":"依据 Architect 蓝图，CE 命名发光昆虫花园模拟器的具体 TypeScript 模块拓扑、公开契约与门禁命令。模块必须真实承载 firefly（萤火虫）、flower（花朵情绪）、moon（月相）、humidity（湿度）四类核心实体与实时灯光舞蹈规则，覆盖 normal/boundary/invalid 三类分支；产出蓝图文档为后续 Director 实现与 QA 提供唯一权威路径。","id":"TASK-1","phase":"planning","scope_paths":["requirements.md","docs/blueprint"],"target_files":["docs/blueprint/glowing-insect-garden.md"],"title":"Chief Engineer 蓝图：发光昆虫花园模拟器模块拓扑与契约","verification_commands":[]},{"absent_fields":["dependencies"],"assigned_to":"Director","depends_on":["TASK-1"],"execution_backend":"code_edit","goal":"依据 CE 蓝图无损实现发光昆虫花园模拟器：完成 src/garden 下的 firefly/flower/moon/humidity 领域模型与 lightDanceCoordinator 实时灯光舞蹈规则、Node CLI 入口、可视化 HTML 入口、示例种子数据、tsconfig 与 package.json 脚本；并落地 tests/garden 测试覆盖 normal/boundary/invalid 分支与 ≥2 条业务断言。","id":"TASK-2","phase":"implementation","scope_paths":["docs/blueprint/glowing-insect-garden.md","src/garden","tests/garden"],"target_files":["package.json","tsconfig.json","index.html","src/garden/domain/firefly.ts","src/garden/domain/flower.ts","src/garden/domain/lunarPhase.ts","src/garden/domain/humiditySensor.ts","src/garden/domain/gardenWorld.ts","src/garden/rules/lightDanceCoordinator.ts","src/garden/seed/flowerSeedData.ts","src/garden/entry/main.ts","tests/garden/lightDanceCoordinator.test.ts"],"title":"Director 实现：发光昆虫花园模拟器核心域、入口与测试","verification_commands":[{"argv":["npm","install"],"cwd":".","modality":"environment_prep"},{"argv":["npm","run","build"],"cwd":".","modality":"build"},{"argv":["npx","tsc","--noEmit"],"cwd":".","modality":"build"},{"argv":["npm","test"],"cwd":".","modality":"test"},{"argv":["npm","start"],"cwd":".","modality":"entrypoint"}]},{"absent_fields":["dependencies"],"assigned_to":"QA","depends_on":["TASK-2"],"execution_backend":"code_edit","goal":"QA 独立运行门禁矩阵并审计业务深度：验证 TypeScript 产物结构、入口可运行性、核心规则（firefly 灯光舞蹈 = f(flower.mood, moon.phase, humidity)）、关键词 firefly/flower/moon/humidity 真实业务出现，以及 normal/boundary/invalid 三类分支的测试覆盖；产出 reports/qa/glowing-insect-garden.md 报告。","id":"TASK-3","phase":"qa","scope_paths":["package.json","tsconfig.json","index.html","src/garden","tests/garden","reports/qa"],"target_files":["reports/qa/glowing-insect-garden.md"],"title":"QA 闭环：发光昆虫花园模拟器门禁矩阵与业务深度审计","verification_commands":[{"argv":["npm","install"],"cwd":".","modality":"environment_prep"},{"argv":["npm","run","build"],"cwd":".","modality":"build"},{"argv":["npx","tsc","--noEmit"],"cwd":".","modality":"build"},{"argv":["npm","test"],"cwd":".","modality":"test"},{"argv":["npm","start"],"cwd":".","modality":"entrypoint"}]}]}},"quality":{"contract_diagnostics":[],"critical_issues":["TASK-1: acceptance permits placeholder or manifest-only execution","factory_typescript_contract_missing:README.md"],"warnings":["factory TypeScript/npm directive requires package, src, engine, test, and README targets"]}}'
)


@pytest.fixture(autouse=True)
def _workspace_streams(tmp_path: Path, _isolated_external_delivery: None) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="pm_local_continuation_component_regression",
        )
    )


def _initial() -> dict[str, Any]:
    candidate = _candidate()
    candidate["tasks"][1]["verification_commands"] = candidate["tasks"][1]["verification_commands"][1:]
    return candidate


def _proposal(prompt: str, *, corrected: bool = False, unsafe: bool = False) -> str:
    view = json.JSONDecoder().raw_decode(prompt.split("Revision base/view/manifest (server-owned):\n", 1)[1])[0]
    updates: list[dict[str, Any]] = [
        {
            "task_id": "TASK-IMPLEMENT",
            "set": {"verification_commands": _candidate()["tasks"][1]["verification_commands"]},
        },
        {
            "task_id": "TASK-QA",
            ("append" if corrected else "set"): {
                "acceptance": ["QA_REPORT.md includes all actual build, test and CLI exit codes"]
            },
        },
    ]
    if unsafe:
        updates[0]["set"]["scope_paths"] = ["../outside"]
    return json.dumps(
        {
            "schema_version": "pm.task_revision.v1",
            "base_hash": view["base_hash"],
            "updates": updates,
            "add_tasks": [],
            "remove_task_ids": [],
        }
    )


def _context() -> dict[str, Any]:
    return {
        "run_id": "pm-local-child",
        "metadata": {
            "llm_max_tokens": 60000,
            "llm_call_timeout_seconds": 750,
            "pm_planning_deadline_epoch_seconds": 9999999999.0,
        },
    }


async def test_pm_diagnosed_field_correction_continues_same_stage_and_original_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    calls: list[tuple[str, dict[str, Any]]] = []
    responses: list[str] = []
    context = _context()
    original_context = copy.deepcopy(context)

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append((message, copy.deepcopy(context)))
        response = json.dumps(_initial()) if len(calls) == 1 else _proposal(message, corrected=len(calls) == 3)
        responses.append(response)
        return {"response": response, "metadata": {}}

    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    result = await adapter._run_pm_stage("pm-task", "Create a TypeScript CLI", {}, context)
    assert result["success"] is True
    assert result["quality_gate"]["score"] >= 80
    assert len(calls) == 3
    assert context == original_context
    assert {call[1]["run_id"] for call in calls} == {"pm-local-child"}
    assert {call[1]["llm_max_tokens"] for call in calls} == {60000}
    assert all(0 < call[1]["llm_call_timeout_seconds"] <= 750 for call in calls)
    views = [
        json.JSONDecoder().raw_decode(prompt.split("Revision base/view/manifest (server-owned):\n", 1)[1])[0]
        for prompt, _ in calls[1:]
    ]
    assert views[0] == views[1]
    assert "TASK-QA" in calls[2][0]
    assert "$.updates[1].set.acceptance" in calls[2][0]
    assert responses[1] in calls[2][0]
    paths = sorted((tmp_path / ".polaris/runtime/signals/pm_revision").glob("*.json"))
    evidence = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    bases = [row for row in evidence if row["schema_version"] == "pm.task_revision_base.v1"]
    derivations = [row for row in evidence if row["schema_version"] == "pm.task_revision_derivation.v1"]
    assert len(bases) == 1
    assert len(derivations) == 2
    assert all(row["base_hash"] == bases[0]["base_hash"] for row in derivations)
    assert all(row["original_response"] == responses[0] for row in derivations)
    assert any(row["revision_response"] == responses[1] and row["raw_merged_candidate"] is None for row in derivations)
    successful = next(row for row in derivations if row["quality"]["ok"] is True)
    by_id = {task["id"]: task for task in successful["raw_merged_candidate"]["tasks"]}
    assert by_id["TASK-IMPLEMENT"]["verification_commands"] == _candidate()["tasks"][1]["verification_commands"]
    assert by_id["TASK-QA"]["verification_commands"] == _candidate()["tasks"][2]["verification_commands"]
    assert by_id["TASK-IMPLEMENT"]["scope_paths"] == _candidate()["tasks"][1]["scope_paths"]


async def test_pm_repeated_canonical_proposal_stops_without_consuming_another_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    calls: list[str] = []
    bad = [""]

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        if len(calls) == 1:
            return {"response": json.dumps(_initial()), "metadata": {}}
        if len(calls) == 2:
            bad[0] = _proposal(message)
        return {"response": json.dumps(json.loads(bad[0]), indent=2), "metadata": {}}

    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    result = await adapter._run_pm_stage("pm-task", "Create a TypeScript CLI", {}, _context())
    assert result["success"] is False
    assert len(calls) == 3
    assert any(row["code"] == "pm.contracts.revision_no_progress" for row in result["quality_gate"]["signals"])


@pytest.mark.parametrize("error", ["provider_billing_exhausted", "factory_physical_attempt_budget_exhausted"])
async def test_pm_continuation_preserves_provider_or_physical_admission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    calls: list[str] = []

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        if len(calls) == 1:
            return {"response": json.dumps(_initial()), "metadata": {}}
        if len(calls) == 2:
            return {"response": _proposal(message), "metadata": {}}
        raise RuntimeError(error)

    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    result = await adapter._run_pm_stage("pm-task", "Create a TypeScript CLI", {}, _context())
    assert result["success"] is False
    assert len(calls) == 3
    assert result["error"] == error


async def test_pm_unsafe_scope_is_terminal_without_attempting_a_fresh_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    calls: list[str] = []

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        return {
            "response": json.dumps(_initial()) if len(calls) == 1 else _proposal(message, unsafe=True),
            "metadata": {},
        }

    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    result = await adapter._run_pm_stage("pm-task", "Create a TypeScript CLI", {}, _context())
    assert result["success"] is False
    assert len(calls) == 2
    assert any(
        d.get("repairable") is False
        for row in result["quality_gate"]["signals"]
        for d in row.get("contract_diagnostics", [])
    )


async def test_pm_local_continuation_cannot_extend_original_absolute_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.adapters.internal import pm_adapter as module

    now = [10000.0]
    monkeypatch.setattr(module, "time", type("Clock", (), {"time": staticmethod(lambda: now[0])})())
    adapter = PMAdapter(workspace=str(tmp_path))
    calls: list[str] = []
    context = _context()
    context["metadata"]["pm_planning_deadline_epoch_seconds"] = 11530.0

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        if len(calls) == 1:
            return {"response": json.dumps(_initial()), "metadata": {}}
        now[0] = 11000.0
        return {"response": _proposal(message), "metadata": {}}

    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    result = await adapter._run_pm_stage("pm-task", "Create a TypeScript CLI", {}, context)
    assert result["success"] is False
    assert len(calls) == 2
    assert "pm_planning_budget_deadline_exhausted:remaining=500,required=750" in result["error"]
    assert context["metadata"]["pm_planning_deadline_epoch_seconds"] == 11530.0


async def test_real_r20_retained_proposal_replay_uses_second_authorized_model_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.adapters.internal import pm_adapter as module
    from polaris.cells.roles.adapters.internal.pm import revision

    packet = copy.deepcopy(_R20_REPLAY["packet"])
    retained = copy.deepcopy(_R20_REPLAY["base"])
    historical_quality = copy.deepcopy(_R20_REPLAY["quality"])
    base = revision.PMRevisionBase(
        retained["original_response"],
        revision.canonical_json(retained["base"]["tasks"]),
        revision.canonical_json(retained["identity"]),
        revision.canonical_json(retained["view"]),
        retained["base_hash"],
    )
    assert revision.content_hash(base.response) == packet["original_response_hash"]
    assert revision.content_hash(packet["revision_response"]) == packet["revision_response_hash"]
    # Third response is an explicit controlled model fixture, never a real provider claim.
    authorized_proposal = copy.deepcopy(packet["revision"])
    qa_update = next(update for update in authorized_proposal["updates"] if update["task_id"] == "TASK-3")
    criteria = qa_update["set"].pop("acceptance")
    qa_update.setdefault("append", {})["acceptance"] = criteria
    responses = [base.response, packet["revision_response"], revision.canonical_json(authorized_proposal)]
    calls: list[str] = []
    adapter = PMAdapter(workspace=str(tmp_path))
    evaluate = adapter._evaluate_contract_quality
    quality_calls = [0]

    def replay_first_historical_quality(*args: Any, **kwargs: Any) -> Any:
        normalized, quality = evaluate(*args, **kwargs)
        quality_calls[0] += 1
        if quality_calls[0] == 1:
            quality = {
                **historical_quality,
                "ok": False,
                "score": 0,
                "summary": "Retained original r20 program-generated quality feedback",
            }
        return normalized, quality

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        return {"response": responses[len(calls) - 1], "metadata": {}}

    # Historical base/quality are retained evidence; all stage persistence and task
    # effects execute in pytest's home sandbox, never the old target workspace.
    monkeypatch.setattr(module, "make_revision_base", lambda *args, **kwargs: base)
    monkeypatch.setattr(adapter, "_evaluate_contract_quality", replay_first_historical_quality)
    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    context = _context()
    context["run_id"] = base.identity["run_id"]
    result = await adapter._run_pm_stage(base.identity["task_id"], "Create a TypeScript CLI", {}, context)
    assert result["success"] is True
    assert result["quality_gate"]["score"] >= 80
    assert len(calls) == 3
    assert packet["revision_response"] in calls[2]
    assert "$.updates[2].set.acceptance" in calls[2]
    evidence = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / ".polaris/runtime/signals/pm_revision").glob("*.json")
    ]
    rows = [row for row in evidence if row["schema_version"] == "pm.task_revision_derivation.v1"]
    assert len(rows) == 2
    assert all(row["base_hash"] == packet["base_hash"] for row in rows)
    assert all(row["original_response_hash"] == packet["original_response_hash"] for row in rows)
    assert any(
        row["revision_response_hash"] == packet["revision_response_hash"] and row["raw_merged_candidate"] is None
        for row in rows
    )
    good = next(row for row in rows if row["quality"]["ok"] is True)
    assert good["revision"] == authorized_proposal
    assert good["merged_candidate_is_original_provider_response"] is False
    for actual, original in zip(good["raw_merged_candidate"]["tasks"], base.tasks, strict=True):
        assert actual["verification_commands"] == original["verification_commands"]
    assert base.tasks == retained["base"]["tasks"]


async def test_pm_changed_rejected_prose_is_not_planning_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    calls: list[str] = []

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        if len(calls) == 1:
            return {"response": json.dumps(_initial()), "metadata": {}}
        proposal = json.loads(_proposal(message))
        proposal["updates"][1]["set"]["acceptance"] = [
            f"QA_REPORT.md retains actual test results, revision {len(calls)}"
        ]
        return {"response": json.dumps(proposal), "metadata": {}}

    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    result = await adapter._run_pm_stage("pm-task", "Create a TypeScript CLI", {}, _context())
    assert result["success"] is False
    assert len(calls) == 3
    assert any(row["code"] == "pm.contracts.revision_no_progress" for row in result["quality_gate"]["signals"])


async def test_pm_standalone_loop_anchors_existing_stage_budget_before_initial_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.adapters.internal import pm_adapter as module

    now = [10000.0]
    monkeypatch.setattr(module, "time", type("Clock", (), {"time": staticmethod(lambda: now[0])})())
    adapter = PMAdapter(workspace=str(tmp_path))
    calls: list[str] = []

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        if len(calls) == 1:
            now[0] = 10200.0
            return {"response": json.dumps(_initial()), "metadata": {}}
        now[0] = 11100.0
        return {"response": _proposal(message), "metadata": {}}

    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    result = await adapter._run_pm_stage(
        "pm-task",
        "Create a TypeScript CLI",
        {},
        {"run_id": "pm-local-child", "metadata": {"llm_max_tokens": 60000, "llm_call_timeout_seconds": 750}},
    )
    assert result["success"] is False
    assert len(calls) == 2
    assert "pm_planning_budget_deadline_exhausted:remaining=400,required=750" in result["error"]


async def test_pm_legal_title_change_with_unchanged_verifier_diagnosis_is_not_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    calls: list[str] = []

    async def provider(message: str, *, context: dict[str, Any]) -> dict[str, Any]:
        calls.append(message)
        if len(calls) == 1:
            return {"response": json.dumps(_initial()), "metadata": {}}
        if len(calls) > 4:
            raise RuntimeError("Unrelated prose changes incorrectly prolonged planning")
        view = json.JSONDecoder().raw_decode(message.split("Revision base/view/manifest (server-owned):\n", 1)[1])[0]
        return {
            "response": json.dumps(
                {
                    "schema_version": "pm.task_revision.v1",
                    "base_hash": view["base_hash"],
                    "updates": [{"task_id": "TASK-IMPLEMENT", "set": {"title": f"Implementation title {len(calls)}"}}],
                    "add_tasks": [],
                    "remove_task_ids": [],
                }
            ),
            "metadata": {},
        }

    monkeypatch.setattr(adapter, "_call_role_llm", provider)
    result = await adapter._run_pm_stage("pm-task", "Create a TypeScript CLI", {}, _context())
    assert result["success"] is False
    assert len(calls) == 2
    assert any(row["code"] == "pm.contracts.revision_no_progress" for row in result["quality_gate"]["signals"])

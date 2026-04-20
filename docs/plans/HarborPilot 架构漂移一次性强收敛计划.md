Polaris 架构漂移一次性强收敛计划（全量 V2 + 双 CLI 主入口并存）
摘要
本计划按你锁定的策略执行：全量 V2、一次性强收敛、CLI 双主入口并存、旧 API 立即切断。
目标是把当前“多实现并行”收敛为“单实现、多入口壳层”，并在同一轮完成前后端、测试、文档、守护规则的同步切换。

当前已核实的关键事实（用于排定优先级）：

src/backend 内 sys.path.insert/append 共 87 处。
pm_tools.py 无上游导入，且自身存在未导入 sys 的潜在错误。
workflow_nodes_compat.py 仅被 test_workflow_chain.py 引用。
director_tools_v2.py 仅被 director_llm_tools.py 引用，而后者无上游调用。
CLAUDE.md 与 pyproject.toml 均引用不存在的 main.py。
运行态存在三层 API 形态并存：/api/v1/*、/pm|/director/*、/v2/*。
锁定决策（已定稿）
API 目标：全量 V2（PM 与 Director 统一进入 /v2/*）。
API 兼容：旧 /pm/* 与 /director/* 立即切断，不保留兼容窗口。
CLI 策略：双主入口并存，但必须共享同一核心实现，禁止再次分叉。
公共 API / 接口 / 类型变更
新增 PM V2 控制面接口：
POST /v2/pm/start
POST /v2/pm/run-once
GET /v2/pm/status
POST /v2/pm/stop
PM 管理接口统一迁移到 /v2/pm/*（含文档、任务、需求相关路径）。
Director 仅保留 /v2/director/*，移除 /director/* 路径族。
旧接口处理策略：返回 410 Gone + 迁移说明（不是静默 404）。
前端契约统一：
api.ts
useProcessOperations.ts
全部改为 /v2/pm/* + /v2/director/*。
状态载荷统一为 V2 结构，移除 PM/Director 混合解析分支。
实施方案（同一轮原子收敛）
1. API 路由图收敛（后端）
新建 pm.py，以 PMService 为唯一执行入口实现 PM 控制面。
将 pm_management.py 迁移或重定向到 V2 路由树，统一前缀 /v2/pm。
在 __init__.py 注册 PM V2 router。
从 compat.py 和 main.py 移除 app.routers.pm 与 app.routers.director 的注册。
新增 legacy tombstone router（集中返回 410）覆盖历史 /pm/* 与 /director/*。
2. 应用入口单实现化
以 main.py 作为唯一真实装配入口（router 注册、生命周期、DI）。
main.py 改为薄适配层，仅转发到 api.main.create_app（保留旧测试签名），移除重复路由装配逻辑。
server.py 保持调用 api.main.create_app，不再依赖 app.main 任何实现细节。
3. CLI 双入口并存但单实现
提取 Director 统一核心执行模块（例如 director_core.py）。
director_v2.py 与 loop-director.py 均仅做参数适配并调用同一核心。
提取 PM 统一核心执行模块（复用 cli.py 逻辑），loop-pm.py 仅保留兼容桥接壳层。
统一默认配置指向 V2 主实现路径（config.py、PM orchestration 默认 director_path、活动配置默认脚本路径）。
清理文档与配置中的不存在入口引用（CLAUDE.md、pyproject.toml、README/CLI 文档）。
4. 弃用模块与重复实现清理
删除 pm_tools.py。
删除 workflow_nodes_compat.py，并重写 test_workflow_chain.py 到 workflow_adapter.py。
删除 director_tools_v2.py 与 director_llm_tools.py（确认无调用后）。
若 director_tools_v2 中有仍需能力，迁入 core/llm_toolkit/* 后再删源文件，避免能力回归。
5. sys.path 根因治理（非补丁化）
在生产代码中建立单一导入约束：core/polaris_loop 全量改为包内相对导入（from .x / from ..x）。
移除 app/api 运行路径中的 sys.path.insert（尤其是 director.py、pm_management.py、utils.ensure_loop_modules）。
仅允许 CLI 壳层和测试引导文件保留最小路径引导；其余目录一律禁止。
添加静态守护脚本并接入 CI，防止 sys.path 回流。
6. 架构守护与防漂移门禁
新增 check_architecture_drift.py，校验：
禁止导入已删除模块。
生产目录禁止 sys.path.insert/append。
CLAUDE.md/关键文档中的路径必须存在。
PM/Director 仅允许 /v2/* 路由声明（旧路由仅 tombstone 例外）。
create_app 仅允许单一实现源。
将该脚本纳入 CI 必跑步骤（与 pytest/e2e 同级门禁）。
测试用例与验收场景
API 合同测试：
/v2/pm/start|run-once|status|stop 全通过。
/v2/director/* 全通过。
旧 /pm/*、/director/* 命中 410 且返回迁移提示。
前端集成测试：
进程控制与状态轮询全部走 V2 路径。
PM 与 Director 面板无旧路径请求残留（抓包/日志断言）。
CLI 一致性测试：
loop-director.py 与 director_v2.py 同输入下结果结构一致。
loop-pm.py 与 cli.py 同输入下关键产物一致。
导入系统测试：
core/polaris_loop 在不注入 loop 专用路径时可正常导入执行。
回归测试：
pytest src/backend/tests
npm run test:e2e
python scripts/run_factory_e2e_smoke.py --workspace .
审计输出：
生成一次完整收敛审计 JSON（含问题、修复、证据路径、门禁结果）。
假设与默认值
默认不保留旧 API 兼容窗口，采用硬切断（410）。
双 CLI 主入口定义为：
PM：cli.py 与 loop-pm.py
Director：director_v2.py 与 loop-director.py
双入口仅是调用入口双态，不允许双实现并行。
所有文本读写继续强制显式 UTF-8。
本次仅修改 Polaris 仓库，不触碰任何目标项目代码。
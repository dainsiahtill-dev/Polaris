# L1-01 R04：模型自选验证的真实交付与资源闭合

记录日期：2026-10-21。运行 `553ab2e9f19245499ce318a26d51b63b` 已通过当前独立 QA，ProjectOutcome 为 `accepted`，正式观察结果 `OBSERVED_ACCEPTED`，实例随后正式 `STOPPED`。目标代码、依赖准备、验证及修复全部由 Polaris 自身工具完成；外部未修改目标、安装依赖、代跑验收或注入诊断。交付后只读静态审阅发现三项 P2，保留为已知限制。

这是冻结版本的一次 L1-01 交付证据。**L1–L12、默认入口切换、旧生产写路径退役仍未完成。**

## 冻结输入与修改机制

- 源码：主线 `a8b820a3d8ba9f3c1c7bc02ffb901a93dc9de1c6`，9373 文件从资格验证到正式停止字节未变。
- 项目：原始目录中的 L1-01「发光昆虫花园模拟器」，TypeScript；原始需求、业务深度、有效入口及反空心要求完整保留。
- 工作区：`~/.polaris/projects/engineering-l1-01-autoverify-20261021-r04`，全新隔离实例，后端 49978、前端 5174。主实例未重启。
- 模型：MiniMax-M3，原配置 SHA256 `75cd934790d7af7ecc6a7bc90860007d34820f417ed9f894dbe8fbe7efe8244a`；输出上限 3000、项目预算 5400 秒、外层预算 6000 秒均保持。
- 原始 `required_checks=[]`，模型负责选择真实验证；原有读写授权、网络授权、独立 QA 要求和唯一输出范围 `dist` 保持。依赖和清单属于验证阶段的只读输入，应先用普通授权工具准备。

本次只修 R03 暴露的归属交接：finalizer 从已有 SQL 事件识别已接受的原生选择，不能将模型的旧配方隐式重跑或改称用户硬性要求。失效或失败证据回到同一 Director，由模型明确选择下一步；宿主固定检查和 UNKNOWN 效果阻断保留。没有增加重试次数、超时、命令黑名单或目标专用修复规则。

资格证据：521 项联合回归通过；KernelOne ALL 十二阶段通过，368 passed、1 skipped；四项有限独立实验覆盖原始计数器反例、固定检查自动路径、冷恢复 UNKNOWN 不重放、旧自动 attempt 不覆盖原生归属；Ruff/Mypy 通过。合成实验只证明这些机制。

## 实际交付链路

PM → CE → Director → QA 均真实执行。前三角色及 QA 的最终 provider request 已核对身份、任务、workspace、原始目标、工具清单和可读 Context 快照。没有让模型维护 hash、义务 ID 或覆盖图。

Director 在约 91.96 秒时，通过实际工具写入首个业务源码 `src/garden/rng.ts`，随后实现 `entities.ts`、`simulation.ts` 和 `main.ts`，提供种子、README 和 CLI。模型自行处理过缺参数拒绝、编译错误和测试失败；外部没有补造反馈或修改产物。普通命令中的管道退出码与其内部 build/test 结果分别判断，未把外层 0 直接计成门禁通过。

最终模型主动调用一次 `verify_project`，`requested_by=[603,call_01a15c23deea718781270f81]`。attempt 610 → completion 627，八条实际子收据均 `ok=true / exit_code=0 / interrupted=false`：

| 检查 | 实际结果 |
| --- | --- |
| `npm run build` | TypeScript 构建通过 |
| `npm run check` | `tsc -p tsconfig.json --noEmit` 通过 |
| `npm test` | 重新构建并执行核心测试，104 项业务断言通过 |
| `npm start -- --ticks 60 --frames 3 --quiet` | CLI 实际执行，最终 tick 60，有有效灯光与情绪统计 |
| 直接 main 入口 | 实际执行；输出当前场景统计 |
| 源码规模检查 | 四个真实生产 TS 文件 |
| 测试站点检查 | 测试文件中 105 个 `check(` 文本出现，其中包含函数定义；实际业务断言 104 项 |
| 必需文件检查 | 种子、README、package.json、tsconfig.json 存在 |

后三条结构检查是辅助证据，不能替代前面的构建、核心规则和真实入口结果。依赖准备已由普通授权命令执行，模型没有在只读配方中重复安装。本次未发生隐式自动验证。

QA 在 frame 639 独立读取源文件，最终事件 785 报告 `approved`，没有 major findings。QA 前后绑定一致：revision 49、verification_seq 627；310 项输入与 Verifier 的 `input_binding.result_manifest` 完全一致。独立观察逐项核对 269 个文件 hash、两个 symlink 和 39 个目录的 children/metadata；主 Agent 重新核对所有文件字节和全部 310 项 metadata，无失效。

本次没有触发“验证后改源码，再由 finalizer 请求新原生选择”的分支。该分支仍以有限真实计数器和冷恢复实验为证据，不能宣称本次远端运行覆盖了它。单次成功也不能证明 system 文案的独立因果收益或稳定成功率。

## 结果、成本与释放

| 维度 | 事实 |
| --- | --- |
| 当前项目 | completed / passed / QA approved / accepted |
| 运行耗时 | 798.146 秒，约 13 分 18 秒；未耗尽预算 |
| 工程事件 | 787 |
| 工具记录 | 89，全部 observed；80 ok、9 false；无 pending |
| 请求捕获 | 65 个唯一 capture/dispatch ID：PM 3、CE 3、Director 47、QA 12 |
| 实际物理 HTTP 发送次数 | UNKNOWN；不把捕获数冒充发送次数 |
| provider 原始 usage | input 85034、output 35859、cache_read_input 1550473、cache_creation_input 0 |
| 账单、单次成功成本、人工分钟 | UNKNOWN；不记零，不从 token 估算冒充账单 |
| 正式观察 | `OBSERVED_ACCEPTED`，exit 0 |
| 正式停止 | `STOPPED`，exit 0；active_runs / pending_tool_calls 均空 |
| 物理释放 | 本次 PID 23465 / 24674 已退出，49978 / 5174 已关闭；主实例 18128 与共享 NATS 18035 仍存活 |

工作台只读浏览器首试通过，28.32 秒：bootstrap 水位 34 后收到真实 WebSocket 35–45 等新事件，当前 Context / final-request 双 200，无额外快照读取、SSE、全局 Bench 或跨工作区事件。它证明捕获窗口内工作台观测成立，不冒充目标 CLI 的验证。

## 已知质量限制

交付后的外部只读静态审阅未发现明确 P1，但发现三项 P2，主 Agent 已核对源码，未执行目标命令复现，未向本次运行注入诊断：

1. CLI 每帧使用 `ceil(ticks/frames)`，总 tick 可能超出请求；例如 13 ticks、6 frames 会推进 18 ticks，0 ticks 也会推进。
2. 三／四连闪参数当前改变单个亮灯窗口宽度，没有实现 README 描述的对应脉冲序列。
3. 种子只检查数组存在，`flowers:[null]` 等畸形实体可能在后续构建中抛错，未兑现文档中的自动回退。

还保留不同 seed 测试混用了不同 tick、moon 集成测试再次乘 brightness 的测试弱点。原始 L1-01 的情绪、月相、湿度对实时灯光的实际影响及必要深度已有证据；不能将本次通过扩展成全部 CLI 输入和文档描述均正确。后续修复仍须由 Polaris 自身执行，并产生新的当前候选与独立验收。

## 证据与后续范围

统一证据根：`~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/`。

- `task7-verification-ownership-qualified-{joint,all}/`：资格结果和源码／模型冻结证明。
- `task7-verification-selection-owner-independent-review/`：四项独立有限实验。
- `task7-l101-autoverify-r04-frozen-inputs/`：原输入、源码和预算指纹。
- `task7-l101-autoverify-r04/`：单次正式启动、绑定、快照及可读最终请求。
- `task7-l101-autoverify-r04-physical-observer/`：最终请求、实际效果、当前 Verifier／QA 绑定。
- `task7-l101-autoverify-r04-browser-audit/`：真实 WebSocket 与 Context 审计。
- `task7-l101-autoverify-r04-product-static-audit/`：三项 P2、弱测试及审阅前后 hash。
- `task7-l101-autoverify-r04-official-closure/`：正式 observe/stop、主 Agent 候选和物理进程／源码证明。
- `task7-l101-autoverify-r04-immutable-delivery/`：26 文件归档及独立 SQL 备份。归档不包含依赖实现和 runtime；依赖的当前 hash 保留在 canonical journal。tar SHA256：`719585eef1e971ee37b84f3cebe01c149c254c647931bcea6d6121889d398b43`。

R01 FAILED、R02 CANCELLED、R03 CANCELLED / UNKNOWN 原样保留；R04 不改写历史。不同平台版本与主动取消的尝试不能合并成同一冻结候选的成功率。

下一批继续既定默认入口切换、旧写路径退役和证据页面同步；随后按新冻结批次逐步扩大 L1–L12。三项 P2 留在修复队列，不用外部代修抹掉，也不将其扩展成第一个项目之外的无限前置研究。所有研发与提交只在 `main`，不创建分支或 worktree。

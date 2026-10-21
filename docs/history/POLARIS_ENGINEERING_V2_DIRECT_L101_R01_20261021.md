# 新默认入口 L1-01 R01：项目接受成立，收尾结果 UNKNOWN

记录日期：2026-10-21。冻结主线 `2c07bfca2ca7c37125f90b3c935633a3e5ad320a` 的 9384 个文件，通过新的内部 Bench CLI 启动全新隔离 L1-01。Polaris 自身完成代码、依赖、失败修复、实际验证和独立 QA，平台最终为 `completed / passed / approved / accepted`。**本次 runner 最终 exit 1，原 STOP 为 UNKNOWN／exit 2；不能将本次整体报告写成全绿。** 随后只读复查确认原进程与端口均已消失，没有重放停止。

## 真实运行与适用版本

- run：`41d09f3558934698af7281eefef74778`；workspace：`~/.polaris/bench/engineering-v2-direct-20261021-r01/workspaces/4cf1cdc78f1a4a5d96591cb6d1d7e2ba/L1-01`。
- 正式入口：`run_factory_bench.py` 的新活动路径，复用 `engineering_acceptance` 和现有 `EngineeringRunService`，默认 `workflow=direct`。PM 未调用，Director 自行决定咨询一次 Chief Engineer，随后独立 QA 实际运行。
- 原始 120 项 catalog SHA256 为 `f8a1af935a9b58d49a099453180a73175abc4a6fedf10917c10cb92609771af3`；本次只选择 L1-01，原始要求全文保留。
- MiniMax-M3 配置 SHA256 为 `75cd934790d7af7ecc6a7bc90860007d34820f417ed9f894dbe8fbe7efe8244a`，冻结 host 为 `0a2fb864ae5af93a58842047cb543fff7558e22ad708a79eb86d588f110e01dc`；原项目／外层预算仍为 5400／6000 秒。
- 全程没有外部目标编辑、依赖安装、目标验收命令、诊断注入或产品事实写入。源码、host、模型及 catalog 在运行直到原停止后均保持不变；后续资源 owner 修改属于新候选，不回标本次证据。

## 交付、验证与当前候选

Director 自行处理两次原生 `write_file` 缺 `file` 参数、真实 TypeScript 构建错误、首轮 30／33 业务测试和 38／40 核心检查失败。缺参数从实际 provider 原生响应已存在，未发现 parser／alias 丢字段；下一实际 provider request 携带了准确失败反馈。没有让普通错误触发新的 PM／CE 合同维护。

正式验证首轮确有 exit 226 失败，证据保留。后续真实 `npm run build`、`npx tsc --noEmit`、33／33 测试、42／42 核心检查及 CLI 入口分别 exit 0。六项最终 shell 检查中的安装 fallback 包含管道并输出 npm ERR，**其 shell 0 不证明安装通过**；依赖准备成功依据是此前 Director 普通工具的实际安装记录。QA 也保留了该限制，不能将全部六项笼统写成内部每一步都成功。

主 Agent 直接从实际 SQLite 重查：Verifier seq804 与 QA seq961 同为 revision62，QA `inputs` 与 Verifier `result_manifest` 完全一致；277 个文件 hash 与现盘重算一致，另有 37 个目录及 2 个 symlink 记录。独立 terminal GET seq963 为 accepted，原平台 verdict 不由外部报告改写。

交付后只读静态审阅保留两项 P2，未外部执行复现或修改：非同步路径的 cooldown 不递减，某些普通花园配置中独奏节奏会停滞；负 ticks 的异常落在 CLI handler 外，与 README 的 exit2 描述不符。它们不扩大为已证的整个平台失败，也不因 QA approved 而抹去。

## 物理请求、工作台与资源

Director、实际 CE 咨询和 QA 的最终物理请求均核对了角色、run、workspace、原始目标、可用工具、schema 参数、token 与快照读取。CE／QA 只读工具范围保持；PM 没有实际调用，不制造调用记录。81 个 dispatch ID 是实际持久观察口径，**实际 HTTP 发送总数、账单和人工分钟仍 UNKNOWN**。

当前默认 URL 的真实只读浏览器观察通过，12.65 秒；收到初始水位 133 之后的真实 runtime.v2 事件，当前 ContextOS 和 final-request 双 200，未发工程写请求、未订阅全局 Bench。旧测试 helper 仍强制要求已退役的 engine URL 参数，其预检失败已保留；本次仅在目标外的私有观察器允许省略该参数，原测试断言体保留，产品源码和真实 URL 均未修改。这不是源码 helper 已修复的证据，也不替代目标验收。

实际工具记录 108 条全部 observed，102 条 `ok=true`、6 条 `ok=false`；终态活动运行和未结工具均为 0。canonical 工程耗时 945.648 秒，外层 CLI 1039.672 秒，不能混用这两个口径。

本次实际停止发生后，Instance Registry 已 stopped、PID 字段清空，但 adapter 对原 PID 48533／50049 的即时探针仍为 true，因此正确保留 STOP UNKNOWN，整 CLI exit1。后续主 Agent 只读核查：原 PID、guard 子进程均不存在，49978／5174 关闭，主实例 18128／49977 和共享 NATS 18035／4222 保持存活。该后续资源闭合事实单列，不覆盖原 UNKNOWN 或补造 STOPPED 收据。

## 首次偏差与下一步

首次收尾偏差属于既有 Instance／Resource owner：启动仅返回 PID，丢弃实际 `Popen`；退出等待只做 kill0，最后等待结果未确认仍清 PID 并标 stopped。独立私有进程实验在原路径复现两个 owned child 变为 Z 后仍判活；仅使用 owner wait／reap 的对照可正常退出。**R01 当时没有采样进程 Z 状态，历史具体原因仍不追认。**

修复只服务于该已证边界：在已有 KernelOne 进程底座确认实际退出及 owner 子进程回收；未确认时保留 PID，不通过增加超时、改弱探针、忽略 UNKNOWN 或重发停止来换绿。修后另起候选资格和全新运行。本次产物接受与后来资源闭合已证明，但严格 runner 全绿、其余 L1–L12 全 120 项和整体目标仍未完成。

## 证据位置

统一根为 `~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/`。

- `task8-bench-qualification/`：冻结候选、834 项联合回归、KernelOne ALL 十二阶段 368 PASS／1 SKIP、质量检查及主线提交。
- `task8-direct-l101-native/original-{stop-report,batch-summary}.json`：原 UNKNOWN 和原批次结果。
- `task8-direct-l101-native/{request,artifact,lifecycle}-observer/`：最终请求、当前产物及独立 terminal GET／真实状态记录。
- `task8-direct-l101-native/browser-observer/root-recording-audit.json`：真实默认工作台观察及量具适用边界。
- `task8-direct-l101-native/immutable-delivery/`：只读 SQLite 备份、当前 manifest 核对和产物归档。
- `task8-direct-l101-native/root-post-terminal-source-and-resources.json`：原停止后的源码／配置无漂移及实际进程／端口关闭证明。
- `task8-stop-owner-repro/`：合成 owned-process 反例及正常回收对照，不当作真实项目证据。

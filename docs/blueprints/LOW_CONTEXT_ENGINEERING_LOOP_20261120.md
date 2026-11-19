# 低上下文工程循环：有界工作集 + 状态投影（2026-11-20）

状态：进行中。基线 main `de3c33f03`。目标：用 ContextOS 同构的
"状态优先投影" 替代"累积冗余消息历史再 emergency trim"，使任意项目自动化开发
在**有界上下文**内完成；由真实 L1-L12 Bench 验证。

## 1. 问题与证据（真实 run，非推测）

`_bounded_request_messages`（`engineering_loop.py`）每轮从 journal 重建完整
message 历史，只有当投影超过 `window*0.95-24k` 时才按 1.0/0.75/0.5/0.3 迭代
丢弃较早轮次；docstring 自认 "A native tool working set has no budget: it grows
with every turn until the provider window refuses the whole request."

对 `~/.polaris/bench_runs/serial/*` 真实 engineering SQLite 的度量：

| run | status | turns | tools | max message tokens |
|---|---|---|---|---|
| L2-15 | failed | 219 | 216 | ~101,938 |
| L4-32 | interrupted | 308 | 312 | ~99,556 |
| L6-58 | completed/passed | 214 | 238 | ~100,949 |
| pelican-bike (7 run) | mixed | 27–316 | 31–339 | 最高 ~100,545 |

即：单项目 200–300 轮、长期贴着 ~100k 窗口运行，仍 failed/interrupted。
`compress_native_tool_history` 丢弃跨度时只插入一行
"【上下文已压缩】省略了 N 条较早的工具轮次"——模型失去的**状态**（计划、已做、
已验证、最近错误）没有任何替代，于是重复已做的工作。

## 2. 目标机制（StateFirst，与 ContextOS 同构）

ContextOS 的四层是 TruthLog / WorkingState / ReceiptStore / ProjectionEngine。
工程 loop 的 durable truth 已经是 `<workspace>/.polaris/runtime/engineering/
sessions.sqlite3`（规则 5）。本方案**不新建第二事实源**，而是从同一 journal
派生一个**有界工作集投影**：

```
journal (durable truth, 只读派生)
   └─→ engineering_state_projection.working_state_message()
          ├─ goal / candidate revision / verification + 最近检查
          ├─ 已改文件（write_file/edit_file 参数）
          ├─ 工具活动计数 / loop 干预次数
          ├─ 最近一次 assistant 文本（模型自报的意图/下一步）
          └─ 最近一条错误（截断）
   └─→ 追加为每秒请求的 trailing user 消息（recorded facts only）
```

同时把"历史预算"从**贴窗口上限**改为**有界工作集**：
`KERNELONE_ENGINEERING_WORKING_SET_TOKENS`（默认保守值，见下）作为压缩预算；
若最终轮本身超过该预算，回退到原窗口派生上限，保证不因预算而失败。

原则：
- 投影只读派生，不持久化、不参与验收、不改变权限/工具/效果。
- 日记仍是唯一真相；历史丢弃不丢事实，状态投影补回"当前态"。
- 视觉投影、工具配对、QA 独立复核、验收维度本体论不变。

## 3. 数据流

```
run_engineering_loop
  ├─ _bounded_request_messages(messages)  ← 预算改为有界工作集
  ├─ host_parts（budget/continuation/loop alert）
  └─ working_state_message(run, events)   ← 新增：director（及可选全角色）
       → options["messages"].append(trailing user)
```

## 4. 边界与不做的事

- 不接 `aggregate_chat` 的 pack 组装（那是另一条已记录的 known-gap 设计决策）；
  本方案只把工程 loop 的上下文**有界化 + 状态化**。
- 不用 receipt 替换工具输出：工程工具输出（命令日志）不可重读，ref 无意义。
- 不删历史、不改权限、不改验收语义、不重放。
- 默认值必须保守：先证明"有界 + 状态"在 L1-01/L2-15 上优于基线，再扩到 120。

## 5. 验证与停止条件

1. 单元/回归：状态投影内容完整且不含控制面字段；有界预算下请求 token 显著下降；
   工具配对/视觉投影不破坏；QA 独立复核路径不变。
2. 真实 L1-01、L2-15 单例：对比基线的 turns / max context / outcome。
3. 指标：turns 下降、max message tokens 低于阈值、outcome 不劣化（accepted 不假阳）。
4. 只有真实 run 证据才允许调低默认预算或扩到 120；失败先对 exact run 做动态 DEBUG。

## 6. 风险

- 预算过低会丢失近期推理细节 → 用状态投影补，且保留最终轮完整。
- 弱模型可能仍重复（QA A/B 曾混合结果）→ 以真实 run 为准，不先验宣称收益。
- 状态投影若含控制面字段会诱导投机 → 显式白名单字段，测试锁定。

## 7. 实现状态（2026-11-20）

已落地：
- `engineering_state_projection.py`：从同一 journal 派生有界工作状态（goal/candidate/
  最近检查/失败检查首错/已改文件/工具计数/loop 干预/最近意图/最近错误），每请求追加为
  单一 trailing user 消息（与 budget/continuation 同消息，budget marker 仍在最前）。
- `_history_allowances`：`KERNELONE_ENGINEERING_WORKING_SET_TOKENS`（默认 32000）为
  工作集预算；窗口派生上限仅作 fallback；required visual 被裁掉时自动回退到窗口上限。
- 测试：`test_engineering_low_context.py`（9）+ 验收/预算/视觉回归。

真实 L1-01（agnes-3.0-flash，隔离实例）观测：
- 88+ 轮，**max message tokens ≈ 32,008**（基线同类 run 长期贴 ~100k）。
- 每请求均带工作状态投影；loop 检测在连续 4 次相同 verify_project 后触发。
- 仍 failed：模型反复 `verify_project(["npm test"])`，该命令在只读验证域写 `dist/`
  触发 EROFS；模型未先用 execute_command 准备构建。串行 L2-15/L4-32/L6-58 无 EROFS
  （0 次），说明这是构建型 TS/JS 项目的类问题，不是低上下文改动引入的。

由此暴露并修复的通用缺陷：
- **普通 execute_command 丢失真实退出码**：run 的 host 策略是 `bash_pipefail_v1`，
  但 loop 的普通过命令走 `resolve_execution_policy(None)`=`posix_sh_v1`（无 pipefail）。
  L1-01 实测 `npm run build 2>&1 | tail -30` 记录为 `ok=True/exit 0`，而 tsc 报
  TS2307/TS2580——模型以为构建成功，永不修复，`dist/` 始终缺失。修复：loop 解析 run
  策略并传给每个普通 execute_command。测试：`test_ordinary_execute_command_honors_run_pipefail_policy`。
- **只读失败反馈与实际失败不一致**：原 note 只提“安装/更新依赖”，与“构建输出写失败”
  漂移。改为共享 `read_only_write_targets`（verification 模块单一来源），feedback 与
  状态投影都点名实际写入路径，并给出“先 execute_command 准备、再只读验证”的可执行指引。
  检测大小写不敏感（coreutils `Read-only file system`；npm `ENOENT ... syscall mkdir`）。
- 验收维度：`clause_findings` 未出现在 provider 工具 schema（生产 3 次 report_review
  0 次带矩阵的根因），已在 registry schema 暴露并在多条款目标下于 dispatch 边界强制全覆盖；
  目标条款超过上限（32）时 fidelity 保持 unknown（不在前缀上判过）。
- **fidelity 条款源污染**（L1-L12 验收阻断）：内部 adapter 的 instruction =
  平台 notice + `build_requirements_doc`（用户 brief + 平台交付/深度/源码/语言合同）。
  机械抽取把 notice 与平台合同当作条款：L1-01 原始候选 65 条、过滤标题/表格分隔后
  仍 55 条（`min_files:3`、`ts_syntax` 等）。修法：`FROZEN_REQUIREMENTS_MARKER`
  单一来源（adapter 与抽取器共用），抽取器从 marker 之后取**第一个 markdown 小节**
  （目标/使用体验）作为条款源，非 markdown 目标回退整文。L1-01 实测 65 → **11** 条。
- **条款上限**：全 120 个 catalog 项目的真实条款数 8..25（p50 17，max 25）。原
  `_MAX_CLAUSES=14` 会截断 85/120 → fidelity 恒 unknown。提高到 **32** 后实测
  **0/120 截断**（max 25）。截断仍 fail-closed。

未解决/待验证：
- L1-01 的 EROFS 准备问题：preparation gate 已强制“先写后验”，需重跑真实 run 确认。
- 工作集默认 32000 是否最优（模型 re-read 较多）需 A/B。
- 低上下文对 L1-L12 通过率的净收益尚未量化。

## 8. 门禁与设计缺口（preparation/wrap-up 已实施；overlay 待 ADR）

**构建型项目的验证只读域 vs 构建输出**：`verify_project` 的 check 在只读验证域运行
（仅 host 声明的 output roots 可写）。当 check 本身构建（如 `npm test` = `tsc && node
dist/...`）时写 `dist/` → EROFS。正确做法是模型先用 `execute_command` 准备构建，再只读
验证；弱模型不照做时会无限重复同一 check（L1-01 实测 24+ 次）。

已实施 **preparation gate**（无需放宽 containment）：任一 verify_project 观测含
`read-only file system`/`erofs`（大小写不敏感）即置 `preparation_pending`；此后
`verify_project` 被拦截（`preparation_gate_enforced`），直到一次**真实写入的**
`execute_command`（`advances_revision`）清除。确定性失败的 check 不能再被反复重跑。
注意：`read_only_write_targets` 与 service 的 `read_only_failure` 检测同时改为大小写不敏感
（coreutils 输出 `Read-only file system`，无 `EROFS` 字样，原检测会漏）。
服务级测试：`test_preparation_gate_requires_writing_execute_before_reverify`。

安全的非对称修法（未实施，需 ADR）：验证域允许工作区可写，但用**输入快照哈希**
（`PreparedProjectVerificationSandbox` 已有此机制）在后置断言中证明输入未被改动；改动
输入即判失败。这样构建输出可写、输入仍不可伪造。**禁止**把 EROFS 目标自动声明为
output root——那会让 check 写任意输入路径，破坏只读保证。

**通过后不收尾**：弱模型在 `verify_project` 通过后仍继续改输出，使 `workspace_revision`
前进、验证证据失效，需要重新验证（L1-01 实测：2 次 passed 都被后续写入作废，115+ 轮
仍在第二轮 Director 帧，从未进入 QA）。

已实施 **wrap-up gate**（`KERNELONE_ENGINEERING_WRAP_UP_GATE`，默认开）：跨轮持久
`candidate_verification_passed` + `post_pass_write_cycles`。**允许 1 次**合法的
“改→再验”循环；当模型再次通过后又发起第 2 个 pass→write 循环时，`write_file`/`edit_file`
被拦截（`wrap_up_enforced`），连续 2 次拦截即 `finish("completed")`，把“实际通过的候选”
交给独立 QA。任何真实写效果使 revision 前进即重置通过标志并计入循环数。QA 是过早收尾的
安全网（独立逐条复核）。单测：`test_wrap_up_gate_blocks_post_pass_edits`（1 次合法循环后
2 拦截→完成、被拦截写未执行）、`test_wrap_up_gate_can_be_disabled`、服务级
`test_wrap_up_gate_sends_passed_candidate_to_qa`（写被拦→QA 复核实际通过候选→accepted）。
`test_writing_after_verified_candidate_requires_actual_fresh_check` 与 stage-gate 用例
通过 env 关闭本门以隔离各自语义。

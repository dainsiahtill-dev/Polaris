# r10 同-run 任务绑定与依赖就绪闭环

状态：实施中。继承已批准无人值守总计划；工程文档不是平台事实源。
原六项审查回归和 r10 四项修复已本地验收；本计划只处理本次真实恢复新断点。
禁止修改生成目标、降低 QA、重跑有效 PM/CE、手工改 lease/token/receipt 或清历史失败。

## 已验证事实与假设边界

1. 同一 `factory_03b00c4e1b1b` 经一个公开 Director-only retry，PM/CE 字节和
   五个不可变快照未改变。九个新请求包含 19 个原生调用，均解码/分发/记录，
   13 个真实 effect；三份 durable BatchReceipt 校验通过。旧账本诊断失配未再现。
2. 新 TASK-1 数字行 5 在 seq124 完成；TASK-2 的依赖仍指向旧失败行 1，
   TASK-3 依赖行 2。质量修复又挑行 1，seq129 失败，0 工具/0 提交。
3. 实际创建者是 Factory `_materialize_pm_plan_taskboard`，不是最初疑似
   adapter fallback。TaskRuntime `ensure_task_row` 原契约为 creation-only，
   失败行会生成新 sibling；不能仅凭任意 metadata 静默改成可重开授权。
4. 私有真实服务复现：旧失败行存在时 ensure 产生新行，完成新行不解除旧数字
   依赖；修复优先级仍挑旧失败行。对照复现：显式重开同一行、领取 attempt2、
   拒绝 attempt1 迟到结算，attempt2 完成后依赖任务能领取。
5. `@types/node` 在原 typescript-only 安装后加入已授权 package；原 lock 与
   node_modules 未刷新。现有 `tsc`/node_modules 存在性判断跳过准备，实际 TS2688。
6. tests 源码归 TASK-3，其 owner 未执行。缺失是真实债务，不能造 stub 或删最终
   测试门；先修绑定使依赖正常推进。是否仍存在独立阶段门问题由新证据决定。

## 执行与权威流

```text
已验 Factory run / CE contract / projection
    |
TaskRuntime 唯一逻辑 owner 解析
    |-- 未存在：保留原创建契约
    |-- 同-run 可恢复失败：显式重开同一行、领取新 attempt
    |-- 跨 run/合同/用户取消/外部活跃 owner/歧义：拒绝，不猜
    |
依赖边保持同一数字 owner；修复也消费该绑定
    |
真实写入/receipt；依赖 manifest 就绪检查与受控准备
    |
source 验证；准备后冻结依赖；完整 QA/入口/settlement
```

## 互斥实施范围

### B：逻辑任务身份（P0）

- runtime `_mixin_recovery_reexec.py`：runtime-owned 显式同-run 恢复和唯一绑定。
- `_mixin_task_rows.py`：只补已有重开操作内部锁下的 optional expected
  identity/status/attempt guard。原调用默认语义不变，不嵌套 flock。
- Factory `_mixin_00.py`：只改 `_materialize_pm_plan_taskboard` 的已验证恢复意图。
- `factory_workspace_quality_impl.py`：只改修复 attempt owner 解析，不动已有部分
  receipt 刷新函数。不以 failed 优先级或 newest sequence 决定授权。
- 两个新 focused 测试文件。新增 public DTO/注册或跨桶文件需主 Agent 先裁决。

### A：依赖就绪（P1）

先给最小 VC 与现有能力复用方案，再实施。共享只读 readiness 判断必须覆盖
manifest/lock/实际安装状态，不能特判当前 package。候选 source 先认证，准备后
才冻结 dependency binding。复用受控 command/native preparation 和真实退出证据；
不得新增裸 npm 执行、抹掉失败或宣称未证明 drain 的命令成功。

排他候选：dependency readiness helper、`factory_workspace_quality.py` prepare、
materialization collector 依赖准备分支、`_mixin_02.py` toolchain helper、新 focused tests。
不改 B 的文件。最终 test/source 诊断仍保留，完整 QA 不放宽。

## 反证与验证

- 真实服务 RED/GREEN：同一逻辑 owner 行不分裂，attempt2 完成解除依赖。
- stale attempt、跨 run/CE hash、用户取消、外部 lease、歧义/移除行不得被复用。
- 并发真实服务检查：锁外检查不足；锁内 CAS 必须阻止迟到重开与 twin rows。
- dependency manifest 后变、已有 binary 但缺包会要求准备；有效 cache 不重复安装。
- 安装失败/不明 drain 保持失败；真实 source inputs、scope、receipts 不伪造。
- 定点原生门禁、受影响套件、Ruff/format/严格类型、独立审查，主 Agent 再验收。
- 新 source freeze、受控同实例升级，再一个 Director-only 恢复；无资格不付费重试。
- 同-run 恢复不是 fresh 全量完成；之后仍需 fresh isolated 四支柱、QA/结算及 N-batch。

## 残余风险

原 r10 已产生历史 sibling，且终态有 runtime_reset_removed。只能消费现存已验证
绑定或正常平台恢复路径；不能为它写迁移、人工重连依赖或从 disk 补造成功。
同-run 身份修复、依赖就绪和完整 fresh 项目验收分开记录，不跨越证明等级。

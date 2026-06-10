# ADR-0090: 弱模型链路硬化不变量(流式工具调用发射/消费契约 + 预算真实化)

## Status

- **Status**: Accepted
- **Date**: 2026-06-10
- **Author**: AI Agent (via diag5e live-run audit)
- **Related**: ADR-0074 (dead-loop prevention), ADR-0077 (speculative execution), ADR-0081 (transaction kernel freeze), `docs/blueprints/WEAK_MODEL_HARNESS_HARDENING_BLUEPRINT_20260610.md` (repo root), `vc-20260610-weak-model-harness-hardening.yaml`

## Context

实测(django-15213,qwen3.6-27b-int4,16k 窗口)表明弱模型分数损失的主要来源是链路自伤而非模型推理失败:

1. 流式 tool-call 渐进事件被当作独立调用:`args={}` 部分版与补全版**都被执行**,空参 `edit_blocks{}` 烧掉强制编辑重试;
2. 畸形 native 参数被解码器静默丢弃后升级为 ASK_USER 挂起,模型从不知道解析错误;
3. ContextOS enforcement 预算是角色静态值,从不与模型窗口取 min;角色 system prompt 在预算检查**之后**经第二次 projection 注入,16k 本地模型实际收到超预算 prompt;
4. 绑定解析失败回退 128k,导致小窗口模型走「事后盲截断」而非「相关性选材」。

## Decision(不变量)

### I1 流式工具调用发射契约(kernelone stream executor)

1. 中流(mid-stream)**禁止**发射参数为空 dict 的 tool_call 事件;空参显式参数一律 provisional(不限 Anthropic 占位)。
2. 流末 flush 是合法无参调用的唯一发射点(`allow_provisional_empty_arguments=True`)。
3. 同一累加器的重复发射只允许「参数更完整」的渐进 refinement,且签名去重保持。

### I2 流式工具调用消费契约(roles.kernel stream_orchestrator)

1. 收集层按 call_id(缺失则 tool+首现序)**keyed upsert**:同 key 后到事件**替换**先到版本,绝不并存。
2. materialize 前执行 subset-supersede 清扫:同名调用 A.args ⊊ B.args(严格子集,含 {})时丢弃 A。
3. 空参调用不得进入投机执行;影子任务异常必须被回收(no unretrieved task exceptions)。

### I3 解码失败反馈契约(turn decision decoder)

1. 畸形 native 工具参数禁止无痕丢弃:parse error 必须记录并可注入 retry 上下文。
2. `native_tool_calls` 非空而解码产物为空时,先走一次 corrective retry(引用具体解析错误),不得直接 ASK_USER 挂起。
3. 空响应在 WAITING_HUMAN 之前必须有恰好一次自动重问。

### I4 预算真实化契约(ContextOS gateway enforcement)

1. enforcement 预算 = `min(role policy.max_context_tokens, resolved_model_window × 0.85)`;窗口解析失败回退角色静态值并告警,**不得抛错杀 turn**。
2. 角色 system prompt 的 token 估计必须计入 enforcement 预算(预留位),禁止在预算检查后注入未计量内容。
3. 角色 system prompt 注入用直接前插(单一共享 helper),**禁止**为此做第二次 ProjectionEngine.project(double projection)。
4. 模型绑定解析失败时,Stage-1 选材窗口回退值由调用方注入(角色策略值),禁止 128k 兜底毒化选材。

### I5 评分可区分契约(unified_judge)

1. 校验器允许返回分级分([0,1] float);类目分 = check 分均值。
2. 空类目不参与加权(权重在非空类目上重归一),禁止空类目白送满分。
3. critical 门禁语义不变:任何 critical 失败 → case FAIL(fail-closed)。

## Consequences

- 正向:弱模型「想对即落地」,空参污染清零;16k 本地模型上下文所见即所配;评分可区分质量层级。
- 负向:投机发射时机变晚(收益轻微下降);W3 评分历史可比性断点(audit 同时保留 binary pass 与 graded score)。
- 回滚:I1 与 I2 互为兜底,任一层可独立回滚;I4.1 钳制系数 0.85 可调。

## Implementation

落点清单见 blueprint §3;验证计划见 VC `vc-20260610-weak-model-harness-hardening.yaml`。

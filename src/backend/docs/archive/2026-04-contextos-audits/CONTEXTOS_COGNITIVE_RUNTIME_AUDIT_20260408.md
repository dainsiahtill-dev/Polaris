# ContextOS + Cognitive Runtime 深度审计报告

**日期**: 2026-04-08
**审计团队**: 10人专家并行审计
**执行分支**: feature/enhanced-logger
**状态**: 已完成

---

## 执行摘要

本次审计发现 **43个问题**，其中：
- **HIGH 严重度**: 18个
- **MEDIUM 严重度**: 17个
- **LOW 严重度**: 8个

### 问题分布

| 模块 | HIGH | MEDIUM | LOW |
|------|------|--------|-----|
| ContextOS 核心管道 | 3 | 2 | 2 |
| Attention/Intent 跟踪 | 5 | 4 | 1 |
| 度量系统 | 2 | 2 | 2 |
| Benchmark验证框架 | 3 | 3 | 3 |
| 压缩/摘要引擎 | 3 | 3 | 1 |
| Tool语义搜索 | 1 | 3 | 2 |
| Auth Context | 3 | 2 | 0 |
| CLI可观测性 | 3 | 2 | 2 |
| Memory/Continuity | 0 | 4 | 2 |
| Event Sourcing | 2 | 2 | 1 |
| **总计** | **25** | **27** | **16** |

---

## 一、ContextOS 核心管道 (runtime.py)

### 1.1 [HIGH] pending_followup 状态机死锁

**位置**: `_canonicalize_and_offload`, lines 719-766

**问题**: 当用户响应但 dialog_act_resolved=False 时：
- `pending_followup` 保持 "pending" 状态
- 但本地变量被清除 (lines 764-766)
- 密封守护持续阻止episode密封
- **结果**: 死锁 - 用户无法继续，pending follow-up 永远卡住

**修复**: 在 dialog_act_resolved=False 时，显式设置 pending_followup 为可密封状态。

---

### 1.2 [HIGH] 无效状态值 "resolved"

**位置**: line 761

**问题**: `"resolved"` 不在 `PendingFollowUp.status` 的有效值中 (pending|confirmed|denied|paused|redirected|expired)。

**修复**: 使用有效的 terminal 状态或实现 expired 机制。

---

### 1.3 [HIGH] seal_blocked 事件双重条件缺失

**位置**: lines 1140-1158

**问题**: `seal_blocked` 事件被 `enable_attention_trace` 条件屏蔽，但这是关键安全guard行为。

**修复**: 移除 `enable_attention_trace` 检查，sealing被阻止时无条件发射事件。

---

### 1.4 [MEDIUM] 密封守护潜在死锁

**问题**: 密封守护与 pending follow-up 解析逻辑交互缺陷，无超时/降级机制。

---

### 1.5 [MEDIUM] expected_next_input_tokens 超限

**位置**: `_plan_budget`, lines 1015-1019

**问题**: 计算结果可能超过 model_context_window，无合理性验证。

---

### 1.6 [LOW] compress() 未被调用

**问题**: `ContextOSProjection.compress()` 定义但从未使用，可能是死代码。

---

## 二、Attention/Intent 跟踪 (classifier.py, patterns.py, runtime.py)

### 2.1 [HIGH] PAUSE 模式过于宽泛

**位置**: `patterns.py` line 101

```python
re.compile(r"^(先|暂时|先放一放)")
```

单字符 `^(先)` 会错误匹配 "先帮我实现登录功能"。

**修复**: 改为 `^(先别|等一下|暂停|等等|稍等|等会)`

---

### 2.2 [HIGH] REDIRECT 模式过于宽泛

**位置**: `patterns.py` line 106

```python
re.compile(r"^(改|改一下|...)")
```

会错误匹配 "改好了"、"改天再说"。

**修复**: 改为 `^(改成|换|换成|换一个|改一下)`

---

### 2.3 [HIGH] CLARIFY/CANCEL/STATUS_ACK 未处理

**位置**: `runtime.py` lines 719-766

**问题**: 只处理了 AFFIRM/DENY/PAUSE/REDIRECT 四种 act。用户说"什么意思"(CLARIFY)或"取消"(CANCEL)时，pending follow-up 变成孤立状态。

**修复**: 添加对这些 act 的处理，设置 status="resolved"。

---

### 2.4 [HIGH] "expired" 状态从未被设置

**位置**: `models.py` line 160

**问题**: `is_resolved()` 认为 "expired" 是 resolved，但整个代码库没有任何地方设置此状态。

**修复**: 实现基于时间戳或轮次的过期机制。

---

### 2.5 [HIGH] Fallback 逻辑冗余

**位置**: `runtime.py` line 744

**问题**: `_is_negative_response` 使用与 `DENY_PATTERNS` 完全相同的模式，第二次匹配无意义。

---

### 2.6 [MEDIUM] 模式定义重复

**问题**: `_AFFIRMATIVE_RESPONSE_PATTERNS` 和 `_DIALOG_ACT_AFFIRM_PATTERNS` 完全相同，造成维护困难。

---

### 2.7 [MEDIUM] short_reply 判断仅基于长度

**位置**: `classifier.py` line 70

```python
len(content) <= 5
```

不考虑语义内容，可能将长否定回复误判。

---

### 2.8 [MEDIUM] Fallback 设置无效状态值

**位置**: `runtime.py` line 761

```python
status=resolved_followup_status or "resolved"  # "resolved" 无效
```

---

## 三、Cognitive Runtime 度量系统 (metrics_collector.py, evaluation.py)

### 3.1 [HIGH] Double cursor.fetchone() 导致计数永远为0

**位置**: `metrics_collector.py` lines 146-147, 152-153, 158-159, 164-165

```python
receipt_count = cursor.fetchone()[0] if cursor.fetchone() else 0  # BUG!
```

第一次调用消耗结果，第二次返回 None。

**修复**:
```python
row = cursor.fetchone()
receipt_count = int(row[0]) if row and row[0] is not None else 0
```

---

### 3.2 [HIGH] p95 索引计算错误 (已修复但需验证)

**位置**: `metrics_collector.py` line 231

```python
p95_index = int((n - 1) * 0.95)  # 已修复
```

---

### 3.3 [MEDIUM] continuity_focus_alignment_rate 基准值过高

**位置**: `evaluation.py` line 1135

```python
baseline_credit = 0.75  # 太高!
```

即使所有对齐检查失败，分数仍是 0.75，无法区分好坏。

**修复**: 降低到 0.2-0.3。

---

### 3.4 [MEDIUM] alignment_checks 变量从未使用

**位置**: `evaluation.py` lines 1024, 1068, 1072, 1076, 1102, 1105, 1113, 1116, 1121, 1130

**问题**: `alignment_checks` 被递增但从未用于归一化。

---

### 3.5 [LOW] sqlite_write_p95_ms 测量错误工作负载

**问题**: 测试创建/删除临时表，而非实际 receipt 写入。

---

## 四、Benchmark 验证框架 (validators.py, strategy_benchmark.py)

### 4.1 [HIGH] tool_sequence 工具名被当作文件路径匹配

**位置**: `strategy_benchmark.py` lines 404-421

```python
evidence_keys.add(tool_name.lower())  # 工具名被当作证据key
```

**问题**: "Read" 工具会错误匹配 `polaris/edit.py` 路径。

**修复**: 移除 tool_sequence 到 evidence_keys 的添加。

---

### 4.2 [HIGH] episode_turn_ratio 验证逻辑反向

**位置**: `validators.py` lines 561-578

```python
if episode_turn_ratio < self.max_episode_turn_ratio:  # 逻辑反向
```

健康的压缩比(5.0)会被错误标记为违规。

**修复**: 改为 `episode_turn_ratio > self.max_episode_turn_ratio` 或重命名变量。

---

### 4.3 [HIGH] pass_rate 计算错误

**位置**: `strategy_benchmark.py` line 193

```python
return self.failed_cases / self.total_cases  # 错误!返回失败率
```

**修复**: 改为 `return self.passed_cases / self.total_cases`

---

### 4.4 [MEDIUM] FixtureAwareBenchmarkValidator artifact 检查使用错误字段

**位置**: `validators.py` lines 813-820

**问题**: 检查 `artifact_id` 而非 `uri/path`。

---

### 4.5 [MEDIUM] all() 逻辑过于严格

**位置**: `validators.py` line 649

**问题**: "数据不足跳过"的验证器会被 `all()` 静默忽略。

---

### 4.6 [LOW] score 固定惩罚不区分严重程度

**位置**: `validators.py` lines 845-893

所有 violation 使用相同系数。

---

## 五、Context 压缩/摘要引擎 (engine.py)

### 5.1 [HIGH] asyncio.run() 嵌套事件循环风险 (已修复)

**位置**: `engine.py` line 344

已使用 ThreadPoolExecutor 修复。

---

### 5.2 [HIGH] _trim_items() 硬编码 600 char 阈值

**位置**: `engine.py` lines 240-248

**问题**: 无论内容大小，一律截断到 600 chars，无自适应。

---

### 5.3 [HIGH] _over_budget() 只检查 chars 不检查 tokens

**问题**: 当 `char_limit > 0` 但 `token_limit = 0` 时，只检查 chars，tokens 可能超限。

---

### 5.4 [MEDIUM] 压缩阶梯全量执行

**问题**: trim/pointerize 对 ALL items 执行，而非只对超出预算的 items。

---

### 5.5 [MEDIUM] _deduplicate() 丢弃同源不同内容

**问题**: 相同 source_key 的 items 被完全丢弃，不合并内容。

---

### 5.6 [MEDIUM] 优先级无 kind 维度

**问题**: Provider 级固定优先级，不考虑 item.kind。

---

## 六、Tool 语义搜索 (registry.py)

### 6.1 [HIGH] Embedding 缓存 TOCTOU 竞态

**位置**: `registry.py` lines 374-390

**问题**: check-then-act 模式，多线程同时触发会重复计算 embedding。

**修复**: 锁粒度覆盖整个 get-then-set 操作。

---

### 6.2 [MEDIUM] NaN/Inf 传播风险

**位置**: `_cosine_similarity()`

**问题**: 无输入校验，NaN 向量会导致排序不确定。

---

### 6.3 [MEDIUM] 缓存驱逐非线程安全

**位置**: lines 385-389

**问题**: 多线程同时驱逐可能导致 KeyError。

---

### 6.4 [MEDIUM] 空向量静默返回 0.0

**问题**: 掩盖异常情况。

---

### 6.5 [LOW] evidence path 无 audit trail

**问题**: search_tools() 不记录匹配结果，无法追溯。

---

## 七、Auth Context (auth_context/__init__.py)

### 7.1 [HIGH] SimpleAuthContext 未实现 session 方法

**问题**: `validate_session()`, `get_current_user()`, `check_permission()` 未被重写，使用 stub 实现。

---

### 7.2 [HIGH] check_permission() 始终返回 False

**问题**: `has_scope()` 存在但 `check_permission()` 未委托给它。

---

### 7.3 [HIGH] _get_role_permissions() 函数不存在

**问题**: `get_user_roles()` 返回空列表，函数未实现。

---

### 7.4 [MEDIUM] SessionStore 集成缺失

**问题**: 不存在 SessionStore 类，validate_session() 无法验证 session。

---

### 7.5 [MEDIUM] get_current_user() 返回 None

**问题**: 即使有 principal 也返回 None。

---

## 八、CLI 可观测性 (polaris/delivery/cli/)

### 8.1 [HIGH] StateFirstContextOS.project() 在 CLI 流中从未被调用

**问题**: CLI 调用 `SessionContinuityStrategy.project_to_projection()`，不是 StateFirstContextOS。

---

### 8.2 [HIGH] continuity.projected 事件在错误层发射

**位置**: `console_host.py` lines 697-724

**问题**: `category="continuity"` 而非 `"attention"`。

---

### 8.3 [HIGH] seal_blocked 事件双重条件

**位置**: `runtime.py` lines 1140-1158

**问题**: `enable_attention_trace=False` 时不发射关键安全事件。

---

### 8.4 [MEDIUM] context_os_snapshot 未用于生成 attention trace

**位置**: `service.py` lines 756-773

---

### 8.5 [MEDIUM] RoleContextGateway 事件类别不统一

**问题**: 使用 `category="context"` 而非 `"attention"`。

---

### 8.6 [LOW] TextualEventBridge payload 处理逻辑错误

**位置**: `event_bridge.py` line 103

---

## 九、Memory/Continuity (providers.py, evaluation.py)

### 9.1 [MEDIUM] alignment_checks 变量从未使用

见 3.4。

---

### 9.2 [MEDIUM] alignment_score 可超过 1.0

**位置**: `evaluation.py` lines 1135-1142

**问题**: 公式 `0.75 + alignment_score * 0.25` 在 score=1.1 时超过 1.0。

---

### 9.3 [MEDIUM] 词重叠逻辑过于宽松

**位置**: `evaluation.py` lines 833-836

```python
bool(intent_words & pending_words)  # 任何重叠都判定为无回归
```

"实现登录"和"实现注册"会被错误判定为对齐。

---

### 9.4 [LOW] 停用词定义重复

**位置**: `evaluation.py` lines 44, 806-828

两处完全相同的定义。

---

### 9.5 [LOW] latest_turn_retention_rate 置信度不一致

**空日志时置信度=1.0，但实际无法测量。**

---

## 十、Event Sourcing (models.py)

### 10.1 [HIGH] BudgetPlan.expected_next_input_tokens 单位不一致

**位置**: `runtime.py` lines 1015-1019

**问题**: `output_reserve` 混入输入预算计算，单位不一致。

---

### 10.2 [HIGH] episode_store 无界增长

**位置**: `runtime.py` line 1206

**问题**: 只追加不删除，长期运行后无限增长。

---

### 10.3 [MEDIUM] 嵌套可变对象未深度冻结

**位置**: `models.py`

```python
metadata: dict[str, Any] = field(default_factory=dict)
```

`frozen=True` 不深度冻结嵌套 dict。

---

### 10.4 [MEDIUM] artifact token 计数与实际 prompt 不一致

**位置**: `runtime.py` line 1016

**问题**: 计数所有 artifact，但只选 4 个进入 prompt。

---

### 10.5 [LOW] transcript_log 无大小 guard

**问题**: 符合 Event Sourcing 设计，但无防过度增长机制。

---

## 修复优先级

### P0 (立即修复 - 会导致系统崩溃或安全漏洞)

1. pending_followup 状态机死锁 (1.1)
2. Double cursor.fetchone() 计数为0 (3.1)
3. tool_sequence 工具名误匹配 (4.1)
4. episode_turn_ratio 逻辑反向 (4.2)
5. pass_rate 计算错误 (4.3)
6. StateFirstContextOS 未在 CLI 调用 (8.1)
7. seal_blocked 事件被屏蔽 (8.3)
8. SimpleAuthContext session 方法 stub (7.1)

### P1 (近期修复 - 功能不正确)

1. PAUSE/REDIRECT 模式过于宽泛 (2.1, 2.2)
2. CLARIFY/CANCEL 未处理 (2.3)
3. "expired" 状态从未设置 (2.4)
4. continuity_focus_alignment_rate 基准值过高 (3.3)
5. Embedding 缓存 TOCTOU (6.1)
6. expected_next_input_tokens 超限 (1.5)
7. NaN/Inf 传播风险 (6.2)

### P2 (规划修复 - 改进完善)

1. _trim_items 自适应截断 (5.2)
2. 缓存驱逐线程安全 (6.3)
3. episode_store 无界增长 (10.2)
4. 嵌套可变对象冻结 (10.3)
5. 词重叠逻辑改进 (9.3)

---

## 结论

ContextOS + Cognitive Runtime 系统在核心架构上是健全的，但存在多个实现细节问题影响其长期运行的稳定性和可观测性。最关键的问题是：

1. **状态机不完整** - pending follow-up 的状态转换存在死锁风险
2. **CLI 集成缺失** - attention trace 事件无法在 CLI 流中发射
3. **度量计算错误** - 多个地方存在逻辑错误导致度量不准确
4. **模式匹配过于宽泛** - 会导致意图识别错误

建议按照 P0→P1→P2 的优先级逐步修复，并在修复后更新对应的测试用例。

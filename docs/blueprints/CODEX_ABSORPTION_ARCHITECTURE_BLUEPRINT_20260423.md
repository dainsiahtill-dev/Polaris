# Codex Mechanism Absorption Architecture Blueprint

**文档编号**: BLUEPRINT-2026-0423-CODEX-ABSORPTION  
**日期**: 2026-04-23  
**状态**: Phase 1 蓝图 — 待 Phase 2 工程落地  
**适用范围**: `src/backend/polaris/kernelone/` 及下游消费模块  
**架构师**: Principal Architect (中书令)  

---

## 1. 一句话目标

将 Codex (OpenAI CLI coding agent) 中经过生产验证的 4 项核心机制，以**零冲突、纯扩展**的方式吸收进 Polaris 现有架构，不做任何平行系统。

---

## 2. 吸收决策矩阵

| Codex 机制 | 吸收 verdict | 理由 | 落点模块 |
|-----------|-------------|------|---------|
| Exponential Backoff + Jitter | **吸收** | Polaris 已有 `BackoffController` 和 `SelfHealingExecutor`，但缺少与 `ErrorCategory` 的自动联动 | `kernelone/resilience/backoff.py` + `kernelone/resilience/retry_policy.py` |
| Dangerous Command Detection | **吸收** | Polaris 已有 `dangerous_patterns.py`，但缺少**结构化审计事件**和**可配置 severity 分级** | `kernelone/security/dangerous_patterns.py` + `kernelone/security/command_auditor.py` |
| SessionSource / SubAgentSource Tracking | **吸收** | Polaris `terminal_console.py` 的 SUPER mode 有 `original_request` 但无**来源溯源链**；TaskMarket 无 `claimed_by` 语义 | `kernelone/traceability/session_source.py` + `cells/runtime/task_market/` |
| Two-Stage Memory Pipeline (job claiming) | **吸收** | TaskMarket 已有 `lease_token`，但缺少**阶段化作业认领**和**去重合并**语义 | `cells/runtime/task_market/public/contracts.py` + `kernelone/akashic/` |
| Token Budget + Auto Compact | **拒绝** | Polaris 已有 `ContextBudgetGate` + `budget_policy.py` + `compaction.py`，功能更完整 |
| Execution Policy DSL (Starlark) | **拒绝** | 引入 Starlark 依赖过重；Polaris 已有 `guardrails.py` + `policy/` 层 |
| Platform Sandbox (Seatbelt/Landlock) | **拒绝** | 平台原生扩展与 Polaris 跨平台目标冲突 |
| SQLite dual-database | **拒绝** | Polaris 无 SQLite 瓶颈，已有 LanceDB + 文件系统混合存储 |
| Mailbox async messaging | **拒绝** | Polaris 以同步角色交接为主，Mailbox mpsc 模型与现有架构冲突 |
| AgentPath hierarchical paths | **拒绝** | Polaris 已有 `task_graph/` + `role/` 的层级标识，AgentPath 是平行概念 |

**最终吸收项：4 项**。其余 6 项因功能重叠或架构冲突而拒绝。

---

## 3. 系统架构图

```
+------------------------------------------------------------------+
|                        Polaris KernelOne                          |
+------------------------------------------------------------------+
|                                                                  |
|  +----------------------+    +------------------------------+   |
|  |  resilience/         |    |  security/                   |   |
|  |  backoff.py          |    |  dangerous_patterns.py       |   |
|  |  retry_policy.py  [NEW] |    |  command_auditor.py     [NEW] |   |
|  |  self_healing.py     |    |  guardrails.py               |   |
|  +----------+-----------+    +--------------+---------------+   |
|             |                               |                    |
|             v                               v                    |
|  +----------------------+    +------------------------------+   |
|  |  errors.py           |    |  traceability/               |   |
|  |  (ErrorCategory,     |    |  session_source.py        [NEW]|   |
|  |   retryable flag)    |    |                              |   |
|  +----------+-----------+    +--------------+---------------+   |
|             |                               |                    |
|             +---------------+---------------+                    |
|                             |                                    |
|                             v                                    |
|  +----------------------------------------------------------+   |
|  |              cells/runtime/task_market/                     |   |
|  |  contracts.py  (扩展: claimed_by, source_chain)        [MOD] |   |
|  +----------------------------------------------------------+   |
|                             |                                    |
|                             v                                    |
|  +----------------------------------------------------------+   |
|  |              delivery/cli/terminal_console.py               |   |
|  |  (扩展: SuperPipelineContext.source_chain)              [MOD] |   |
|  +----------------------------------------------------------+   |
|                                                                  |
+------------------------------------------------------------------+
```

---

## 4. 模块责任划分

### 4.1 模块 A: Retry Policy with ErrorCategory Auto-Binding

**文件**: `src/backend/polaris/kernelone/resilience/retry_policy.py` (NEW)  
**现有依赖**: `backoff.py`, `self_healing.py`, `errors.py`  
**职责**:
- 将 `ErrorCategory` 的 transient 类别自动映射到 `RetryStrategy` 参数
- 提供 `RetryPolicy` 配置对象（max_attempts, base_delay, max_delay, jitter_ratio）
- 提供 `should_retry(error: Exception) -> bool` 统一判断
- 与 `BackoffController` 联动：transient 错误自动触发指数退避

**核心数据流**:
```
LLM call raises RateLimitError
    -> classify_error() -> ErrorCategory.TRANSIENT_RATE_LIMIT
    -> RetryPolicy.should_retry() -> True
    -> BackoffController.on_failure() -> compute delay with jitter
    -> SelfHealingExecutor retries with new delay
```

**不做什么**:
- 不替换 `BackoffController`（扩展其配置接口）
- 不替换 `SelfHealingExecutor`（扩展其 failure classification）

### 4.2 模块 B: Command Auditor with Severity Grading

**文件**: `src/backend/polaris/kernelone/security/command_auditor.py` (NEW)  
**现有依赖**: `dangerous_patterns.py`, `audit.py`  
**职责**:
- 对 `is_dangerous_command()` 的命中结果进行 **severity 分级** (CRITICAL / HIGH / MEDIUM / LOW)
- 生成结构化审计事件 `CommandAuditEvent`（含命令原文、匹配模式、severity、建议动作）
- 提供 `CommandAuditResult` 供调用方决策（阻断 / 警告 / 记录）
- 支持可配置策略：哪些 severity 触发阻断，哪些仅记录

**Severity 映射**:
| 模式 | Severity | 默认动作 |
|------|----------|---------|
| `rm -rf /`, `mkfs`, `dd if=/dev/zero` | CRITICAL | 阻断 + 审计 |
| `curl \| sh`, `wget \| sh`, `powershell -enc` | HIGH | 阻断 + 审计 |
| `eval(`, `exec(`, `os.system` | MEDIUM | 警告 + 审计 |
| `chmod 777`, `chown -R` | LOW | 记录 |

**不做什么**:
- 不替换 `dangerous_patterns.py`（在其之上封装语义层）
- 不引入新的策略 DSL（使用现有 `guardrails.py` 的策略接口）

### 4.3 模块 C: SessionSource Tracking

**文件**: `src/backend/polaris/kernelone/traceability/session_source.py` (NEW)  
**现有依赖**: `terminal_console.py` 的 `SuperPipelineContext`  
**职责**:
- 定义 `SessionSource` 枚举：`USER_DIRECT`, `PM_DELEGATED`, `ARCHITECT_DESIGNED`, `CHIEF_ENGINEER_ANALYZED`, `DIRECTOR_EXECUTED`, `QA_VALIDATED`
- 定义 `SourceChain` 类：不可变链表，记录请求的来源溯源链
- 在 `SuperPipelineContext` 中扩展 `source_chain: SourceChain` 字段
- 在 TaskMarket 的 `TaskWorkItemResultV1` 中扩展 `claimed_by: str` 和 `source_chain: list[str]`

**核心数据流**:
```
User input -> terminal_console
    -> SessionSource.USER_DIRECT
    -> PM 规划后 -> SourceChain.append(PM_DELEGATED)
    -> Director 执行 -> SourceChain.append(DIRECTOR_EXECUTED)
    -> TaskMarket.publish() 携带完整 source_chain
    -> 下游 Cell 可读取来源，做差异化处理
```

**不做什么**:
- 不替换 `original_request` 字段（在其旁新增 `source_chain`）
- 不修改角色核心逻辑（仅扩展上下文传递）

### 4.4 模块 D: TaskMarket Job Claiming (Two-Stage)

**文件**: `src/backend/polaris/cells/runtime/task_market/public/contracts.py` (MOD)  
**现有依赖**: `ClaimTaskWorkItemCommandV1`, `lease_token`  
**职责**:
- 在 TaskWorkItem 中扩展 `stage1_claimed_by: str | None` 和 `stage2_claimed_by: str | None`
- 提供 `try_claim_stage1()` / `try_claim_stage2()` 语义（借鉴 Codex 的 `try_claim_stage1_job`）
- 支持 `claim_or_merge()`：若同一 job 已被认领，返回已有认领者而非失败
- 在 `TaskWorkItemResultV1` 中记录 `consolidated_from: list[str]`（合并来源列表）

**核心数据流**:
```
Stage 1 (Extract):
    Worker A -> try_claim_stage1(job_id) -> success
    Worker B -> try_claim_stage1(job_id) -> returns Worker A (merge)
    Worker A completes -> job.stage1_result = result

Stage 2 (Consolidate):
    Worker C -> try_claim_stage2(job_id) -> success (需 stage1 完成)
    Worker C reads stage1_result -> produces final consolidated result
```

**不做什么**:
- 不替换现有 `lease_token` 机制（在其上叠加阶段语义）
- 不引入新的存储后端（复用 TaskMarket 现有存储）

---

## 5. 技术选型理由

| 决策 | 选型 | 理由 |
|------|------|------|
| Backoff 算法 | 复用 `build_backoff_seconds()` + 20% jitter | 已有实现，Codex 也是 20% jitter，无需改动 |
| Retry 配置 | dataclass `RetryPolicy` | 与 Polaris 的 dataclass-heavy 风格一致 |
| Severity 分级 | 4 级枚举 | 对齐安全行业惯例，足够区分处理策略 |
| SourceChain | 不可变 tuple 链表 | 避免传递中的篡改，天然线程安全 |
| Job Claiming | 两阶段 CAS 语义 | 与 Codex 一致，复用现有 lease_token 原子性 |
| 审计格式 | JSON-compatible dict | 与 Polaris 现有审计系统 `audit.py` 对齐 |

---

## 6. 集成点详述

### 6.1 与 errors.py 的集成

```python
# retry_policy.py 中
from polaris.kernelone.errors import ErrorCategory, classify_error

_TRANSIENT_CATEGORIES = {
    ErrorCategory.TRANSIENT_NETWORK,
    ErrorCategory.TRANSIENT_RATE_LIMIT,
    ErrorCategory.TRANSIENT_RESOURCE,
    ErrorCategory.SERVICE_UNAVAILABLE,
    ErrorCategory.TEMPORARY_FAILURE,
    ErrorCategory.SYSTEM_TIMEOUT,
    ErrorCategory.SYSTEM_CAPACITY,
    ErrorCategory.TIMEOUT,
    ErrorCategory.RATE_LIMIT,
    ErrorCategory.NETWORK_ERROR,
}

def should_retry(error: Exception) -> bool:
    category = classify_error(error)
    return category in _TRANSIENT_CATEGORIES
```

### 6.2 与 terminal_console.py 的集成

```python
# terminal_console.py 中 SuperPipelineContext 扩展
@dataclass
class SuperPipelineContext:
    original_request: str
    source_chain: SourceChain = field(default_factory=lambda: SourceChain.root(SessionSource.USER_DIRECT))
    # ... 现有字段不变
```

### 6.3 与 task_market/contracts.py 的集成

```python
# TaskWorkItemResultV1 扩展
@dataclass
class TaskWorkItemResultV1:
    # ... 现有字段
    claimed_by: str = ""           # [NEW] 谁认领了此工作项
    source_chain: list[str] = field(default_factory=list)  # [NEW] 来源链
    consolidated_from: list[str] = field(default_factory=list)  # [NEW] 合并来源
```

---

## 7. 质量门禁

每项吸收落地后必须通过：

1. **Ruff**: `ruff check . --fix && ruff format .` — 零报错
2. **Mypy**: `mypy <module>.py` — `Success: no issues found`
3. **Pytest**: `pytest <test_module>.py -v` — 100% PASS
4. **集成测试**: 在 `kernelone/resilience/tests/`, `kernelone/security/tests/` 中补充测试
5. **无回归**: 现有 `test_disaster_recovery.py`, `test_guardrails.py` 全部通过

---

## 8. Phase 2 工程委派

| 工程师 | 代号 | 负责模块 | 交付物 |
|--------|------|---------|--------|
| Engineer 1 | 工部主事 | Retry Policy (`retry_policy.py`) | 模块 + 测试 + 与 `self_healing.py` 集成 |
| Engineer 2 | 监察御史 | Command Auditor (`command_auditor.py`) | 模块 + 测试 + severity 分级策略 |
| Engineer 3 | 起居舍人 | SessionSource (`session_source.py`) | 模块 + 与 `terminal_console.py` 集成 |
| Engineer 4 | 仓部郎中 | TaskMarket Claiming (`contracts.py` 扩展) | 两阶段认领 + 合并语义 + 测试 |
| Engineer 5 | 都官郎中 | 质量门禁 + 集成测试 | 全链路测试 + Ruff/Mypy/Pytest 通过 |

---

## 9. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| `terminal_console.py` 过大（~3000 行），集成易出错 | 中 | 中 | 仅新增字段，不修改现有方法体； Engineer 3 需做 diff 审查 |
| TaskMarket 契约变更影响下游 Cell | 中 | 高 | 新增字段均有默认值，向后兼容； Engineer 4 需验证所有 Cell 编译 |
| Severity 分级策略争议 | 低 | 低 | 策略可配置，默认保守（CRITICAL/HIGH 阻断） |
| Backoff 参数与现有调优冲突 | 低 | 中 | `RetryPolicy` 为纯新增，不影响现有硬编码参数 |

---

## 10. 验收标准

- [ ] `RetryPolicy.should_retry()` 正确识别所有 transient ErrorCategory
- [ ] `CommandAuditor` 对 `rm -rf /` 返回 CRITICAL + 阻断建议
- [ ] `SourceChain` 在 SUPER mode 中从 USER_DIRECT 传递到 DIRECTOR_EXECUTED
- [ ] `try_claim_stage1()` 返回 (success, claimant) 二元组
- [ ] 所有新增代码通过 Ruff + Mypy + Pytest
- [ ] 现有测试零回归

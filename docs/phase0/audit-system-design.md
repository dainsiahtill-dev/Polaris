# 审计系统设计文档

**版本**: 1.0  
**日期**: 2026-03-04  
**编码**: UTF-8

---

## 一、当前审计架构

### 1.1 现有组件关系

Polaris 审计系统由四个核心组件构成，形成完整的证据收集、存储、审计闭环：

| 组件 | 路径 | 职责 |
|------|------|------|
| **IndependentAuditService** | `application/audit_service.py` | 独立 QA 审计服务，使用独立 LLM 角色（门下省）确保公正性 |
| **EvidenceCollector** | `domain/verification/evidence_collector.py` | 证据收集器，实时收集文件变更、工具输出、验证结果、LLM交互、策略违规 |
| **LogStore** | `infrastructure/persistence/log_store.py` | JSONL 格式日志存储，存储在 workspace 外部避免污染 |
| **EvidenceStore** | `infrastructure/persistence/evidence_store.py` | 证据文件持久化，支持多版本和角色特定导出 |

### 1.2 数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                         执行阶段 (Director)                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────┐    ┌──────────────────────────────────────┐  │
│  │  EvidenceCollector │───▶│  EvidencePackage                    │  │
│  │  (实时证据收集)    │    │  - FileEvidence                     │  │
│  │                   │    │  - ToolEvidence                     │  │
│  │  收集:            │    │  - VerificationEvidence              │  │
│  │  - 文件变更       │    │  - LLMEvidence                      │  │
│  │  - 工具执行       │    │  - PolicyViolations                 │  │
│  │  - 验证结果       │    │  - AuditEntries                      │  │
│  │  - LLM 交互      │    └──────────────────────────────────────┘  │
│  │  - 策略检查       │                    │                         │
│  └──────────────────┘                    ▼                         │
│                                  ┌──────────────────────────────┐  │
│                                  │    EvidenceStore              │  │
│                                  │    (外部持久化)               │  │
│                                  │    evidence_{iteration}.json  │  │
│                                  │    evidence_log.jsonl         │  │
│                                  └──────────────────────────────┘  │
│                                         │                           │
│                                         ▼                           │
│                                  ┌──────────────────────────────┐  │
│                                  │    LogStore                  │  │
│                                  │    (外部日志)                │  │
│                                  │    events.jsonl              │  │
│                                  │    director.log              │  │
│                                  │    task_{id}/task.log        │  │
│                                  └──────────────────────────────┘  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         审计阶段 (QA)                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────┐    ┌──────────────────────────────────────┐  │
│  │  IndependentAudit │◀───│  EvidencePackage + LogStore          │  │
│  │  Service         │    │  (门下省独立审计)                     │  │
│  │                  │    │                                       │  │
│  │  输出:           │    │  审计输入:                            │  │
│  │  - AuditVerdict │    │  - plan_text                         │  │
│  │    - accepted    │    │  - changed_files                     │  │
│  │    - findings    │    │  - executor_output                   │  │
│  │    - summary     │    │  - tool_results                      │  │
│  └──────────────────┘    └──────────────────────────────────────┘  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.3 存储位置策略

所有审计数据存储在 workspace 外部，避免污染目标项目：

1. **优先**: Ramdisk (`X:\`) - 若 `POLARIS_STATE_TO_RAMDISK` 启用
2. **备选**: 系统缓存目录 (`%LOCALAPPDATA%\Polaris\cache` 或 `~/.cache/polaris`)
3. **兜底**: 显式 `POLARIS_RUNTIME_ROOT`

路径结构: `{runtime_base}/.polaris/projects/{workspace_key}/runtime/`

---

## 二、审计日志 Schema 设计

### 2.1 审计事件结构

基于现有 `LogStore.write_event()` 和 `EvidenceCollector` 实现，定义统一审计事件 Schema：

```json
{
  "event_id": "uuid-v4",
  "timestamp": "2026-03-04T12:00:00.000Z",
  "event_type": "task_start | task_complete | tool_execution | llm_call | verification | policy_check | audit_verdict",
  "version": "1.0",
  "source": {
    "role": "pm | architect | chief_engineer | director | qa",
    "agent_id": "agent-instance-identifier",
    "workspace": "/path/to/workspace"
  },
  "task": {
    "task_id": "task-uuid",
    "iteration": 0,
    "run_id": "run-uuid"
  },
  "resource": {
    "type": "file | directory | tool | llm | policy | verdict",
    "path": "/path/to/resource",
    "operation": "create | read | update | delete | execute"
  },
  "action": {
    "name": "action-name",
    "parameters": {},
    "result": "success | failure | partial"
  },
  "data": {},
  "context": {},
  "hash": "sha256-first-16-chars"
}
```

### 2.2 字段定义

| 字段 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `event_id` | string (UUID) | 是 | 事件唯一标识 |
| `timestamp` | string (ISO8601) | 是 | 事件发生时间，UTC |
| `event_type` | enum | 是 | 事件类型 |
| `version` | string | 是 | Schema 版本 |
| `source.role` | enum | 是 | 产生事件的角色 |
| `source.agent_id` | string | 否 | Agent 实例标识 |
| `source.workspace` | string | 是 | 工作区路径 |
| `task.task_id` | string | 是 | 关联任务 ID |
| `task.iteration` | integer | 是 | 迭代编号 |
| `task.run_id` | string | 否 | 运行批次 ID |
| `resource.type` | enum | 是 | 资源类型 |
| `resource.path` | string | 是 | 资源路径 |
| `resource.operation` | enum | 是 | 操作类型 |
| `action.name` | string | 是 | 动作名称 |
| `action.parameters` | object | 否 | 动作参数 |
| `action.result` | enum | 是 | 执行结果 |
| `data` | object | 否 | 事件数据负载 |
| `context` | object | 否 | 扩展上下文 |
| `hash` | string | 是 | 前16字段的 SHA256 哈希（防篡改） |

### 2.3 事件类型枚举

```python
class AuditEventType(Enum):
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    TOOL_EXECUTION = "tool_execution"
    LLM_CALL = "llm_call"
    VERIFICATION = "verification"
    POLICY_CHECK = "policy_check"
    AUDIT_VERDICT = "audit_verdict"
    FILE_CHANGE = "file_change"
    SECURITY_VIOLATION = "security_violation"
```

### 2.4 索引策略

为支持高效查询，建议建立以下索引：

| 索引类型 | 索引字段 | 用途 |
|----------|----------|------|
| **主索引** | `timestamp` | 时间范围查询 |
| **任务索引** | `task.task_id` | 按任务筛选 |
| **角色索引** | `source.role` | 按角色统计 |
| **事件索引** | `event_type` | 按类型聚合 |
| **资源索引** | `resource.path` | 文件操作审计 |
| **结果索引** | `action.result` | 成功/失败统计 |

**实现建议**:
- 使用 JSONL 存储时，按月份分区: `events-2026-03.jsonl`
- 可选引入 Elasticsearch/Opensearch 做全文检索
- 本地查询使用 `grep` + `jq` 组合

---

## 三、导出格式设计

### 3.1 JSON 导出

**用途**: 完整审计记录、系统间集成、程序化处理

```json
{
  "export_metadata": {
    "export_id": "uuid",
    "exported_at": "2026-03-04T12:00:00.000Z",
    "workspace": "/path/to/workspace",
    "time_range": {
      "start": "2026-03-01T00:00:00.000Z",
      "end": "2026-03-04T23:59:59.000Z"
    },
    "filters": {
      "event_types": ["task_complete", "audit_verdict"],
      "task_ids": ["task-1", "task-2"]
    },
    "record_count": 150
  },
  "events": [
    { /* 审计事件对象 */ }
  ],
  "summary": {
    "total_events": 150,
    "by_type": { "task_complete": 10, "audit_verdict": 10 },
    "by_role": { "director": 50, "qa": 30 },
    "pass_rate": 0.85
  },
  "integrity": {
    "first_hash": "sha256-of-first-event",
    "last_hash": "sha256-of-last-event",
    "chain_valid": true
  }
}
```

### 3.2 CSV 导出

**用途**: 电子表格分析、Excel 导入、合规报告

```csv
event_id,timestamp,event_type,role,task_id,resource_path,operation,result
euuid-001,2026-03-04T10:00:00Z,task_start,director,task-001,,,success
euuid-002,2026-03-04T10:00:01Z,tool_execution,director,task-001,/src/main.py,execute,success
euuid-003,2026-03-04T10:00:05Z,verification,qa,task-001,,,success
euuid-004,2026-03-04T10:00:06Z,audit_verdict,qa,task-001,,,pass
```

**CSV 列映射**:

| CSV 列 | JSON 字段 |
|--------|-----------|
| event_id | event_id |
| timestamp | timestamp |
| event_type | event_type |
| role | source.role |
| task_id | task.task_id |
| resource_path | resource.path |
| operation | resource.operation |
| result | action.result |

### 3.3 Syslog 导出 (RFC 5424)

**用途**: 企业 SIEM 集成、集中日志收集

```
<134>1 2026-03-04T10:00:00.000Z host polaris 1 task_start - - {"event_id":"uuid","role":"director","task_id":"task-001","workspace":"/path/to/workspace"}
```

**Syslog 映射规则**:

| Syslog 字段 | JSON 字段 |
|-------------|-----------|
| PRIORITY | 134 (facility=16 local0 + severity=6 info) |
| VERSION | 1 |
| TIMESTAMP | timestamp |
| HOSTNAME | source.agent_id |
| APP-NAME | "polaris" |
| PROCID | task.iteration |
| MSGID | event_type |
| STRUCTURED-DATA | 序列化的事件数据 |
| MSG | 事件摘要 |

---

## 四、留存策略

### 4.1 保留期限

| 数据类型 | 保留期限 | 依据 |
|----------|----------|------|
| **审计事件** | 90 天 | 默认业务周期 |
| **证据文件** | 180 天 | 支持问题追溯 |
| **LLM 交互哈希** | 365 天 | 合规要求 |
| **完整证据包** | 90 天 | 空间与合规平衡 |
| **审计结论** | 永久 | 法律合规要求 |

**配置参数**:

```python
AUDIT_RETENTION_DAYS = {
    "events": 90,
    "evidence": 180,
    "llm_hashes": 365,
    "verdicts": None,  # 永久
}
```

### 4.2 归档策略

```
┌─────────────────────────────────────────────────────────────┐
│                    数据生命周期                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  热数据 (0-30天)     温数据 (31-90天)    冷数据 (90天+)     │
│  ────────────────   ───────────────   ────────────────      │
│  SSD/本地存储        标准存储           对象存储 (S3/OSS)   │
│  实时索引            月度压缩            年度归档            │
│  快速查询            批量查询            合规保留            │
│                                                              │
│  events.jsonl       events-2026-01.jsonl   archive/       │
│  evidence/          evidence-archive/        (压缩)       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**归档触发条件**:
- 时间触发: 每日 UTC 00:00 检查
- 容量触发: 磁盘使用 > 80%
- 手动触发: 管理员执行归档命令

### 4.3 合规要求

| 合规标准 | 要求 | 实现方式 |
|----------|------|----------|
| **GDPR** | 数据可删除、可携带 | 提供数据导出 API、数据销毁 API |
| **SOX** | 变更审计追踪 | 完整文件变更记录、工具执行日志 |
| **ISO27001** | 访问控制、完整性 | 哈希链、访问日志、只读存储 |
| **SOC2** | 审计日志保护 | 外部存储、不可篡改、保留策略 |

---

## 五、API 设计

### 5.1 审计查询接口

#### 5.1.1 查询审计事件

```
GET /v2/audit/events

Query Parameters:
  - start_time: ISO8601 起始时间 (必填)
  - end_time: ISO8601 结束时间 (必填)
  - event_type: 事件类型过滤 (可选)
  - role: 角色过滤 (可选)
  - task_id: 任务 ID 过滤 (可选)
  - limit: 返回数量限制 (默认 100, 最大 1000)
  - offset: 分页偏移 (默认 0)

Response 200:
{
  "events": [...],
  "pagination": {
    "total": 150,
    "limit": 100,
    "offset": 0,
    "has_more": true
  }
}
```

#### 5.1.2 查询审计结论

```
GET /v2/audit/verdicts

Query Parameters:
  - task_id: 任务 ID (可选)
  - accepted: PASS|FAIL (可选)
  - since: 时间范围起点 (可选)

Response 200:
{
  "verdicts": [
    {
      "task_id": "task-001",
      "accepted": true,
      "summary": "验收通过",
      "findings": [],
      "timestamp": "2026-03-04T10:00:00Z"
    }
  ]
}
```

#### 5.1.3 获取审计统计

```
GET /v2/audit/stats

Query Parameters:
  - start_time: 起始时间 (必填)
  - end_time: 结束时间 (必填)
  - group_by: 聚合维度 (event_type|role|task_id)

Response 200:
{
  "stats": {
    "total_events": 150,
    "by_type": { "task_complete": 10, "audit_verdict": 10 },
    "by_role": { "director": 50, "qa": 30 },
    "pass_rate": 0.85,
    "avg_verification_time_ms": 5000
  },
  "time_range": { "start": "2026-03-01", "end": "2026-03-04" }
}
```

### 5.2 导出接口

#### 5.2.1 导出为 JSON

```
GET /v2/audit/export/json

Query Parameters:
  - start_time: 起始时间 (必填)
  - end_time: 结束时间 (必填)
  - event_types: 逗号分隔的事件类型 (可选)
  - include_data: 是否包含完整数据负载 (默认 false)

Response 200:
Content-Type: application/json
Content-Disposition: attachment; filename="audit-export-2026-03-04.json"
```

#### 5.2.2 导出为 CSV

```
GET /v2/audit/export/csv

Query Parameters:
  - start_time: 起始时间 (必填)
  - end_time: 结束时间 (必填)
  - columns: 逗号分隔的列名 (默认: event_id,timestamp,event_type,role,task_id,result)

Response 200:
Content-Type: text/csv
Content-Disposition: attachment; filename="audit-export-2026-03-04.csv"
```

#### 5.2.3 导出为 Syslog

```
GET /v2/audit/export/syslog

Query Parameters:
  - start_time: 起始时间 (必填)
  - end_time: 结束时间 (必填)
  - facility: Syslog facility (默认 16=local0)

Response 200:
Content-Type: application/octet-stream
```

### 5.3 管理接口

#### 5.3.1 数据清理

```
POST /v2/audit/cleanup

Body:
{
  "before": "2026-01-01T00:00:00Z",
  "dry_run": true,
  "target": "events|evidence|all"
}

Response 200:
{
  "would_delete": 150,
  "would_free_mb": 250,
  "affected_tasks": ["task-001", "task-002"]
}
```

#### 5.3.2 验证完整性

```
GET /v2/audit/integrity

Response 200:
{
  "chain_valid": true,
  "first_event_hash": "abc123...",
  "last_event_hash": "def456...",
  "gap_count": 0,
  "verified_at": "2026-03-04T12:00:00Z"
}
```

---

## 六、增强建议

### 6.1 Append-Only 机制

**目标**: 防止审计日志被篡改或选择性删除

**实现方案**:

```python
class AppendOnlyLogStore:
    """Append-only audit log store with write protection."""
    
    def __init__(self, runtime_root: Path):
        self.log_file = runtime_root / "audit.jsonl"
        self._ensure_initialized()
    
    def _ensure_initialized(self):
        """Initialize with marker if empty."""
        if not self.log_file.exists():
            with open(self.log_file, "w", encoding="utf-8") as f:
                f.write("# POLARIS AUDIT LOG - APPEND ONLY\n")
    
    def append(self, event: Dict) -> None:
        """Append event - only operation allowed."""
        # Write to temp, then atomic rename
        temp_file = self.log_file.with_suffix('.tmp')
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        
        # Atomic append
        with open(self.log_file, "ab") as target:
            with open(temp_file, "rb") as source:
                target.write(source.read())
        
        temp_file.unlink()
    
    def _prevent_truncation(self):
        """Reject any write mode that truncates."""
        # Override open to prevent 'w' mode
        pass
```

**文件系统级保护**:
- 设置文件为只读属性 (`chmod 444`)
- 使用 WORM (Write Once Read Many) 存储
- 定期校验文件哈希

### 6.2 关键字段签名（防篡改）

**目标**: 检测审计记录是否被篡改

**实现方案 - 哈希链**:

```python
import hmac
import hashlib

class SignedAuditLog:
    """Audit log with cryptographic integrity verification."""
    
    def __init__(self, secret_key: str):
        self.secret_key = secret_key.encode()
        self._last_hash = None
    
    def _compute_signature(self, event: Dict, previous_hash: str) -> str:
        """Compute HMAC-SHA256 signature for event."""
        payload = json.dumps(event, sort_keys=True, ensure_ascii=False)
        message = f"{previous_hash}:{payload}".encode()
        return hmac.new(self.secret_key, message, hashlib.sha256).hexdigest()
    
    def append(self, event: Dict) -> Dict:
        """Append signed event."""
        event["_prev_hash"] = self._last_hash or "GENESIS"
        event["_signature"] = self._compute_signature(
            event, 
            event["_prev_hash"]
        )
        
        # Update chain
        content = json.dumps(event, sort_keys=True, ensure_ascii=False)
        self._last_hash = hashlib.sha256(content.encode()).hexdigest()
        
        return event
    
    def verify_chain(self, events: List[Dict]) -> bool:
        """Verify entire chain integrity."""
        prev_hash = "GENESIS"
        
        for event in events:
            expected_sig = self._compute_signature(event, prev_hash)
            if event.get("_signature") != expected_sig:
                return False
            prev_hash = event.get("_prev_hash", "GENESIS")
        
        return True
```

**事件签名结构**:

```json
{
  "event_id": "uuid",
  "timestamp": "2026-03-04T10:00:00Z",
  "event_type": "task_complete",
  "_prev_hash": "abc123...",
  "_signature": "hmac-sha256-signature"
}
```

### 6.3 实时审计告警

**目标**: 及时发现异常行为和安全威胁

**告警规则引擎**:

```python
from enum import Enum

class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class AuditAlertRule:
    """Audit alert rule definition."""
    
    def __init__(
        self,
        name: str,
        condition: Callable[[AuditEvent], bool],
        severity: AlertSeverity,
        message_template: str,
    ):
        self.name = name
        self.condition = condition
        self.severity = severity
        self.message_template = message_template

# 预定义告警规则
AUDIT_ALERT_RULES = [
    AuditAlertRule(
        name="security_violation",
        condition=lambda e: e.event_type == "security_violation",
        severity=AlertSeverity.CRITICAL,
        message_template="Security violation detected: {details}"
    ),
    AuditAlertRule(
        name="excessive_tool_calls",
        condition=lambda e: e.data.get("tool_call_count", 0) > 100,
        severity=AlertSeverity.HIGH,
        message_template="Excessive tool calls: {count}"
    ),
    AuditAlertRule(
        name="failed_verification",
        condition=lambda e: e.event_type == "verification" and e.action.result == "failure",
        severity=AlertSeverity.MEDIUM,
        message_template="Verification failed: {task_id}"
    ),
    AuditAlertRule(
        name="audit_rejected",
        condition=lambda e: e.event_type == "audit_verdict" and e.data.get("accepted") == False,
        severity=AlertSeverity.HIGH,
        message_template="Audit rejected task: {task_id}"
    ),
]
```

**告警通道**:

| 通道 | 配置 | 用途 |
|------|------|------|
| **Webhook** | `AUDIT_ALERT_WEBHOOK_URL` | 集成 PagerDuty, Slack |
| **Email** | `AUDIT_ALERT_SMTP_*` | 管理员通知 |
| **日志** | 写入 `alerts.log` | 本地记录 |
| **Syslog** | 转发到 SIEM | 企业合规 |

**告警输出示例**:

```json
{
  "alert_id": "alert-uuid",
  "triggered_at": "2026-03-04T10:00:00Z",
  "rule": "security_violation",
  "severity": "critical",
  "message": "Security violation: Unauthorized file access attempt",
  "event": { /* 触发告警的事件 */ },
  "workspace": "/path/to/workspace"
}
```

---

## 七、总结

本文档定义了 Polaris Phase 0 审计系统的完整设计方案：

1. **当前架构**: 已实现的四个核心组件（IndependentAuditService、EvidenceCollector、LogStore、EvidenceStore）形成完整的审计闭环

2. **Schema 设计**: 统一的审计事件结构，支持时间、任务、角色、事件类型、资源等多维度索引

3. **导出格式**: JSON（完整数据）、CSV（分析）、Syslog（SIEM 集成）三种格式满足不同场景

4. **留存策略**: 90/180/365 天分级保留，支持热/温/冷数据生命周期管理

5. **API 设计**: 查询、导出、管理三类接口，覆盖审计全流程

6. **增强建议**: Append-only 机制、哈希链签名、实时告警提供企业级安全保障

---

*本文档为设计规范，实现时需参考代码中的实际接口和数据结构。*

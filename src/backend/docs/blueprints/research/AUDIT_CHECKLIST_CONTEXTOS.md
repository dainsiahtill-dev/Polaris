# ContextOS 深度审计清单

**版本**: v1.0  
**日期**: 2026-04-12  
**审计对象**: polaris/kernelone/context/context_os/  
**审计目标**: 评估事件溯源完整性、压缩质量、意图切换准确性

---

## 1. 事件溯源 (Event Sourcing) 审计

### 1.1 事件日志完整性

#### 检查项 ES-01: 序列号连续性
- [ ] 读取 runtime/events/*.jsonl 文件
- [ ] 验证 seq 字段是否严格递增
- [ ] 检查序列号轮转点 (seq 大幅下降)
- [ ] 确认轮转时是否有事件丢失

**审计方法**:
```python
def audit_sequence_continuity(events):
    drops = []
    for i in range(1, len(events)):
        if events[i]['seq'] < events[i-1]['seq']:
            drops.append((i, events[i-1]['seq'], events[i]['seq']))
    return drops
```

**通过标准**: 
- 轮转次数 <= 1次/1000事件
- 无 seq 重复

---

#### 检查项 ES-02: 事件ID唯一性
- [ ] 提取所有 event_id
- [ ] 检查是否有重复
- [ ] 确认 event_id 生成算法(UUID/ULID)

**通过标准**: 100%唯一性

---

#### 检查项 ES-03: 事件配对完整性
- [ ] 统计 llm_call_start 和 llm_call_end 数量
- [ ] 统计 tool_call 和 tool_result 数量
- [ ] 检查未配对的事件

**通过标准**:
- start/end 数量差异 <= 1 (允许最后一个未结束)
- tool_call/tool_result 数量相等

---

#### 检查项 ES-04: 迭代追踪 (Iteration Tracking)
- [ ] 检查 llm_call 的 iteration 字段
- [ ] 检查 tool_call 的 iteration 字段 ⚠️ **重点关注**
- [ ] 验证同一次 turn 的所有事件 iteration 一致

**预期发现**:
```python
# 当前状态 (Bug)
llm_call_end  iter=14  ✅
tool_call     iter=None ❌  # 应该为 14
tool_result   iter=None ❌  # 应该为 14
```

**影响评估**:
- [ ] 无法按 turn 追踪工具调用链
- [ ] 断路器难以定位具体 turn
- [ ] 调试困难

---

#### 检查项 ES-05: 时间戳一致性
- [ ] 验证 ts_epoch 单调递增
- [ ] 检查 ts 和 ts_epoch 是否对应
- [ ] 检查时区处理 (UTC)

**通过标准**: 时间戳漂移 < 1秒

---

### 1.2 事件内容审计

#### 检查项 EC-01: Run ID 管理
- [ ] 统计不同 run_id 数量
- [ ] 检查 run_id 生命周期
- [ ] 验证跨 run 事件隔离

**审计方法**:
```python
def audit_run_lifecycle(events):
    runs = defaultdict(list)
    for e in events:
        runs[e['run_id']].append(e)
    
    for run_id, run_events in runs.items():
        print(f"Run {run_id}: {len(run_events)} events")
        print(f"  Duration: {start} -> {end}")
        print(f"  Max iteration: {max_iter}")
```

---

#### 检查项 EC-02: 高迭代检测
- [ ] 识别 iteration > 15 的 run
- [ ] 分析高迭代原因
- [ ] 检查是否伴随 tool_error

**预警阈值**:
- 🟡 iteration > 10: 需关注
- 🔴 iteration > 20: 可能死循环

---

#### 检查项 EC-03: 错误事件分析
- [ ] 统计 tool_error 和 llm_error
- [ ] 分类错误类型
- [ ] 检查错误恢复模式

**错误分类**:
```python
error_categories = {
    'authorization': [],  # 授权失败
    'timeout': [],        # 超时
    'invalid_args': [],   # 参数错误
    'execution': [],      # 执行失败
    'stream_cancelled': [], # 流取消
}
```

---

## 2. 上下文压缩 (Context Compression) 审计

### 2.1 Turn-Block 压缩

#### 检查项 CC-01: 压缩触发条件
- [ ] 统计压缩触发次数
- [ ] 验证触发阈值 (50 events)
- [ ] 检查紧急压缩触发

**审计代码**:
```python
compressions = [e for e in events 
                if e.get('data', {}).get('compression_applied')]
```

---

#### 检查项 CC-02: 当前 Turn 保护
- [ ] 验证最新 turn 的事件是否被完整保留
- [ ] 检查 source_turns 字段使用
- [ ] 确认当前 turn 不会被压缩分割

**测试方法**:
1. 构造 60+ 事件场景
2. 触发压缩
3. 验证最新 turn 完整性

---

#### 检查项 CC-03: 工具链完整性
- [ ] 检查 tool_call + tool_result 是否成对保留
- [ ] 验证中间结果被正确折叠

**通过标准**: 同一 turn 内的工具调用链不被分割

---

### 2.2 压缩质量评估

#### 检查项 CQ-01: 信息保留率
- [ ] 对比压缩前后关键信息
- [ ] 检查错误关键字保留 (ERROR, exception等)
- [ ] 验证文件路径保留

**质量指标**:
```python
quality_metrics = {
    'error_keywords_retained': 0.0,  # 目标: >95%
    'file_paths_retained': 0.0,      # 目标: >90%
    'decision_points_retained': 0.0, # 目标: >80%
}
```

---

#### 检查项 CQ-02: 压缩后可用性
- [ ] 人工检查压缩后的提示词可读性
- [ ] 验证 LLM 能否理解压缩内容
- [ ] 检查是否出现"压缩幻觉"

**测试方法**:
1. 选取 10 个压缩样本
2. 人工评分 1-5
3. 统计平均分

---

## 3. 意图切换 (Intent Switch) 审计

### 3.1 检测准确性

#### 检查项 IS-01: 动词识别
- [ ] 测试 view_verbs 列表覆盖度
- [ ] 测试 write_verbs 列表覆盖度
- [ ] 检查中英文混合场景

**测试用例**:
```python
test_cases = [
    ("看下server.js", "创建role_logger.py"),  # 应触发
    ("分析代码结构", "写个测试"),             # 应触发
    ("修改配置", "提交更改"),                 # 不应触发 (write->write)
    ("read the file", "edit it"),             # 应触发
]
```

---

#### 检查项 IS-02: 摘要生成
- [ ] 检查摘要是否保留关键发现
- [ ] 验证摘要长度 (< 100字符)
- [ ] 测试 artifacts 数量准确性

**示例验证**:
```
输入: "分析server.js实现"
输出: "[已完成: 分析server.js] 已探明3个对象; 决策: Express路由分析完成"

检查点:
- [ ] 包含原目标
- [ ] 包含对象数量
- [ ] 包含关键决策
```

---

### 3.2 状态同步

#### 检查项 SS-01: Latest Intent vs Current Goal
- [ ] 检查两者是否可能冲突
- [ ] 验证切换后 Current Goal 更新
- [ ] 检查旧 Goal 清理

**审计方法**:
```python
def audit_intent_sync(events):
    for e in events:
        if 'intent_switch' in str(e):
            print(f"Switch at {e['ts']}")
            print(f"  Old: {e['data'].get('old_intent')}")
            print(f"  New: {e['data'].get('new_intent')}")
```

---

## 4. 可观测性 (Observability) 审计

### 4.1 指标收集

#### 检查项 OB-01: 断路器指标
- [ ] 验证 circuit_breaker_triggers 计数
- [ ] 检查 breaker_type 标签分类
- [ ] 确认工具名称记录

**预期指标**:
```
role_kernel_circuit_breaker_total{breaker_type="same_tool"} 5
role_kernel_circuit_breaker_total{breaker_type="stagnation"} 2
```

---

#### 检查项 OB-02: 意图切换指标
- [ ] 验证 intent_switches_detected 计数
- [ ] 检查 verb 分类统计

---

#### 检查项 OB-03: 压缩指标
- [ ] 验证 emergency_compactions 计数
- [ ] 检查 event_count 分布

---

### 4.2 日志完整性

#### 检查项 LI-01: 断路器事件日志
- [ ] 搜索 Circuit Breaker 相关日志
- [ ] 检查是否包含 recovery_hint
- [ ] 验证错误上下文完整

**预期发现**:
```
⚠️ 当前日志中未找到 Circuit Breaker 事件
原因可能是:
1. 断路器未触发
2. 触发但未 emit 事件
3. 事件被轮转删除
```

---

## 5. 边界案例测试

### 5.1 极端场景

#### 测试 BC-01: 单 Turn 大量工具调用
- [ ] 构造单 turn 20+ 工具调用场景
- [ ] 验证 Turn-Block 压缩行为
- [ ] 检查是否触发断路器

#### 测试 BC-02: 快速意图切换
- [ ] 模拟 3 秒内 2 次意图切换
- [ ] 检查状态一致性
- [ ] 验证摘要累积

#### 测试 BC-03: 长时间运行
- [ ] 模拟 1 小时连续对话
- [ ] 检查内存使用
- [ ] 验证日志轮转

---

## 6. 审计工具

### 6.1 自动化脚本

```python
# audit_contextos.py
import json
from collections import defaultdict

class ContextOSAuditor:
    def __init__(self, log_path):
        self.events = self._load_events(log_path)
        self.findings = []
    
    def audit_sequence_continuity(self):
        """ES-01: 序列号连续性"""
        drops = []
        for i in range(1, len(self.events)):
            if self.events[i]['seq'] < self.events[i-1]['seq']:
                drops.append(i)
        
        if drops:
            self.findings.append({
                'id': 'ES-01',
                'severity': 'WARNING',
                'message': f'Found {len(drops)} sequence drops',
                'locations': drops[:5]  # 前5个
            })
    
    def audit_iteration_tracking(self):
        """ES-04: 迭代追踪"""
        tool_calls = [e for e in self.events if e.get('event') == 'tool_call']
        null_iters = [e for e in tool_calls if e.get('iteration') is None]
        
        if null_iters:
            self.findings.append({
                'id': 'ES-04',
                'severity': 'HIGH',
                'message': f'{len(null_iters)}/{len(tool_calls)} tool calls have iteration=None',
                'example': null_iters[0] if null_iters else None
            })
    
    def audit_high_iterations(self):
        """EC-02: 高迭代检测"""
        by_run = defaultdict(list)
        for e in self.events:
            by_run[e.get('run_id')].append(e.get('iteration', 0))
        
        for run_id, iters in by_run.items():
            max_iter = max(iters)
            if max_iter > 15:
                self.findings.append({
                    'id': 'EC-02',
                    'severity': 'WARNING',
                    'message': f'Run {run_id[-8:]} has high iteration: {max_iter}',
                    'run_id': run_id
                })
    
    def generate_report(self):
        """生成审计报告"""
        return {
            'total_events': len(self.events),
            'findings_count': len(self.findings),
            'findings_by_severity': {
                'CRITICAL': len([f for f in self.findings if f['severity'] == 'CRITICAL']),
                'HIGH': len([f for f in self.findings if f['severity'] == 'HIGH']),
                'WARNING': len([f for f in self.findings if f['severity'] == 'WARNING']),
            },
            'findings': self.findings
        }

# 使用示例
if __name__ == '__main__':
    auditor = ContextOSAuditor('director.llm.events.jsonl')
    auditor.audit_sequence_continuity()
    auditor.audit_iteration_tracking()
    auditor.audit_high_iterations()
    report = auditor.generate_report()
    print(json.dumps(report, indent=2))
```

---

## 7. 审计报告模板

```markdown
# ContextOS 审计报告

**执行日期**: 2026-XX-XX  
**审计人员**: [C-01, C-02, C-03]  
**日志来源**: director.llm.events.jsonl

## 执行摘要

| 指标 | 数值 | 评级 |
|------|------|------|
| 总事件数 | XXX | - |
| 序列号下降次数 | X | 🟡/🔴 |
| 重复 event_id | X | 🟢/🔴 |
| Tool Call iteration=None | XX% | 🟢/🟡/🔴 |
| 高迭代 Run (>15) | X | 🟢/🟡/🔴 |
| 错误事件 | X | 🟢/🟡/🔴 |

## 详细发现

### 高优先级

#### [ES-04] Tool Call 迭代追踪缺失
- **严重程度**: HIGH
- **描述**: 59/59 tool calls have iteration=None
- **影响**: 无法按 turn 追踪工具调用
- **建议修复**: 在 emit_event 时传递 iteration

### 中优先级
...

### 低优先级
...

## 改进建议

1. ...
2. ...
3. ...

## 附录

- 原始日志样本
- 测试脚本输出
- 截图/图表
```

---

## 8. 签名

**审计完成确认**:

| 角色 | 姓名 | 签名 | 日期 |
|------|------|------|------|
| ContextOS 专家1 | | | |
| ContextOS 专家2 | | | |
| ContextOS 专家3 | | | |

**审核确认**:

| 角色 | 姓名 | 签名 | 日期 |
|------|------|------|------|
| 首席架构师 | | | |
| 研究主席 | | | |

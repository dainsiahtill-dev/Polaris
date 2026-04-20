# 决策追溯系统设计

> 关联: [实施路线图](../implementation-roadmap.md) Phase 1.1
> 依赖: [EvidenceBundle 设计](./evidence-bundle.md)
> 状态: DESIGN
> 最后更新: 2024-03-08

---

## 用户场景

### 场景 1: 回顾历史决策
```
用户: "上个月 AGI 优化的错误处理，具体改了什么？"
系统: 展示决策卡片 → 点击展开 → 显示代码 diff + 测试报告
```

### 场景 2: 理解代码变更原因
```
用户: "这行代码为什么这样写？"
系统: 右键代码 → "查看相关决策" → 显示修改此代码的决策历史
```

### 场景 3: 审查证据链
```
用户: "这个优化真的有效吗？"
系统: 展示决策 → 显示 test_results → 显示 performance_snapshot
```

---

## 追溯维度

### 时间维度
```
决策时间线:
2024-01-10  [决策A] 重构错误处理
    └── Evidence: 修改了 error.ts, handler.ts
2024-01-15  [决策B] 添加 trace_id
    └── Evidence: 修改了 error.ts
    └── 关联: 基于决策A的发现
```

### 代码维度
```
文件: src/utils/error.ts
├── 第 10-20 行: [决策A] 重构错误处理
├── 第 15 行:    [决策B] 添加 trace_id
└── 第 30 行:    [决策C] 优化日志格式
```

### 目标维度
```
目标: "优化系统性能"
├── [决策A] 重构错误处理 → Evidence: perf +20%
├── [决策B] 优化数据库查询 → Evidence: perf +15%
└── [决策C] 缓存热点数据 → Evidence: perf +30%
```

---

## 数据模型扩展

### DecisionRecord 新增字段

```python
@dataclass
class DecisionRecord:
    # 已有字段...
    decision_id: str
    actor: str
    summary: str
    verdict: str
    timestamp: datetime

    # 新增: 证据关联
    evidence_bundle_id: Optional[str]  # → EvidenceBundle

    # 新增: 追溯链接
    parent_decision_id: Optional[str]  # 父决策（形成链）
    related_goal_id: Optional[str]     # 关联目标

    # 新增: 代码定位（便于从代码查决策）
    affected_files: List[str]          # 影响的文件列表
    affected_symbols: List[str]        # 影响的符号（函数/类）
```

### 代码-决策索引

```python
# 用于快速查询"某段代码关联哪些决策"
class CodeDecisionIndex:
    """代码到决策的倒排索引"""

    def add_mapping(self, file_path: str, line_range: Tuple[int, int], decision_id: str):
        """记录某段代码关联的决策"""

    def find_decisions(
        self,
        file_path: str,
        line_number: Optional[int] = None,
    ) -> List[str]:
        """查询文件/行号关联的决策ID列表"""
```

存储格式:
```json
// .polaris/runtime/code_decision_index.json
{
  "src/utils/error.ts": {
    "10-30": ["decision-001", "decision-003"],
    "15-25": ["decision-002"]
  }
}
```

---

## API 设计

### 后端 API

```python
# GET /api/resident/decisions/{decision_id}
# 返回 DecisionRecord + EvidenceBundle

# GET /api/resident/decisions/{decision_id}/trace
# 返回决策链: parent -> current -> children

# GET /api/resident/code/decisions?file=src/utils/error.ts&line=15
# 返回影响该代码行的决策列表

# GET /api/resident/goals/{goal_id}/decisions
# 返回该目标下的所有决策
```

### WebSocket 事件

```typescript
// 决策更新时推送
{
  type: 'decision_recorded',
  payload: {
    decision_id: string,
    summary: string,
    evidence_bundle_id: string,
    affected_files: string[],
  }
}
```

---

## 前端组件

### DecisionCard（增强版）

```typescript
interface DecisionCardProps {
  decision: DecisionRecord;
  showEvidence?: boolean;      // 是否展开证据
  showTrace?: boolean;         // 是否显示决策链
}

// 展示:
// ┌─────────────────────────────────────┐
// │ [优化错误处理] 2024-01-10            │
// │ 状态: ✅ success                     │
// │ [展开证据] [查看决策链] [影响文件]    │
// └─────────────────────────────────────┘
```

### CodeDecisionLens（代码透镜）

```typescript
// 在代码编辑器中显示决策标记
// 类似于 VSCode 的 CodeLens

// 显示效果:
//    10: function handleError(err) {
// [AGI: 重构错误处理 · 2024-01-10]
//    11:   const traceId = generateTraceId();
// [AGI: 添加 trace_id · 2024-01-15]
```

### DecisionTimeline（决策时间线）

```typescript
interface DecisionTimelineProps {
  workspace: string;
  filter?: {
    goal_id?: string;
    file_path?: string;
    date_range?: [Date, Date];
  };
}

// 垂直时间线展示
```

---

## 集成点

### 与 Director Workflow 集成

```python
# director_task_workflow.py

async def on_task_complete(task, result):
    # 现有: 记录决策
    decision = await record_resident_decision(...)

    # 新增: 创建证据包并关联
    bundle = await evidence_service.create_from_director_run(
        workspace=workspace,
        run_id=run_id,
        task_results=[result],
    )
    decision.evidence_bundle_id = bundle.bundle_id
    decision.affected_files = [c.path for c in bundle.change_set]

    await save_decision(decision)
```

### 与 RealTimeFileDiff 集成

```typescript
// 复用现有的 RealTimeFileDiff 组件展示证据

function EvidenceViewer({ bundleId }: { bundleId: string }) {
  const bundle = useEvidenceBundle(bundleId);

  return (
    <div>
      {bundle.change_set.map(change => (
        <RealTimeFileDiff
          key={change.path}
          path={change.path}
          patch={change.patch}
        />
      ))}
    </div>
  );
}
```

---

## 实现步骤

1. **数据模型扩展** (`models.py`)
   - DecisionRecord 新增字段
   - CodeDecisionIndex 索引类

2. **服务层** (`traceability_service.py`)
   - 决策链查询
   - 代码-决策索引维护

3. **API 层** (`api/v2/resident.py`)
   - 新增追溯相关端点

4. **前端组件**
   - 增强 DecisionCard
   - DecisionTimeline 时间线
   - CodeDecisionLens（代码编辑器集成）

5. **索引构建**
   - 历史决策迁移（如果有）
   - 定期索引更新

---

## 验收标准

```typescript
// E2E 测试
it('should trace decision to code changes', async () => {
  // 1. 执行 Director 任务
  const run = await directorService.executeGoal('优化错误处理');

  // 2. 获取产生的决策
  const decision = await residentService.getDecisions({
    source_run_id: run.id,
  })[0];

  // 3. 决策有关联的证据包
  expect(decision.evidence_bundle_id).toBeDefined();

  // 4. 证据包包含变更
  const bundle = await evidenceService.getBundle(decision.evidence_bundle_id);
  expect(bundle.change_set.length).toBeGreaterThan(0);

  // 5. 能从代码查到决策
  const decisions = await residentService.findDecisionsByCode({
    file: 'src/utils/error.ts',
    line: 15,
  });
  expect(decisions.map(d => d.id)).toContain(decision.id);
});
```

---

## 相关文档

- [EvidenceBundle 设计](./evidence-bundle.md)
- [实施路线图](../implementation-roadmap.md)

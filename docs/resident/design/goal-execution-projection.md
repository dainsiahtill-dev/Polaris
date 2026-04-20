# 目标执行投影设计

> 关联: [实施路线图](../implementation-roadmap.md) Phase 1.2
> 依赖: Phase 1.1 统一变更证据模型
> 状态: DESIGN → IMPLEMENTATION
> 最后更新: 2024-03-08

---

## 核心概念

**执行投影 (Execution Projection)** 是**派生视图**，不污染持久化的 `GoalProposal`。

```
持久状态 (Persistent)          派生视图 (Projected)
┌─────────────────┐           ┌─────────────────────────┐
│ GoalProposal    │──────────→│ GoalExecutionView       │
│ - goal_id       │  实时计算  │ - stage                 │
│ - title         │           │ - percent               │
│ - status        │           │ - current_task          │
│ - materialized  │           │ - eta_minutes           │
└─────────────────┘           └─────────────────────────┘
                                      ↑
                                      │ 关联
                              ┌───────┴───────┐
                              │ TaskProgress  │
                              │ (Director)    │
                              └───────────────┘
```

---

## 用户场景

### 场景 1: 实时查看目标进度
```
用户: 查看 AGI 工作区
系统:
  目标: 优化错误处理
  进度: coding ████████░░ 65%
  当前: 重构 error_handler.ts
  预计: 8 分钟完成
```

### 场景 2: 阶段自动推断
```
PM/Director 任务执行中...

阶段推断规则:
- planning  → 任务以 "plan", "设计", "分析" 开头
- coding    → 任务包含 "实现", "编写", "重构", "修改"
- testing   → 任务包含 "测试", "验证", "fix", "修复"
- review    → 任务包含 "审查", "review", "检查"
```

### 场景 3: 多目标并行监控
```
目标 A: [planning ██░░░░░░░░ 20%] 设计新架构
目标 B: [coding   ██████░░░░ 60%] 重构模块
目标 C: [testing  █████████░ 90%] 验证修复
```

---

## 数据模型

### GoalExecutionView (派生视图)

```python
@dataclass
class GoalExecutionView:
    """目标执行投影 - 派生视图，不持久化"""
    goal_id: str

    # 执行阶段
    stage: Literal["planning", "coding", "testing", "review", "completed", "unknown"]

    # 进度百分比 (0.0 - 1.0)
    percent: float

    # 当前正在执行的任务
    current_task: Optional[str]

    # 预计剩余时间（分钟）
    eta_minutes: Optional[int]

    # 任务统计
    total_tasks: int
    completed_tasks: int
    failed_tasks: int

    # 时间戳
    started_at: Optional[str]
    updated_at: str

    # 关联的任务列表
    task_progress: List[TaskProgressItem]
```

### TaskProgressItem

```python
@dataclass
class TaskProgressItem:
    """单个任务的进度"""
    task_id: str
    subject: str
    status: Literal["pending", "in_progress", "completed", "failed", "blocked"]
    progress_percent: float  # 0.0 - 1.0
    started_at: Optional[str]
    completed_at: Optional[str]
```

---

## 阶段推断算法

```python
def infer_stage(tasks: List[TaskProgressItem]) -> str:
    """从任务列表推断当前阶段"""

    # 优先级: failed > in_progress > completed
    active_tasks = [t for t in tasks if t.status in ("in_progress", "failed")]

    if not active_tasks and all(t.status == "completed" for t in tasks):
        return "completed"

    # 检查第一个未完成的任务
    pending = [t for t in tasks if t.status != "completed"]
    if not pending:
        return "completed"

    first_pending = pending[0]
    subject = first_pending.subject.lower()

    # 阶段关键词匹配
    stage_keywords = {
        "planning": ["plan", "设计", "分析", "调研", "方案", "架构", "proposal", "design"],
        "coding": ["实现", "编写", "重构", "修改", "添加", "更新", "implement", "code", "refactor", "write", "modify", "add"],
        "testing": ["测试", "验证", "fix", "修复", "test", "verify", "check", "debug"],
        "review": ["审查", "review", "检查", "audit", "inspect"],
    }

    for stage, keywords in stage_keywords.items():
        if any(kw in subject for kw in keywords):
            return stage

    # 默认: 根据完成比例推断
    completed = len([t for t in tasks if t.status == "completed"])
    ratio = completed / len(tasks) if tasks else 0

    if ratio < 0.2:
        return "planning"
    elif ratio < 0.7:
        return "coding"
    else:
        return "testing"
```

---

## ETA 估算算法

```python
def estimate_eta(tasks: List[TaskProgressItem]) -> Optional[int]:
    """估算剩余时间（分钟）"""

    # 已完成的任务作为基准
    completed = [t for t in tasks if t.status == "completed" and t.started_at and t.completed_at]

    if not completed:
        # 无历史数据，使用默认估算
        pending = [t for t in tasks if t.status != "completed"]
        return len(pending) * 5  # 默认每个任务 5 分钟

    # 计算平均任务耗时
    durations = []
    for task in completed:
        try:
            start = datetime.fromisoformat(task.started_at)
            end = datetime.fromisoformat(task.completed_at)
            durations.append((end - start).total_seconds() / 60)
        except:
            continue

    if not durations:
        return None

    avg_duration = sum(durations) / len(durations)

    # 估算剩余任务
    remaining = [t for t in tasks if t.status in ("pending", "in_progress", "blocked")]
    eta = int(len(remaining) * avg_duration)

    # 边界限制
    return max(1, min(eta, 120))  # 最少 1 分钟，最多 2 小时
```

---

## API 设计

### 后端 API

```python
# GET /api/v2/resident/goals/{goal_id}/execution
# 返回 GoalExecutionView

{
  "goal_id": "goal-xxx",
  "stage": "coding",
  "percent": 0.65,
  "current_task": "重构 error_handler.ts",
  "eta_minutes": 12,
  "total_tasks": 10,
  "completed_tasks": 6,
  "failed_tasks": 0,
  "started_at": "2024-03-08T10:00:00Z",
  "updated_at": "2024-03-08T10:30:00Z",
  "task_progress": [
    {
      "task_id": "task-1",
      "subject": "分析现有错误处理",
      "status": "completed",
      "progress_percent": 1.0
    },
    {
      "task_id": "task-2",
      "subject": "重构 error_handler.ts",
      "status": "in_progress",
      "progress_percent": 0.5
    }
  ]
}

# GET /api/v2/resident/goals/execution/bulk
# 批量获取执行投影（用于概览页）

{
  "goals": [
    {"goal_id": "goal-1", "stage": "coding", "percent": 0.65, ...},
    {"goal_id": "goal-2", "stage": "planning", "percent": 0.20, ...}
  ]
}
```

### WebSocket 事件

```typescript
// goal_execution_update - 执行进度更新
{
  type: 'goal_execution_update',
  payload: {
    goal_id: 'goal-xxx',
    stage: 'coding',
    percent: 0.65,
    current_task: '重构 error_handler.ts',
    eta_minutes: 12,
    timestamp: '2024-03-08T10:30:00Z'
  }
}

// goal_execution_started - 开始执行
{
  type: 'goal_execution_started',
  payload: {
    goal_id: 'goal-xxx',
    started_at: '2024-03-08T10:00:00Z'
  }
}

// goal_execution_completed - 执行完成
{
  type: 'goal_execution_completed',
  payload: {
    goal_id: 'goal-xxx',
    final_stage: 'completed',
    completed_at: '2024-03-08T10:45:00Z'
  }
}
```

---

## 前端组件

### GoalItem 增强（进度条）

```typescript
interface GoalItemProps {
  goal: ResidentGoalPayload;
  executionView?: GoalExecutionView;  // 新增
  ...
}

// 显示效果:
// ┌─────────────────────────────────────────┐
// │ 目标: 优化错误处理                        │
// │ [coding] ████████░░ 65% · 预计8分钟      │
// │ 当前: 重构 error_handler.ts              │
// └─────────────────────────────────────────┘
```

### ExecutionProgressBar 组件

```typescript
interface ExecutionProgressBarProps {
  stage: 'planning' | 'coding' | 'testing' | 'review' | 'completed';
  percent: number;
  etaMinutes?: number;
  currentTask?: string;
}

// 阶段颜色:
// planning: amber (黄色)
// coding: cyan (青色)
// testing: violet (紫色)
// review: blue (蓝色)
// completed: emerald (绿色)
```

---

## 集成点

### 与 Director Workflow 集成

```python
# director_task_workflow.py

async def on_task_progress(task, progress):
    # 现有: 发送 task progress
    await broadcast_task_progress(task, progress)

    # 新增: 触发 goal execution projection 更新
    await update_goal_execution_projection(
        goal_id=task.goal_id,
        task_progress=progress
    )

    # 新增: WebSocket 推送
    await runtime_ws.broadcast({
        "type": "goal_execution_update",
        "payload": build_execution_view(task.goal_id).to_dict()
    })
```

### 与现有 runtime_projection.py 集成

```python
# app/services/runtime_projection.py

def build_runtime_projection(workspace: str) -> dict:
    projection = {...}  # 现有逻辑

    # 新增: 包含 goal execution 信息
    resident_service = get_resident_service(workspace)
    goals = resident_service.list_goals()

    projection["goal_executions"] = [
        build_goal_execution_projection(goal, workspace)
        for goal in goals
        if goal.status in ("approved", "materialized")
    ]

    return projection
```

---

## 实现步骤

1. **数据模型** (`execution_projection.py`)
   - `GoalExecutionView` dataclass
   - `TaskProgressItem` dataclass
   - `Stage` enum

2. **核心算法** (`execution_projection.py`)
   - `infer_stage()` - 阶段推断
   - `estimate_eta()` - ETA 估算
   - `build_goal_execution_projection()` - 构建投影

3. **服务层集成** (`service.py`)
   - 新增 `get_goal_execution_view(goal_id)`
   - 新增 `list_goal_executions()`

4. **API 层** (`api/v2/resident.py`)
   - `GET /goals/{goal_id}/execution`
   - `GET /goals/execution/bulk`

5. **WebSocket 集成** (`runtime_ws.py`)
   - `goal_execution_update` 事件推送

6. **前端组件**
   - 增强 `GoalItem` 显示进度条
   - 新增 `ExecutionProgressBar` 组件

---

## 验收标准

```typescript
// E2E 测试
it('should display goal execution progress', async () => {
  // 1. 启动目标执行
  await residentService.runGoal('goal-xxx');

  // 2. 获取执行投影
  const execution = await residentService.getGoalExecution('goal-xxx');
  expect(execution.stage).toBeOneOf(['planning', 'coding', 'testing', 'review']);
  expect(execution.percent).toBeGreaterThanOrEqual(0);
  expect(execution.percent).toBeLessThanOrEqual(1);

  // 3. 前端显示进度条
  const progressBar = screen.getByTestId('goal-progress');
  expect(progressBar).toHaveTextContent(/coding/);
  expect(progressBar).toHaveTextContent(/\d+%/);
});
```

---

## 相关文档

- [统一变更证据模型](./evidence-bundle.md) (Phase 1.1)
- [决策追溯系统](./decision-traceability.md)
- [实施路线图](../implementation-roadmap.md)

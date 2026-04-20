# Polaris UI 实时推送增强方案

## 一、现状分析

### 1.1 已有的实时推送能力

| 组件 | 实时推送内容 | 实现方式 |
|------|-------------|----------|
| PMWorkspace | 任务列表、当前阶段、当前执行任务 | useWebSocket + useLiveTaskQueues |
| PMTaskPanel | 任务状态筛选、任务详情 | React props 传递，定时刷新 |
| DirectorWorkspace | 执行任务列表、进度、当前任务、文件变更 | useWebSocket + fileEditEvents |
| RealtimeActivityPanel | LLM思考流、工具执行日志、运行时事件 | WebSocket channel 订阅 |
| RealTimeFileDiff | 代码变更 diff 展示 | FileEditEvent 事件处理 |
| MissionControl | 统一作战指挥台视图 | RuntimeProvider + V2 协议 |

### 1.2 现有架构

```
后端 WebSocket
    │
    ├── status 消息 ──► useWebSocket ──► PM/Director Workspace
    ├── snapshot 消息 ──► 任务列表更新
    ├── line 消息 ──► 实时日志流 (dialogue/runtime_events/llm)
    └── FILE_WRITTEN 事件 ──► FileEditEvent ──► RealTimeFileDiff
```

### 1.3 存在的问题

1. **PM 任务编排感知不足**：
   - 任务状态变化依赖定时轮询，延迟较高
   - 没有实时显示当前正在执行的任务节点
   - 缺少任务依赖关系的可视化

2. **Director 任务执行感知不足**：
   - 任务列表状态更新不够实时
   - 没有清晰显示"当前正在执行哪个任务"
   - 缺少任务完成/失败/阻塞状态的实时动画反馈

3. **代码变更 diff 感知不足**：
   - 文件变更事件已捕获，但 diff 展示可增强
   - 切换到 Director 代码页面时需要更直观的 view diff
   - 需要实时显示：新增(绿色)、删除(红色)、修改(黄色)

---

## 二、增强目标

### 2.1 PM 任务编排实时推送

| 目标 | 描述 |
|------|------|
| 任务状态实时变化 | 任务状态变更后 1 秒内 UI 更新 |
| 当前任务高亮 | 正在执行的任务带脉冲动画和明显标记 |
| 任务节点进度 | 显示当前节点在任务列表中的位置 |
| 任务统计实时更新 | pending/running/completed/blocked/failed 数量实时变化 |

### 2.2 Director 任务执行实时推送

| 目标 | 描述 |
|------|------|
| 任务列表实时更新 | 执行中/完成/失败/阻塞状态实时变化 |
| 当前任务指示 | 顶部 Header 实时显示当前执行任务标题 |
| 任务状态动画 | running 带脉冲，completed 带完成动画，failed 带错误标记 |
| 进度条实时 | 顶部进度条随任务完成实时推进 |

### 2.3 代码变更 diff 实时推送

| 目标 | 描述 |
|------|------|
| 实时 diff 视图 | 文件变更后自动展开 diff |
| 颜色编码 | 新增绿色(+)、删除红色(-)、修改黄色(~) |
| 自动滚动 | 新事件来时自动滚动到顶部 |
| 变更统计 | 实时显示新增/删除/修改行数 |

---

## 三、实施方案

### 3.1 PM 任务编排增强

#### 3.1.1 增强 PMTaskPanel 组件

```typescript
// 新增 props 和状态
interface PMTaskPanelProps {
  tasks: PmTask[];
  selectedTaskId: string | null;
  onTaskSelect: (taskId: string) => void;
  pmRunning: boolean;
  // 新增：实时任务状态映射
  taskStatusMap?: Record<string, {
    status: 'pending' | 'running' | 'completed' | 'blocked' | 'failed';
    progress?: number;
    updatedAt?: string;
  }>;
}

// 任务列表项新增实时状态样式
function TaskListItem({ task, selected, onClick, pmRunning, liveStatus }: TaskListItemProps) {
  const isRunning = liveStatus?.status === 'running';
  const isCompleted = liveStatus?.status === 'completed';
  const isBlocked = liveStatus?.status === 'blocked';
  const isFailed = liveStatus?.status === 'failed';
  
  return (
    <div className={cn(
      // running 状态：脉冲动画 + 边框高亮
      isRunning && 'animate-pulse border-l-4 border-amber-500 bg-amber-500/10',
      // completed 状态：完成动画
      isCompleted && 'opacity-70',
      // blocked/failed 状态：错误标记
      (isBlocked || isFailed) && 'border-l-4 border-red-500 bg-red-500/10',
    )}>
      {/* 任务内容 */}
    </div>
  );
}
```

#### 3.1.2 添加任务状态动画 CSS

```css
/* 任务运行中脉冲动画 */
@keyframes task-running-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.4); }
  50% { box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }
}

.task-running {
  animation: task-running-pulse 2s ease-in-out infinite;
}

/* 任务完成检查动画 */
@keyframes task-completed-check {
  0% { transform: scale(0); opacity: 0; }
  50% { transform: scale(1.2); }
  100% { transform: scale(1); opacity: 1; }
}

.task-completed-icon {
  animation: task-completed-check 0.5s ease-out;
}

/* 任务失败震动动画 */
@keyframes task-failed-shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-2px); }
  75% { transform: translateX(2px); }
}

.task-failed {
  animation: task-failed-shake 0.3s ease-in-out;
}
```

#### 3.1.3 增强 PMWorkspace Header

```typescript
// PMWorkspace.tsx Header 增强
function PMWorkspaceHeader({ 
  currentTask, 
  taskStats,
  currentPhase 
}: {
  currentTask: PmTask | null;
  taskStats: { pending: number; running: number; completed: number; blocked: number; failed: number };
  currentPhase: string;
}) {
  return (
    <header>
      {/* 实时任务统计 - 使用动画数字 */}
      <div className="flex items-center gap-4">
        <StatBadge 
          label="待办" 
          count={taskStats.pending} 
          color="slate"
          animate={taskStats.pending > 0}
        />
        <StatBadge 
          label="进行中" 
          count={taskStats.running} 
          color="amber"
          animate={taskStats.running > 0}
        />
        <StatBadge 
          label="已完成" 
          count={taskStats.completed} 
          color="emerald"
          animate={taskStats.completed > 0}
        />
        <StatBadge 
          label="阻塞" 
          count={taskStats.blocked} 
          color="red"
          animate={taskStats.blocked > 0}
        />
      </div>
      
      {/* 当前执行任务 - 带脉冲动画 */}
      {currentTask && pmRunning && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 animate-pulse">
          <Zap className="w-3.5 h-3.5 text-amber-400" />
          <span className="text-xs text-amber-300 truncate">
            正在执行: {currentTask.title}
          </span>
        </div>
      )}
    </header>
  );
}
```

### 3.2 Director 任务执行增强

#### 3.2.1 增强 DirectorTaskPanel 组件

```typescript
// DirectorTaskPanel.tsx 增强
interface DirectorTaskPanelProps {
  tasks: ExecutionTask[];
  selectedTaskId: string | null;
  onTaskSelect: (taskId: string) => void;
  onExecute: () => void;
  isExecuting: boolean;
  // 新增：实时任务状态
  liveTaskStatus?: Record<string, {
    status: 'pending' | 'running' | 'completed' | 'blocked' | 'failed';
    progress?: number;
    output?: string;
    error?: string;
  }>;
}

// 任务卡片增强状态显示
function TaskCard({ task, liveStatus }: { task: ExecutionTask; liveStatus?: TaskLiveStatus }) {
  const status = liveStatus?.status || task.status;
  const isRunning = status === 'running';
  
  return (
    <div className={cn(
      'p-3 rounded-xl border transition-all',
      // 运行中：脉冲边框 + 背景
      isRunning && 'border-indigo-500 bg-indigo-500/10 animate-pulse',
      // 完成：绿色标记
      status === 'completed' && 'border-emerald-500/30',
      // 失败：红色标记 + 震动
      status === 'failed' && 'border-red-500 bg-red-500/10 animate-shake',
      // 阻塞：黄色标记
      status === 'blocked' && 'border-yellow-500 bg-yellow-500/10',
    )}>
      {/* 进度条 - 运行中时实时更新 */}
      {isRunning && (
        <div className="mt-3">
          <div className="flex items-center justify-between text-[10px]">
            <span>进度</span>
            <span>{liveStatus?.progress || task.progress || 0}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-800 overflow-hidden">
            <div 
              className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-500"
              style={{ width: `${liveStatus?.progress || task.progress || 0}%` }}
            />
          </div>
        </div>
      )}
      
      {/* 错误信息 - 失败时显示 */}
      {(status === 'failed' || status === 'blocked') && liveStatus?.error && (
        <div className="mt-2 p-2 rounded bg-red-500/10 border border-red-500/20">
          <p className="text-[10px] text-red-400">{liveStatus.error}</p>
        </div>
      )}
    </div>
  );
}
```

#### 3.2.2 增强 DirectorWorkspace Header

```typescript
// DirectorWorkspace.tsx Header 增强
function DirectorWorkspaceHeader({ 
  currentTaskId,
  currentTaskTitle,
  taskStats,
  progress,
  directorRunning 
}: {
  currentTaskId: string | null;
  currentTaskTitle: string | null;
  taskStats: { pending: number; running: number; completed: number; blocked: number; failed: number };
  progress: number;
  directorRunning: boolean;
}) {
  return (
    <header>
      {/* 实时进度条 */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-indigo-500/70" />
          <span className="text-xs text-slate-400">进度</span>
          <span className="text-xs font-mono text-indigo-400">
            {taskStats.completed}/{taskStats.total}
          </span>
          <div className="w-20 h-1.5 rounded-full bg-slate-800 overflow-hidden">
            <div 
              className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-purple-400 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      </div>
      
      {/* 当前执行任务 - 实时更新 */}
      {currentTaskTitle && directorRunning && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-indigo-500/10 border border-indigo-500/20 animate-pulse">
          <Loader2 className="w-3.5 h-3.5 text-indigo-400 animate-spin" />
          <span className="text-xs text-indigo-300 truncate">
            正在执行: {currentTaskTitle}
          </span>
        </div>
      )}
      
      {/* 失败任务警告 */}
      {taskStats.failed > 0 && (
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-red-500/10 border border-red-500/20">
          <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
          <span className="text-xs text-red-400">{taskStats.failed} 个任务失败</span>
        </div>
      )}
    </header>
  );
}
```

### 3.3 代码变更 diff 实时推送增强

#### 3.3.1 增强 RealTimeFileDiff 组件

```typescript
// RealTimeFileDiff.tsx 增强
interface RealTimeFileDiffProps {
  filePath: string;
  operation: 'create' | 'modify' | 'delete';
  patch?: string;
  oldContent?: string;
  newContent?: string;
  compact?: boolean;
  // 新增：实时状态
  isNew?: boolean;           // 新文件变更
  isHighlighting?: boolean; // 高亮显示动画
  onClose?: () => void;
}

// 新增：实时变更高亮组件
function RealTimeChangeHighlight({ 
  children, 
  isNew 
}: { 
  children: React.ReactNode; 
  isNew: boolean;
}) {
  return (
    <div className={cn(
      'transition-all duration-300',
      isNew && 'animate-flash-green',  // 新增时绿色闪烁
    )}>
      {children}
    </div>
  );
}

// CSS 动画
const styles = `
  @keyframes flash-green {
    0% { background-color: rgba(16, 185, 129, 0.3); }
    100% { background-color: transparent; }
  }
  .animate-flash-green {
    animation: flash-green 1s ease-out;
  }
  
  @keyframes flash-red {
    0% { background-color: rgba(239, 68, 68, 0.3); }
    100% { background-color: transparent; }
  }
  .animate-flash-red {
    animation: flash-red 1s ease-out;
  }
`;
```

#### 3.3.2 增强 DirectorCodePanel 组件

```typescript
// DirectorCodePanel.tsx 增强
function DirectorCodePanel({ workspace, fileEditEvents }: DirectorCodePanelProps) {
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);
  const [recentEventId, setRecentEventId] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  
  // 新事件来时自动展开并滚动
  useEffect(() => {
    if (fileEditEvents.length > 0) {
      const latestEvent = fileEditEvents[fileEditEvents.length - 1];
      setRecentEventId(latestEvent.id);
      setExpandedEventId(latestEvent.id);
      
      // 自动滚动到最新
      if (listRef.current) {
        listRef.current.scrollTop = 0;
      }
    }
  }, [fileEditEvents.length]);
  
  return (
    <div className="h-full flex flex-col">
      {/* 实时变更统计 */}
      <div className="h-12 flex items-center justify-between px-4 border-b border-white/5">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-medium text-slate-200">实时代码变更</h2>
          {fileEditEvents.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 animate-pulse">
                {fileEditEvents.filter(e => e.operation === 'create').length} 新建
              </span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-400">
                {fileEditEvents.filter(e => e.operation === 'modify').length} 修改
              </span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-500/20 text-red-400">
                {fileEditEvents.filter(e => e.operation === 'delete').length} 删除
              </span>
            </div>
          )}
        </div>
      </div>
      
      {/* 变更列表 */}
      <div className="flex-1 overflow-hidden flex" ref={listRef}>
        <div className="flex-1 overflow-auto p-4">
          {recentEvents.map((event, index) => (
            <div 
              key={event.id}
              className={cn(
                'mb-2 rounded-xl border transition-all',
                // 最新事件高亮
                event.id === recentEventId && index === 0 
                  ? 'border-indigo-500 bg-indigo-500/10 animate-pulse' 
                  : 'border-white/5 bg-white/5'
              )}
            >
              {/* 事件内容 */}
              <RealTimeFileDiff
                filePath={event.filePath}
                operation={event.operation}
                patch={event.patch}
                compact
                isNew={event.id === recentEventId}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

---

## 四、实现步骤

### 阶段一：PM 任务编排增强

1. [ ] 修改 `PMTaskPanel.tsx` - 添加任务状态动画和实时状态映射
2. [ ] 添加任务状态相关的 CSS 动画样式
3. [ ] 修改 `PMWorkspace.tsx` - 增强 Header 显示当前任务和统计
4. [ ] 修改 `useWebSocket.ts` - 添加任务状态实时更新逻辑

### 阶段二：Director 任务执行增强

1. [ ] 修改 `DirectorWorkspace.tsx` - 增强 Header 显示当前任务和进度
2. [ ] 修改 `DirectorTaskPanel.tsx` - 添加任务状态动画和实时状态
3. [ ] 添加 Director 任务状态相关的 CSS 动画样式
4. [ ] 确保 fileEditEvents 实时推送正常工作

### 阶段三：代码变更 diff 增强

1. [ ] 修改 `RealTimeFileDiff.tsx` - 添加实时高亮动画
2. [ ] 修改 `DirectorCodePanel.tsx` - 增强变更列表实时显示
3. [ ] 添加 diff 变更统计和实时更新

### 阶段四：测试和验证

1. [ ] 单元测试 - 各组件渲染测试
2. [ ] 集成测试 - 完整流程实时推送验证
3. [ ] E2E 测试 - 用户交互流程验证

---

## 五、验收标准

### 5.1 PM 任务编排

- [ ] 任务状态变更后 1 秒内 UI 更新
- [ ] 当前执行任务带脉冲动画
- [ ] 任务统计数字实时变化有动画效果

### 5.2 Director 任务执行

- [ ] 任务列表状态实时更新
- [ ] Header 实时显示当前执行任务
- [ ] 进度条随任务完成实时推进
- [ ] 失败任务有明显的错误标记

### 5.3 代码变更 diff

- [ ] 文件变更后自动展开 diff
- [ ] 新增代码显示绿色，删除显示红色
- [ ] 变更统计实时更新

---

## 六、风险和缓解

| 风险 | 缓解措施 |
|------|----------|
| 实时推送数据量过大导致性能问题 | 使用 React.memo 优化，使用节流/防抖 |
| 动画过多导致用户体验混乱 | 控制动画频率，仅关键状态变化时触发 |
| 前后端协议不匹配 | 使用已有的 WebSocket 协议，保持兼容性 |

---

## 七、相关文件清单

### 需要修改的文件

1. `src/frontend/src/app/components/pm/PMTaskPanel.tsx`
2. `src/frontend/src/app/components/pm/PMWorkspace.tsx`
3. `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
4. `src/frontend/src/app/components/director/DirectorCodePanel.tsx`
5. `src/frontend/src/app/components/director/RealTimeFileDiff.tsx`
6. `src/frontend/src/hooks/useWebSocket.ts`

### 需要新增的文件

1. `src/frontend/src/app/styles/task-animations.css` - 任务状态动画

---

*文档版本：v1.0*
*创建时间：2026-03-04*

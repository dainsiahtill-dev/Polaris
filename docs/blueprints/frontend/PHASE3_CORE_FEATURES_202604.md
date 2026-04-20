# Phase 3: 核心功能补全蓝图

> **技术栈**: Electron + React 18 + TypeScript + Vite + Zustand + Framer Motion
> **优先级**: 🟡 中
> **工期**: 4-6 周
> **前置条件**: Phase 2 完成
> **目标**: 补全与世界顶级产品的功能差距

---

## 🎯 任务清单

### T1: Kanban 看板视图 [HIGH]

**当前状态**: 仅列表视图
**目标**: 实现 Linear/Jira 风格看板

**实现方案**:

```typescript
// src/frontend/src/app/components/kanban/KanbanBoard.tsx
import { DragDropContext, Droppable, Draggable, DropResult } from '@hello-pangea/dnd';
import { motion } from 'framer-motion';
import { useCallback } from 'react';

interface Task {
  id: string;
  subject: string;
  status: TaskStatus;
  priority: 'low' | 'medium' | 'high' | 'urgent';
}

type TaskStatus = 'backlog' | 'todo' | 'in_progress' | 'done';

interface KanbanColumn {
  id: TaskStatus;
  title: string;
  tasks: Task[];
}

interface KanbanBoardProps {
  tasks: Task[];
  onTaskMove: (taskId: string, from: TaskStatus, to: TaskStatus, index: number) => void;
}

export const KanbanBoard: React.FC<KanbanBoardProps> = ({ tasks, onTaskMove }) => {
  const columns: KanbanColumn[] = [
    { id: 'backlog', title: 'Backlog', tasks: [] },
    { id: 'todo', title: 'To Do', tasks: [] },
    { id: 'in_progress', title: 'In Progress', tasks: [] },
    { id: 'done', title: 'Done', tasks: [] },
  ];

  // 按状态分组
  const groupedTasks = tasks.reduce((acc, task) => {
    const column = acc.find((col) => col.id === task.status);
    if (column) {
      column.tasks.push(task);
    }
    return acc;
  }, columns);

  const handleDragEnd = useCallback(
    (result: DropResult) => {
      const { draggableId, source, destination } = result;

      if (!destination) return;
      if (source.droppableId === destination.droppableId && source.index === destination.index) {
        return;
      }

      onTaskMove(
        draggableId,
        source.droppableId as TaskStatus,
        destination.droppableId as TaskStatus,
        destination.index
      );
    },
    [onTaskMove]
  );

  return (
    <DragDropContext onDragEnd={handleDragEnd}>
      <div className="kanban-board flex gap-4 overflow-x-auto p-4">
        {groupedTasks.map((column) => (
          <Droppable droppableId={column.id} key={column.id}>
            {(provided, snapshot) => (
              <motion.div
                ref={provided.innerRef}
                {...provided.droppableProps}
                className={`kanban-column min-w-[280px] w-[280px] rounded-lg p-3 ${
                  snapshot.isDraggingOver ? 'bg-accent/20' : 'bg-card'
                }`}
                layout
              >
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold text-sm">{column.title}</h3>
                  <span className="text-xs text-muted-foreground">
                    {column.tasks.length}
                  </span>
                </div>

                {column.tasks.map((task, index) => (
                  <Draggable draggableId={task.id} index={index} key={task.id}>
                    {(provided, snapshot) => (
                      <motion.div
                        ref={provided.innerRef}
                        {...provided.draggableProps}
                        {...provided.dragHandleProps}
                        className={`kanban-card p-3 mb-2 rounded-md border cursor-grab active:cursor-grabbing ${
                          snapshot.isDragging
                            ? 'shadow-lg border-primary'
                            : 'border-border hover:border-primary/50'
                        }`}
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: index * 0.05 }}
                      >
                        <p className="text-sm font-medium truncate">{task.subject}</p>
                        <PriorityBadge priority={task.priority} />
                      </motion.div>
                    )}
                  </Draggable>
                ))}
                {provided.placeholder}
              </motion.div>
            )}
          </Droppable>
        ))}
      </div>
    </DragDropContext>
  );
};
```

**Why**: `@hello-pangea/dnd` 是 React DnD 的社区维护版本，Framer Motion 提供流畅的动画过渡

**执行步骤**:
1. [ ] 安装 `@hello-pangea/dnd framer-motion`
2. [ ] 创建 `KanbanBoard.tsx`
3. [ ] 创建 `KanbanColumn.tsx`
4. [ ] 创建 `KanbanCard.tsx`
5. [ ] 集成到 `ProjectProgressPanel.tsx`
6. [ ] 添加任务移动 API 调用
7. [ ] 编写测试

**验收标准**:
- [ ] 拖拽任务到不同列
- [ ] 状态更新同步到后端
- [ ] 触摸设备支持

---

### T2: 任务依赖图可视化 [MEDIUM]

**当前状态**: 无
**目标**: 展示任务间的依赖关系

**实现方案**:

```typescript
// src/frontend/src/app/components/graph/TaskDependencyGraph.tsx
import { useMemo } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
} from 'reactflow';
import 'reactflow/dist/style.css';

interface Task {
  id: string;
  subject: string;
  status: TaskStatus;
  depends_on: string[];
}

interface TaskDependencyGraphProps {
  tasks: Task[];
  onTaskClick: (taskId: string) => void;
}

export const TaskDependencyGraph: React.FC<TaskDependencyGraphProps> = ({
  tasks,
  onTaskClick,
}) => {
  const { nodes, edges } = useMemo(() => {
    const taskMap = new Map(tasks.map((t) => [t.id, t]));

    const flowNodes: Node[] = tasks.map((task, index) => ({
      id: task.id,
      data: { label: task.subject, status: task.status },
      position: {
        x: (index % 4) * 200,
        y: Math.floor(index / 4) * 100,
      },
      style: {
        background: getStatusColor(task.status),
        color: 'white',
        borderRadius: 8,
        padding: 10,
        fontSize: 12,
      },
    }));

    const flowEdges: Edge[] = tasks.flatMap((task) =>
      task.depends_on
        .filter((depId) => taskMap.has(depId))
        .map((depId) => ({
          id: `${depId}-${task.id}`,
          source: depId,
          target: task.id,
          type: 'smoothstep',
          animated: task.status === 'in_progress',
          style: { stroke: '#888' },
        }))
    );

    return { nodes: flowNodes, edges: flowEdges };
  }, [tasks]);

  const [flowNodes, setNodes, onNodesChange] = useNodesState(nodes);
  const [flowEdges, setEdges, onEdgesChange] = useEdgesState(edges);

  return (
    <div className="h-[400px] w-full">
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={(_, node) => onTaskClick(node.id)}
        fitView
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
};

const getStatusColor = (status: TaskStatus): string => {
  const colors: Record<TaskStatus, string> = {
    backlog: '#6b7280',
    todo: '#3b82f6',
    in_progress: '#f59e0b',
    done: '#10b981',
  };
  return colors[status] || '#6b7280';
};
```

**Why**: React Flow 是 React 生态中成熟的流程图库，支持自定义节点、边动画、交互

**执行步骤**:
1. [ ] 安装 `reactflow`
2. [ ] 创建 `TaskDependencyGraph.tsx`
3. [ ] 自定义节点样式
4. [ ] 添加循环依赖检测
5. [ ] 集成到 `PlanBoard.tsx`
6. [ ] 编写测试

**验收标准**:
- [ ] 依赖关系正确展示
- [ ] 点击节点显示任务详情
- [ ] 支持循环依赖检测

---

### T3: 主题切换器 [MEDIUM]

**当前状态**: 仅暗色主题
**目标**: 亮/暗/系统自动主题

**实现方案**:

```typescript
// src/frontend/src/app/hooks/useTheme.ts
import { useEffect, useState, useCallback } from 'react';
import { useSettingsStore } from '@/app/store/settingsStore';

type Theme = 'dark' | 'light' | 'system';

export function useTheme() {
  const themeSetting = useSettingsStore((s) => s.theme);
  const setThemeSetting = useSettingsStore((s) => s.setTheme);
  const [resolvedTheme, setResolvedTheme] = useState<'dark' | 'light'>('dark');

  const applyTheme = useCallback((theme: Theme) => {
    const root = document.documentElement;

    if (theme === 'system') {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      root.classList.toggle('dark', prefersDark);
      setResolvedTheme(prefersDark ? 'dark' : 'light');
    } else {
      root.classList.toggle('dark', theme === 'dark');
      setResolvedTheme(theme);
    }
  }, []);

  // 初始化应用主题
  useEffect(() => {
    applyTheme(themeSetting);
  }, [themeSetting, applyTheme]);

  // 监听系统主题变化
  useEffect(() => {
    if (themeSetting !== 'system') return;

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      applyTheme('system');
    };

    mediaQuery.addEventListener('change', handler);
    return () => mediaQuery.removeEventListener('change', handler);
  }, [themeSetting, applyTheme]);

  return {
    theme: themeSetting,
    resolvedTheme,
    setTheme: setThemeSetting,
  };
}
```

```tsx
// src/frontend/src/app/components/ThemeSwitcher.tsx
import { useTheme } from '@/app/hooks/useTheme';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Sun, Moon, Monitor } from 'lucide-react';

export const ThemeSwitcher: React.FC = () => {
  const { theme, setTheme } = useTheme();

  const options = [
    { value: 'light', label: '浅色', icon: Sun },
    { value: 'dark', label: '深色', icon: Moon },
    { value: 'system', label: '跟随系统', icon: Monitor },
  ] as const;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="切换主题">
          <Sun className="h-[1.2rem] w-[1.2rem] rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
          <Moon className="absolute h-[1.2rem] w-[1.2rem] rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {options.map((opt) => (
          <DropdownMenuItem
            key={opt.value}
            onClick={() => setTheme(opt.value)}
            className={theme === opt.value ? 'bg-accent' : ''}
          >
            <opt.icon className="mr-2 h-4 w-4" />
            {opt.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};
```

**Why**: 使用 Zustand persist 持久化用户偏好，useEffect cleanup 防止内存泄漏

**执行步骤**:
1. [ ] 创建 `useTheme.ts` hook
2. [ ] 定义亮色主题 CSS 变量
3. [ ] 创建 `ThemeSwitcher.tsx` 组件
4. [ ] 添加到 `SettingsModal.tsx`
5. [ ] 测试切换

**验收标准**:
- [ ] 三种主题模式正常切换
- [ ] 主题持久化
- [ ] 无闪烁 (FOUC)

---

### T4: 日志导出功能 [MEDIUM]

**当前状态**: 仅查看，无导出
**目标**: 支持 JSON/CSV/PDF 导出

**实现方案**:

```typescript
// src/frontend/src/app/components/logs/LogExporter.tsx
import { useCallback } from 'react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Download, FileJson, FileSpreadsheet, FileText } from 'lucide-react';

interface LogEntry {
  timestamp: string;
  level: 'debug' | 'info' | 'warn' | 'error';
  message: string;
  source?: string;
  metadata?: Record<string, unknown>;
}

type ExportFormat = 'json' | 'csv' | 'pdf';

interface LogExporterProps {
  logs: LogEntry[];
  filename?: string;
}

export const LogExporter: React.FC<LogExporterProps> = ({ logs, filename = 'polaris-logs' }) => {
  const exportAsJSON = useCallback(() => {
    const json = JSON.stringify(logs, null, 2);
    downloadFile(json, `${filename}.json`, 'application/json');
  }, [logs, filename]);

  const exportAsCSV = useCallback(() => {
    const headers = ['Timestamp', 'Level', 'Message', 'Source'];
    const rows = logs.map((log) => [
      log.timestamp,
      log.level,
      `"${log.message.replace(/"/g, '""')}"`,
      log.source || '',
    ]);

    const csv = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    downloadFile(csv, `${filename}.csv`, 'text/csv');
  }, [logs, filename]);

  const exportAsPDF = useCallback(async () => {
    const { default: jsPDF } = await import('jspdf');
    const doc = new jsPDF();

    doc.setFontSize(12);
    doc.text('Polaris Logs', 10, 10);

    let y = 20;
    logs.slice(0, 50).forEach((log) => {
      doc.setFontSize(8);
      doc.text(`${log.timestamp} [${log.level}] ${log.message}`, 10, y);
      y += 5;
      if (y > 280) return;
    });

    doc.save(`${filename}.pdf`);
  }, [logs, filename]);

  const handleExport = async (format: ExportFormat) => {
    switch (format) {
      case 'json':
        exportAsJSON();
        break;
      case 'csv':
        exportAsCSV();
        break;
      case 'pdf':
        await exportAsPDF();
        break;
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          <Download className="mr-2 h-4 w-4" />
          导出
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => handleExport('json')}>
          <FileJson className="mr-2 h-4 w-4" />
          JSON 格式
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleExport('csv')}>
          <FileSpreadsheet className="mr-2 h-4 w-4" />
          CSV 表格
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => handleExport('pdf')}>
          <FileText className="mr-2 h-4 w-4" />
          PDF 报告
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

function downloadFile(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
```

**Why**: 动态导入 `jspdf` 减少初始包体积，按需加载

**验收标准**:
- [ ] 三种格式导出正常
- [ ] 导出文件可正常打开
- [ ] 大日志文件优化处理

---

### T5: Electron 系统托盘 + 通知 [MEDIUM]

**当前状态**: 无托盘，无通知
**目标**: 实现系统托盘和桌面通知

**实现方案**:

```typescript
// src/electron/main.cjs

const { Tray, Menu, nativeImage, Notification } = require('electron');
const path = require('path');

let tray = null;
let mainWindow = null;

function createTray() {
  // 创建托盘图标 (16x16 或 32x32)
  const iconPath = path.join(__dirname, 'assets', 'icon.png');
  let icon;

  try {
    icon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 });
  } catch {
    // 回退: 创建空白图标
    icon = nativeImage.createEmpty();
  }

  tray = new Tray(icon);
  tray.setToolTip('Polaris');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '显示 Polaris',
      click: () => {
        mainWindow?.show();
        mainWindow?.focus();
      },
    },
    {
      label: '隐藏到托盘',
      click: () => mainWindow?.hide(),
    },
    { type: 'separator' },
    {
      label: '新建任务',
      click: () => {
        mainWindow?.show();
        mainWindow?.webContents.send('hp:action', { type: 'new-task' });
      },
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => app.quit(),
    },
  ]);

  tray.setContextMenu(contextMenu);

  // 单击托盘图标切换窗口显示
  tray.on('click', () => {
    if (mainWindow?.isVisible()) {
      mainWindow.hide();
    } else {
      mainWindow?.show();
      mainWindow?.focus();
    }
  });
}

// 显示通知
function showNotification(title, body, options = {}) {
  if (!Notification.isSupported()) {
    console.warn('Notifications not supported on this system');
    return;
  }

  const notification = new Notification({
    title,
    body,
    icon: path.join(__dirname, 'assets', 'icon.png'),
    silent: options.silent ?? false,
  });

  notification.on('click', () => {
    mainWindow?.show();
    mainWindow?.focus();
  });

  notification.show();
}

// IPC handlers
ipcMain.handle('hp:notification-show', async (event, { title, body, options }) => {
  showNotification(title, body, options);
  return { success: true };
});
```

```typescript
// src/electron/preload.cjs

contextBridge.exposeInMainWorld('polaris', {
  // ... existing APIs
  notification: {
    show: (title: string, body: string, options?: { silent?: boolean }) =>
      ipcRenderer.invoke('hp:notification-show', { title, body, options }),
  },
});
```

```typescript
// src/frontend/src/app/hooks/useNotifications.ts
import { useCallback } from 'react';

interface NotificationOptions {
  title: string;
  body: string;
  silent?: boolean;
}

export function useNotifications() {
  const show = useCallback((options: NotificationOptions) => {
    if (window.polaris?.notification) {
      window.polaris.notification.show(options.title, options.body, {
        silent: options.silent,
      });
    }
  }, []);

  return { show };
}
```

**Why**: Electron Notification API 提供原生桌面通知，IPC 通道确保安全通信

**执行步骤**:
1. [ ] 创建托盘图标资源
2. [ ] 在 `main.cjs` 添加托盘逻辑
3. [ ] 添加通知 IPC 通道
4. [ ] 在 `preload.cjs` 暴露 API
5. [ ] 创建 `useNotifications` hook
6. [ ] 集成到关键事件

**验收标准**:
- [ ] 托盘图标显示
- [ ] 托盘菜单正常工作
- [ ] 点击图标切换窗口显示
- [ ] 桌面通知正常弹出

---

## 📋 Phase 3 验收清单

- [ ] T1: Kanban 看板视图
- [ ] T2: 任务依赖图可视化
- [ ] T3: 主题切换器
- [ ] T4: 日志导出功能
- [ ] T5: Electron 托盘 + 通知
- [ ] 所有新功能通过测试
- [ ] `npm run typecheck` 通过

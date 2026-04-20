# Phase 2: 架构重构蓝图

> **技术栈**: Electron + React 18 + TypeScript + Vite + Zustand
> **优先级**: 🟠 高
> **工期**: 3-4 周
> **前置条件**: Phase 1 完成
> **目标**: 统一架构，提升可维护性和性能

---

## 🎯 任务清单

### T1: useRuntime Hook 拆分 [HIGH]

**问题描述**:
- `useRuntime.ts` 约 1200 行，职责过重
- 包含: pmStatus, directorStatus, logs, tasks, workers, dialogueEvents
- 难以测试和维护

**重构方案**:

```
src/frontend/src/app/hooks/
├── useRuntimeStore.ts          # Zustand store (单一数据源)
├── usePmStatus.ts             # PM 状态管理
├── useDirectorStatus.ts       # Director 状态管理
├── useRuntimeLogs.ts          # 日志流管理
├── useRuntimeTasks.ts         # 任务队列管理
├── useRuntimeWorkers.ts       # Worker 状态管理
└── useRuntimeDialogue.ts      # 对话事件管理
```

```typescript
// src/frontend/src/app/hooks/useRuntimeStore.ts
import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

interface RuntimeState {
  // State
  pmStatus: PmStatus | null;
  directorStatus: DirectorStatus | null;
  logs: LogEntry[];
  tasks: Task[];
  workers: Worker[];
  dialogueEvents: DialogueEvent[];

  // Actions
  setPmStatus: (status: PmStatus | null) => void;
  setDirectorStatus: (status: DirectorStatus | null) => void;
  appendLog: (log: LogEntry) => void;
  clearLogs: () => void;

  // Selectors (computed)
  getWorkerById: (id: string) => Worker | undefined;
}

export const useRuntimeStore = create<RuntimeState>()(
  immer((set, get) => ({
    // Initial state
    pmStatus: null,
    directorStatus: null,
    logs: [],
    tasks: [],
    workers: [],
    dialogueEvents: [],

    // Actions
    setPmStatus: (status) => set({ pmStatus: status }),
    setDirectorStatus: (status) => set({ directorStatus: status }),
    appendLog: (log) =>
      set((state) => {
        state.logs.push(log);
        // 保留最近 1000 条
        if (state.logs.length > 1000) {
          state.logs = state.logs.slice(-1000);
        }
      }),
    clearLogs: () => set({ logs: [] }),

    // Selectors
    getWorkerById: (id) => get().workers.find((w) => w.id === id),
  }))
);
```

```typescript
// src/frontend/src/app/hooks/usePmStatus.ts
// 专门管理 PM 状态，从 WebSocket 消息中提取

import { useRuntimeStore } from './useRuntimeStore';
import { useEffect, useCallback } from 'react';

export function usePmStatus() {
  const pmStatus = useRuntimeStore((s) => s.pmStatus);
  const setPmStatus = useRuntimeStore((s) => s.setPmStatus);

  const handlePmUpdate = useCallback((data: PmStatus) => {
    setPmStatus(data);
  }, [setPmStatus]);

  // 注册消息处理器
  useEffect(() => {
    window.polaris.pmy.onStatusUpdate(handlePmUpdate);
    return () => {
      // Cleanup: 组件卸载时移除监听
      window.polaris.pmy.offStatusUpdate(handlePmUpdate);
    };
  }, [handlePmUpdate]);

  return { pmStatus };
}
```

**Why**:
1. Zustand + Immer 提供不可变更新，避免直接 mutation
2. 单一数据源 (Single Source of Truth) 便于调试
3. Selector 模式避免不必要的重渲染
4. useEffect cleanup 防止内存泄漏

**执行步骤**:
1. [ ] 创建 `useRuntimeStore.ts` (Zustand)
2. [ ] 拆分出子 hooks (`usePmStatus.ts`, `useDirectorStatus.ts`, etc.)
3. [ ] 重构 `useRuntime.ts` 为聚合层
4. [ ] 更新所有消费者组件
5. [ ] 添加单元测试

**验收标准**:
- [ ] `useRuntime.ts` 降至 300 行以内
- [ ] 每个子 hook 独立可测试
- [ ] 单元测试覆盖率 > 80%

---

### T2: 统一状态管理到 Zustand [MEDIUM]

**问题描述**:
- 当前混合使用: Zustand + Context + useState
- 无统一缓存策略

**重构方案**:

```typescript
// src/frontend/src/app/store/settingsStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface SettingsState {
  theme: 'dark' | 'light' | 'system';
  compactMode: boolean;
  fontSize: number;
  setTheme: (theme: 'dark' | 'light' | 'system') => void;
  setCompactMode: (compact: boolean) => void;
  setFontSize: (size: number) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      theme: 'dark',
      compactMode: false,
      fontSize: 14,
      setTheme: (theme) => set({ theme }),
      setCompactMode: (compactMode) => set({ compactMode }),
      setFontSize: (fontSize) => set({ fontSize }),
    }),
    { name: 'polaris:settings' }
  )
);
```

```typescript
// src/frontend/src/app/store/uiStore.ts
// 统一管理 UI 状态

interface UIState {
  // Modal states
  isSettingsOpen: boolean;
  isLogsOpen: boolean;
  isHistoryOpen: boolean;

  // Panel states
  sidebarCollapsed: boolean;
  rightPanelTab: 'context' | 'memo' | 'memory' | 'snapshot';

  // Actions
  openSettings: () => void;
  closeSettings: () => void;
  toggleSidebar: () => void;
  setRightPanelTab: (tab: UIState['rightPanelTab']) => void;
}

export const useUIStore = create<UIState>()((set) => ({
  isSettingsOpen: false,
  isLogsOpen: false,
  isHistoryOpen: false,
  sidebarCollapsed: false,
  rightPanelTab: 'context',

  openSettings: () => set({ isSettingsOpen: true }),
  closeSettings: () => set({ isSettingsOpen: false }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setRightPanelTab: (rightPanelTab) => set({ rightPanelTab }),
}));
```

**Why**: Zustand 的 persist middleware 自动处理 localStorage 持久化，无需手动管理

**执行步骤**:
1. [ ] 创建 `useSettingsStore.ts` (统一设置)
2. [ ] 创建 `useUIStore.ts` (统一 UI 状态)
3. [ ] 迁移 Context → Zustand
4. [ ] 添加 electron-store 持久化 (用于敏感数据)
5. [ ] 编写测试

**验收标准**:
- [ ] 移除所有状态管理 Context (除 Provider 必要包装)
- [ ] Zustand store 有完整类型定义
- [ ] 状态持久化正常工作

---

### T3: React Query 缓存层 [MEDIUM]

**问题描述**:
- 无集中缓存
- 重复请求无去重
- 无请求取消机制

**引入方案**:

```typescript
// src/frontend/src/lib/queryClient.ts
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactNode } from 'react';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5分钟
      gcTime: 30 * 60 * 1000,  // 30分钟
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});

export function QueryProvider({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}
```

```typescript
// src/frontend/src/app/hooks/useTasks.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { taskService } from '@/services/taskService';

export function useTasks(workspace: string) {
  return useQuery({
    queryKey: ['tasks', workspace],
    queryFn: () => taskService.getTasks(workspace),
    enabled: !!workspace,
  });
}

export function useCreateTask() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (task: CreateTaskInput) => taskService.createTask(task),
    onSuccess: () => {
      // Invalidate 相关查询
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
}
```

**Why**: React Query 自动处理缓存、重试、请求取消，是 React 生态的标准数据获取方案

**执行步骤**:
1. [ ] 安装 `@tanstack/react-query`
2. [ ] 创建 `queryClient.ts` 配置
3. [ ] 在 `App.tsx` 添加 QueryProvider
4. [ ] 重构 `useSettings`, `useFactory` 使用 React Query
5. [ ] 添加请求取消 (AbortController)

**验收标准**:
- [ ] React Query 正常工作
- [ ] 重复请求被缓存
- [ ] 组件卸载时请求取消

---

### T4: 测试覆盖提升 [HIGH]

**当前覆盖率**: ~40%
**目标覆盖率**: 80%

**React Testing Library 最佳实践**:

```typescript
// src/frontend/src/app/components/SettingsModal.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { SettingsModal } from './SettingsModal';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SettingsProvider } from '@/contexts/SettingsContext';

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        {children}
      </SettingsProvider>
    </QueryClientProvider>
  );
};

describe('SettingsModal', () => {
  it('should open settings modal', async () => {
    const { getByRole } = render(<SettingsModal isOpen={true} onClose={vi.fn()} />, {
      wrapper: createWrapper(),
    });

    expect(getByRole('dialog')).toBeInTheDocument();
  });

  it('should close on backdrop click', async () => {
    const onClose = vi.fn();
    const { getByTestId } = render(
      <SettingsModal isOpen={true} onClose={onClose} />,
      { wrapper: createWrapper() }
    );

    fireEvent.click(screen.getByTestId('settings-backdrop'));

    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });
});
```

**Hook 测试**:

```typescript
// src/frontend/src/app/hooks/useProcessOperations.test.ts
import { renderHook, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { useProcessOperations } from './useProcessOperations';

// Mock Electron API
vi.mock('@/lib/electron', () => ({
  invoke: vi.fn(),
}));

describe('useProcessOperations', () => {
  it('should start PM process', async () => {
    const { result } = renderHook(() => useProcessOperations());

    await waitFor(() => {
      expect(result.current.startPm).toBeDefined();
    });
  });
});
```

**执行步骤**:
1. [ ] 为 `SettingsModal` 编写测试
2. [ ] 为 `LogsModal` 编写测试
3. [ ] 补充 `useProcessOperations` 测试
4. [ ] 添加服务层集成测试
5. [ ] 生成覆盖率报告

**验收标准**:
- [ ] SettingsModal 测试覆盖率 > 80%
- [ ] LogsModal 测试覆盖率 > 80%
- [ ] 关键 hook 测试覆盖率 > 70%

---

### T5: WebSocket 降级策略完善 [MEDIUM]

**问题描述**:
- WebSocket 断开后降级轮询实现不完整

**重构方案**:

```typescript
// src/frontend/src/app/hooks/useWebSocketWithFallback.ts
import { useEffect, useRef, useState, useCallback } from 'react';

type ConnectionState = 'connected' | 'connecting' | 'disconnected';

interface UseWebSocketOptions {
  url: string;
  channels: string[];
  onMessage: (data: unknown) => void;
  onError?: (error: Error) => void;
  fallbackEndpoint?: string;
  fallbackInterval?: number;
}

export function useWebSocketWithFallback({
  url,
  channels,
  onMessage,
  onError,
  fallbackEndpoint,
  fallbackInterval = 5000,
}: UseWebSocketOptions) {
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const fallbackTimerRef = useRef<number | null>(null);
  const reconnectAttempts = useRef(0);

  const connect = useCallback(() => {
    setConnectionState('connecting');

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnectionState('connected');
        reconnectAttempts.current = 0;

        // 发送订阅消息
        ws.send(JSON.stringify({
          type: 'SUBSCRIBE',
          channels,
        }));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          onMessage(data);
        } catch {
          onMessage(event.data);
        }
      };

      ws.onerror = (error) => {
        onError?.(new Error('WebSocket error'));
      };

      ws.onclose = () => {
        setConnectionState('disconnected');
        wsRef.current = null;
        startFallbackPolling();
      };
    } catch (error) {
      setConnectionState('disconnected');
      startFallbackPolling();
    }
  }, [url, channels, onMessage, onError]);

  const startFallbackPolling = useCallback(() => {
    if (!fallbackEndpoint || fallbackTimerRef.current) return;

    fallbackTimerRef.current = window.setInterval(async () => {
      try {
        const response = await fetch(fallbackEndpoint);
        const data = await response.json();
        onMessage({ type: 'fallback', data });
      } catch {
        // 静默失败，继续轮询
      }
    }, fallbackInterval);
  }, [fallbackEndpoint, fallbackInterval, onMessage]);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;

    if (fallbackTimerRef.current) {
      clearInterval(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }

    setConnectionState('disconnected');
  }, []);

  useEffect(() => {
    connect();
    return () => disconnect(); // Cleanup
  }, [connect, disconnect]);

  return {
    connectionState,
    reconnect: connect,
    disconnect,
  };
}
```

**Why**:
1. useCallback 避免不必要的重渲染
2. useRef 存储 WebSocket 实例，避免闭包问题
3. useEffect cleanup 防止内存泄漏
4. 指数退避重连策略

**验收标准**:
- [ ] WebSocket 断开自动重连
- [ ] 重连失败自动降级轮询
- [ ] 状态 UI 正确显示

---

## 📋 Phase 2 验收清单

- [ ] T1: `useRuntimeStore.ts` Zustand 重构
- [ ] T2: Zustand 统一状态管理
- [ ] T3: React Query 缓存层
- [ ] T4: 测试覆盖率 40% → 80%
- [ ] T5: WebSocket 降级策略完善
- [ ] 所有新代码通过 `npm run typecheck`
- [ ] 所有新代码通过 `npm run test`

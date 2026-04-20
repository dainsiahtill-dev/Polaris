/**
 * useTaskDependencies Hook 测试
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useTaskDependencies } from '../useTaskDependencies';
import type { PmTask } from '@/types/task';
import { TaskStatus } from '@/types/task';

// 测试数据
const baseTasks: PmTask[] = [
  {
    id: 'task-1',
    title: '任务 1',
    status: TaskStatus.PENDING,
    done: false,
    priority: 1,
    acceptance: [],
  },
  {
    id: 'task-2',
    title: '任务 2',
    status: TaskStatus.PENDING,
    done: false,
    priority: 2,
    acceptance: [],
    dependencies: ['task-1'],
  },
  {
    id: 'task-3',
    title: '任务 3',
    status: TaskStatus.PENDING,
    done: false,
    priority: 3,
    acceptance: [],
    dependencies: ['task-2'],
  },
];

describe('useTaskDependencies', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('节点生成', () => {
    it('应该为空任务列表生成空节点数组', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: [] })
      );

      expect(result.current.nodes).toEqual([]);
      expect(result.current.edges).toEqual([]);
    });

    it('应该为每个任务生成一个节点', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks })
      );

      expect(result.current.nodes.length).toBe(3);
    });

    it('节点应该包含正确的任务数据', () => {
      const onTaskClick = vi.fn();
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks, onTaskClick })
      );

      const task1Node = result.current.nodes.find((n) => n.id === 'task-1');
      expect(task1Node).toBeDefined();
      expect(task1Node?.data.label).toBe('任务 1');
      expect(task1Node?.data.status).toBe(TaskStatus.PENDING);
    });
  });

  describe('边生成', () => {
    it('应该为空任务列表生成空边数组', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: [] })
      );

      expect(result.current.edges).toEqual([]);
    });

    it('应该为依赖关系生成边', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks })
      );

      // task-2 依赖 task-1
      const edge1 = result.current.edges.find(
        (e) => e.source === 'task-1' && e.target === 'task-2'
      );
      expect(edge1).toBeDefined();

      // task-3 依赖 task-2
      const edge2 = result.current.edges.find(
        (e) => e.source === 'task-2' && e.target === 'task-3'
      );
      expect(edge2).toBeDefined();
    });

    it('不应该为不存在的依赖生成边', () => {
      const tasksWithInvalidDeps: PmTask[] = [
        {
          id: 'task-1',
          title: '任务 1',
          status: TaskStatus.PENDING,
          done: false,
          priority: 1,
          acceptance: [],
          dependencies: ['non-existent-task'],
        },
      ];

      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: tasksWithInvalidDeps })
      );

      expect(result.current.edges).toEqual([]);
    });
  });

  describe('循环依赖检测', () => {
    it('应该正确检测无循环依赖', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks, detectCycles: true })
      );

      expect(result.current.cycleInfo.hasCycles).toBe(false);
      expect(result.current.cycleInfo.cycleTaskIds.size).toBe(0);
    });

    it('应该正确检测循环依赖', () => {
      const cyclicTasks: PmTask[] = [
        {
          id: 'task-a',
          title: '任务 A',
          status: TaskStatus.PENDING,
          done: false,
          priority: 1,
          acceptance: [],
          dependencies: ['task-b'],
        },
        {
          id: 'task-b',
          title: '任务 B',
          status: TaskStatus.PENDING,
          done: false,
          priority: 2,
          acceptance: [],
          dependencies: ['task-a'], // 形成循环
        },
      ];

      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: cyclicTasks, detectCycles: true })
      );

      expect(result.current.cycleInfo.hasCycles).toBe(true);
      expect(result.current.cycleInfo.cycleTaskIds.has('task-a')).toBe(true);
      expect(result.current.cycleInfo.cycleTaskIds.has('task-b')).toBe(true);
    });

    it('应该禁用循环检测当 detectCycles 为 false', () => {
      const cyclicTasks: PmTask[] = [
        {
          id: 'task-a',
          title: '任务 A',
          status: TaskStatus.PENDING,
          done: false,
          priority: 1,
          acceptance: [],
          dependencies: ['task-b'],
        },
        {
          id: 'task-b',
          title: '任务 B',
          status: TaskStatus.PENDING,
          done: false,
          priority: 2,
          acceptance: [],
          dependencies: ['task-a'],
        },
      ];

      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: cyclicTasks, detectCycles: false })
      );

      expect(result.current.cycleInfo.hasCycles).toBe(false);
      expect(result.current.cycleInfo.cycleTaskIds.size).toBe(0);
    });
  });

  describe('任务深度计算', () => {
    it('应该正确计算无依赖任务的深度为 0', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks })
      );

      expect(result.current.getTaskDepth('task-1')).toBe(0);
    });

    it('应该正确计算单层依赖的深度', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks })
      );

      expect(result.current.getTaskDepth('task-2')).toBe(1);
    });

    it('应该正确计算多层依赖的深度', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks })
      );

      expect(result.current.getTaskDepth('task-3')).toBe(2);
    });

    it('应该返回 -1 对于不存在的任务', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks })
      );

      expect(result.current.getTaskDepth('non-existent')).toBe(-1);
    });
  });

  describe('taskNodeMap', () => {
    it('应该包含所有任务的节点映射', () => {
      const { result } = renderHook(() =>
        useTaskDependencies({ tasks: baseTasks })
      );

      expect(result.current.taskNodeMap.size).toBe(3);
      expect(result.current.taskNodeMap.has('task-1')).toBe(true);
      expect(result.current.taskNodeMap.has('task-2')).toBe(true);
      expect(result.current.taskNodeMap.has('task-3')).toBe(true);
    });
  });
});

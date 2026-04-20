/**
 * useTaskDependencies - 任务依赖图数据处理 Hook
 *
 * 处理任务数据，生成节点和边，检测循环依赖
 */

import { useMemo, useCallback } from 'react';
import { type Edge } from '@xyflow/react';
import type { PmTask } from '@/types/task';
import { TaskStatus } from '@/types/task';
import { type TaskNodeData, type TaskNode } from './TaskNode';

export interface UseTaskDependenciesOptions {
  /** 任务列表 */
  tasks: PmTask[];
  /** 任务点击回调 */
  onTaskClick?: (taskId: string) => void;
  /** 是否检测循环依赖 */
  detectCycles?: boolean;
}

export interface CycleInfo {
  /** 是否存在循环依赖 */
  hasCycles: boolean;
  /** 循环依赖中的任务 ID 集合 */
  cycleTaskIds: Set<string>;
  /** 每个循环依赖路径 */
  cycles: string[][];
}

export interface TaskDependencyResult {
  /** React Flow 节点 */
  nodes: TaskNode[];
  /** React Flow 边 */
  edges: Edge[];
  /** 循环依赖信息 */
  cycleInfo: CycleInfo;
  /** 任务 ID 到节点的映射 */
  taskNodeMap: Map<string, TaskNode>;
  /** 获取任务深度（用于布局） */
  getTaskDepth: (taskId: string) => number;
}

/**
 * 检测有向图中的循环依赖
 * 使用 Kahn 算法（BFS 拓扑排序）
 */
function detectCycles(tasks: PmTask[]): CycleInfo {
  if (tasks.length === 0) {
    return { hasCycles: false, cycleTaskIds: new Set(), cycles: [] };
  }

  const taskMap = new Map(tasks.map((t) => [t.id, t]));
  const inDegree = new Map<string, number>();
  const adjacency = new Map<string, string[]>();

  // 初始化
  for (const task of tasks) {
    inDegree.set(task.id, 0);
    adjacency.set(task.id, []);
  }

  // 构建图
  for (const task of tasks) {
    const deps = task.dependencies || [];
    for (const depId of deps) {
      if (taskMap.has(depId)) {
        adjacency.get(depId)?.push(task.id);
        inDegree.set(task.id, (inDegree.get(task.id) || 0) + 1);
      }
    }
  }

  // Kahn 算法检测循环
  const queue: string[] = [];
  const visited: string[] = [];
  const cycleTaskIds = new Set<string>();

  // 初始化入度为 0 的节点
  for (const [taskId, degree] of inDegree) {
    if (degree === 0) {
      queue.push(taskId);
    }
  }

  while (queue.length > 0) {
    const node = queue.shift()!;
    visited.push(node);

    for (const neighbor of adjacency.get(node) || []) {
      const newDegree = (inDegree.get(neighbor) || 0) - 1;
      inDegree.set(neighbor, newDegree);
      if (newDegree === 0) {
        queue.push(neighbor);
      }
    }
  }

  // 未访问的节点在循环中
  for (const task of tasks) {
    if (!visited.includes(task.id)) {
      cycleTaskIds.add(task.id);
    }
  }

  // 找出具体循环路径
  const cycles: string[][] = [];
  if (cycleTaskIds.size > 0) {
    // 对于简单检测，只要存在 cycleTaskIds 就认为有循环
    cycles.push([...cycleTaskIds]);
  }

  return {
    hasCycles: cycleTaskIds.size > 0,
    cycleTaskIds,
    cycles,
  };
}

/**
 * 使用拓扑排序计算任务深度
 * 深度表示任务在依赖链中的层级
 */
function computeTaskDepths(
  tasks: PmTask[],
  cycleInfo: CycleInfo
): Map<string, number> {
  const depths = new Map<string, number>();
  const taskMap = new Map(tasks.map((t) => [t.id, t]));

  // 初始化深度
  for (const task of tasks) {
    depths.set(task.id, 0);
  }

  // 计算每个任务的深度
  // 深度 = max(所有前置依赖的深度) + 1
  for (const task of tasks) {
    const deps = task.dependencies || [];
    if (deps.length === 0) {
      depths.set(task.id, 0);
    } else {
      let maxDepth = 0;
      for (const depId of deps) {
        if (taskMap.has(depId)) {
          const depDepth = depths.get(depId) || 0;
          maxDepth = Math.max(maxDepth, depDepth + 1);
        }
      }
      depths.set(task.id, maxDepth);
    }
  }

  return depths;
}

/**
 * useTaskDependencies Hook
 *
 * 处理任务列表，生成 React Flow 节点和边
 */
export function useTaskDependencies({
  tasks,
  onTaskClick,
  detectCycles: shouldDetectCycles = true,
}: UseTaskDependenciesOptions): TaskDependencyResult {
  // 检测循环依赖
  const cycleInfo = useMemo<CycleInfo>(() => {
    if (!shouldDetectCycles) {
      return { hasCycles: false, cycleTaskIds: new Set(), cycles: [] };
    }
    return detectCycles(tasks);
  }, [tasks, shouldDetectCycles]);

  // 计算任务深度
  const depths = useMemo(() => {
    return computeTaskDepths(tasks, cycleInfo);
  }, [tasks, cycleInfo]);

  // 生成节点
  const nodes = useMemo<TaskNode[]>(() => {
    if (tasks.length === 0) return [];

    // 收集被依赖的任务
    const dependedUpon = new Set<string>();
    for (const task of tasks) {
      const deps = task.dependencies || [];
      for (const depId of deps) {
        if (tasks.some((t) => t.id === depId)) {
          dependedUpon.add(depId);
        }
      }
    }

    // 按深度分组以优化布局
    const depthGroups = new Map<number, PmTask[]>();
    for (const task of tasks) {
      const depth = depths.get(task.id) || 0;
      const group = depthGroups.get(depth) || [];
      group.push(task);
      depthGroups.set(depth, group);
    }

    // 生成节点
    const nodeSpacingX = 220;
    const nodeSpacingY = 120;
    const startX = 50;
    const startY = 50;

    const resultNodes: TaskNode[] = [];

    for (const [depth, groupTasks] of depthGroups) {
      for (let i = 0; i < groupTasks.length; i++) {
        const task = groupTasks[i];
        const deps = task.dependencies || [];

        // 检查是否有依赖
        const hasDependencies = deps.some(
          (depId) => tasks.some((t) => t.id === depId)
        );

        resultNodes.push({
          id: task.id,
          type: 'taskNode',
          position: {
            x: startX + depth * nodeSpacingX,
            y: startY + i * nodeSpacingY + depth * nodeSpacingY * 0.5,
          },
          data: {
            label: task.title,
            status: task.status,
            description: task.description || task.goal,
            hasDependencies,
            isDependedUpon: dependedUpon.has(task.id),
            inCycle: cycleInfo.cycleTaskIds.has(task.id),
            onClick: onTaskClick,
          },
        });
      }
    }

    return resultNodes;
  }, [tasks, depths, cycleInfo.cycleTaskIds, onTaskClick]);

  // 生成边
  const edges = useMemo<Edge[]>(() => {
    if (tasks.length === 0) return [];

    const taskIds = new Set(tasks.map((t) => t.id));

    const resultEdges: Edge[] = [];

    for (const task of tasks) {
      const deps = task.dependencies || [];

      for (const depId of deps) {
        // 只连接存在于任务列表中的依赖
        if (!taskIds.has(depId)) continue;

        const inCycle =
          cycleInfo.cycleTaskIds.has(task.id) &&
          cycleInfo.cycleTaskIds.has(depId);

        resultEdges.push({
          id: `edge-${depId}-${task.id}`,
          source: depId,
          target: task.id,
          type: 'smoothstep',
          animated: task.status === TaskStatus.IN_PROGRESS,
          style: {
            stroke: inCycle ? '#ef4444' : '#64748b',
            strokeWidth: inCycle ? 2 : 1,
          },
          className: inCycle ? 'stroke-red-500' : '',
        });
      }
    }

    return resultEdges;
  }, [tasks, cycleInfo.cycleTaskIds]);

  // 任务 ID 到节点的映射
  const taskNodeMap = useMemo(() => {
    return new Map(nodes.map((n) => [n.id, n]));
  }, [nodes]);

  // 获取任务深度
  const getTaskDepth = useCallback(
    (taskId: string): number => {
      return depths.get(taskId) ?? -1;
    },
    [depths]
  );

  return {
    nodes,
    edges,
    cycleInfo,
    taskNodeMap,
    getTaskDepth,
  };
}

export default useTaskDependencies;

/**
 * TaskDependencyGraph - 任务依赖图可视化组件
 *
 * 基于 @xyflow/react 渲染任务依赖关系图
 */

import { memo, useCallback, useEffect } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  useNodesState,
  useEdgesState,
  type Edge,
  type NodeMouseHandler,
  Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { AlertTriangle, GitBranch } from 'lucide-react';
import type { PmTask } from '@/types/task';
import { TaskNode, type TaskNodeData, type TaskNode as TaskNodeType } from './TaskNode';
import { useTaskDependencies, type CycleInfo } from './useTaskDependencies';
import { cn } from '@/app/components/ui/utils';

export interface TaskDependencyGraphProps {
  /** 任务列表 */
  tasks: PmTask[];
  /** 任务点击回调 */
  onTaskClick?: (taskId: string) => void;
  /** 是否显示控制面板 */
  showControls?: boolean;
  /** 是否检测循环依赖 */
  detectCycles?: boolean;
  /** 高度 */
  height?: string;
  /** 自定义类名 */
  className?: string;
}

interface CycleWarningProps {
  cycleInfo: CycleInfo;
  onTaskClick?: (taskId: string) => void;
}

const CycleWarning = memo(function CycleWarning({
  cycleInfo,
  onTaskClick,
}: CycleWarningProps) {
  if (!cycleInfo.hasCycles) return null;

  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-red-500/20 border border-red-500/30 rounded-lg">
      <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
      <div className="flex-1 min-w-0">
        <span className="text-xs font-medium text-red-300">
          检测到循环依赖
        </span>
        <div className="flex flex-wrap gap-1 mt-1">
          {Array.from(cycleInfo.cycleTaskIds)
            .slice(0, 5)
            .map((taskId) => (
              <button
                key={taskId}
                type="button"
                onClick={() => onTaskClick?.(taskId)}
                className="px-2 py-0.5 text-[10px] bg-red-500/30 hover:bg-red-500/50 text-red-200 rounded transition-colors"
              >
                {taskId.substring(0, 8)}...
              </button>
            ))}
          {cycleInfo.cycleTaskIds.size > 5 && (
            <span className="text-[10px] text-red-400/70">
              +{cycleInfo.cycleTaskIds.size - 5} 个
            </span>
          )}
        </div>
      </div>
    </div>
  );
});

/** 节点类型映射 */
const nodeTypes = {
  taskNode: TaskNode,
};

export function TaskDependencyGraph({
  tasks,
  onTaskClick,
  showControls = true,
  detectCycles = true,
  height = '400px',
  className,
}: TaskDependencyGraphProps) {
  // 使用 hook 处理依赖数据
  const { nodes: computedNodes, edges: computedEdges, cycleInfo } =
    useTaskDependencies({
      tasks,
      onTaskClick,
      detectCycles,
    });

  // React Flow 状态管理
  const [nodes, setNodes, onNodesChange] = useNodesState<TaskNodeType>(computedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(computedEdges);

  // 同步计算结果到状态
  // 注意：当 tasks 变化时，重新初始化节点和边
  useEffect(() => {
    setNodes(computedNodes);
    setEdges(computedEdges);
  }, [computedNodes, computedEdges, setNodes, setEdges]);

  // 节点点击处理
  const onNodeClick: NodeMouseHandler<TaskNodeType> = useCallback(
    (_, node) => {
      onTaskClick?.(node.id);
    },
    [onTaskClick]
  );

  // 面板点击（清除选择）
  const onPaneClick = useCallback(() => {
    // 可以在这里添加取消选择的逻辑
  }, []);

  // 空状态
  if (tasks.length === 0) {
    return (
      <div
        className={cn(
          'rounded-lg border border-slate-700 bg-slate-900/50 flex flex-col items-center justify-center',
          className
        )}
        style={{ height }}
      >
        <GitBranch className="w-12 h-12 text-slate-600 mb-3" />
        <p className="text-sm text-slate-400">暂无任务数据</p>
        <p className="text-xs text-slate-500 mt-1">
          添加任务以查看依赖关系图
        </p>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'rounded-lg border border-slate-700 bg-slate-900/50 overflow-hidden',
        className
      )}
      style={{ height }}
      data-testid="task-dependency-graph"
    >
      {/* 顶部信息栏 */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-700/50 bg-slate-800/30">
        <div className="flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-slate-400" />
          <span className="text-xs font-medium text-slate-300">
            任务依赖图
          </span>
          <span className="text-[10px] text-slate-500">
            {tasks.length} 个任务
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* 状态统计 */}
          <div className="flex items-center gap-2 text-[10px] text-slate-400">
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-slate-500" />
              待处理
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-amber-500" />
              进行中
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              已完成
            </span>
          </div>
        </div>
      </div>

      {/* 循环依赖警告 */}
      {cycleInfo.hasCycles && (
        <div className="px-3 py-2">
          <CycleWarning cycleInfo={cycleInfo} onTaskClick={onTaskClick} />
        </div>
      )}

      {/* 图表区域 */}
      <div className="flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{
            padding: 0.2,
          }}
          minZoom={0.1}
          maxZoom={2}
          defaultEdgeOptions={{
            type: 'smoothstep',
          }}
          proOptions={{
            hideAttribution: true,
          }}
        >
          {showControls && (
            <Controls
              className="bg-slate-800 border-slate-700 [&>button]:bg-slate-700 [&>button]:border-slate-600 [&>button:hover]:bg-slate-600 [&>button]:text-slate-300"
              showInteractive={false}
            />
          )}
          <Background
            gap={20}
            size={1}
            color="rgba(148, 163, 184, 0.15)"
            className="bg-slate-900"
          />
        </ReactFlow>
      </div>
    </div>
  );
}

export default TaskDependencyGraph;

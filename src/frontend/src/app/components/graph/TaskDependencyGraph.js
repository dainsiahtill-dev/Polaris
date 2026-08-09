import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * TaskDependencyGraph - 任务依赖图可视化组件
 *
 * 基于 @xyflow/react 渲染任务依赖关系图
 */
import { memo, useCallback, useEffect } from 'react';
import { Background, Controls, ReactFlow, useNodesState, useEdgesState, } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { AlertTriangle, GitBranch } from 'lucide-react';
import { TaskNode } from './TaskNode';
import { useTaskDependencies } from './useTaskDependencies';
import { cn } from '@/app/components/ui/utils';
const CycleWarning = memo(function CycleWarning({ cycleInfo, onTaskClick, }) {
    if (!cycleInfo.hasCycles)
        return null;
    return (_jsxs("div", { className: "flex items-center gap-2 px-3 py-2 bg-red-500/20 border border-red-500/30 rounded-lg", children: [_jsx(AlertTriangle, { className: "w-4 h-4 text-red-400 shrink-0" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("span", { className: "text-xs font-medium text-red-300", children: "\u68C0\u6D4B\u5230\u5FAA\u73AF\u4F9D\u8D56" }), _jsxs("div", { className: "flex flex-wrap gap-1 mt-1", children: [Array.from(cycleInfo.cycleTaskIds)
                                .slice(0, 5)
                                .map((taskId) => (_jsxs("button", { type: "button", onClick: () => onTaskClick?.(taskId), className: "px-2 py-0.5 text-[10px] bg-red-500/30 hover:bg-red-500/50 text-red-200 rounded transition-colors", children: [taskId.substring(0, 8), "..."] }, taskId))), cycleInfo.cycleTaskIds.size > 5 && (_jsxs("span", { className: "text-[10px] text-red-400/70", children: ["+", cycleInfo.cycleTaskIds.size - 5, " \u4E2A"] }))] })] })] }));
});
/** 节点类型映射 */
const nodeTypes = {
    taskNode: TaskNode,
};
export function TaskDependencyGraph({ tasks, onTaskClick, showControls = true, detectCycles = true, height = '400px', className, }) {
    // 使用 hook 处理依赖数据
    const { nodes: computedNodes, edges: computedEdges, cycleInfo } = useTaskDependencies({
        tasks,
        onTaskClick,
        detectCycles,
    });
    // React Flow 状态管理
    const [nodes, setNodes, onNodesChange] = useNodesState(computedNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState(computedEdges);
    // 同步计算结果到状态
    // 注意：当 tasks 变化时，重新初始化节点和边
    useEffect(() => {
        setNodes(computedNodes);
        setEdges(computedEdges);
    }, [computedNodes, computedEdges, setNodes, setEdges]);
    // 节点点击处理
    const onNodeClick = useCallback((_, node) => {
        onTaskClick?.(node.id);
    }, [onTaskClick]);
    // 面板点击（清除选择）
    const onPaneClick = useCallback(() => {
        // 可以在这里添加取消选择的逻辑
    }, []);
    // 空状态
    if (tasks.length === 0) {
        return (_jsxs("div", { className: cn('rounded-lg border border-slate-700 bg-slate-900/50 flex flex-col items-center justify-center', className), style: { height }, children: [_jsx(GitBranch, { className: "w-12 h-12 text-slate-600 mb-3" }), _jsx("p", { className: "text-sm text-slate-400", children: "\u6682\u65E0\u4EFB\u52A1\u6570\u636E" }), _jsx("p", { className: "text-xs text-slate-500 mt-1", children: "\u6DFB\u52A0\u4EFB\u52A1\u4EE5\u67E5\u770B\u4F9D\u8D56\u5173\u7CFB\u56FE" })] }));
    }
    return (_jsxs("div", { className: cn('rounded-lg border border-slate-700 bg-slate-900/50 overflow-hidden', className), style: { height }, "data-testid": "task-dependency-graph", children: [_jsxs("div", { className: "flex items-center justify-between px-3 py-2 border-b border-slate-700/50 bg-slate-800/30", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(GitBranch, { className: "w-4 h-4 text-slate-400" }), _jsx("span", { className: "text-xs font-medium text-slate-300", children: "\u4EFB\u52A1\u4F9D\u8D56\u56FE" }), _jsxs("span", { className: "text-[10px] text-slate-500", children: [tasks.length, " \u4E2A\u4EFB\u52A1"] })] }), _jsx("div", { className: "flex items-center gap-2", children: _jsxs("div", { className: "flex items-center gap-2 text-[10px] text-slate-400", children: [_jsxs("span", { className: "flex items-center gap-1", children: [_jsx("span", { className: "w-2 h-2 rounded-full bg-slate-500" }), "\u5F85\u5904\u7406"] }), _jsxs("span", { className: "flex items-center gap-1", children: [_jsx("span", { className: "w-2 h-2 rounded-full bg-amber-500" }), "\u8FDB\u884C\u4E2D"] }), _jsxs("span", { className: "flex items-center gap-1", children: [_jsx("span", { className: "w-2 h-2 rounded-full bg-emerald-500" }), "\u5DF2\u5B8C\u6210"] })] }) })] }), cycleInfo.hasCycles && (_jsx("div", { className: "px-3 py-2", children: _jsx(CycleWarning, { cycleInfo: cycleInfo, onTaskClick: onTaskClick }) })), _jsx("div", { className: "flex-1", children: _jsxs(ReactFlow, { nodes: nodes, edges: edges, onNodesChange: onNodesChange, onEdgesChange: onEdgesChange, onNodeClick: onNodeClick, onPaneClick: onPaneClick, nodeTypes: nodeTypes, fitView: true, fitViewOptions: {
                        padding: 0.2,
                    }, minZoom: 0.1, maxZoom: 2, defaultEdgeOptions: {
                        type: 'smoothstep',
                    }, proOptions: {
                        hideAttribution: true,
                    }, children: [showControls && (_jsx(Controls, { className: "bg-slate-800 border-slate-700 [&>button]:bg-slate-700 [&>button]:border-slate-600 [&>button:hover]:bg-slate-600 [&>button]:text-slate-300", showInteractive: false })), _jsx(Background, { gap: 20, size: 1, color: "rgba(148, 163, 184, 0.15)", className: "bg-slate-900" })] }) })] }));
}
export default TaskDependencyGraph;

import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useMemo } from 'react';
import { DragDropContext } from '@hello-pangea/dnd';
import { Loader2 } from 'lucide-react';
import { KanbanColumn } from './KanbanColumn';
import { COLUMN_ORDER, COLUMN_CONFIG, } from './types';
/** 将任务数组按状态分组到 Kanban 列 */
function groupTasksByStatus(tasks) {
    const groups = new Map();
    for (const status of COLUMN_ORDER) {
        groups.set(status, []);
    }
    for (const task of tasks) {
        const existing = groups.get(task.status);
        if (existing) {
            existing.push(task);
        }
        else {
            // 未知状态的任务放入 backlog
            const backlog = groups.get('backlog');
            if (backlog) {
                backlog.push({ ...task, status: 'backlog' });
            }
        }
    }
    return groups;
}
export function KanbanBoard({ tasks, completedIds = new Set(), currentTaskId, onTaskMove, onTaskClick, onAddTask, isLoading = false, className = '', }) {
    /** 按状态分组后的列数据 */
    const columns = useMemo(() => {
        const grouped = groupTasksByStatus(tasks);
        return COLUMN_ORDER.map((status) => ({
            id: status,
            title: COLUMN_CONFIG[status].title,
            titleZh: COLUMN_CONFIG[status].titleZh,
            tasks: grouped.get(status) ?? [],
            color: COLUMN_CONFIG[status].color,
        }));
    }, [tasks]);
    /** 处理拖拽结束事件 */
    const handleDragEnd = useCallback((result) => {
        const { draggableId, source, destination } = result;
        // 未放置到有效目标
        if (!destination)
            return;
        // 源位置和目标位置相同
        if (source.droppableId === destination.droppableId &&
            source.index === destination.index) {
            return;
        }
        const event = {
            taskId: draggableId,
            from: source.droppableId,
            to: destination.droppableId,
            fromIndex: source.index,
            toIndex: destination.index,
        };
        onTaskMove(event);
    }, [onTaskMove]);
    if (isLoading) {
        return (_jsxs("div", { className: `flex items-center justify-center h-64 ${className}`, children: [_jsx(Loader2, { className: "size-6 text-accent animate-spin" }), _jsx("span", { className: "ml-2 text-sm text-text-muted", children: "Loading tasks..." })] }));
    }
    return (_jsx(DragDropContext, { onDragEnd: handleDragEnd, children: _jsx("div", { className: `kanban-board flex gap-4 overflow-x-auto p-4 ${className}`, children: columns.map((column) => (_jsx(KanbanColumn, { column: column, completedIds: completedIds, currentTaskId: currentTaskId, onTaskClick: onTaskClick, onAddTask: onAddTask }, column.id))) }) }));
}
/** 从 PmTask 转换为 KanbanTask */
export function convertToKanbanTask(pmTask, defaultStatus = 'todo') {
    // 解析状态字符串
    const rawStatus = pmTask.status || pmTask.state || defaultStatus;
    const statusLower = rawStatus.toLowerCase();
    let status = defaultStatus;
    if (statusLower.includes('backlog')) {
        status = 'backlog';
    }
    else if (statusLower.includes('todo') || statusLower.includes('pending')) {
        status = 'todo';
    }
    else if (statusLower.includes('progress') || statusLower.includes('running')) {
        status = 'in_progress';
    }
    else if (statusLower.includes('done') || statusLower.includes('complete') || statusLower.includes('success')) {
        status = 'done';
    }
    // 解析优先级数字到标签
    let priority = 'medium';
    const p = pmTask.priority;
    if (typeof p === 'number') {
        if (p >= 4)
            priority = 'urgent';
        else if (p >= 3)
            priority = 'high';
        else if (p >= 2)
            priority = 'medium';
        else
            priority = 'low';
    }
    return {
        id: pmTask.id,
        title: pmTask.title || pmTask.goal || pmTask.summary || 'Untitled Task',
        goal: pmTask.goal,
        summary: pmTask.summary,
        priority,
        status,
        done: pmTask.done || pmTask.completed || false,
        completed: pmTask.completed,
    };
}

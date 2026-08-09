import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { memo } from 'react';
import { Droppable } from '@hello-pangea/dnd';
import { motion } from 'framer-motion';
import { MoreHorizontal, Plus } from 'lucide-react';
import { KanbanCard } from './KanbanCard';
import { COLUMN_CONFIG } from './types';
function KanbanColumnComponent({ column, completedIds, currentTaskId, onTaskClick, onAddTask, }) {
    const config = COLUMN_CONFIG[column.id];
    return (_jsxs("div", { className: "kanban-column flex-shrink-0 w-[280px]", children: [_jsxs("div", { className: `flex items-center justify-between mb-3 px-1`, children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: `size-2 rounded-full ${column.id === 'backlog' ? 'bg-slate-500' : column.id === 'todo' ? 'bg-blue-500' : column.id === 'in_progress' ? 'bg-amber-500' : 'bg-emerald-500'}` }), _jsx("h3", { className: "font-semibold text-sm text-text-main", children: column.title }), _jsx("span", { className: "text-xs text-text-dim bg-white/5 px-1.5 py-0.5 rounded-full", children: column.tasks.length })] }), _jsxs("div", { className: "flex items-center gap-1", children: [onAddTask && (_jsx("button", { type: "button", onClick: () => onAddTask(column.id), className: "p-1 rounded hover:bg-white/10 text-text-dim hover:text-text-main transition-colors", title: "Add task", children: _jsx(Plus, { className: "size-3.5" }) })), _jsx("button", { type: "button", className: "p-1 rounded hover:bg-white/10 text-text-dim hover:text-text-main transition-colors", title: "Column options", children: _jsx(MoreHorizontal, { className: "size-3.5" }) })] })] }), _jsx(Droppable, { droppableId: column.id, children: (provided, snapshot) => (_jsxs(motion.div, { ref: provided.innerRef, ...provided.droppableProps, className: `kanban-column-content min-h-[200px] rounded-lg p-2 transition-colors ${snapshot.isDraggingOver
                        ? 'bg-accent/10 border-2 border-dashed border-accent/30'
                        : 'bg-white/[0.02] border border-transparent'}`, layout: true, children: [column.tasks.length === 0 && !snapshot.isDraggingOver ? (_jsx("div", { className: "h-full flex items-center justify-center text-xs text-text-dim opacity-50 py-8", children: "No tasks" })) : (column.tasks.map((task, index) => (_jsx(KanbanCard, { task: task, index: index, isCompleted: completedIds.has(task.id) || task.completed || task.done, isCurrent: currentTaskId === task.id }, task.id)))), provided.placeholder] })) })] }));
}
export const KanbanColumn = memo(KanbanColumnComponent);

import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { memo } from 'react';
import { Draggable } from '@hello-pangea/dnd';
import { motion } from 'framer-motion';
import { CheckCircle, Clock, ArrowRight } from 'lucide-react';
import { PriorityBadge } from './PriorityBadge';
function KanbanCardComponent({ task, index, isCompleted, isCurrent }) {
    return (_jsx(Draggable, { draggableId: task.id, index: index, children: (provided, snapshot) => {
            const cardStyle = {
                ...provided.draggableProps.style,
            };
            return (_jsx("div", { ref: provided.innerRef, ...provided.draggableProps, ...provided.dragHandleProps, className: `kanban-card p-3 mb-2 rounded-md border cursor-grab transition-shadow ${snapshot.isDragging
                    ? 'shadow-xl border-primary bg-card'
                    : 'border-border hover:border-primary/50'} ${isCurrent ? 'ring-2 ring-accent/50' : ''}`, children: _jsxs(motion.div, { initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.2, delay: index * 0.05 }, style: cardStyle, children: [_jsxs("div", { className: "flex items-start justify-between gap-2", children: [_jsxs("div", { className: "min-w-0 flex-1", children: [_jsx("p", { className: "text-sm font-medium text-text-main truncate", title: task.title, children: task.title }), task.goal && (_jsx("p", { className: "mt-1 text-xs text-text-muted line-clamp-2", title: task.goal, children: task.goal }))] }), _jsx("div", { className: "flex-shrink-0", children: _jsx(PriorityBadge, { priority: task.priority, showLabel: false }) })] }), _jsxs("div", { className: "mt-3 flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-1.5 text-xs text-text-dim", children: [isCompleted ? (_jsx(CheckCircle, { className: "size-3.5 text-status-success" })) : isCurrent ? (_jsx(ArrowRight, { className: "size-3.5 text-accent animate-pulse" })) : (_jsx(Clock, { className: "size-3.5" })), _jsx("span", { children: isCompleted ? 'Completed' : isCurrent ? 'In Progress' : 'Pending' })] }), task.id && (_jsxs("span", { className: "text-[10px] font-mono text-text-dim opacity-60", children: ["#", task.id.slice(-6)] }))] })] }) }));
        } }));
}
export const KanbanCard = memo(KanbanCardComponent);

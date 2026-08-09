import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { memo } from 'react';
import { ArrowRight, CheckCircle, Clock } from 'lucide-react';
const toDisplayText = (value) => {
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean')
        return String(value).trim();
    return '';
};
const isReadableTaskTitle = (value) => {
    const text = toDisplayText(value);
    if (!text)
        return false;
    return !/^\d+$/.test(text);
};
const pickTaskTitle = (task, fallback) => {
    const record = task;
    const candidates = [record.subject, task.title, task.goal, record.summary, record.description];
    for (const candidate of candidates) {
        if (isReadableTaskTitle(candidate))
            return toDisplayText(candidate);
    }
    return fallback;
};
function TaskListComponent({ tasks, completedSet, currentTaskKey, taskKey, isTaskDone, clampText, }) {
    const toAcceptanceText = (item) => {
        if (typeof item === 'string') {
            return item.trim();
        }
        if (typeof item === 'object' && item && 'description' in item) {
            return String(item.description || '').trim();
        }
        return '';
    };
    return (_jsx("div", { className: "flex min-h-0 flex-1 flex-col gap-2 overflow-auto pr-1 custom-scrollbar", children: tasks.length === 0 ? (_jsx("div", { className: "col-span-full rounded-xl border border-dashed border-white/10 bg-white/5 p-6 text-center text-sm text-text-dim", children: "\u5F85PM Office\u51FA\u5177Task\u6E05\u5355..." })) : (tasks.map((task, index) => {
            const key = taskKey(task);
            const isCompleted = completedSet.has(key) || isTaskDone(task);
            const isCurrent = currentTaskKey === key;
            const idText = toDisplayText(task.id);
            const title = pickTaskTitle(task, idText || `Task ${index + 1}`);
            const goalText = toDisplayText(task.goal);
            const goal = goalText && goalText !== title ? goalText : '';
            const acceptance = Array.isArray(task.acceptance)
                ? task.acceptance
                    .map((item) => toAcceptanceText(item))
                    .filter((item) => item.length > 0)
                    .slice(0, 3)
                : [];
            return (_jsxs("div", { "data-testid": "project-task-item", "data-task-id": idText, className: `rounded-xl border p-3 transition-all duration-300 ${isCurrent
                    ? 'soft-raised border-white/[0.15]'
                    : isCompleted
                        ? 'border-status-success/30 bg-status-success/5 opacity-80'
                        : 'border-white/5 bg-white/5 hover:border-white/10 hover:bg-white/10'}`, children: [_jsxs("div", { className: "flex flex-wrap items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0 flex-1", children: [_jsx("div", { "data-testid": "project-task-title", className: "text-sm font-semibold leading-6 text-text-main", children: clampText(title, 120) }), goal ? _jsx("div", { "data-testid": "project-task-goal", className: "mt-2 text-xs text-text-muted", children: clampText(goal, 180) }) : null, _jsxs("div", { "data-testid": "project-task-metadata", className: "mt-2 flex flex-wrap items-center gap-2 text-[11px] text-text-dim", children: [_jsxs("span", { className: "font-mono", children: ["\u4EFB\u52A1 #", index + 1] }), idText ? (_jsxs("span", { className: "font-mono", children: ["ID ", idText] })) : null, task.priority !== undefined ? (_jsxs("span", { children: ["\u4F18\u5148\u7EA7 ", task.priority] })) : null] })] }), _jsxs("div", { "data-testid": "project-task-status", className: "flex items-center gap-2 text-xs text-text-dim", children: [isCompleted ? (_jsx(CheckCircle, { className: "size-4 text-status-success" })) : isCurrent ? (_jsx(ArrowRight, { className: "size-4 text-accent" })) : (_jsx(Clock, { className: "size-4 text-text-dim" })), _jsx("span", { className: "soft-chip rounded-full px-2 py-0.5", children: isCompleted ? '已完成' : isCurrent ? '进行中' : '待开始' })] })] }), acceptance.length > 0 ? (_jsx("div", { className: "mt-3 flex flex-wrap gap-2 text-[11px] text-text-muted", children: acceptance.map((item, idx) => (_jsx("span", { "data-testid": "project-task-acceptance", className: "rounded-full bg-bg-surface/50 px-2 py-0.5 border border-white/5", children: clampText(item, 80) }, `${key}-acc-${idx}`))) })) : null] }, `${key || title}-${index}`));
        })) }));
}
export const TaskList = memo(TaskListComponent, (prevProps, nextProps) => {
    // Custom comparison - only re-render if tasks or completedSet changes
    return (prevProps.tasks === nextProps.tasks &&
        prevProps.completedSet === nextProps.completedSet &&
        prevProps.currentTaskKey === nextProps.currentTaskKey);
});

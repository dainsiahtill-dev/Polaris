import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { memo } from 'react';
export const TaskQueue = memo(function TaskQueue({ queueItems }) {
    return (_jsxs("div", { className: "mt-4 border-t border-white/5 pt-3", children: [_jsxs("div", { className: "flex items-center justify-between text-xs text-text-muted", children: [_jsx("span", { className: "font-medium uppercase tracking-wide", children: "PM Office \u2192 Chief Engineer \u2192 Director Task\u961F\u5217" }), _jsx("span", { className: "font-mono", children: queueItems.length ? `${queueItems.length} 项` : '-' })] }), _jsx("div", { className: "mt-2 max-h-40 space-y-1 overflow-auto pr-1 custom-scrollbar", children: queueItems.length === 0 ? (_jsx("div", { className: "text-xs text-text-dim", children: "\u6682\u65E0\u5F85\u6D3ETask" })) : (queueItems.map((item, idx) => (_jsxs("div", { className: `flex items-center justify-between gap-2 rounded-md px-2 py-1 text-xs transition-colors ${item.isCurrent
                        ? 'bg-accent/20 text-accent border border-accent/20'
                        : item.isCompleted
                            ? 'bg-status-success/10 text-status-success/80'
                            : 'bg-white/5 text-text-dim hover:bg-white/10'}`, children: [_jsxs("div", { className: "min-w-0 flex-1 truncate", children: [_jsxs("span", { className: "text-text-dim/50 mr-2 font-mono", children: ["#", idx + 1] }), item.title] }), _jsx("span", { className: "shrink-0 text-[10px] opacity-70", children: item.isCompleted ? '已完成' : item.isCurrent ? '进行中' : '待开始' })] }, `${item.key}-${idx}`)))) })] }));
});

import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { Activity, Clock, Cpu, HardDrive, CheckCircle2, } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
export function PMStatusBar({ pmRunning, taskCount, completedCount, iteration, }) {
    const progress = taskCount > 0 ? Math.round((completedCount / taskCount) * 100) : 0;
    return (_jsxs("footer", { className: "h-8 flex items-center justify-between px-4 border-t border-white/10 bg-slate-950/80 backdrop-blur-sm text-[11px]", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsx("div", { className: "flex items-center gap-2", children: pmRunning ? (_jsxs(_Fragment, { children: [_jsx("div", { className: "w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" }), _jsx("span", { className: "text-emerald-400 font-medium", children: "PM Running" })] })) : (_jsxs(_Fragment, { children: [_jsx("div", { className: "w-1.5 h-1.5 rounded-full bg-slate-500" }), _jsx("span", { className: "text-slate-500", children: "PM Stopped" })] })) }), iteration !== undefined && (_jsxs("div", { className: "flex items-center gap-1.5 text-slate-400", children: [_jsx(Clock, { className: "w-3 h-3" }), _jsxs("span", { children: ["\u8FED\u4EE3 ", iteration] })] })), _jsx("div", { className: "w-px h-3 bg-white/10" }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(CheckCircle2, { className: "w-3 h-3 text-slate-500" }), _jsxs("span", { className: "text-slate-400", children: ["\u4EFB\u52A1: ", _jsxs("span", { className: "text-amber-400 font-mono", children: [completedCount, "/", taskCount] })] }), _jsx("div", { className: "w-16 h-1 rounded-full bg-slate-800 overflow-hidden", children: _jsx("div", { className: "h-full rounded-full bg-gradient-to-r from-amber-500 to-amber-400 transition-all duration-500", style: { width: `${progress}%` } }) }), _jsxs("span", { className: "text-slate-500 font-mono", children: [progress, "%"] })] })] }), _jsxs("div", { className: "flex items-center gap-4 text-slate-500", children: [_jsxs("div", { className: "flex items-center gap-1.5", children: [_jsx(Cpu, { className: "w-3 h-3" }), _jsx("span", { children: "PM Core" })] }), _jsxs("div", { className: "flex items-center gap-1.5", children: [_jsx(HardDrive, { className: "w-3 h-3" }), _jsx("span", { children: "Storage OK" })] }), _jsxs("div", { className: "flex items-center gap-1.5", children: [_jsx(Activity, { className: "w-3 h-3" }), _jsx("span", { className: "text-emerald-400/80", children: "Connected" })] })] })] }));
}
function StatusIndicator({ status, label }) {
    const configs = {
        running: { color: 'bg-emerald-400', animate: true },
        stopped: { color: 'bg-slate-500', animate: false },
        error: { color: 'bg-red-400', animate: true },
        warning: { color: 'bg-amber-400', animate: true },
    };
    const config = configs[status];
    return (_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: cn('w-1.5 h-1.5 rounded-full', config.color, config.animate && 'animate-pulse') }), _jsx("span", { className: cn('font-medium', status === 'running' && 'text-emerald-400', status === 'stopped' && 'text-slate-500', status === 'error' && 'text-red-400', status === 'warning' && 'text-amber-400'), children: label })] }));
}

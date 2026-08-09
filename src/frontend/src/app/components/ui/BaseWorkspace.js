import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { ChevronLeft, ListTodo, Activity, FileCode, Terminal, Bug, FileText, History, BarChart3, } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { MiniStatusBadge } from '@/app/components/ai-dialogue/ManusStyleStatusIndicator';
/** 主题配置 */
const THEME_CONFIG = {
    amber: {
        border: 'border-amber-500/20',
        bgGradient: 'bg-gradient-to-r from-slate-900 via-slate-900 to-amber-950/20',
        active: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
        idle: 'text-slate-500 hover:text-slate-300 hover:bg-white/5',
    },
    indigo: {
        border: 'border-indigo-500/20',
        bgGradient: 'bg-gradient-to-r from-slate-900 via-slate-900 to-indigo-950/20',
        active: 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30',
        idle: 'text-slate-500 hover:text-slate-300 hover:bg-white/5',
    },
    emerald: {
        border: 'border-emerald-500/20',
        bgGradient: 'bg-gradient-to-r from-slate-900 via-slate-900 to-emerald-950/20',
        active: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
        idle: 'text-slate-500 hover:text-slate-300 hover:bg-white/5',
    },
};
export function BaseWorkspace({ title, subtitle, theme, isRunning = false, currentPhase, isExecutingTool, onBack, navItems, activeView, onViewChange, children, rightPanel, rightPanelSize = 35, showRightPanel = true, }) {
    const config = THEME_CONFIG[theme];
    return (_jsxs("div", { className: "h-screen flex flex-col bg-slate-950", children: [_jsxs("header", { className: cn('h-14 flex items-center justify-between px-4 border-b', config.border, config.bgGradient), children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("button", { onClick: onBack, className: "p-2 rounded-lg hover:bg-white/10 transition-colors", children: _jsx(ChevronLeft, { className: "w-4 h-4 text-slate-400" }) }), _jsx("div", { className: "w-px h-6 bg-white/10" }), _jsxs("div", { children: [_jsx("div", { className: "text-sm font-semibold text-slate-200", children: title }), subtitle && (_jsx("div", { className: "text-[10px] text-slate-500", children: subtitle }))] })] }), _jsx("div", { className: "flex items-center gap-3", children: (isRunning || currentPhase) && (_jsx(MiniStatusBadge, { phase: isExecutingTool ? 'tool_running' :
                                currentPhase === 'planning' ? 'thinking' :
                                    currentPhase === 'implementation' ? 'executing' :
                                        isRunning ? 'executing' : 'idle', theme: theme })) })] }), _jsxs("div", { className: "flex-1 flex overflow-hidden", children: [_jsx("nav", { className: "w-14 flex flex-col items-center py-4 gap-2 border-r border-white/5 bg-slate-950/50", children: navItems.map((item) => (_jsx(NavButton, { icon: item.icon, label: item.label, active: activeView === item.id, onClick: () => onViewChange(item.id), theme: theme }, item.id))) }), _jsxs(PanelGroup, { direction: "horizontal", className: "flex-1", children: [_jsx(Panel, { defaultSize: showRightPanel && rightPanel ? 100 - rightPanelSize : 100, minSize: 40, children: _jsx("div", { className: "h-full overflow-hidden", children: children }) }), rightPanel && showRightPanel && (_jsxs(_Fragment, { children: [_jsx(PanelResizeHandle, { className: "w-1 bg-white/5 hover:bg-white/10 transition-colors" }), _jsx(Panel, { defaultSize: rightPanelSize, minSize: 25, maxSize: 50, children: rightPanel })] }))] })] })] }));
}
/** 导航按钮组件 */
function NavButton({ icon, label, active, onClick, theme, }) {
    const config = THEME_CONFIG[theme];
    return (_jsxs("button", { onClick: onClick, className: cn('w-10 h-10 rounded-lg flex flex-col items-center justify-center gap-0.5 transition-all border', active
            ? config.active
            : config.idle), title: label, children: [icon, _jsx("span", { className: "text-[8px]", children: label })] }));
}
/** 预设导航项 */
export const DEFAULT_NAV_ITEMS = {
    pm: [
        { id: 'tasks', label: '任务', icon: _jsx(ListTodo, { className: "w-4 h-4" }) },
        { id: 'activity', label: '实时', icon: _jsx(Activity, { className: "w-4 h-4" }) },
        { id: 'documents', label: '文档', icon: _jsx(FileText, { className: "w-4 h-4" }) },
        { id: 'history', label: '历史', icon: _jsx(History, { className: "w-4 h-4" }) },
        { id: 'analytics', label: '统计', icon: _jsx(BarChart3, { className: "w-4 h-4" }) },
    ],
    director: [
        { id: 'tasks', label: '任务', icon: _jsx(ListTodo, { className: "w-4 h-4" }) },
        { id: 'activity', label: '实时', icon: _jsx(Activity, { className: "w-4 h-4" }) },
        { id: 'code', label: '代码', icon: _jsx(FileCode, { className: "w-4 h-4" }) },
        { id: 'terminal', label: '终端', icon: _jsx(Terminal, { className: "w-4 h-4" }) },
        { id: 'debug', label: '调试', icon: _jsx(Bug, { className: "w-4 h-4" }) },
    ],
};

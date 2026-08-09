import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
/** ManusStyleStatusIndicator - Manus 风格实时状态指示器
 *
 * 特性：
 * - 即时状态反馈
 * - 实时显示 LLM 思考过程
 * - 工具调用进度
 * - 动画效果增强感知速度
 */
import { useState, useEffect, useRef } from 'react';
import { Brain, Loader2, Zap, CheckCircle2, AlertCircle, Terminal, ChevronRight, Cpu, Sparkles, Activity, } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
const PHASE_CONFIG = {
    idle: {
        icon: _jsx(Sparkles, { className: "w-4 h-4" }),
        label: '就绪',
        color: 'text-slate-400',
        bgColor: 'bg-slate-500/10',
        borderColor: 'border-slate-500/20',
        animation: '',
    },
    thinking: {
        icon: _jsx(Brain, { className: "w-4 h-4" }),
        label: '思考中',
        color: 'text-slate-400',
        bgColor: 'bg-slate-500/10',
        borderColor: 'border-slate-500/20',
        animation: '',
    },
    executing: {
        icon: _jsx(Zap, { className: "w-4 h-4" }),
        label: '执行中',
        color: 'text-amber-400',
        bgColor: 'bg-amber-500/10',
        borderColor: 'border-amber-500/30',
        animation: 'animate-pulse',
    },
    tool_running: {
        icon: _jsx(Terminal, { className: "w-4 h-4" }),
        label: '工具运行',
        color: 'text-amber-400',
        bgColor: 'bg-amber-500/10',
        borderColor: 'border-amber-500/20',
        animation: '',
    },
    completed: {
        icon: _jsx(CheckCircle2, { className: "w-4 h-4" }),
        label: '已完成',
        color: 'text-emerald-400',
        bgColor: 'bg-emerald-500/10',
        borderColor: 'border-emerald-500/30',
        animation: '',
    },
    error: {
        icon: _jsx(AlertCircle, { className: "w-4 h-4" }),
        label: '出错',
        color: 'text-red-400',
        bgColor: 'bg-red-500/10',
        borderColor: 'border-red-500/30',
        animation: '',
    },
};
const THEME_COLORS = {
    indigo: {
        primary: 'slate-400',
        gradient: 'from-slate-600/20 to-slate-500/20',
        glow: 'shadow-slate-500/10',
    },
    amber: {
        primary: 'amber-400',
        gradient: 'from-amber-500/20 to-orange-500/20',
        glow: 'shadow-amber-500/20',
    },
    cyan: {
        primary: 'slate-400',
        gradient: 'from-slate-600/20 to-slate-500/20',
        glow: 'shadow-slate-500/10',
    },
    emerald: {
        primary: 'emerald-400',
        gradient: 'from-emerald-500/20 to-teal-500/20',
        glow: 'shadow-emerald-500/20',
    },
};
export function ManusStyleStatusIndicator({ phase, message, thinking, toolName, progress, isVisible = true, theme = 'indigo', }) {
    const [displayThinking, setDisplayThinking] = useState('');
    const [showThinking, setShowThinking] = useState(true);
    const thinkingRef = useRef(null);
    const config = PHASE_CONFIG[phase];
    const themeConfig = THEME_COLORS[theme];
    // 打字机效果显示思考内容
    useEffect(() => {
        if (thinking) {
            setDisplayThinking('');
            let index = 0;
            // UI-only typewriter animation; stream content is delivered by runtime WS.
            const timer = setInterval(() => {
                if (index < thinking.length) {
                    setDisplayThinking(thinking.slice(0, index + 1));
                    index++;
                }
                else {
                    clearInterval(timer);
                }
            }, 30);
            return () => clearInterval(timer);
        }
    }, [thinking]);
    // 自动滚动到底部
    useEffect(() => {
        if (thinkingRef.current) {
            thinkingRef.current.scrollTop = thinkingRef.current.scrollHeight;
        }
    }, [displayThinking]);
    if (!isVisible || phase === 'idle') {
        return null;
    }
    return (_jsxs("div", { className: cn('rounded-lg border backdrop-blur-sm transition-all duration-300', config.bgColor, config.borderColor, 'overflow-hidden'), children: [_jsxs("div", { className: cn('flex items-center justify-between px-3 py-2', 'bg-slate-800/50'), children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: cn('animate-spin-slow', phase === 'thinking' && 'animate-spin'), children: config.icon }), _jsx("span", { className: cn('text-sm font-medium', config.color), children: config.label }), message && (_jsxs(_Fragment, { children: [_jsx(ChevronRight, { className: "w-3 h-3 text-slate-500" }), _jsx("span", { className: "text-xs text-slate-400 truncate max-w-[200px]", children: message })] }))] }), progress !== undefined && (_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: "w-24 h-1.5 bg-slate-700 rounded-full overflow-hidden", children: _jsx("div", { className: "h-full rounded-full transition-all duration-300", style: {
                                        width: `${progress}%`,
                                        backgroundColor: themeConfig.primary === 'indigo-400' ? '#818cf8' :
                                            themeConfig.primary === 'amber-400' ? '#fbbf24' :
                                                themeConfig.primary === 'cyan-400' ? '#22d3ee' :
                                                    themeConfig.primary === 'emerald-400' ? '#34d399' :
                                                        themeConfig.primary === 'purple-400' ? '#a78bfa' :
                                                            themeConfig.primary === 'rose-400' ? '#fb7185' :
                                                                themeConfig.primary === 'slate-400' ? '#94a3b8' : '#94a3b8',
                                    } }) }), _jsxs("span", { className: "text-[10px] text-slate-500", children: [progress, "%"] })] }))] }), (phase === 'thinking' || phase === 'tool_running') && displayThinking && (_jsxs("div", { className: "border-t border-white/5", children: [_jsxs("button", { onClick: () => setShowThinking(!showThinking), className: "w-full flex items-center justify-between px-3 py-1.5 text-[10px] text-slate-500 hover:text-slate-400 transition-colors", children: [_jsxs("span", { className: "flex items-center gap-1", children: [_jsx(Brain, { className: "w-3 h-3" }), "\u601D\u8003\u8FC7\u7A0B"] }), _jsx("span", { children: showThinking ? '▼' : '▶' })] }), showThinking && (_jsx("div", { ref: thinkingRef, className: "px-3 pb-3 max-h-32 overflow-auto", children: _jsxs("pre", { className: "text-[11px] text-slate-400 font-mono whitespace-pre-wrap", children: [displayThinking, _jsx("span", { className: "animate-pulse", children: "\u258B" })] }) }))] })), phase === 'tool_running' && toolName && (_jsx("div", { className: "border-t border-white/5 px-3 py-2", children: _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Terminal, { className: "w-3 h-3 text-cyan-400" }), _jsxs("span", { className: "text-xs text-cyan-300", children: ["\u6B63\u5728\u6267\u884C: ", toolName] })] }) }))] }));
}
// 简化版 - 用于嵌入在其他组件中
export function MiniStatusBadge({ phase, theme = 'indigo', }) {
    const config = PHASE_CONFIG[phase];
    const themeConfig = THEME_COLORS[theme];
    return (_jsxs("div", { className: cn('inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-medium border', config.bgColor, config.borderColor, config.color, config.animation), children: [phase === 'thinking' && _jsx(Loader2, { className: "w-3 h-3 animate-spin" }), phase === 'executing' && _jsx(Zap, { className: "w-3 h-3" }), phase === 'tool_running' && _jsx(Cpu, { className: "w-3 h-3" }), phase === 'completed' && _jsx(CheckCircle2, { className: "w-3 h-3" }), phase === 'error' && _jsx(AlertCircle, { className: "w-3 h-3" }), phase === 'idle' && _jsx(Activity, { className: "w-3 h-3" }), config.label] }));
}

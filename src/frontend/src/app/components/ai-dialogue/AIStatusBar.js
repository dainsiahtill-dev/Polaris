import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * AI 状态栏组件
 *
 * 显示状态警告、错误信息和调试面板
 */
import { useState } from 'react';
import { AlertCircle, ChevronDown } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { THEME_COLOR_MAP, getStatusWarningMessage } from './AIDialogueHeader';
/**
 * 状态警告面板
 */
function StatusWarningPanel({ statusKind, roleName, error, debug, theme, onRetry, noticeMode = 'full', }) {
    const [showDebug, setShowDebug] = useState(false);
    if (noticeMode === 'hidden' || statusKind === 'loading' || statusKind === 'ready') {
        return null;
    }
    const { title, detail } = getStatusWarningMessage(statusKind, roleName, error);
    const isWarningOnly = statusKind === 'blocked' || statusKind === 'unconfigured' || statusKind === 'warning';
    if (noticeMode === 'compact') {
        return (_jsx("div", { "data-testid": "ai-status-warning", className: cn('border-b px-3 py-1.5 text-[11px]', isWarningOnly
                ? 'border-amber-400/[0.15] bg-amber-500/5 text-amber-100'
                : 'border-red-500/[0.15] bg-red-500/5 text-red-100'), children: _jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(AlertCircle, { className: cn('h-3.5 w-3.5 shrink-0', isWarningOnly ? 'text-amber-300' : 'text-red-300') }), _jsx("span", { className: "shrink-0 font-medium", children: title }), _jsx("span", { className: cn('min-w-0 truncate', isWarningOnly ? 'text-amber-100/65' : 'text-red-100/65'), title: detail, children: detail }), _jsx(Button, { variant: "ghost", size: "sm", onClick: onRetry, className: cn('ml-auto h-6 shrink-0 px-2 text-[10px]', isWarningOnly
                            ? 'text-amber-200 hover:bg-amber-500/10 hover:text-amber-100'
                            : 'text-red-200 hover:bg-red-500/10 hover:text-red-100'), children: "\u91CD\u8BD5" })] }) }));
    }
    return (_jsx("div", { "data-testid": "ai-status-warning", className: "px-4 py-2 bg-red-500/10 border-b border-red-500/20", children: _jsxs("div", { className: "flex items-start gap-2", children: [_jsx(AlertCircle, { className: "w-4 h-4 text-red-400 mt-0.5 flex-shrink-0" }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("p", { className: "text-xs text-red-400 font-medium", children: title }), _jsx("p", { className: "text-[10px] text-red-400/70 mt-0.5", children: detail }), error && (_jsxs("p", { className: "text-[10px] text-red-400/50 mt-1 font-mono", children: ["\u9519\u8BEF: ", error] })), debug && Object.keys(debug).length > 0 && (_jsxs("div", { className: "mt-2", children: [_jsxs("button", { onClick: () => setShowDebug(!showDebug), className: "flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-400", children: [showDebug ? '隐藏调试信息' : '显示调试信息', _jsx(ChevronDown, { className: cn('w-3 h-3 transition-transform', showDebug && 'rotate-180') })] }), showDebug && (_jsx("pre", { className: "mt-1 p-2 rounded bg-slate-950 border border-white/5 text-[10px] text-slate-500 font-mono overflow-auto max-h-40", children: JSON.stringify(debug, null, 2) }))] }))] }), _jsx(Button, { variant: "ghost", size: "sm", onClick: onRetry, className: "h-6 text-[10px] text-red-400 hover:text-red-300 hover:bg-red-500/10 flex-shrink-0", children: "\u91CD\u8BD5" })] }) }));
}
/**
 * 历史对话面板
 */
export function AIHistoryPanel({ conversations, currentConversationId, theme, welcomeMessage, onNewConversation, onSelectConversation, }) {
    const themeColors = THEME_COLOR_MAP[theme.primary] || THEME_COLOR_MAP.slate;
    return (_jsx("div", { className: "border-b border-white/10 bg-slate-900/80", children: _jsxs("div", { className: "p-3", children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsx("span", { className: "text-xs text-slate-400", children: "\u5386\u53F2\u5BF9\u8BDD" }), _jsx(Button, { variant: "ghost", size: "sm", className: "h-6 text-[10px] text-slate-400 hover:text-slate-200", onClick: onNewConversation, children: "+ \u65B0\u5BF9\u8BDD" })] }), _jsx("div", { className: "max-h-48 overflow-auto space-y-1", children: conversations.length === 0 ? (_jsx("p", { className: "text-[10px] text-slate-500 text-center py-2", children: "\u6682\u65E0\u5386\u53F2\u5BF9\u8BDD" })) : (conversations.map((conv) => (_jsxs("button", { onClick: () => onSelectConversation(conv.id), className: cn('w-full text-left px-3 py-2 rounded-lg text-[11px] transition-colors', conv.id === currentConversationId
                            ? 'text-slate-100'
                            : 'hover:bg-white/5 text-slate-300'), style: conv.id === currentConversationId ? {
                            backgroundColor: themeColors.bg,
                            color: themeColors.text,
                        } : undefined, children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "truncate flex-1", children: conv.title || '未命名对话' }), _jsx("span", { className: "text-[9px] text-slate-500 ml-2", children: new Date(conv.updated_at).toLocaleDateString('zh-CN') })] }), _jsxs("div", { className: "text-[9px] text-slate-500 mt-0.5", children: [conv.message_count, " \u6761\u6D88\u606F"] })] }, conv.id)))) })] }) }));
}
/**
 * AI 状态栏
 */
export function AIStatusBar(props) {
    return _jsx(StatusWarningPanel, { ...props });
}

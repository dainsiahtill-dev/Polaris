import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * AI 对话面板头部组件
 *
 * 显示角色图标、名称、状态和操作按钮
 */
import { Sparkles, History, RefreshCw, MoreHorizontal, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
/**
 * AI 对话面板头部
 */
export function AIDialogueHeader({ theme, roleName, statusDisplay, configuredProviderLabel, configuredModelLabel, hasConversation, showHistory, isChatReady, statusKind, onLoadHistory, onClear, onToggleHistory, }) {
    return (_jsxs("div", { className: "flex h-14 min-w-0 items-center justify-between gap-3 border-b border-white/10 bg-slate-900 px-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx("div", { className: cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-700/80"), children: _jsx(Sparkles, { className: "w-3.5 h-3.5 text-slate-300" }) }), _jsxs("div", { className: "min-w-0", children: [_jsx("h3", { className: "truncate text-sm font-medium text-slate-200", children: "AI \u52A9\u624B" }), _jsx("p", { className: "truncate text-[10px] text-slate-500", children: isChatReady
                                    ? `${configuredProviderLabel} · ${configuredModelLabel}`
                                    : statusKind === 'loading'
                                        ? `${roleName} 状态检查中`
                                        : statusKind === 'unconfigured'
                                            ? `${roleName} 未配置`
                                            : statusKind === 'blocked'
                                                ? `${roleName} 已阻塞`
                                                : `${roleName} 状态获取失败` })] })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-1", children: [statusDisplay, _jsxs(Button, { variant: "ghost", size: "icon", className: "h-7 w-7 text-slate-400 hover:text-slate-200 relative", onClick: () => {
                            onToggleHistory();
                            if (!showHistory)
                                onLoadHistory();
                        }, title: "\u5386\u53F2\u5BF9\u8BDD", children: [_jsx(History, { className: "w-3.5 h-3.5" }), hasConversation && (_jsx("span", { className: "absolute -top-0.5 -right-0.5 w-2 h-2 bg-green-500 rounded-full" }))] }), _jsx(Button, { variant: "ghost", size: "icon", className: "h-7 w-7 text-slate-400 hover:text-slate-200", onClick: onClear, title: "\u6E05\u7A7A\u5BF9\u8BDD", children: _jsx(RefreshCw, { className: "w-3.5 h-3.5" }) }), _jsx(Button, { variant: "ghost", size: "icon", className: "h-7 w-7 text-slate-400 hover:text-slate-200", "aria-label": "\u66F4\u591A\u5BF9\u8BDD\u64CD\u4F5C", title: "\u66F4\u591A\u5BF9\u8BDD\u64CD\u4F5C", children: _jsx(MoreHorizontal, { className: "w-3.5 h-3.5" }) })] })] }));
}
/**
 * 获取状态警告消息
 */
export function getStatusWarningMessage(statusKind, roleName, error) {
    if (statusKind === 'blocked') {
        return {
            title: `${roleName} 当前被阻塞`,
            detail: error || '当前角色运行门禁未通过，请先解除阻塞后再继续。',
        };
    }
    if (statusKind === 'unconfigured') {
        return {
            title: `${roleName} LLM 未配置`,
            detail: `请在设置中配置 ${roleName} 角色的 Provider 和 Model`,
        };
    }
    return {
        title: `${roleName} 状态获取失败`,
        detail: '状态检查请求失败，请先排查后端运行时、数据库初始化或网络错误。',
    };
}
/**
 * 主题颜色映射
 */
export const THEME_COLOR_MAP = {
    amber: {
        bg: 'rgba(245, 158, 11, 0.1)',
        border: 'rgba(245, 158, 11, 0.2)',
        text: '#fbbf24',
    },
    purple: {
        bg: 'rgba(168, 85, 247, 0.1)',
        border: 'rgba(168, 85, 247, 0.2)',
        text: '#a78bfa',
    },
    emerald: {
        bg: 'rgba(16, 185, 129, 0.1)',
        border: 'rgba(16, 185, 129, 0.2)',
        text: '#34d399',
    },
    rose: {
        bg: 'rgba(244, 63, 94, 0.1)',
        border: 'rgba(244, 63, 94, 0.2)',
        text: '#fb7185',
    },
    cyan: {
        bg: 'rgba(6, 182, 212, 0.1)',
        border: 'rgba(6, 182, 212, 0.2)',
        text: '#22d3ee',
    },
    indigo: {
        bg: 'rgba(99, 102, 241, 0.1)',
        border: 'rgba(99, 102, 241, 0.2)',
        text: '#818cf8',
    },
    slate: {
        bg: 'rgba(148, 163, 184, 0.1)',
        border: 'rgba(148, 163, 184, 0.2)',
        text: '#94a3b8',
    },
};

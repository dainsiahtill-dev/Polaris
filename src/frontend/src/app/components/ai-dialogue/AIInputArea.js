import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * AI 输入区域组件
 *
 * 消息输入框和发送按钮
 */
import { memo } from 'react';
import { Send } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { THEME_COLOR_MAP } from './AIDialogueHeader';
/**
 * AI 输入区域 (memoized)
 */
const AIInputAreaComponent = memo(function AIInputArea({ value, onChange, onKeyDown, onSend, isLoading, isChatReady, isExplicitlyUnconfigured, statusKind, blockedReason, roleName, theme, }) {
    // 获取占位符
    const getPlaceholder = () => {
        if (blockedReason)
            return blockedReason;
        if (isChatReady)
            return '输入消息...';
        if (statusKind === 'loading')
            return `${roleName} 状态检查中...`;
        if (isExplicitlyUnconfigured)
            return `请先配置 ${roleName} LLM...`;
        return `${roleName} 状态异常，请先重试`;
    };
    // 获取主题颜色
    const themeColors = THEME_COLOR_MAP[theme.primary] || THEME_COLOR_MAP.slate;
    return (_jsxs("div", { className: "p-4 border-t border-white/10 bg-slate-900/50", children: [_jsx("div", { className: "flex items-center gap-2", children: _jsxs("div", { className: "relative flex-1", children: [_jsx(Input, { value: value, onChange: (e) => onChange(e.target.value), onKeyDown: onKeyDown, placeholder: getPlaceholder(), disabled: isLoading || !isChatReady, className: "pr-10 h-10 bg-slate-950 border-white/10 text-slate-200 placeholder:text-slate-600 disabled:opacity-50", style: {
                                borderColor: themeColors.border,
                            } }), _jsx(Button, { size: "icon", variant: "ghost", className: "absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7 disabled:opacity-50 text-slate-400 hover:text-slate-200", onClick: onSend, disabled: !value.trim() || isLoading || !isChatReady, children: _jsx(Send, { className: "w-4 h-4" }) })] }) }), _jsx("p", { className: "text-[10px] text-slate-600 mt-2 text-center", children: isChatReady
                    ? '按 Enter 发送，Shift + Enter 换行'
                    : blockedReason
                        ? '解除阻塞后即可开始对话'
                        : statusKind === 'loading'
                            ? '正在检查角色状态'
                            : isExplicitlyUnconfigured
                                ? '配置 LLM 后即可开始对话'
                                : '恢复角色状态接口后即可开始对话' })] }));
});
export { AIInputAreaComponent as AIInputArea };
export default AIInputAreaComponent;

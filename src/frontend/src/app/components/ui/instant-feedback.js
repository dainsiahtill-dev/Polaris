import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** InstantFeedbackButton - 即时反馈按钮
 *
 * 特性：
 * - 点击立即显示加载状态
 * - 支持多种状态：idle, loading, success, error
 * - 内置防抖防止重复点击
 * - 状态图标和动画
 */
import { useState, useCallback, forwardRef } from 'react';
import { cn } from '@/app/components/ui/utils';
import { Loader2, CheckCircle2, AlertCircle, ChevronRight } from 'lucide-react';
export const InstantFeedbackButton = forwardRef(({ children, className, state: controlledState, loadingText, successText, errorText, onClick, disableDebounce, successResetMs = 2000, errorResetMs = 3000, disabled, ...props }, ref) => {
    const [uncontrolledState, setUncontrolledState] = useState('idle');
    const [isLoading, setIsLoading] = useState(false);
    // 如果是受控组件，使用 controlledState；否则使用内部状态
    const state = controlledState ?? uncontrolledState;
    const handleClick = useCallback(async (e) => {
        if (isLoading || disabled)
            return;
        // 如果没有 onClick 回调，直接返回
        if (!onClick)
            return;
        // 开始加载
        setIsLoading(true);
        setUncontrolledState('loading');
        try {
            // 执行回调
            const result = onClick(e);
            // 如果返回的是 Promise，等待完成
            if (result instanceof Promise) {
                await result;
            }
            // 成功
            setUncontrolledState('success');
            // 自动重置
            setTimeout(() => {
                setUncontrolledState('idle');
            }, successResetMs);
        }
        catch (error) {
            // 失败
            setUncontrolledState('error');
            // 自动重置
            setTimeout(() => {
                setUncontrolledState('idle');
            }, errorResetMs);
        }
        finally {
            setIsLoading(false);
        }
    }, [onClick, isLoading, disabled, successResetMs, errorResetMs]);
    // 计算显示的文本
    const getDisplayText = () => {
        switch (state) {
            case 'loading':
                return loadingText || children;
            case 'success':
                return successText || children;
            case 'error':
                return errorText || children;
            default:
                return children;
        }
    };
    // 获取图标
    const getIcon = () => {
        switch (state) {
            case 'loading':
                return _jsx(Loader2, { className: "w-4 h-4 animate-spin" });
            case 'success':
                return _jsx(CheckCircle2, { className: "w-4 h-4" });
            case 'error':
                return _jsx(AlertCircle, { className: "w-4 h-4" });
            default:
                return _jsx(ChevronRight, { className: "w-4 h-4" });
        }
    };
    // 状态样式
    const stateStyles = {
        idle: '',
        loading: 'border-amber-500/50 bg-amber-500/10 text-amber-300',
        success: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300',
        error: 'border-red-500/50 bg-red-500/10 text-red-300',
    };
    return (_jsxs("button", { ref: ref, className: cn(
        // 基础样式
        'inline-flex items-center justify-center gap-2', 'rounded-lg px-4 py-2 text-sm font-medium', 'transition-all duration-200', 'focus:outline-none focus:ring-2 focus:ring-offset-2', 
        // 禁用状态
        (disabled || isLoading) && 'opacity-50 cursor-not-allowed', 
        // 状态样式
        state !== 'idle' && stateStyles[state], className), disabled: disabled || isLoading, onClick: handleClick, ...props, children: [getIcon(), _jsx("span", { children: getDisplayText() })] }));
});
InstantFeedbackButton.displayName = 'InstantFeedbackButton';
const STATUS_CONFIG = {
    idle: {
        icon: '○',
        color: 'text-slate-500',
        bg: 'bg-slate-500/10',
    },
    loading: {
        icon: '◐',
        color: 'text-amber-500',
        bg: 'bg-amber-500/10',
        animate: true,
    },
    success: {
        icon: '●',
        color: 'text-emerald-500',
        bg: 'bg-emerald-500/10',
    },
    error: {
        icon: '✕',
        color: 'text-red-500',
        bg: 'bg-red-500/10',
    },
    warning: {
        icon: '⚠',
        color: 'text-amber-500',
        bg: 'bg-amber-500/10',
    },
};
export function StatusIndicator({ variant, text, description, animate = true, className, }) {
    const config = STATUS_CONFIG[variant];
    return (_jsxs("div", { className: cn('flex items-center gap-3 px-3 py-2 rounded-lg border', config.bg, 'border-white/5', className), children: [_jsx("div", { className: cn('w-2 h-2 rounded-full', config.color, animate && variant === 'loading' && 'animate-pulse') }), _jsxs("div", { children: [_jsx("div", { className: cn('text-sm font-medium', config.color), children: text }), description && (_jsx("div", { className: "text-xs text-slate-500", children: description }))] })] }));
}

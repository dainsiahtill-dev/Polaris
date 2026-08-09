import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * AI 消息列表组件
 *
 * 渲染对话消息气泡，支持流式状态显示
 */
import { useState, useRef, useEffect } from 'react';
import { User, Bot, AlertCircle, Copy, Check, Brain, ChevronDown, } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { SmartContentRenderer } from './SmartContentRenderer';
import { ManusStyleStatusIndicator } from './ManusStyleStatusIndicator';
function MessageBubble({ message, theme, onCopy }) {
    const isUser = message.role === 'user';
    const isSystem = message.role === 'system';
    const isError = message.error;
    const [showThinking, setShowThinking] = useState(false);
    const [copied, setCopied] = useState(false);
    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(message.content);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        }
        catch {
            // Ignore copy errors
        }
    };
    // 获取消息气泡背景色
    const getBubbleBgClass = () => {
        if (isUser) {
            switch (theme.primary) {
                case 'amber': return 'bg-amber-600 text-white rounded-tr-sm';
                case 'purple': return 'bg-purple-600 text-white rounded-tr-sm';
                case 'emerald': return 'bg-emerald-600 text-white rounded-tr-sm';
                case 'rose': return 'bg-rose-600 text-white rounded-tr-sm';
                case 'cyan': return 'bg-cyan-600 text-white rounded-tr-sm';
                case 'indigo': return 'bg-indigo-600 text-white rounded-tr-sm';
                default: return 'bg-slate-600 text-white rounded-tr-sm';
            }
        }
        if (isError) {
            return 'bg-red-500/10 text-red-400 rounded-tl-sm border border-red-500/20';
        }
        if (isSystem) {
            return 'bg-slate-800/80 text-slate-400 rounded-tl-sm border border-white/5';
        }
        return 'bg-slate-800 text-slate-200 rounded-tl-sm border border-white/10';
    };
    // 获取头像样式
    const getAvatarClass = () => {
        if (isUser)
            return 'bg-slate-700';
        if (isError)
            return 'bg-red-500/20';
        if (isSystem)
            return 'bg-slate-800';
        return cn('bg-gradient-to-br', theme.gradient);
    };
    return (_jsxs("div", { className: cn('group flex gap-3', isUser ? 'flex-row-reverse' : 'flex-row'), children: [_jsx("div", { className: cn('w-7 h-7 rounded-lg flex-shrink-0 flex items-center justify-center', getAvatarClass()), children: isUser ? (_jsx(User, { className: "w-3.5 h-3.5 text-slate-300" })) : isError ? (_jsx(AlertCircle, { className: "w-3.5 h-3.5 text-red-400" })) : (_jsx(Bot, { className: "w-3.5 h-3.5 text-white" })) }), _jsxs("div", { className: cn('flex-1 max-w-[85%]', isUser ? 'text-right' : 'text-left'), children: [_jsxs("div", { className: cn('inline-block text-left px-3 py-2 rounded-lg text-sm relative', getBubbleBgClass()), children: [_jsx(SmartContentRenderer, { content: message.content }), !isUser && !isSystem && !isError && (_jsx("button", { onClick: handleCopy, className: "absolute -right-8 top-1 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded-md hover:bg-white/5 text-slate-400 hover:text-slate-200", title: "\u590D\u5236", children: copied ? (_jsx(Check, { className: "w-3.5 h-3.5 text-emerald-400" })) : (_jsx(Copy, { className: "w-3.5 h-3.5" })) }))] }), !isUser && !isSystem && !isError && message.thinking && (_jsxs("div", { className: "mt-2", children: [_jsxs("button", { onClick: () => setShowThinking(!showThinking), className: "flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-400", children: [_jsx(Brain, { className: "w-3 h-3" }), showThinking ? '隐藏思考过程' : '显示思考过程', _jsx(ChevronDown, { className: cn('w-3 h-3 transition-transform', showThinking && 'rotate-180') })] }), showThinking && (_jsx("div", { className: "mt-1 p-2 rounded-lg bg-slate-950/50 border border-white/5", children: _jsx("p", { className: "text-[11px] text-slate-500 whitespace-pre-wrap", children: message.thinking }) }))] })), _jsx("p", { className: "text-[10px] text-slate-600 mt-1", children: message.timestamp.toLocaleTimeString('zh-CN', {
                            hour: '2-digit',
                            minute: '2-digit',
                        }) })] })] }));
}
/**
 * AI 消息列表
 */
export function AIMessageList({ messages, isLoading, theme, roleName, }) {
    const messagesEndRef = useRef(null);
    // Auto-scroll to bottom
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);
    const lastMessage = messages.length > 0 ? messages[messages.length - 1] : null;
    return (_jsxs("div", { className: "flex-1 overflow-auto p-4 space-y-4", children: [messages.map((message, index) => (_jsx(MessageBubble, { message: message, theme: theme, onCopy: () => { } }, message.id))), isLoading && lastMessage && (_jsx(ManusStyleStatusIndicator, { phase: lastMessage.statusPhase || 'thinking', message: lastMessage.thinking ? '正在思考...' : lastMessage.content ? '生成回复中...' : '等待响应...', thinking: lastMessage.thinking, toolName: lastMessage.toolName, progress: lastMessage.progress, theme: theme.primary })), _jsx("div", { ref: messagesEndRef })] }));
}

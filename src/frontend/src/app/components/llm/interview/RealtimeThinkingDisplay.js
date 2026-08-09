import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useState } from 'react';
import { Brain, MessageSquare, Terminal, Trash2 } from 'lucide-react';
const KIND_STYLES = {
    reasoning: {
        label: 'Reasoning',
        badge: 'bg-amber-500/20 text-amber-200 border-amber-500/30',
        icon: Brain,
        border: 'border-amber-500/20',
        bg: 'bg-amber-500/5',
    },
    command_execution: {
        label: 'Command',
        badge: 'bg-white/[0.08] text-slate-200 border-white/[0.12]',
        icon: Terminal,
        border: 'border-white/10',
        bg: 'bg-white/[0.04]',
    },
    agent_message: {
        label: 'Agent Message',
        badge: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
        icon: MessageSquare,
        border: 'border-emerald-500/20',
        bg: 'bg-emerald-500/5',
    },
};
const STATUS_LABELS = {
    in_progress: '执行中',
    completed: '已完成',
    failed: '失败',
};
const extractTaggedBlock = (text, tags) => {
    if (!text)
        return undefined;
    for (const tag of tags) {
        const regex = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, 'i');
        const match = text.match(regex);
        if (match && match[1]) {
            return match[1].trim();
        }
    }
    return undefined;
};
// Strip XML tags from text for display
const stripXmlTags = (text) => {
    if (!text)
        return '';
    return text.replace(/<[^>]+>/g, '').trim();
};
// Remove system prompt leakage (common patterns)
const cleanModelOutput = (text) => {
    if (!text)
        return '';
    const patterns = [
        /The user is asking me to[\s\S]*?this approach demonstrates these competencies[\s\S]*?/gi,
        /According to my instructions:[\s\S]*?-\s*I must answer RIGHT NOW[\s\S]*?-\s*I cannot ask for clarification[\s\S]*?/gi,
        /ROLE: You are a job CANDIDATE[\s\S]*?/gi,
        /IMMEDIATE ACTION REQUIRED:[\s\S]*?/gi,
        /FORBIDDEN RESPONSES[\s\S]*?/gi,
    ];
    let cleaned = text;
    patterns.forEach(pattern => {
        cleaned = cleaned.replace(pattern, '');
    });
    return cleaned.trim();
};
export function RealtimeThinkingDisplay({ events, enabled = false, isStreaming = false, onClear, className, dense = false, }) {
    const outputRef = useRef(null);
    const [autoScroll, setAutoScroll] = useState(true);
    useEffect(() => {
        const el = outputRef.current;
        if (!el || !autoScroll)
            return;
        el.scrollTop = el.scrollHeight;
    }, [events, autoScroll]);
    useEffect(() => {
        const el = outputRef.current;
        if (!el)
            return;
        const onScroll = () => {
            const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
            setAutoScroll(nearBottom);
        };
        el.addEventListener('scroll', onScroll);
        return () => el.removeEventListener('scroll', onScroll);
    }, []);
    const emptyText = enabled
        ? '等待思考过程输出...'
        : '开启 Debug 模式 + 实时流式 后可查看思考过程。';
    return (_jsxs("div", { className: `flex min-h-0 flex-col soft-inset rounded-lg ${dense ? 'p-2' : 'p-3'} ${className || ''}`, children: [_jsxs("div", { className: `${dense ? 'mb-1' : 'mb-2'} flex shrink-0 items-center justify-between text-[10px] text-text-dim`, children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "uppercase tracking-wide", children: "\u5B9E\u65F6\u601D\u8003\u8FC7\u7A0B" }), _jsx("span", { className: "text-[9px]", children: isStreaming ? 'streaming...' : autoScroll ? '自动滚动' : '已暂停滚动' })] }), onClear ? (_jsxs("button", { type: "button", onClick: onClear, className: "flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[9px] hover:border-white/30", children: [_jsx(Trash2, { className: "size-3" }), "\u6E05\u7A7A"] })) : null] }), _jsx("div", { ref: outputRef, className: `min-h-0 flex-1 overflow-y-auto pr-1 ${dense ? 'max-h-16 space-y-1' : 'max-h-56 space-y-2'}`, children: events.length === 0 ? (_jsx("div", { className: "text-[11px] text-text-dim italic", children: emptyText })) : (events.map((event) => {
                    const styles = KIND_STYLES[event.kind];
                    const Icon = styles.icon;
                    const time = new Date(event.timestamp).toLocaleTimeString();
                    const statusLabel = event.status ? STATUS_LABELS[event.status] || event.status : '';
                    const derivedThinking = event.thinking || extractTaggedBlock(event.raw, ['thinking', 'think', 'reasoning', 'analysis']);
                    const derivedAnswer = event.answer || extractTaggedBlock(event.raw, ['answer', 'final', 'response']);
                    return (_jsxs("div", { className: `rounded-md border ${styles.border} ${styles.bg} ${dense ? 'p-2 text-[10px] space-y-1' : 'p-3 text-[11px] space-y-2'}`, children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-semibold text-text-main", children: [_jsx(Icon, { className: "size-3" }), _jsx("span", { children: styles.label })] }), _jsxs("div", { className: "flex items-center gap-2", children: [statusLabel ? (_jsx("span", { className: `text-[9px] uppercase tracking-wide px-2 py-0.5 rounded border ${styles.badge}`, children: statusLabel })) : null, _jsx("span", { className: "text-[9px] text-text-dim", children: time })] })] }), event.kind === 'reasoning' ? (_jsxs("div", { className: "space-y-2", children: [derivedThinking ? (_jsxs("div", { className: "rounded border border-amber-500/20 bg-amber-500/5 p-2", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wide text-amber-300 mb-1", children: "\u601D\u8003\u94FE" }), _jsx("div", { className: "text-text-main whitespace-pre-wrap", children: cleanModelOutput(derivedThinking) })] })) : null, derivedAnswer ? (_jsxs("div", { className: "rounded border border-emerald-500/20 bg-emerald-500/5 p-2", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wide text-emerald-300 mb-1", children: "\u4F5C\u7B54" }), _jsx("div", { className: "text-text-main whitespace-pre-wrap", children: derivedAnswer })] })) : event.text ? (_jsx("div", { className: "text-text-main whitespace-pre-wrap", children: cleanModelOutput(stripXmlTags(event.text)) })) : null] })) : null, event.kind === 'command_execution' ? (_jsxs("div", { className: "space-y-2", children: [event.command ? (_jsx("pre", { className: "text-[10px] text-slate-100 soft-inset rounded p-2 whitespace-pre-wrap font-mono", children: event.command })) : null, event.output ? (_jsx("pre", { className: "text-[10px] text-text-main bg-black/30 rounded p-2 border border-white/5 whitespace-pre-wrap font-mono max-h-32 overflow-auto", children: event.output })) : null, typeof event.exitCode === 'number' ? (_jsxs("div", { className: "text-[10px] text-text-dim", children: ["Exit code: ", event.exitCode] })) : null] })) : null, event.kind === 'agent_message' ? (_jsxs("div", { className: "space-y-2", children: [derivedThinking ? (_jsxs("div", { className: "rounded border border-amber-500/20 bg-amber-500/5 p-2", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wide text-amber-300 mb-1", children: "\u601D\u8003\u94FE" }), _jsx("div", { className: "text-text-main whitespace-pre-wrap", children: cleanModelOutput(derivedThinking) })] })) : null, derivedAnswer ? (_jsxs("div", { className: "rounded border border-emerald-500/20 bg-emerald-500/5 p-2", children: [_jsx("div", { className: "text-[9px] uppercase tracking-wide text-emerald-300 mb-1", children: "\u4F5C\u7B54" }), _jsx("div", { className: "text-text-main whitespace-pre-wrap", children: derivedAnswer })] })) : event.raw ? (_jsx("div", { className: "text-text-main whitespace-pre-wrap", children: cleanModelOutput(stripXmlTags(event.raw)) })) : null] })) : null] }, `${event.id}-${event.timestamp}`));
                })) })] }));
}

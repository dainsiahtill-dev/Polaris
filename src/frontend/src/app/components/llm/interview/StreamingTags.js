import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useState } from 'react';
import { Brain, MessageSquare, Loader2 } from 'lucide-react';
const TAG_EVENT_TYPES = [
    'thinking_start',
    'thinking_chunk',
    'thinking_end',
    'answer_start',
    'answer_chunk',
    'answer_end',
];
const isTagEvent = (event) => {
    if (!event || typeof event !== 'object')
        return false;
    const e = event;
    return typeof e.type === 'string' && TAG_EVENT_TYPES.includes(e.type);
};
export function StreamingTags({ events, isStreaming = false, onClear, className, dense = false, }) {
    const outputRef = useRef(null);
    const [autoScroll, setAutoScroll] = useState(true);
    const [contentState, setContentState] = useState({
        thinking: '',
        answer: '',
        isThinkingActive: false,
        isAnswerActive: false,
        lastUpdate: Date.now(),
    });
    useEffect(() => {
        let newThinking = '';
        let newAnswer = '';
        let isThinkingActive = false;
        let isAnswerActive = false;
        for (const event of events) {
            if (!isTagEvent(event))
                continue;
            switch (event.type) {
                case 'thinking_start':
                    isThinkingActive = true;
                    newThinking = '';
                    break;
                case 'thinking_chunk':
                    if (event.data.content) {
                        newThinking += event.data.content;
                    }
                    break;
                case 'thinking_end':
                    isThinkingActive = false;
                    break;
                case 'answer_start':
                    isAnswerActive = true;
                    newAnswer = '';
                    break;
                case 'answer_chunk':
                    if (event.data.content) {
                        newAnswer += event.data.content;
                    }
                    break;
                case 'answer_end':
                    isAnswerActive = false;
                    break;
            }
        }
        setContentState((prev) => {
            if (prev.thinking === newThinking &&
                prev.answer === newAnswer &&
                prev.isThinkingActive === isThinkingActive &&
                prev.isAnswerActive === isAnswerActive) {
                return prev;
            }
            return {
                thinking: newThinking,
                answer: newAnswer,
                isThinkingActive,
                isAnswerActive,
                lastUpdate: Date.now(),
            };
        });
    }, [events]);
    useEffect(() => {
        const el = outputRef.current;
        if (!el || !autoScroll)
            return;
        el.scrollTop = el.scrollHeight;
    }, [contentState.lastUpdate, autoScroll]);
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
    const formatTimestamp = (timestamp) => {
        try {
            return new Date(timestamp).toLocaleTimeString();
        }
        catch {
            return '--:--:--';
        }
    };
    const renderThinkingSection = () => {
        if (!contentState.thinking && !contentState.isThinkingActive) {
            return null;
        }
        return (_jsxs("div", { className: `${dense ? 'mb-2 p-2' : 'mb-3 p-3'} rounded border border-amber-500/20 bg-amber-500/5`, children: [_jsxs("div", { className: `${dense ? 'mb-1 text-[10px]' : 'mb-2 text-xs'} flex items-center gap-2 text-amber-200`, children: [_jsx(Brain, { className: "h-3 w-3" }), _jsx("span", { children: "\u601D\u8003\u94FE" }), contentState.isThinkingActive && (_jsx(Loader2, { className: "h-3 w-3 animate-spin" }))] }), _jsxs("div", { className: `${dense ? 'text-[10px]' : 'text-xs'} whitespace-pre-wrap break-words leading-relaxed text-amber-100/80`, children: [contentState.thinking, contentState.isThinkingActive && (_jsx("span", { className: "ml-1 inline-block h-3 w-0.5 bg-amber-400/70 align-middle" }))] })] }));
    };
    const renderAnswerSection = () => {
        if (!contentState.answer && !contentState.isAnswerActive) {
            return null;
        }
        return (_jsxs("div", { className: `${dense ? 'p-2' : 'p-3'} rounded border border-emerald-500/20 bg-emerald-500/5`, children: [_jsxs("div", { className: `${dense ? 'mb-1 text-[10px]' : 'mb-2 text-xs'} flex items-center gap-2 text-emerald-200`, children: [_jsx(MessageSquare, { className: "h-3 w-3" }), _jsx("span", { children: "\u4F5C\u7B54" }), contentState.isAnswerActive && (_jsx(Loader2, { className: "h-3 w-3 animate-spin" }))] }), _jsxs("div", { className: `${dense ? 'text-[10px]' : 'text-xs'} whitespace-pre-wrap break-words leading-relaxed text-emerald-100/80`, children: [contentState.answer, contentState.isAnswerActive && (_jsx("span", { className: "ml-1 inline-block h-3 w-0.5 bg-emerald-400/70 align-middle" }))] })] }));
    };
    const hasContent = contentState.thinking || contentState.answer || contentState.isThinkingActive || contentState.isAnswerActive;
    if (!hasContent && !isStreaming) {
        return (_jsxs("div", { className: `flex min-h-0 flex-col rounded-lg border border-white/10 bg-black/40 ${dense ? 'p-2' : 'p-3'} ${className || ''}`, children: [_jsxs("div", { className: "flex shrink-0 items-center justify-between text-[10px] text-text-dim", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "uppercase tracking-wide", children: "\u6D41\u5F0F\u6807\u7B7E\u89E3\u6790" }), _jsx("span", { className: "text-[9px]", children: "\u7B49\u5F85\u6570\u636E..." })] }), onClear && (_jsx("button", { type: "button", onClick: onClear, className: "flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[9px] hover:border-white/30", children: "Clear" }))] }), _jsx("div", { className: `${dense ? 'py-2' : 'py-4'} text-center text-[10px] text-text-dim`, children: "\u5F00\u542F\u6D41\u5F0F\u9762\u8BD5\u540E\u53EF\u67E5\u770Bthinking\u548Canswer\u7684\u5B9E\u65F6\u89E3\u6790" })] }));
    }
    return (_jsxs("div", { className: `flex min-h-0 flex-col rounded-lg border border-white/10 bg-black/40 ${dense ? 'p-2' : 'p-3'} ${className || ''}`, children: [_jsxs("div", { className: `${dense ? 'mb-1' : 'mb-2'} flex shrink-0 items-center justify-between text-[10px] text-text-dim`, children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "uppercase tracking-wide", children: "\u6D41\u5F0F\u6807\u7B7E\u89E3\u6790" }), _jsx("span", { className: "text-[9px]", children: isStreaming ? 'streaming...' : autoScroll ? 'auto-scroll' : 'paused' })] }), onClear && (_jsx("button", { type: "button", onClick: onClear, className: "flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[9px] hover:border-white/30", children: "Clear" }))] }), _jsxs("div", { ref: outputRef, className: `min-h-0 flex-1 overflow-y-auto pr-1 scrollbar-thin scrollbar-thumb-white/10 ${dense ? 'max-h-16 space-y-1' : 'max-h-96 space-y-2'}`, children: [renderThinkingSection(), renderAnswerSection()] })] }));
}
export default StreamingTags;

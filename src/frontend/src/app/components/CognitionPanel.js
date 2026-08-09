import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { motion, AnimatePresence } from 'framer-motion';
import { Brain, Lightbulb, Sparkles, Zap, Database, ArrowRight, Trash2, Gauge } from 'lucide-react';
import { useMemo, useState } from 'react';
import { apiFetch } from '@/api';
import { toast } from 'sonner';
export function CognitionPanel({ events, loading, anthroState }) {
    const [activeTab, setActiveTab] = useState('stream');
    const resolvedAnthroState = anthroState ?? null;
    // Filter for PromptContext events (Cognitive Recall)
    const recallEvents = useMemo(() => {
        return events
            .filter(e => e.type === 'prompt_context' || (e.kind === 'observation' && e.name === 'prompt_context'))
            .reverse() // Newest first
            .slice(0, 20);
    }, [events]);
    // Filter for Reflection events (Insights)
    const reflectionEvents = useMemo(() => {
        return events
            .filter(e => e.name === 'reflection' || (e.kind === 'observation' && e.name === 'reflection'))
            .reverse();
    }, [events]);
    const latestStats = useMemo(() => {
        const last = recallEvents[0];
        if (!last)
            return { step: 0, tokens: 0, phase: 'idle' };
        const ctx = (last.output || last.content || {});
        return {
            step: ctx?.step || 0,
            tokens: ctx?.token_usage_estimate || 0,
            phase: ctx?.phase || 'idle'
        };
    }, [recallEvents]);
    // Calculate Mood
    const mood = useMemo(() => {
        if (!resolvedAnthroState)
            return { key: 'unknown', label: '未判', color: 'text-gray-500' };
        if (resolvedAnthroState.recent_error_count === 0)
            return { key: 'confident', label: '从容', color: 'text-green-400' };
        if (resolvedAnthroState.recent_error_count < 3)
            return { key: 'cautious', label: '谨慎', color: 'text-yellow-400' };
        return { key: 'frustrated', label: '受阻', color: 'text-red-400' };
    }, [resolvedAnthroState]);
    return (_jsxs("div", { className: "h-full flex flex-col border-l border-white/5 bg-[#18181b] relative overflow-hidden", children: [_jsxs("div", { className: "px-5 py-4 border-b border-white/5 bg-white/[0.03] sticky top-0 z-20", children: [_jsx(ThoughtChainHUD, { phase: latestStats.phase }), _jsxs("div", { className: "flex items-center justify-between mb-4 mt-4", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "size-10 rounded-lg bg-white/5 flex items-center justify-center text-slate-300 border border-white/10", children: _jsx(Brain, { className: "size-5" }) }), _jsxs("div", { children: [_jsx("h2", { className: "text-sm font-heading font-bold text-gray-100 tracking-wide", children: "\u5FC3\u955C\u53F0" }), _jsxs("div", { className: "flex items-center gap-2 text-[10px] text-gray-500 font-mono mt-0.5", children: [_jsxs("span", { className: `flex items-center gap-1 font-bold ${mood.color}`, children: [_jsx(Gauge, { className: "size-3" }), mood.label] }), _jsx("span", { children: "\u2022" }), _jsxs("span", { children: ["\u6B65\u6B21 ", latestStats.step] })] })] })] }), _jsxs("div", { className: "text-right", children: [_jsx("div", { className: "text-[10px] text-gray-500", children: "\u8BB0\u5FC6\u6761\u76EE" }), _jsx("div", { className: "text-sm font-mono text-gray-300", children: resolvedAnthroState?.total_memories || 0 })] })] }), _jsxs("div", { className: "flex p-1 bg-black/20 rounded-lg border border-white/5", children: [_jsxs("button", { onClick: () => setActiveTab('stream'), className: `flex-1 flex items-center justify-center gap-2 py-1.5 text-xs rounded-md transition-all ${activeTab === 'stream' ? 'bg-white/10 text-white shadow-sm' : 'text-gray-500 hover:text-gray-300'}`, children: [_jsx(Zap, { className: "size-3" }), " \u8FFD\u5FC6\u6D41"] }), _jsxs("button", { onClick: () => setActiveTab('reflections'), className: `flex-1 flex items-center justify-center gap-2 py-1.5 text-xs rounded-md transition-all ${activeTab === 'reflections' ? 'bg-white/10 text-white shadow-sm' : 'text-gray-500 hover:text-gray-300'}`, children: [_jsx(Lightbulb, { className: "size-3" }), " \u7701\u601D\u5F55"] })] })] }), _jsx("div", { className: "flex-1 overflow-y-auto p-4 custom-scrollbar relative z-10", children: _jsxs(AnimatePresence, { mode: 'wait', children: [activeTab === 'stream' && (_jsx(motion.div, { initial: { opacity: 0, x: -10 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: 10 }, className: "space-y-4", children: recallEvents.length === 0 ? (_jsxs("div", { className: "flex flex-col items-center justify-center h-40 text-gray-600 gap-2", children: [_jsx(Brain, { className: "size-8 opacity-20" }), _jsx("span", { className: "text-xs", children: "\u6682\u65E0\u8BA4\u77E5\u4E8B\u4EF6" })] })) : (recallEvents.map((event, idx) => {
                                const rawContext = event.output ?? event.content;
                                const ctx = isPromptContextObj(rawContext) ? rawContext : null;
                                if (!ctx)
                                    return null;
                                return (_jsx(RecallCard, { context: ctx, timestamp: event.timestamp ?? '' }, String(event.id ?? idx)));
                            })) }, "stream")), activeTab === 'reflections' && (_jsx(motion.div, { initial: { opacity: 0, x: 10 }, animate: { opacity: 1, x: 0 }, exit: { opacity: 0, x: -10 }, className: "space-y-4", children: reflectionEvents.length === 0 ? (_jsxs("div", { className: "flex flex-col items-center justify-center h-40 text-gray-600 gap-2", children: [_jsx(Sparkles, { className: "size-8 opacity-20" }), _jsx("span", { className: "text-xs", children: "\u6682\u65E0\u7701\u601D\u6761\u76EE" })] })) : (reflectionEvents.map((event, idx) => {
                                const rawItems = Array.isArray(event.output) ? event.output : event.output ? [event.output] : [];
                                const items = rawItems.filter(isReflectionItem);
                                return items.map((item, subIdx) => (_jsx(ReflectionCard, { reflection: item }, `${String(event.id ?? idx)}-${subIdx}`)));
                            })) }, "reflections"))] }) })] }));
}
function ThoughtChainHUD({ phase }) {
    const steps = ['察势', '追忆', '省思', '施令'];
    // Map phase string to index (approximate)
    const currentIdx = useMemo(() => {
        const p = phase.toLowerCase();
        if (p.includes('observation') || p.includes('context'))
            return 0;
        if (p.includes('prompt') || p.includes('retrieval'))
            return 1;
        if (p.includes('reflection') || p.includes('plan'))
            return 2;
        if (p.includes('action') || p.includes('execute'))
            return 3;
        return -1;
    }, [phase]);
    return (_jsx("div", { className: "flex items-center justify-between bg-black/30 rounded-full px-4 py-2 border border-white/5 mb-2", children: steps.map((step, i) => {
            const isActive = i === currentIdx;
            const isPast = i < currentIdx;
            return (_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: `text-[10px] font-mono transition-colors duration-300 ${isActive ? 'text-white font-bold' : isPast ? 'text-blue-400' : 'text-gray-600'}`, children: step }), i < steps.length - 1 && (_jsx(ArrowRight, { className: `size-3 ${isPast ? 'text-blue-500/50' : 'text-gray-700'}` }))] }, step));
        }) }));
}
function RecallCard({ context, timestamp }) {
    if (!context || !context.run_id)
        return null;
    const handleDelete = async (id) => {
        toast.promise(apiFetch(`/anthropomorphic/memories/${id}`, { method: 'DELETE' }), {
            loading: '正在删除记忆...',
            success: '记忆已裁撤',
            error: '删除失败'
        });
    };
    return (_jsxs(motion.div, { initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, className: "group relative rounded-xl border border-white/5 bg-white/[0.02] p-3 hover:bg-white/[0.05] transition-colors overflow-hidden", children: [_jsx("div", { className: "absolute top-0 left-0 w-1 h-full bg-gradient-to-b from-blue-500/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" }), _jsxs("div", { className: "flex justify-between items-start mb-2", children: [_jsx("div", { className: "flex items-center gap-2", children: _jsx("div", { className: "px-1.5 py-0.5 rounded bg-blue-500/10 border border-blue-500/20 text-[10px] text-blue-300 font-mono", children: context.phase }) }), _jsx("span", { className: "text-[10px] text-gray-600 font-mono", children: timestamp })] }), _jsxs("div", { className: "space-y-2", children: [_jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-1.5 text-[10px] text-gray-500 mb-1", children: [_jsx(Database, { className: "size-3" }), _jsx("span", { children: "\u68C0\u7D22\u4E0A\u4E0B\u6587" })] }), context.retrieved_mem_ids && context.retrieved_mem_ids.length > 0 ? (_jsx("div", { className: "grid grid-cols-1 gap-1.5", children: context.retrieved_mem_ids.map((id, i) => {
                                    const score = context.retrieved_mem_scores ? context.retrieved_mem_scores[i] : 0;
                                    return (_jsxs("div", { className: "group/item flex items-center justify-between text-[10px] px-2 py-1 rounded-sm bg-white/5 text-gray-400 border border-white/5 hover:border-white/10 transition-colors", children: [_jsxs("div", { className: "flex items-center gap-2 overflow-hidden", children: [_jsxs("span", { className: "truncate max-w-[120px] font-mono opacity-70", children: [id.split('-')[0], "..."] }), score > 0 && (_jsx("div", { className: "h-1 w-12 bg-gray-700 rounded-full overflow-hidden", children: _jsx("div", { className: "h-full bg-blue-500", style: { width: `${score * 100}%` } }) }))] }), _jsx("button", { onClick: () => handleDelete(id), className: "opacity-0 group-hover/item:opacity-100 p-1 hover:bg-red-500/20 hover:text-red-400 rounded transition-all", title: "\u5220\u9664\u8BB0\u5FC6", children: _jsx(Trash2, { className: "size-3" }) })] }, id));
                                }) })) : (_jsx("span", { className: "text-[10px] text-gray-700 italic", children: "\u65E0\u5339\u914D\u8BB0\u5FC6" }))] }), _jsxs("div", { className: "flex items-center justify-between pt-2 border-t border-white/5", children: [_jsx("span", { className: "text-[10px] text-gray-600", children: "\u68C0\u7D22\u7B56\u7565" }), _jsx("span", { className: "text-[10px] text-blue-400/80 font-mono", children: context.strategy === 'hybrid' ? '混合' : (context.strategy || '混合') })] })] })] }));
}
function isPromptContextObj(value) {
    if (!value || typeof value !== 'object')
        return false;
    const ctx = value;
    return typeof ctx.run_id === 'string' && ctx.run_id.length > 0 && typeof ctx.step === 'number';
}
function isReflectionItem(value) {
    if (!value || typeof value !== 'object')
        return false;
    const item = value;
    return typeof item.text === 'string' && item.text.length > 0;
}
function ReflectionCard({ reflection }) {
    return (_jsx(motion.div, { initial: { scale: 0.95, opacity: 0 }, animate: { scale: 1, opacity: 1 }, whileHover: { scale: 1.02 }, className: "relative p-4 rounded-lg border border-white/10 bg-white/[0.02] transition-all hover:bg-white/[0.04]", children: _jsxs("div", { className: "flex items-start gap-3", children: [_jsx("div", { className: "mt-0.5 p-1.5 rounded-md bg-white/5 text-slate-400 border border-white/10", children: _jsx(Sparkles, { className: "size-4" }) }), _jsxs("div", { className: "flex-1", children: [_jsx("h4", { className: "text-xs font-bold text-gray-200 uppercase tracking-widest mb-1 opacity-80", children: "\u6D1E\u89C1" }), _jsx("p", { className: "text-sm text-gray-300 leading-relaxed font-medium", children: reflection.text }), _jsxs("div", { className: "mt-3 flex flex-wrap gap-2 text-[10px]", children: [reflection.scope && reflection.scope.map((s) => (_jsx("span", { className: "px-1.5 py-0.5 rounded bg-white/5 text-slate-400 border border-white/10", children: s }, s))), _jsxs("span", { className: "ml-auto flex items-center gap-1 text-gray-500", children: ["\u7F6E\u4FE1\u5EA6: ", Math.round((reflection.confidence || 0) * 100), "%"] })] })] })] }) }));
}


import { motion, AnimatePresence } from 'framer-motion';
import { Brain, Lightbulb, Sparkles, Zap, Database, ArrowRight, Activity, Trash2, Gauge } from 'lucide-react';
import { useMemo, useState } from 'react';
import { apiFetch } from '@/api';
import { toast } from 'sonner';

// Types derived from backend schema
interface PromptContextObj {
    run_id: string;
    phase: string;
    step: number;
    persona_id: string;
    retrieved_mem_ids: string[];
    retrieved_mem_scores?: number[];
    retrieved_ref_ids: string[];
    token_usage_estimate: number;
    strategy?: string;
}

interface CognitionEvent {
    id?: string | number;
    type?: string;
    kind?: string;
    name?: string;
    output?: unknown;
    content?: unknown;
    timestamp?: string;
}

interface ReflectionItem {
    text?: string;
    scope?: string[];
    confidence?: number;
}

interface CognitionPanelProps {
    events: CognitionEvent[]; // Stream of raw events to filter
    loading?: boolean;
    anthroState?: AnthroState | null;
}

interface AnthroState {
    last_reflection_step: number;
    recent_error_count: number;
    total_memories: number;
    total_reflections: number;
}

export function CognitionPanel({ events, loading, anthroState }: CognitionPanelProps) {
    const [activeTab, setActiveTab] = useState<'stream' | 'reflections'>('stream');
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
        if (!last) return { step: 0, tokens: 0, phase: 'idle' };
        const ctx = (last.output || last.content || {}) as PromptContextObj;
        return {
            step: ctx?.step || 0,
            tokens: ctx?.token_usage_estimate || 0,
            phase: ctx?.phase || 'idle'
        };
    }, [recallEvents]);

    // Calculate Mood
    const mood = useMemo(() => {
        if (!resolvedAnthroState) return { key: 'unknown', label: '未判', color: 'text-gray-500' };
        if (resolvedAnthroState.recent_error_count === 0) return { key: 'confident', label: '从容', color: 'text-green-400' };
        if (resolvedAnthroState.recent_error_count < 3) return { key: 'cautious', label: '谨慎', color: 'text-yellow-400' };
        return { key: 'frustrated', label: '受阻', color: 'text-red-400' };
    }, [resolvedAnthroState]);

    return (
        <div className="h-full flex flex-col glass border-l border-white/5 bg-gradient-to-b from-[#1e1e1e] to-[#0f0f0f] relative overflow-hidden">

            {/* Background Pulse for Mood */}
            <div className={`absolute top-0 right-0 w-full h-20 opacity-10 blur-3xl pointer-events-none transition-colors duration-1000
          ${mood.key === 'confident' ? 'bg-green-500' : mood.key === 'cautious' ? 'bg-yellow-500' : 'bg-red-500'}`}
            />

            {/* Header */}
            <div className="px-5 py-4 border-b border-white/5 bg-white/5 backdrop-blur-md sticky top-0 z-20">

                {/* Thought Chain HUD */}
                <ThoughtChainHUD phase={latestStats.phase} />

                <div className="flex items-center justify-between mb-4 mt-4">
                    <div className="flex items-center gap-3">
                        <div className="relative">
                            <div className="absolute inset-0 bg-purple-500 blur-lg opacity-20 animate-pulse"></div>
                            <div className="relative size-10 rounded-xl bg-gradient-to-br from-purple-500/10 to-blue-500/10 flex items-center justify-center text-purple-300 border border-white/10 shadow-[0_0_15px_rgba(168,85,247,0.15)]">
                                <Brain className="size-5" />
                            </div>
                        </div>
                        <div>
                            <h2 className="text-sm font-heading font-bold text-gray-100 tracking-wide">心镜台</h2>
                            <div className="flex items-center gap-2 text-[10px] text-gray-500 font-mono mt-0.5">
                                <span className={`flex items-center gap-1 font-bold ${mood.color}`}>
                                    <Gauge className="size-3" />
                                    {mood.label}
                                </span>
                                <span>•</span>
                                <span>步次 {latestStats.step}</span>
                            </div>
                        </div>
                    </div>
                    <div className="text-right">
                        <div className="text-[10px] text-gray-500">记忆条目</div>
                        <div className="text-sm font-mono text-gray-300">{resolvedAnthroState?.total_memories || 0}</div>
                    </div>
                </div>

                {/* Tabs */}
                <div className="flex p-1 bg-black/20 rounded-lg border border-white/5">
                    <button
                        onClick={() => setActiveTab('stream')}
                        className={`flex-1 flex items-center justify-center gap-2 py-1.5 text-xs rounded-md transition-all ${activeTab === 'stream' ? 'bg-white/10 text-white shadow-sm' : 'text-gray-500 hover:text-gray-300'}`}
                    >
                        <Zap className="size-3" /> 追忆流
                    </button>
                    <button
                        onClick={() => setActiveTab('reflections')}
                        className={`flex-1 flex items-center justify-center gap-2 py-1.5 text-xs rounded-md transition-all ${activeTab === 'reflections' ? 'bg-white/10 text-white shadow-sm' : 'text-gray-500 hover:text-gray-300'}`}
                    >
                        <Lightbulb className="size-3" /> 省思录
                    </button>
                </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-4 custom-scrollbar relative z-10">
                <AnimatePresence mode='wait'>
                    {activeTab === 'stream' && (
                        <motion.div
                            key="stream"
                            initial={{ opacity: 0, x: -10 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: 10 }}
                            className="space-y-4"
                        >
                            {recallEvents.length === 0 ? (
                                <div className="flex flex-col items-center justify-center h-40 text-gray-600 gap-2">
                                    <Brain className="size-8 opacity-20" />
                                    <span className="text-xs">暂无认知事件</span>
                                </div>
                            ) : (
                                recallEvents.map((event, idx) => {
                                    const rawContext = event.output ?? event.content;
                                    const ctx = isPromptContextObj(rawContext) ? rawContext : null;
                                    if (!ctx) return null;
                                    return (
                                        <RecallCard key={String(event.id ?? idx)} context={ctx} timestamp={event.timestamp ?? ''} />
                                    );
                                })
                            )}
                        </motion.div>
                    )}

                    {activeTab === 'reflections' && (
                        <motion.div
                            key="reflections"
                            initial={{ opacity: 0, x: 10 }}
                            animate={{ opacity: 1, x: 0 }}
                            exit={{ opacity: 0, x: -10 }}
                            className="space-y-4"
                        >
                            {reflectionEvents.length === 0 ? (
                                <div className="flex flex-col items-center justify-center h-40 text-gray-600 gap-2">
                                    <Sparkles className="size-8 opacity-20" />
                                    <span className="text-xs">暂无省思条目</span>
                                </div>
                            ) : (
                                reflectionEvents.map((event, idx) => {
                                    const rawItems = Array.isArray(event.output) ? event.output : event.output ? [event.output] : [];
                                    const items = rawItems.filter(isReflectionItem);
                                    return items.map((item, subIdx: number) => (
                                        <ReflectionCard key={`${String(event.id ?? idx)}-${subIdx}`} reflection={item} />
                                    ));
                                })
                            )}
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}

function ThoughtChainHUD({ phase }: { phase: string }) {
    const steps = ['察势', '追忆', '省思', '施令'];
    // Map phase string to index (approximate)
    const currentIdx = useMemo(() => {
        const p = phase.toLowerCase();
        if (p.includes('observation') || p.includes('context')) return 0;
        if (p.includes('prompt') || p.includes('retrieval')) return 1;
        if (p.includes('reflection') || p.includes('plan')) return 2;
        if (p.includes('action') || p.includes('execute')) return 3;
        return -1;
    }, [phase]);

    return (
        <div className="flex items-center justify-between bg-black/40 rounded-full px-4 py-2 border border-white/5 mb-2 shadow-inner">
            {steps.map((step, i) => {
                const isActive = i === currentIdx;
                const isPast = i < currentIdx;

                return (
                    <div key={step} className="flex items-center gap-2">
                        <div className={`text-[10px] font-mono transition-colors duration-300 ${isActive ? 'text-white font-bold' : isPast ? 'text-blue-400' : 'text-gray-600'}`}>
                            {step}
                        </div>
                        {i < steps.length - 1 && (
                            <ArrowRight className={`size-3 ${isPast ? 'text-blue-500/50' : 'text-gray-700'}`} />
                        )}
                    </div>
                )
            })}
        </div>
    )
}

function RecallCard({ context, timestamp }: { context: PromptContextObj; timestamp: string }) {
    if (!context || !context.run_id) return null;

    const handleDelete = async (id: string) => {
        toast.promise(apiFetch(`/anthropomorphic/memories/${id}`, { method: 'DELETE' }), {
            loading: '正在删除记忆...',
            success: '记忆已裁撤',
            error: '删除失败'
        });
    }

    return (
        <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="group relative rounded-xl border border-white/5 bg-white/[0.02] p-3 hover:bg-white/[0.05] transition-colors overflow-hidden"
        >
            <div className="absolute top-0 left-0 w-1 h-full bg-gradient-to-b from-blue-500/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>

            <div className="flex justify-between items-start mb-2">
                <div className="flex items-center gap-2">
                    <div className="px-1.5 py-0.5 rounded bg-blue-500/10 border border-blue-500/20 text-[10px] text-blue-300 font-mono">
                        {context.phase}
                    </div>
                </div>
                <span className="text-[10px] text-gray-600 font-mono">{timestamp}</span>
            </div>

            <div className="space-y-2">
                {/* Memories */}
                <div>
                    <div className="flex items-center gap-1.5 text-[10px] text-gray-500 mb-1">
                        <Database className="size-3" />
                        <span>检索上下文</span>
                    </div>
                    {context.retrieved_mem_ids && context.retrieved_mem_ids.length > 0 ? (
                        <div className="grid grid-cols-1 gap-1.5">
                            {context.retrieved_mem_ids.map((id, i) => {
                                const score = context.retrieved_mem_scores ? context.retrieved_mem_scores[i] : 0;
                                return (
                                    <div key={id} className="group/item flex items-center justify-between text-[10px] px-2 py-1 rounded-sm bg-white/5 text-gray-400 border border-white/5 hover:border-white/10 transition-colors">
                                        <div className="flex items-center gap-2 overflow-hidden">
                                            <span className="truncate max-w-[120px] font-mono opacity-70">{id.split('-')[0]}...</span>
                                            {score > 0 && (
                                                <div className="h-1 w-12 bg-gray-700 rounded-full overflow-hidden">
                                                    <div className="h-full bg-blue-500" style={{ width: `${score * 100}%` }} />
                                                </div>
                                            )}
                                        </div>

                                        <button
                                            onClick={() => handleDelete(id)}
                                            className="opacity-0 group-hover/item:opacity-100 p-1 hover:bg-red-500/20 hover:text-red-400 rounded transition-all"
                                            title="删除记忆"
                                        >
                                            <Trash2 className="size-3" />
                                        </button>
                                    </div>
                                )
                            })}
                        </div>
                    ) : (
                        <span className="text-[10px] text-gray-700 italic">无匹配记忆</span>
                    )}
                </div>

                {/* Strategy */}
                <div className="flex items-center justify-between pt-2 border-t border-white/5">
                    <span className="text-[10px] text-gray-600">检索策略</span>
                    <span className="text-[10px] text-blue-400/80 font-mono">{context.strategy === 'hybrid' ? '混合' : (context.strategy || '混合')}</span>
                </div>
            </div>
        </motion.div>
    );
}

function isPromptContextObj(value: unknown): value is PromptContextObj {
    if (!value || typeof value !== 'object') return false;
    const ctx = value as PromptContextObj;
    return typeof ctx.run_id === 'string' && ctx.run_id.length > 0 && typeof ctx.step === 'number';
}

function isReflectionItem(value: unknown): value is ReflectionItem {
    if (!value || typeof value !== 'object') return false;
    const item = value as ReflectionItem;
    return typeof item.text === 'string' && item.text.length > 0;
}

function ReflectionCard({ reflection }: { reflection: ReflectionItem }) {
    return (
        <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            whileHover={{ scale: 1.02 }}
            className="relative p-4 rounded-xl border border-white/10 bg-gradient-to-br from-purple-500/5 to-transparent backdrop-blur-sm shadow-sm transition-all hover:bg-white/5"
        >
            <div className="absolute -right-4 -top-4 size-20 bg-purple-500/10 blur-xl rounded-full pointer-events-none"></div>

            <div className="flex items-start gap-3 relative z-10">
                <div className="mt-0.5 p-1.5 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">
                    <Sparkles className="size-4" />
                </div>
                <div className="flex-1">
                    <h4 className="text-xs font-bold text-gray-200 uppercase tracking-widest mb-1 opacity-80">洞见</h4>
                    <p className="text-sm text-gray-300 leading-relaxed font-medium">
                        {reflection.text}
                    </p>

                    <div className="mt-3 flex flex-wrap gap-2 text-[10px]">
                        {reflection.scope && reflection.scope.map((s: string) => (
                            <span key={s} className="px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-300/70 border border-purple-500/10">
                                {s}
                            </span>
                        ))}
                        <span className="ml-auto flex items-center gap-1 text-gray-500">
                            置信度: {Math.round((reflection.confidence || 0) * 100)}%
                        </span>
                    </div>
                </div>
            </div>
        </motion.div>
    )
}

"use client";
import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMemo } from 'react';
import { CheckCircle2, Circle, Loader2, Clock, AlertCircle } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
const PHASES = [
    { id: 'agents', label: 'AGENTS', description: 'Docs Setup' },
    { id: 'planning', label: 'Planning', description: 'PM Office Planning' },
    { id: 'chief_engineer', label: 'CE', description: 'Director Design' },
    { id: 'director', label: 'Director', description: 'Engineering Execution' },
    { id: 'qa', label: 'QA', description: 'QA Review' },
];
export function PhaseIndicator({ currentPhase, phaseStatuses, qualityScore, retryAttempt, maxRetries, className, }) {
    const currentIndex = useMemo(() => {
        return PHASES.findIndex((p) => p.id === currentPhase);
    }, [currentPhase]);
    const getPhaseStatus = (phaseId, index) => {
        if (phaseStatuses?.[phaseId]) {
            return phaseStatuses[phaseId].status;
        }
        if (index < currentIndex)
            return 'completed';
        if (index === currentIndex)
            return 'running';
        return 'pending';
    };
    return (_jsxs("div", { "data-testid": "phase-indicator", className: cn("soft-panel-subtle rounded-xl p-4", className), children: [_jsxs("div", { className: "relative", children: [_jsx("div", { className: "absolute left-0 right-0 top-5 flex items-center", children: _jsx("div", { className: "h-0.5 flex-1 bg-white/10" }) }), _jsx("div", { className: "relative flex justify-between", children: PHASES.map((phase, index) => {
                            const status = getPhaseStatus(phase.id, index);
                            const isLast = index === PHASES.length - 1;
                            return (_jsxs("div", { "data-testid": `phase-indicator-${phase.id}`, "data-phase-status": status, className: "flex flex-col items-center", children: [_jsx("div", { className: cn("relative z-10 flex h-10 w-10 items-center justify-center rounded-full border-2 transition-all duration-300", status === 'completed' && "border-emerald-500 bg-emerald-500/20 text-emerald-400", status === 'running' && "border-amber-500 bg-amber-500/20 text-amber-400", status === 'failed' && "border-red-500 bg-red-500/20 text-red-400", status === 'blocked' && "border-orange-500 bg-orange-500/20 text-orange-400", status === 'pending' && "border-white/20 bg-white/5 text-white/30"), children: status === 'completed' ? (_jsx(CheckCircle2, { className: "h-5 w-5" })) : status === 'running' ? (_jsx(Loader2, { className: "h-5 w-5 animate-spin" })) : status === 'failed' ? (_jsx(AlertCircle, { className: "h-5 w-5" })) : (_jsx(Circle, { className: "h-5 w-5" })) }), _jsxs("div", { className: "mt-2 text-center", children: [_jsx("div", { className: cn("text-xs font-bold transition-colors", status === 'completed' && "text-emerald-400", status === 'running' && "text-amber-400", status === 'failed' && "text-red-400", status === 'pending' && "text-white/30"), children: phase.label }), _jsx("div", { className: "text-[10px] text-white/40", children: phase.description })] }), !isLast && (_jsx("div", { className: cn("absolute top-5 h-0.5 transition-all duration-500", index < currentIndex ? "bg-emerald-500/50" : "bg-white/10"), style: {
                                            left: `${(index / (PHASES.length - 1)) * 100 + 5}%`,
                                            right: `${100 - ((index + 1) / (PHASES.length - 1)) * 100 + 5}%`,
                                        } }))] }, phase.id));
                        }) })] }), currentPhase && currentPhase !== 'idle' && currentPhase !== 'complete' && (_jsx("div", { className: "mt-4 rounded-lg border border-white/5 bg-white/[0.02] p-3", children: _jsxs("div", { className: "flex items-center gap-3", children: [_jsx(Clock, { className: "h-4 w-4 text-amber-400" }), _jsxs("div", { className: "flex-1", children: [_jsxs("div", { className: "text-xs font-medium text-white/70", children: ["\u5F53\u524D\u9636\u6BB5: ", PHASES.find((p) => p.id === currentPhase)?.label || currentPhase] }), _jsx("div", { className: "text-[10px] text-white/40", children: phaseStatuses?.[currentPhase]?.detail || '正在执行中...' })] }), currentPhase === 'planning' && qualityScore !== undefined && (_jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("div", { className: cn("rounded px-2 py-1 text-xs font-bold", qualityScore >= 80
                                        ? "bg-emerald-500/20 text-emerald-400"
                                        : qualityScore >= 60
                                            ? "bg-amber-500/20 text-amber-400"
                                            : "bg-red-500/20 text-red-400"), children: [qualityScore, "\u5206"] }), retryAttempt !== undefined && maxRetries !== undefined && (_jsxs("div", { className: "text-[10px] text-white/40", children: [retryAttempt, "/", maxRetries] }))] }))] }) }))] }));
}

"use client";

import { useMemo } from 'react';
import { CheckCircle2, Circle, Loader2, Clock, AlertCircle } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';

export type Phase =
  | 'agents'
  | 'planning'
  | 'chief_engineer'
  | 'director'
  | 'qa'
  | 'complete'
  | 'failed'
  | 'idle';

export interface PhaseStatus {
  phase: Phase;
  status: 'completed' | 'running' | 'pending' | 'failed' | 'blocked';
  detail?: string;
  meta?: Record<string, unknown>;
}

interface PhaseIndicatorProps {
  currentPhase: Phase;
  phaseStatuses?: Record<string, PhaseStatus>;
  qualityScore?: number;
  retryAttempt?: number;
  maxRetries?: number;
  className?: string;
}

const PHASES: { id: Phase; label: string; description: string }[] = [
  { id: 'agents', label: 'AGENTS', description: 'Docs Setup' },
  { id: 'planning', label: 'Planning', description: 'PM Office Planning' },
  { id: 'chief_engineer', label: 'CE', description: 'Director Design' },
  { id: 'director', label: 'Director', description: 'Engineering Execution' },
  { id: 'qa', label: 'QA', description: 'QA Review' },
];

export function PhaseIndicator({
  currentPhase,
  phaseStatuses,
  qualityScore,
  retryAttempt,
  maxRetries,
  className,
}: PhaseIndicatorProps) {
  const currentIndex = useMemo(() => {
    return PHASES.findIndex((p) => p.id === currentPhase);
  }, [currentPhase]);

  const getPhaseStatus = (phaseId: Phase, index: number): PhaseStatus['status'] => {
    if (phaseStatuses?.[phaseId]) {
      return phaseStatuses[phaseId].status;
    }
    if (index < currentIndex) return 'completed';
    if (index === currentIndex) return 'running';
    return 'pending';
  };

  return (
    <div
      data-testid="phase-indicator"
      className={cn("rounded-xl border border-white/10 bg-white/5 p-4", className)}
    >
      {/* 阶段流程 */}
      <div className="relative">
        {/* 连接线 */}
        <div className="absolute left-0 right-0 top-5 flex items-center">
          <div className="h-0.5 flex-1 bg-white/10" />
        </div>

        {/* 阶段节点 */}
        <div className="relative flex justify-between">
          {PHASES.map((phase, index) => {
            const status = getPhaseStatus(phase.id, index);
            const isLast = index === PHASES.length - 1;

            return (
              <div
                key={phase.id}
                data-testid={`phase-indicator-${phase.id}`}
                data-phase-status={status}
                className="flex flex-col items-center"
              >
                {/* 节点图标 */}
                <div
                  className={cn(
                    "relative z-10 flex h-10 w-10 items-center justify-center rounded-full border-2 transition-all duration-300",
                    status === 'completed' && "border-emerald-500 bg-emerald-500/20 text-emerald-400",
                    status === 'running' && "border-amber-500 bg-amber-500/20 text-amber-400 shadow-[0_0_12px_rgba(245,158,11,0.3)]",
                    status === 'failed' && "border-red-500 bg-red-500/20 text-red-400",
                    status === 'blocked' && "border-orange-500 bg-orange-500/20 text-orange-400",
                    status === 'pending' && "border-white/20 bg-white/5 text-white/30",
                  )}
                >
                  {status === 'completed' ? (
                    <CheckCircle2 className="h-5 w-5" />
                  ) : status === 'running' ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : status === 'failed' ? (
                    <AlertCircle className="h-5 w-5" />
                  ) : (
                    <Circle className="h-5 w-5" />
                  )}

                  {/* 运行中的脉冲效果 */}
                  {status === 'running' && (
                    <span className="absolute inset-0 rounded-full animate-ping bg-amber-500/20" />
                  )}
                </div>

                {/* 标签 */}
                <div className="mt-2 text-center">
                  <div
                    className={cn(
                      "text-xs font-bold transition-colors",
                      status === 'completed' && "text-emerald-400",
                      status === 'running' && "text-amber-400",
                      status === 'failed' && "text-red-400",
                      status === 'pending' && "text-white/30",
                    )}
                  >
                    {phase.label}
                  </div>
                  <div className="text-[10px] text-white/40">{phase.description}</div>
                </div>

                {/* 连接线进度 */}
                {!isLast && (
                  <div
                    className={cn(
                      "absolute top-5 h-0.5 transition-all duration-500",
                      index < currentIndex ? "bg-emerald-500/50" : "bg-white/10",
                    )}
                    style={{
                      left: `${(index / (PHASES.length - 1)) * 100 + 5}%`,
                      right: `${100 - ((index + 1) / (PHASES.length - 1)) * 100 + 5}%`,
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 当前阶段详情 */}
      {currentPhase && currentPhase !== 'idle' && currentPhase !== 'complete' && (
        <div className="mt-4 rounded-lg border border-white/5 bg-white/[0.02] p-3">
          <div className="flex items-center gap-3">
            <Clock className="h-4 w-4 text-amber-400" />
            <div className="flex-1">
              <div className="text-xs font-medium text-white/70">
                当前阶段: {PHASES.find((p) => p.id === currentPhase)?.label || currentPhase}
              </div>
              <div className="text-[10px] text-white/40">
                {phaseStatuses?.[currentPhase]?.detail || '正在执行中...'}
              </div>
            </div>

            {/* 质量门控信息 */}
            {currentPhase === 'planning' && qualityScore !== undefined && (
              <div className="flex items-center gap-2">
                <div
                  className={cn(
                    "rounded px-2 py-1 text-xs font-bold",
                    qualityScore >= 80
                      ? "bg-emerald-500/20 text-emerald-400"
                      : qualityScore >= 60
                        ? "bg-amber-500/20 text-amber-400"
                        : "bg-red-500/20 text-red-400",
                  )}
                >
                  {qualityScore}分
                </div>
                {retryAttempt !== undefined && maxRetries !== undefined && (
                  <div className="text-[10px] text-white/40">
                    {retryAttempt}/{maxRetries}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

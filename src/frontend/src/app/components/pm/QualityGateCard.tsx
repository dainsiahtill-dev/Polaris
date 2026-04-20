"use client";

import { useMemo } from 'react';
import { CheckCircle2, XCircle, AlertTriangle, AlertCircle, RotateCcw, Target } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';

export interface QualityIssue {
  type: 'critical' | 'warning' | 'info';
  message: string;
  suggestion?: string;
}

export interface QualityGateData {
  score: number;
  passed: boolean;
  attempt: number;
  maxAttempts: number;
  summary?: string;
  issues: QualityIssue[];
  metrics?: Record<string, number>;
}

interface QualityGateCardProps {
  data?: QualityGateData | null;
  className?: string;
}

export function QualityGateCard({ data, className }: QualityGateCardProps) {
  const scoreColor = useMemo(() => {
    if (!data) return 'text-white/30';
    if (data.score >= 80) return 'text-emerald-400';
    if (data.score >= 60) return 'text-amber-400';
    return 'text-red-400';
  }, [data]);

  const scoreBg = useMemo(() => {
    if (!data) return 'bg-white/5';
    if (data.score >= 80) return 'bg-emerald-500/10 border-emerald-500/30';
    if (data.score >= 60) return 'bg-amber-500/10 border-amber-500/30';
    return 'bg-red-500/10 border-red-500/30';
  }, [data]);

  if (!data) return null;

  const criticalCount = data.issues.filter((i) => i.type === 'critical').length;
  const warningCount = data.issues.filter((i) => i.type === 'warning').length;

  return (
    <div
      className={cn(
        "rounded-xl border p-4 transition-all",
        scoreBg,
        className,
      )}
    >
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Target className={cn("h-4 w-4", scoreColor)} />
          <span className="text-xs font-bold text-white/70">质量门控检查</span>
        </div>

        <div className="flex items-center gap-2">
          {/* 分数 */}
          <div className={cn("text-lg font-bold", scoreColor)}>
            {data.score}
            <span className="text-xs font-normal text-white/30">/100</span>
          </div>

          {/* 通过状态 */}
          {data.passed ? (
            <CheckCircle2 className="h-5 w-5 text-emerald-400" />
          ) : (
            <XCircle className="h-5 w-5 text-red-400" />
          )}
        </div>
      </div>

      {/* 重试信息 */}
      {data.maxAttempts > 1 && (
        <div className="mt-2 flex items-center gap-2 text-[10px] text-white/40">
          <RotateCcw className="h-3 w-3" />
          <span>
            重试 {data.attempt}/{data.maxAttempts}
            {data.attempt >= data.maxAttempts && !data.passed && (
              <span className="ml-1 text-red-400">(已达最大重试)</span>
            )}
          </span>
        </div>
      )}

      {/* 摘要 */}
      {data.summary && (
        <div className="mt-2 text-[11px] text-white/50">{data.summary}</div>
      )}

      {/* 统计 */}
      <div className="mt-3 flex gap-3">
        {criticalCount > 0 && (
          <div className="flex items-center gap-1 text-[10px] text-red-400">
            <AlertCircle className="h-3 w-3" />
            {criticalCount} 关键问题
          </div>
        )}
        {warningCount > 0 && (
          <div className="flex items-center gap-1 text-[10px] text-amber-400">
            <AlertTriangle className="h-3 w-3" />
            {warningCount} 警告
          </div>
        )}
      </div>

      {/* 问题列表 */}
      {data.issues.length > 0 && (
        <div className="mt-3 space-y-2 max-h-32 overflow-y-auto">
          {data.issues.map((issue, idx) => (
            <div
              key={idx}
              className={cn(
                "rounded-lg border p-2 text-[11px]",
                issue.type === 'critical'
                  ? "border-red-500/20 bg-red-500/5"
                  : issue.type === 'warning'
                    ? "border-amber-500/20 bg-amber-500/5"
                    : "border-white/5 bg-white/[0.02]",
              )}
            >
              <div className="flex items-start gap-2">
                {issue.type === 'critical' ? (
                  <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" />
                ) : issue.type === 'warning' ? (
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
                ) : (
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-white/40" />
                )}
                <div className="flex-1">
                  <div className="text-white/70">{issue.message}</div>
                  {issue.suggestion && (
                    <div className="mt-1 text-white/40">💡 {issue.suggestion}</div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 指标 */}
      {data.metrics && Object.keys(data.metrics).length > 0 && (
        <div className="mt-3 grid grid-cols-3 gap-2">
          {Object.entries(data.metrics).map(([key, value]) => (
            <div key={key} className="rounded bg-white/5 p-2 text-center">
              <div className="text-[10px] text-white/40">{key}</div>
              <div className="text-xs font-mono text-white/70">{value}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

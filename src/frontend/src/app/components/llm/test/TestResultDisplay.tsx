import { CheckCircle2, AlertTriangle, Activity, Brain, Clock } from 'lucide-react';
import type { TestResult } from './types';

interface TestResultDisplayProps {
  result: TestResult;
}

const formatTokens = (count?: number) => {
  if (typeof count !== 'number' || Number.isNaN(count)) return '—';
  return count.toLocaleString();
};

export function TestResultDisplay({ result }: TestResultDisplayProps) {
  if (!result) return null;
  const ready = result.ready;
  const grade = result.grade || (ready ? 'PASS' : 'FAIL');

  return (
    <div className="rounded-xl border border-white/10 bg-black/30 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-text-main">
          {ready ? (
            <CheckCircle2 className="size-4 text-emerald-400" />
          ) : (
            <AlertTriangle className="size-4 text-red-400" />
          )}
          <span className="font-semibold">测试结果</span>
        </div>
        <span
          className={`text-[10px] uppercase tracking-wider px-2 py-1 rounded border ${
            ready
              ? 'bg-emerald-500/20 text-emerald-200 border-emerald-500/40'
              : 'bg-red-500/20 text-red-200 border-red-500/40'
          }`}
        >
          {grade}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3 text-xs">
        <div className="rounded-lg border border-white/10 bg-black/20 p-2">
          <div className="flex items-center gap-1 text-[10px] text-text-dim">
            <Clock className="size-3" />
            延迟
          </div>
          <div className="text-sm text-text-main mt-1">
            {typeof result.latencyMs === 'number' ? `${Math.round(result.latencyMs)} ms` : '—'}
          </div>
        </div>
        <div className="rounded-lg border border-white/10 bg-black/20 p-2">
          <div className="flex items-center gap-1 text-[10px] text-text-dim">
            <Activity className="size-3" />
            Tokens
          </div>
          <div className="text-sm text-text-main mt-1">
            {formatTokens(result.usage?.totalTokens)}
            {result.usage?.estimated ? <span className="text-[9px] text-text-dim ml-1">(估算)</span> : null}
          </div>
        </div>
        <div className="rounded-lg border border-white/10 bg-black/20 p-2">
          <div className="flex items-center gap-1 text-[10px] text-text-dim">
            <Brain className="size-3" />
            思考能力
          </div>
          <div className="text-sm text-text-main mt-1">
            {result.thinking?.supportsThinking === undefined
              ? '—'
              : result.thinking.supportsThinking
                ? '支持'
                : '不支持'}
            {typeof result.thinking?.confidence === 'number' ? (
              <span className="text-[9px] text-text-dim ml-1">{Math.round(result.thinking.confidence * 100)}%</span>
            ) : null}
          </div>
        </div>
      </div>

      {result.suites && result.suites.length > 0 ? (
        <div className="space-y-2">
          <div className="text-[10px] text-text-dim">套件结果</div>
          <div className="grid grid-cols-2 gap-2">
            {result.suites.map((suite) => (
              <div
                key={suite.name}
                className={`rounded border px-2 py-1 text-[10px] flex items-center justify-between ${
                  suite.ok
                    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
                    : 'border-red-500/30 bg-red-500/10 text-red-200'
                }`}
              >
                <span className="capitalize">{suite.name}</span>
                <span>{suite.ok ? 'PASS' : 'FAIL'}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {result.report ? (
        <details className="text-[10px] text-text-dim">
          <summary className="cursor-pointer">查看原始报告</summary>
          <pre className="mt-2 whitespace-pre-wrap break-words text-[10px] text-text-muted font-mono">
            {JSON.stringify(result.report, null, 2)}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

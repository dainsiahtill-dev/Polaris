interface InterviewReportProps {
  report?: {
    status?: string;
    final_score?: number;
    thinking?: {
      supports_thinking?: boolean;
      confidence?: number;
      format?: string;
    };
    details?: {
      recommendation?: string;
      reason?: string;
      threshold?: number;
    };
  } | null;
  roleLabel: string;
}

const STATUS_STYLES: Record<string, string> = {
  PASSED: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
  FAILED: 'bg-amber-500/20 text-amber-200 border-amber-500/30',
  REJECTED: 'bg-red-500/20 text-red-200 border-red-500/30'
};

export function InterviewReport({ report, roleLabel }: InterviewReportProps) {
  if (!report) {
    return (
      <div className="rounded-lg border border-white/10 bg-black/20 p-4 text-xs text-text-dim">
        暂无面试报告。
      </div>
    );
  }

  const status = report.status || 'UNKNOWN';
  const badge = STATUS_STYLES[status] || 'bg-white/10 text-text-main border-white/20';
  const score =
    typeof report.final_score === 'number'
      ? Math.round(Math.min(1, Math.max(0, report.final_score)) * 100)
      : null;
  const thinkingSupport = report.thinking?.supports_thinking;
  const thinkingConfidence = report.thinking?.confidence;

  return (
    <div className="rounded-lg border border-white/10 bg-black/30 p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${badge}`}>
            {status}
          </span>
          <span className="text-xs text-text-main font-semibold">{roleLabel} 面试报告</span>
        </div>
        {score !== null ? (
          <div className="text-xs text-text-dim">
            综合评分： <span className="text-text-main font-semibold">{score}</span>
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
        <div className="rounded-md border border-white/10 bg-black/20 p-3">
          <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">思考能力</div>
          <div className="text-text-main font-semibold">
            {thinkingSupport ? '支持' : '未检测到'}
          </div>
          <div className="text-text-dim">
            置信度：{thinkingConfidence !== undefined ? Math.round(thinkingConfidence * 100) : '无'}%
          </div>
        </div>
        <div className="rounded-md border border-white/10 bg-black/20 p-3">
          <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">建议</div>
          <div className="text-text-main font-semibold">
            {report.details?.recommendation || '暂无建议。'}
          </div>
          {report.details?.reason ? (
            <div className="text-text-dim mt-1">{report.details.reason}</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

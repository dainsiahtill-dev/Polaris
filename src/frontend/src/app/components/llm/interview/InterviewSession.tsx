import { ArrowLeft, Loader2, MessageSquare } from 'lucide-react';
import { InterviewReport } from './InterviewReport';
import { ThinkingDisplay } from './ThinkingDisplay';

interface InterviewCase {
  id?: string;
  question?: string;
  output?: string;
  thinking?: string;
  answer?: string;
  score?: number;
  criteria_hits?: string[];
  missing_criteria?: string[];
  notes?: string;
}

interface InterviewSuiteReport {
  status?: string;
  final_score?: number;
  thinking?: {
    supports_thinking?: boolean;
    confidence?: number;
    format?: string;
    thinking_text?: string;
  };
  cases?: InterviewCase[];
  details?: {
    recommendation?: string;
    reason?: string;
    threshold?: number;
  };
}

interface InterviewSessionProps {
  roleLabel: string;
  roleId: string;
  report?: InterviewSuiteReport | null;
  running?: boolean;
  error?: string | null;
  onBack?: () => void;
}

export function InterviewSession({
  roleLabel,
  roleId,
  report,
  running,
  error,
  onBack
}: InterviewSessionProps) {
  const cases = Array.isArray(report?.cases) ? report?.cases : [];
  const thinkingMeta = report?.thinking;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs text-text-dim uppercase tracking-wide">面试会审</div>
          <h3 className="text-lg font-semibold text-text-main">
            面试进行中: {roleLabel} 岗位
          </h3>
          <div className="text-[10px] text-text-dim">角色 ID: {roleId}</div>
        </div>
        {onBack ? (
          <button
            onClick={onBack}
            className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-white/30 flex items-center gap-1"
          >
            <ArrowLeft className="size-3" />
            返回殿前
          </button>
        ) : null}
      </div>

      {running ? (
        <div className="rounded-lg border border-white/10 bg-black/20 p-6 text-center text-sm text-text-dim">
          <Loader2 className="size-4 animate-spin inline-block mr-2" />
          面试进行中，正在收集应答...
        </div>
      ) : null}

      {error ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">
          {error}
        </div>
      ) : null}

      {!running && report ? (
        <>
          <ThinkingDisplay
            title="思维能力速览"
            thinking={thinkingMeta?.thinking_text}
            confidence={thinkingMeta?.confidence}
            format={thinkingMeta?.format}
          />

          <div className="space-y-4">
            {cases.map((item, idx) => (
              <div key={item.id || idx} className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs font-semibold text-text-main">
                    <MessageSquare className="size-4 text-cyan-300" />
                    <span>面试问题 {idx + 1}</span>
                  </div>
                  {typeof item.score === 'number' ? (
                    <span className="text-[10px] text-text-dim uppercase tracking-wide">
                      评分: {Math.round(item.score * 100)}
                    </span>
                  ) : null}
                </div>

                <div className="text-xs text-text-dim">{item.question}</div>

                <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-3">
                  <div className="rounded-lg border border-white/10 bg-black/30 p-3">
                    <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">应聘者回答</div>
                    <pre className="text-[11px] text-text-main whitespace-pre-wrap font-mono max-h-48 overflow-auto">
                      {item.answer || item.output || '(无应答)'}
                    </pre>
                  </div>
                  <ThinkingDisplay
                    title="思考过程"
                    thinking={item.thinking}
                    confidence={thinkingMeta?.confidence}
                    format={thinkingMeta?.format}
                  />
                </div>

                <div className="rounded-lg border border-white/10 bg-black/20 p-3 text-xs">
                  <div className="text-[10px] uppercase tracking-wide text-text-dim mb-1">面试评价</div>
                  <div className="text-text-main">{item.notes || '待评议。'}</div>
                  {Array.isArray(item.criteria_hits) && item.criteria_hits.length > 0 ? (
                    <div className="text-text-dim mt-2">
                      命中要点: {item.criteria_hits.join(', ')}
                    </div>
                  ) : null}
                  {Array.isArray(item.missing_criteria) && item.missing_criteria.length > 0 ? (
                    <div className="text-text-dim mt-1">
                      缺失要点: {item.missing_criteria.join(', ')}
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>

          <InterviewReport report={report} roleLabel={roleLabel} />
        </>
      ) : null}
    </div>
  );
}

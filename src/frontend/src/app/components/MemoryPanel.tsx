import { Database, Clock, AlertCircle, ChevronDown, ChevronRight, CheckCircle, XCircle, FileText, ListChecks, Target } from 'lucide-react';
import { useMemo, useState } from 'react';

interface MemoryPanelProps {
  content: string;
  mtime: string;
  loading: boolean;
  error: string | null;
  collapsed?: boolean;
  onToggle?: () => void;
}

export function MemoryPanel({ content, mtime, loading, error, collapsed, onToggle }: MemoryPanelProps) {
  const [showRaw, setShowRaw] = useState(false);
  const parsed = useMemo(() => {
    const text = (content || '').trim();
    if (!text) return null;
    try {
      return JSON.parse(text) as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [content]);

  const lastRunAt = typeof parsed?.last_run_at === 'string' ? parsed?.last_run_at : '';
  const lastRoundIndex = typeof parsed?.last_round_index === 'number' ? parsed?.last_round_index : null;
  const lastTargetIndex = typeof parsed?.last_target_index === 'number' ? parsed?.last_target_index : null;
  const lastTarget = typeof parsed?.last_target === 'string' ? parsed?.last_target : '';
  const lastSummary = typeof parsed?.last_summary === 'string' ? parsed?.last_summary : '';
  const lastNext = typeof parsed?.last_next_step === 'string' ? parsed?.last_next_step : '';
  const lastLogPath = typeof parsed?.last_log_path === 'string' ? parsed?.last_log_path : '';
  const lastRespPath = typeof parsed?.last_response_path === 'string' ? parsed?.last_response_path : '';
  const lastExit = typeof parsed?.last_exit_code === 'number' ? parsed?.last_exit_code : null;
  const lastError = typeof parsed?.last_error === 'string' ? parsed?.last_error : '';
  const gapAt = typeof parsed?.last_gap_review_at === 'string' ? parsed?.last_gap_review_at : '';
  const gapPath = typeof parsed?.last_gap_report_path === 'string' ? parsed?.last_gap_report_path : '';
  const statusOk = lastExit === 0 && !lastError;
  const knownKeys = useMemo(
    () =>
      new Set([
        'last_run_at',
        'last_round_index',
        'last_target_index',
        'last_target',
        'last_summary',
        'last_next_step',
        'last_log_path',
        'last_response_path',
        'last_exit_code',
        'last_error',
        'last_gap_review_at',
        'last_gap_report_path',
      ]),
    [],
  );
  const otherEntries = useMemo(() => {
    if (!parsed) return [];
    const entries = Object.entries(parsed).filter(([k]) => !knownKeys.has(k));
    entries.sort(([a], [b]) => a.localeCompare(b));
    return entries;
  }, [parsed, knownKeys]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const toggleKey = (k: string) => setExpanded((prev) => ({ ...prev, [k]: !prev[k] }));
  const isComplex = (v: unknown) => typeof v === 'object' && v !== null;
  const brief = (v: unknown): string => {
    if (v === null || v === undefined) return '(null)';
    if (typeof v === 'string') {
      const t = v.trim();
      return t.length > 160 ? t.slice(0, 157) + '...' : t;
    }
    if (typeof v === 'number' || typeof v === 'boolean') return String(v);
    try {
      const t = JSON.stringify(v);
      return t.length > 200 ? t.slice(0, 197) + '...' : t;
    } catch {
      return String(v);
    }
  };

  return (
    <div className="soft-panel-subtle h-full flex flex-col">
      <div className="soft-panel-subtle px-4 py-3 border-b flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database className="size-4 text-accent" />
          <h2 className="text-sm font-semibold text-text-main">记忆</h2>
        </div>
        <div className="flex items-center gap-3 text-xs text-text-dim">
          <div className="flex items-center gap-1">
            <Clock className="size-3" />
            <span>{mtime || '-'}</span>
          </div>
          {!collapsed ? (
            <button
              type="button"
              onClick={() => setShowRaw((prev) => !prev)}
              className="rounded px-2 py-1 text-[11px] text-text-muted hover:bg-white/70"
              aria-label={showRaw ? '隐藏原始 JSON' : '显示原始 JSON'}
            >
              {showRaw ? '隐藏原始' : '显示原始'}
            </button>
          ) : null}

        </div>
      </div>

      {collapsed ? null : (
        <div className="flex-1 overflow-auto">
          {error ? (
            <div className="p-4 text-sm text-status-error flex items-center gap-2">
              <AlertCircle className="size-4" />
              <span>{error}</span>
            </div>
          ) : null}
          {loading ? (
            <div className="p-4 text-sm text-text-muted">加载中...</div>
          ) : (
            <div className="p-3 space-y-3">
              {parsed ? (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="soft-panel rounded p-3">
                      <div className="flex items-center gap-2 text-xs text-text-muted">
                        {statusOk ? (
                          <CheckCircle className="size-4 text-status-success" />
                        ) : (
                          <XCircle className="size-4 text-status-error" />
                        )}
                        <span className={`rounded px-2 py-0.5 text-[10px] ${statusOk ? 'bg-emerald-500/15 text-status-success' : 'bg-red-500/15 text-status-error'}`}>
                          {statusOk ? '通过' : '失败'}
                        </span>
                        {lastRunAt ? (
                          <span className="ml-2 flex items-center gap-1 text-text-muted">
                            <Clock className="size-3" />
                            {lastRunAt}
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-2 text-xs text-text-main">
                        {typeof lastRoundIndex === 'number' || typeof lastTargetIndex === 'number' ? (
                          <div className="flex items-center gap-2">
                            <Target className="size-3.5 text-accent" />
                            <span>轮次: {lastRoundIndex ?? '-'} / 目标序号: {lastTargetIndex ?? '-'}</span>
                          </div>
                        ) : null}
                        {lastTarget ? <div className="mt-1 text-text-muted">目标：{lastTarget}</div> : null}
                      </div>
                    </div>

                    <div className="soft-panel rounded p-3">
                      <div className="flex items-center gap-2 text-xs text-text-muted">
                        <ListChecks className="size-4 text-gold" />
                        <span>摘要与下一步</span>
                      </div>
                      <div className="mt-2 space-y-1">
                        <div className="text-xs text-text-main">
                          <span className="text-text-muted">摘要：</span>
                          {lastSummary || '(无)'}
                        </div>
                        <div className="text-xs text-text-main">
                          <span className="text-text-muted">下一步：</span>
                          {lastNext || '(无)'}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div className="soft-panel rounded p-3">
                      <div className="flex items-center gap-2 text-xs text-text-muted">
                        <FileText className="size-4 text-accent" />
                        <span>关联文件</span>
                      </div>
                      <div className="mt-2 space-y-1 text-[11px] text-text-muted">
                        {lastLogPath ? <div>日志：{lastLogPath}</div> : null}
                        {lastRespPath ? <div>响应：{lastRespPath}</div> : null}
                      </div>
                    </div>
                    <div className="soft-panel rounded p-3">
                      <div className="flex items-center gap-2 text-xs text-text-muted">
                        <AlertCircle className="size-4 text-gold" />
                        <span>缺口复盘</span>
                      </div>
                      <div className="mt-2 space-y-1 text-[11px] text-text-muted">
                        {gapAt ? <div>时间：{gapAt}</div> : <div className="text-text-dim">(未记录)</div>}
                        {gapPath ? <div>报告：{gapPath}</div> : null}
                      </div>
                    </div>
                  </div>

                  {lastError ? (
                    <div className="rounded border border-red-500/20 bg-red-500/10 p-3 text-xs text-status-error">
                      <div className="flex items-center gap-2">
                        <XCircle className="size-4" />
                        <span>错误</span>
                      </div>
                      <div className="mt-2">{lastError}</div>
                    </div>
                  ) : null}

                  {otherEntries.length > 0 ? (
                    <div className="soft-panel rounded p-3">
                      <div className="flex items-center gap-2 text-xs text-text-muted">
                        <ListChecks className="size-4 text-accent" />
                        <span>其他字段</span>
                      </div>
                      <div className="mt-2 space-y-2">
                        {otherEntries.map(([key, value]) => {
                          const complex = isComplex(value);
                          const open = !!expanded[key];
                          return (
                            <div key={key} className="soft-inset rounded p-2">
                              <div className="flex items-center justify-between">
                                <div className="text-[11px] text-text-muted">{key}</div>
                                {complex ? (
                                  <button
                                    type="button"
                                    onClick={() => toggleKey(key)}
                                    className="rounded px-2 py-0.5 text-[10px] text-text-muted hover:bg-white/70"
                                  >
                                    {open ? '收起' : '展开'}
                                  </button>
                                ) : null}
                              </div>
                              <div className="mt-1 text-xs text-text-main">{brief(value)}</div>
                              {complex && open ? (
                                <pre className="soft-inset mt-2 rounded p-2 text-[11px] text-text-main font-mono leading-relaxed whitespace-pre-wrap">
                                  <code>{(() => {
                                    try {
                                      return JSON.stringify(value, null, 2);
                                    } catch {
                                      return String(value);
                                    }
                                  })()}</code>
                                </pre>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="soft-panel rounded p-3 text-xs text-text-muted">
                  (无可解析的内存快照)
                </div>
              )}

              {showRaw ? (
                <pre className="soft-inset rounded p-3 text-[11px] text-text-main font-mono leading-relaxed whitespace-pre-wrap">
                  <code>{content || '(空)'}</code>
                </pre>
              ) : null}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

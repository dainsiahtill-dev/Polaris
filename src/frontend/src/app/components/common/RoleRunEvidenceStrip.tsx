import { Loader2, RefreshCw, XCircle } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';

type RoleRunEvidenceTone = 'amber' | 'cyan' | 'emerald';

interface RoleRunEvidenceStripProps {
  tone: RoleRunEvidenceTone;
  testId: string;
  endpoint: string;
  workspace?: string | null;
  loading: boolean;
  error?: string | null;
  status?: string | null;
  details?: string[];
  message?: string | null;
  refreshTestId?: string;
  refreshDisabled?: boolean;
  refreshLoading?: boolean;
  refreshLabel?: string;
  realtimePushActive?: boolean;
  onRefresh?: () => void;
  cancelTestId: string;
  cancelDisabled: boolean;
  cancelLoading: boolean;
  onCancel: () => void;
  cancelResultTestId: string;
  cancelResultEndpoint: string;
  cancelResultVisible: boolean;
  cancelResultLoading: boolean;
  cancelResultMessage?: string | null;
  cancelResultError?: string | null;
}

const TONE_CLASSES = {
  amber: {
    border: 'border-amber-500/15',
    endpoint: 'text-amber-200/80',
    result: 'text-amber-200/80',
  },
  cyan: {
    border: 'border-cyan-500/15',
    endpoint: 'text-cyan-200/80',
    result: 'text-cyan-200/80',
  },
  emerald: {
    border: 'border-emerald-500/15',
    endpoint: 'text-emerald-200/80',
    result: 'text-emerald-200/80',
  },
} satisfies Record<RoleRunEvidenceTone, Record<string, string>>;

export function roleRunEvidenceEndpoint(endpoint: string, workspace?: string | null): string {
  const value = String(workspace || '').trim();
  if (!value) {
    return endpoint;
  }
  const separator = endpoint.includes('?') ? '&' : '?';
  return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}

export function RoleRunEvidenceStrip({
  tone,
  testId,
  endpoint,
  workspace,
  loading,
  error,
  status,
  details = [],
  message,
  refreshTestId,
  refreshDisabled = false,
  refreshLoading = false,
  refreshLabel = '刷新运行快照',
  realtimePushActive = false,
  onRefresh,
  cancelTestId,
  cancelDisabled,
  cancelLoading,
  onCancel,
  cancelResultTestId,
  cancelResultEndpoint,
  cancelResultVisible,
  cancelResultLoading,
  cancelResultMessage,
  cancelResultError,
}: RoleRunEvidenceStripProps) {
  const styles = TONE_CLASSES[tone];
  const visibleEndpoint = roleRunEvidenceEndpoint(endpoint, workspace);
  const visibleCancelEndpoint = roleRunEvidenceEndpoint(cancelResultEndpoint, workspace);
  const snapshotParts = [status || 'unknown', ...details.filter(Boolean)];
  const hasSnapshot = Boolean(status) || details.filter(Boolean).length > 0;
  const statusText = loading
    ? hasSnapshot
      ? [...snapshotParts, '刷新中'].join(' · ')
      : '正在读取运行快照...'
    : error
      ? error
      : snapshotParts.join(' · ');

  return (
    <div
      className={cn(
        'flex flex-wrap items-center justify-between gap-2 border-b bg-slate-950/70 px-4 py-2 text-[11px]',
        styles.border,
      )}
      data-testid={testId}
      data-endpoint={visibleEndpoint}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <span
          className={cn('rounded border border-white/10 bg-slate-950/70 px-1.5 py-0.5 text-[9px] font-medium', styles.endpoint)}
          title={visibleEndpoint}
          data-testid={`${testId}-endpoint`}
          data-endpoint={visibleEndpoint}
        >
          API
        </span>
        <span className={error ? 'text-rose-300' : 'text-slate-300'}>{statusText}</span>
        {!loading && !error && message ? (
          <span className="max-w-[360px] truncate text-slate-500" title={message}>
            {message}
          </span>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {realtimePushActive ? (
          <span
            className="rounded border border-emerald-500/15 bg-emerald-500/10 px-2 py-1 font-mono text-[10px] text-emerald-200"
            data-testid={`${testId}-realtime-push`}
          >
            实时推送
          </span>
        ) : null}
        {onRefresh ? (
          <button
            type="button"
            onClick={onRefresh}
            data-testid={refreshTestId}
            disabled={refreshDisabled || refreshLoading}
            title={refreshLabel}
            aria-label={refreshLabel}
            className="inline-flex h-6 w-6 cursor-pointer items-center justify-center rounded border border-white/10 bg-white/5 text-slate-300 transition-colors hover:bg-white/10 hover:text-slate-100 disabled:cursor-not-allowed disabled:border-slate-600/20 disabled:bg-slate-700/20 disabled:text-slate-500"
          >
            {refreshLoading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onCancel}
          data-testid={cancelTestId}
          disabled={cancelDisabled}
          className="inline-flex h-6 cursor-pointer items-center gap-1 rounded border border-rose-500/20 bg-rose-500/10 px-2 text-[11px] text-rose-200 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:border-slate-600/20 disabled:bg-slate-700/20 disabled:text-slate-500"
        >
          {cancelLoading ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <XCircle className="h-3 w-3" />
          )}
          取消
        </button>
        {cancelResultVisible ? (
          <span
            className={cancelResultError ? 'text-rose-300' : styles.result}
            data-testid={cancelResultTestId}
            data-endpoint={visibleCancelEndpoint}
            title={visibleCancelEndpoint}
          >
            {cancelResultLoading ? 'cancelling' : cancelResultError || cancelResultMessage}
          </span>
        ) : null}
      </div>
    </div>
  );
}

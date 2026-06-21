/**
 * ContextStoreStatsPanel — ContextOS 实时视图的「运行时上下文存储 · TTL/容量」面板。
 *
 * 数据源：
 *   - GET  /v2/context/admin/stats     → 容量 + 配置 + 最近一次 sweep 报告
 *   - POST /v2/context/admin/sweep     → 用户手动触发 sweep（sweep 是 destructive 的，故仅暴露按钮）
 *
 * 状态：
 *   - disabled（admin 端点未启用，默认）  → 渲染 stats-disabled hint，提示用户启用 KERNELONE_CONTEXT_ADMIN_ENABLED
 *   - error                              → 错误信息 + 保留 last successful data
 *   - ready                              → 完整面板：file_count / total_bytes / 利用条 / 配置 / 最近 sweep 报告 / sweep 按钮
 *   - loading (无历史)                    → 骨架占位（"读取中…"）
 *   - idle (组件未挂载 / 关闭)            → 不渲染（fail-closed 静默）
 *
 * 原则：
 *   - 完全只读 + 显式 destructive 按钮；按钮在 disabled / loading 状态下 disabled。
 *   - 「强制 sweep」是 destructive 操作（删除最早文件直到回到 cap 内），按钮 label 明确
 *     注明「清理 (destructive)」以避免误点。
 *   - 任何伪造精度都用占位符（—）而非伪 0；缺字段一律 null。
 *   - 复用 contextos 既有视觉语言（status dot / ring / text color），与决策表/角色卡保持一致。
 */

import { useMemo, useState } from 'react';
import {
  AlertCircle,
  Database,
  Loader2,
  RefreshCw,
  Settings2,
  Trash2,
} from 'lucide-react';

import { Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';

import {
  classifyStatus,
  deriveNextSweepAt,
  deriveOldestAgeSeconds,
  formatBytes,
  formatElapsedShort,
  formatRelativeSeconds,
  STATS_STATUS_COLOR,
  STATS_STATUS_LABEL,
  type ContextStoreStatsResponse,
  type StatsStatus,
} from './contextosStoreStats';
import { useContextStoreStats } from './useContextStoreStats';

export interface ContextStoreStatsPanelProps {
  workspace?: string | null;
  /** 父组件传入的开关（默认 ON）。disable 后面板静默不渲染。 */
  enabled?: boolean;
}

export function ContextStoreStatsPanel({ workspace, enabled = true }: ContextStoreStatsPanelProps) {
  const { state, refresh, triggerSweep } = useContextStoreStats({ workspace, enabled });
  const [sweepPending, setSweepPending] = useState(false);
  const [sweepError, setSweepError] = useState<string | null>(null);

  const onTriggerSweep = async () => {
    if (sweepPending) return;
    setSweepPending(true);
    setSweepError(null);
    const result = await triggerSweep();
    setSweepPending(false);
    if (!result.ok) setSweepError(result.error);
  };

  if (!enabled) return null;

  return (
    <section
      data-testid="contextos-store-stats-panel"
      className="flex flex-col rounded-xl border border-white/[0.07] bg-bg-panel/40 backdrop-blur-sm"
    >
      <header className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <Database className="h-3.5 w-3.5 shrink-0 text-accent-secondary" />
          <span className="truncate text-xs font-semibold text-text-main">上下文存储 · TTL/容量</span>
          <span className="truncate text-[10px] text-text-dim">
            runtime/contexts · ContextStoreRetention
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {state.kind === 'ready' && <StatusDot status={classifyStatusFromStats(state.data)} />}
          {state.kind === 'ready' && !state.isAdmin && (
            <span className="rounded bg-white/5 px-1.5 py-0.5 text-[9px] text-text-dim">只读</span>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void refresh()}
            disabled={state.kind === 'loading'}
            data-testid="contextos-store-stats-refresh"
            title="立即拉取最新统计"
            aria-label="刷新上下文存储统计"
            className="border-accent-secondary/30 text-accent-secondary hover:bg-accent-secondary/10"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', state.kind === 'loading' && 'animate-spin')} />
          </Button>
        </div>
      </header>
      <div className="min-h-0 flex-1 p-3" data-testid="contextos-store-stats-body">
        {renderBody({
          state,
          sweepPending,
          sweepError,
          onTriggerSweep: () => {
            void onTriggerSweep();
          },
        })}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Internal renderers
// ---------------------------------------------------------------------------

interface RenderBodyParams {
  state: ReturnType<typeof useContextStoreStats>['state'];
  sweepPending: boolean;
  sweepError: string | null;
  onTriggerSweep: () => void;
}

function renderBody({ state, sweepPending, sweepError, onTriggerSweep }: RenderBodyParams) {
  if (state.kind === 'idle') {
    return (
      <div className="rounded-lg border border-dashed border-white/10 px-3 py-5 text-center text-[11px] text-text-dim">
        待命
      </div>
    );
  }
  if (state.kind === 'loading' && !state.previous) {
    return (
      <div className="flex items-center gap-2 px-3 py-5 text-[11px] text-text-dim" data-testid="contextos-store-stats-loading">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        读取中…
      </div>
    );
  }
  if (state.kind === 'disabled') {
    return <DisabledHint reason={state.reason} />;
  }
  if (state.kind === 'error' && !state.previous) {
    return <ErrorMessage message={state.message} onRetry={() => undefined} />;
  }
  // ready | error-with-previous | loading-with-previous → 渲染历史数据
  const data = state.kind === 'ready' ? state.data
    : state.kind === 'error' ? state.previous
    : state.previous;
  if (!data) return null;
  const isAdmin = state.kind === 'ready' ? state.isAdmin : false;
  return <ReadyView data={data} sweepPending={sweepPending} sweepError={sweepError} onTriggerSweep={onTriggerSweep} errorMessage={state.kind === 'error' ? state.message : null} isAdmin={isAdmin} />;
}

// --- disabled hint ---------------------------------------------------------

function DisabledHint({ reason }: { reason: string }) {
  return (
    <div
      data-testid="contextos-store-stats-disabled"
      className="rounded-lg border border-dashed border-white/10 bg-white/[0.02] px-3 py-3"
    >
      <div className="flex items-start gap-2">
        <Settings2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-dim" />
        <div className="min-w-0">
          <div className="text-[12px] font-semibold text-text-main">管理员端点未启用</div>
          <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
            上下文存储统计由 <code className="rounded bg-black/30 px-1 font-mono text-[10px] text-text-main">/v2/context/admin/stats</code> 提供；该端点由环境变量
            <code className="mx-0.5 rounded bg-black/30 px-1 font-mono text-[10px] text-text-main">KERNELONE_CONTEXT_ADMIN_ENABLED</code>
            控守，未启用时返回 404/ADMIN_DISABLED。
          </p>
          <p className="mt-1 text-[10px] leading-relaxed text-text-dim">
            启用方式：在后端进程环境变量中设置 <code className="font-mono">KERNELONE_CONTEXT_ADMIN_ENABLED=1</code> 后重启。存储 TTL/容量策略本身（<code className="font-mono">ContextStoreRetention</code>，默认 TTL=7d / 500MB / 20k 文件）仍在后台 on-read gate 持续运行，仅统计不可见。
          </p>
          {reason && (
            <div className="mt-2 rounded border border-white/10 bg-black/20 px-2 py-1 font-mono text-[10px] text-text-dim">
              后端响应：{reason}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// --- error -----------------------------------------------------------------

function ErrorMessage({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      data-testid="contextos-store-stats-error"
      className="flex items-start gap-2 rounded-lg border border-status-error/30 bg-status-error/10 px-3 py-3"
    >
      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-status-error" />
      <div className="min-w-0 flex-1">
        <div className="text-[12px] font-semibold text-status-error">读取统计失败</div>
        <div className="mt-1 truncate font-mono text-[10px] text-text-muted" title={message}>
          {message}
        </div>
      </div>
      <Button variant="ghost" size="sm" onClick={onRetry} className="text-text-muted hover:bg-white/5">
        重试
      </Button>
    </div>
  );
}

// --- ready view ------------------------------------------------------------

interface ReadyViewProps {
  data: ContextStoreStatsResponse;
  sweepPending: boolean;
  sweepError: string | null;
  onTriggerSweep: () => void;
  errorMessage: string | null;
  isAdmin: boolean;
}

function ReadyView({ data, sweepPending, sweepError, onTriggerSweep, errorMessage, isAdmin }: ReadyViewProps) {
  const status = classifyStatusFromStats(data);
  const statusColor = STATS_STATUS_COLOR[status];
  const oldestAgeSec = deriveOldestAgeSeconds(data);
  const oldestAgeLabel = formatRelativeSeconds(typeof data.oldest_mtime === 'number' ? data.oldest_mtime : null);
  const lastSweepLabel = data.last_sweep_at > 0 ? formatRelativeSeconds(data.last_sweep_at) : '从未';
  const nextSweepAt = deriveNextSweepAt(data);
  const nextSweepLabel = nextSweepAt !== null ? formatRelativeSeconds(nextSweepAt) : null;
  const ttlLabel = data.config.ttl_seconds ? formatElapsedShort(data.config.ttl_seconds * 1000) : null;
  const sweepIntervalLabel = data.config.sweep_min_interval_seconds
    ? formatElapsedShort(data.config.sweep_min_interval_seconds * 1000)
    : null;
  const enabled = data.config.enabled !== false;

  const filesRatio = useMemo(() => {
    if (!data.config.max_files || data.config.max_files <= 0) return null;
    return data.file_count / data.config.max_files;
  }, [data.file_count, data.config.max_files]);

  const bytesRatio = useMemo(() => {
    if (!data.config.max_total_bytes || data.config.max_total_bytes <= 0) return null;
    return data.total_bytes / data.config.max_total_bytes;
  }, [data.total_bytes, data.config.max_total_bytes]);

  return (
    <div className="space-y-3" data-testid="contextos-store-stats-ready">
      {errorMessage && (
        <div
          data-testid="contextos-store-stats-freshness-warning"
          className="flex items-start gap-2 rounded-md border border-status-warning/30 bg-status-warning/10 px-2 py-1.5 text-[10px] text-status-warning"
        >
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="font-mono">最新拉取失败：{errorMessage}（展示为最近一次成功数据）</span>
        </div>
      )}

      {/* 顶部 3 项：状态 / 文件数 / 字节 */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Pill label="状态" tone={statusColor} value={STATS_STATUS_LABEL[status]} sub={enabled ? 'on-read gate' : 'retention disabled'} />
        <Pill label="文件数" tone="neutral" value={data.file_count.toLocaleString()} sub={data.config.max_files ? `上限 ${data.file_count >= data.config.max_files ? data.config.max_files.toLocaleString() : data.config.max_files.toLocaleString()}` : '无上限'} />
        <Pill label="占用字节" tone="neutral" value={formatBytes(data.total_bytes)} sub={data.config.max_total_bytes ? `上限 ${formatBytes(data.config.max_total_bytes)}` : '无上限'} />
      </div>

      {/* 利用条 */}
      <div className="space-y-2 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
        <UtilizationBar label="文件数利用比" ratio={filesRatio} current={data.file_count} max={data.config.max_files} formatMax={(n) => n.toLocaleString()} />
        <UtilizationBar label="字节利用比" ratio={bytesRatio} current={data.total_bytes} max={data.config.max_total_bytes} formatMax={formatBytes} />
      </div>

      {/* 配置 + 时间 */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <InfoCard title="策略配置" entries={[
          { k: 'TTL', v: ttlLabel ?? '—' },
          { k: '最大字节', v: data.config.max_total_bytes ? formatBytes(data.config.max_total_bytes) : '—' },
          { k: '最大文件数', v: data.config.max_files ? data.config.max_files.toLocaleString() : '—' },
          { k: 'sweep 间隔', v: sweepIntervalLabel ?? '—' },
        ]} />
        <InfoCard title="时间轴" entries={[
          { k: '最近 sweep', v: lastSweepLabel ?? '—' },
          { k: '下次 sweep', v: nextSweepLabel ?? '—' },
          { k: '最旧文件', v: oldestAgeLabel ?? '—' },
          { k: '年龄（秒）', v: oldestAgeSec !== null ? oldestAgeSec.toLocaleString() : '—' },
        ]} />
      </div>

      {/* 最近 sweep 报告 */}
      {data.last_sweep_report && (
        <SweepReportCard report={data.last_sweep_report} />
      )}

      {/* sweep 按钮 - 仅在 admin 端点可用时显示 */}
      {isAdmin && (
        <div className="flex items-center justify-between gap-2 rounded-lg border border-status-warning/20 bg-status-warning/5 px-3 py-2">
          <div className="min-w-0 text-[10px] leading-relaxed text-text-muted">
            <div className="font-semibold text-text-main">强制清理（destructive）</div>
            <div className="truncate" title="按 oldest-first 顺序删除最早文件直到回到 TTL/容量上限；不可恢复。">
              按 oldest-first 顺序删除最早文件直到回到 TTL/容量上限。
            </div>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onTriggerSweep}
            disabled={sweepPending || !enabled}
            data-testid="contextos-store-stats-sweep"
            aria-label="强制清理上下文存储"
            className="border-status-warning/40 text-status-warning hover:bg-status-warning/15"
          >
            {sweepPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="mr-1 h-3.5 w-3.5" />
            )}
            清理
          </Button>
        </div>
      )}

      {sweepError && (
        <div className="rounded-md border border-status-error/30 bg-status-error/10 px-2 py-1.5 font-mono text-[10px] text-status-error">
          sweep 失败：{sweepError}
        </div>
      )}
    </div>
  );
}

function SweepReportCard({ report }: { report: ContextStoreStatsResponse['last_sweep_report'] }) {
  if (!report) return null;
  const triggers = report.triggers ?? [];
  const removedBytes = report.removed_bytes ?? 0;
  const removedFiles = report.removed_files ?? 0;
  const elapsed = formatElapsedShort(report.elapsed_ms);
  return (
    <div
      data-testid="contextos-store-stats-last-sweep"
      className="rounded-lg border border-white/[0.06] bg-black/20 p-2.5"
    >
      <div className="mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-text-dim">
        <span>最近 sweep 报告</span>
        {elapsed && <span className="font-mono normal-case">{elapsed}</span>}
      </div>
      <div className="grid grid-cols-3 gap-2">
        <Mini label="扫描" value={(report.scanned_files ?? 0).toLocaleString()} />
        <Mini label="删除文件" value={removedFiles.toLocaleString()} />
        <Mini label="释放字节" value={formatBytes(removedBytes)} />
      </div>
      {triggers.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {triggers.map((trigger) => (
            <span key={trigger} className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[9px] text-text-muted">
              {trigger}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Pill({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone: 'neutral' | { dot: string; ring: string; text: string };
}) {
  const style = tone === 'neutral' ? null : tone;
  return (
    <div
      className={cn(
        'rounded-lg border px-2.5 py-2',
        style ? style.ring : 'border-white/[0.06] bg-white/[0.02]',
      )}
    >
      <div className="text-[9px] uppercase tracking-wider text-text-dim">{label}</div>
      <div className={cn('mt-0.5 font-mono text-sm font-bold', style ? style.text : 'text-text-main')}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[9px] text-text-dim" title={sub}>{sub}</div>}
    </div>
  );
}

function UtilizationBar({
  label,
  ratio,
  current,
  max,
  formatMax,
}: {
  label: string;
  ratio: number | null;
  current: number | null;
  max: number | null;
  formatMax: (n: number) => string;
}) {
  const r = typeof ratio === 'number' && Number.isFinite(ratio) ? Math.max(0, Math.min(1, ratio)) : null;
  const widthPct = r === null ? 0 : Math.max(2, Math.round(r * 100));
  const tone = r === null
    ? 'bg-text-dim'
    : r >= 0.95
      ? 'bg-status-error'
      : r >= 0.7
        ? 'bg-status-warning'
        : 'bg-accent-secondary';
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-text-muted">{label}</span>
        <span className="font-mono text-text-main">
          {r === null ? '—' : `${Math.round(r * 100)}%`}
          {current !== null && max !== null && (
            <span className="ml-1 text-text-dim">
              {typeof current === 'number' && current > 1024 ? formatBytes(current) : current.toLocaleString()}
              {' / '}
              {formatMax(max)}
            </span>
          )}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/5">
        <div className={cn('h-full rounded-full transition-all duration-500', tone)} style={{ width: `${widthPct}%` }} />
      </div>
    </div>
  );
}

function InfoCard({
  title,
  entries,
}: {
  title: string;
  entries: Array<{ k: string; v: string }>;
}) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-text-dim">{title}</div>
      <dl className="space-y-1">
        {entries.map((entry) => (
          <div key={entry.k} className="grid grid-cols-[1fr_auto] items-baseline gap-2 text-[10px]">
            <dt className="text-text-muted">{entry.k}</dt>
            <dd className="font-mono text-text-main">{entry.v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-white/[0.02] px-2 py-1">
      <div className="text-[9px] uppercase tracking-wider text-text-dim">{label}</div>
      <div className="font-mono text-[11px] font-bold text-text-main">{value}</div>
    </div>
  );
}

function StatusDot({ status }: { status: StatsStatus }) {
  const color = STATS_STATUS_COLOR[status];
  return (
    <StatusBadge color={status === 'ok' ? 'success' : status === 'critical' ? 'error' : status === 'warning' ? 'warning' : 'default'} variant="dot">
      <span className={cn('font-mono text-[10px]', color.text)}>{STATS_STATUS_LABEL[status]}</span>
    </StatusBadge>
  );
}

function classifyStatusFromStats(data: ContextStoreStatsResponse): StatsStatus {
  return classifyStatus({
    file_count: data.file_count,
    total_bytes: data.total_bytes,
    max_files: data.config.max_files,
    max_total_bytes: data.config.max_total_bytes,
    enabled: data.config.enabled,
  });
}
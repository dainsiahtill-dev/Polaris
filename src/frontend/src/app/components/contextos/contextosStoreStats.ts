/**
 * Context store stats — pure view-model.
 *
 * 解析 /v2/context/admin/stats 的响应（来自 ContextStoreRetention.get_stats）
 * 为 ContextOS 实时视图展示用的派生模型。所有数据均来自后端响应——本模块不
 * 伪造精度、不臆造字段；缺失字段诚实保留为 null/0。
 *
 * 后端 admin 端点（默认关闭，由 KERNELONE_CONTEXT_ADMIN_ENABLED 控制）：
 *   GET /v2/context/admin/stats
 *   {
 *     workspace, contexts_root, file_count, total_bytes,
 *     oldest_mtime, newest_mtime, config: {...},
 *     last_sweep_at, last_sweep_report: {...} | null
 *   }
 *
 * 当端点返回 404 / ADMIN_DISABLED 时，前端展示「stats-disabled hint」提示
 * 用户启用 KERNELONE_CONTEXT_ADMIN_ENABLED。本视图层只做呈现，不做开关探测。
 */

export interface ContextStoreStatsConfig {
  ttl_seconds: number | null;
  max_total_bytes: number | null;
  max_files: number | null;
  sweep_min_interval_seconds: number | null;
  enabled: boolean | null;
}

export interface ContextStoreSweepReport {
  scanned_files: number | null;
  removed_files: number | null;
  removed_bytes: number | null;
  kept_files: number | null;
  total_bytes_after: number | null;
  elapsed_ms: number | null;
  triggers: string[] | null;
}

export interface ContextStoreStatsResponse {
  workspace: string;
  contexts_root: string;
  file_count: number;
  total_bytes: number;
  oldest_mtime: number | null;
  newest_mtime: number | null;
  config: ContextStoreStatsConfig;
  last_sweep_at: number;
  last_sweep_report: ContextStoreSweepReport | null;
}

export type StatsStatus = 'ok' | 'warning' | 'critical' | 'disabled' | 'empty';

/** 状态严重度 → 颜色类。颜色类名复用 contextos 既有 tailwind 调色板。 */
export const STATS_STATUS_COLOR: Record<StatsStatus, { dot: string; ring: string; text: string }> = {
  ok: { dot: 'bg-status-success', ring: 'border-status-success/40 bg-status-success/10', text: 'text-status-success' },
  warning: { dot: 'bg-status-warning', ring: 'border-status-warning/40 bg-status-warning/10', text: 'text-status-warning' },
  critical: { dot: 'bg-status-error', ring: 'border-status-error/40 bg-status-error/10', text: 'text-status-error' },
  disabled: { dot: 'bg-text-dim', ring: 'border-white/10 bg-white/[0.02]', text: 'text-text-dim' },
  empty: { dot: 'bg-text-dim', ring: 'border-white/10 bg-white/[0.02]', text: 'text-text-dim' },
};

/** 状态 → 展示标签（中文）。 */
export const STATS_STATUS_LABEL: Record<StatsStatus, string> = {
  ok: '健康',
  warning: '接近上限',
  critical: '超限',
  disabled: '已禁用',
  empty: '空',
};

/** 容量利用比阈值（0..1）→ 状态映射。
 *  - < 0.7         → ok
 *  - [0.7, 0.95)   → warning
 *  - >= 0.95       → critical
 *  - disabled      → disabled
 *  - file_count==0 → empty
 */
export function classifyStatus(params: {
  file_count: number;
  total_bytes: number;
  max_files: number | null;
  max_total_bytes: number | null;
  enabled: boolean | null;
}): StatsStatus {
  const { file_count, total_bytes, max_files, max_total_bytes, enabled } = params;
  if (enabled === false) return 'disabled';
  if (file_count === 0 && total_bytes === 0) return 'empty';
  // 任一维度超 95% → critical；任意维度 ≥ 70% → warning；否则 ok。
  const ratios: number[] = [];
  if (typeof max_files === 'number' && max_files > 0) {
    ratios.push(file_count / max_files);
  }
  if (typeof max_total_bytes === 'number' && max_total_bytes > 0) {
    ratios.push(total_bytes / max_total_bytes);
  }
  if (ratios.length === 0) return 'empty';
  const maxRatio = Math.max(...ratios);
  if (maxRatio >= 0.95) return 'critical';
  if (maxRatio >= 0.7) return 'warning';
  return 'ok';
}

/** 把 epoch 秒（后端 last_sweep_at / oldest_mtime 是 float 秒）转成 "Ns 前 / Nm 前 / Nh 前" 中文新鲜度串。
 * 输入非有限数 → null（用于"未知"占位）。 */
export function formatRelativeSeconds(epochSeconds: number | null, nowMs: number = Date.now()): string | null {
  if (typeof epochSeconds !== 'number' || !Number.isFinite(epochSeconds)) return null;
  const deltaMs = nowMs - epochSeconds * 1000;
  if (!Number.isFinite(deltaMs) || deltaMs < 0) return '刚刚';
  const seconds = Math.floor(deltaMs / 1000);
  if (seconds < 5) return '刚刚';
  if (seconds < 60) return `${seconds}s 前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m 前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h 前`;
  const days = Math.floor(hours / 24);
  return `${days}d 前`;
}

/** 把毫秒时长（保留后端 elapsed_ms）转成 "Xms / Xs / Xm" 紧凑串；非有限 → null。 */
export function formatElapsedShort(ms: number | null): string | null {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes.toFixed(1)}m`;
  return `${Math.round(minutes / 60)}h`;
}

/** 字节数 → 人类可读（KB/MB/GB）。后端 max_total_bytes 默认 500MB。 */
export function formatBytes(bytes: number | null): string {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'] as const;
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const fixed = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(fixed)} ${units[index]}`;
}

/** 把后端 stat 响应的 loose 字段收紧成视图模型（容错：缺字段用 null/0 兜底）。
 * 不抛错；不可解析的数字降为 0/NaN-friendly 形式。 */
export function parseContextStoreStatsResponse(payload: unknown): ContextStoreStatsResponse | null {
  if (!payload || typeof payload !== 'object') return null;
  const obj = payload as Record<string, unknown>;
  const configRaw = obj.config && typeof obj.config === 'object' ? (obj.config as Record<string, unknown>) : {};
  const reportRaw = obj.last_sweep_report && typeof obj.last_sweep_report === 'object'
    ? (obj.last_sweep_report as Record<string, unknown>)
    : null;
  return {
    workspace: typeof obj.workspace === 'string' ? obj.workspace : '',
    contexts_root: typeof obj.contexts_root === 'string' ? obj.contexts_root : '',
    file_count: toIntOrZero(obj.file_count),
    total_bytes: toIntOrZero(obj.total_bytes),
    oldest_mtime: toFloatOrNull(obj.oldest_mtime),
    newest_mtime: toFloatOrNull(obj.newest_mtime),
    config: {
      ttl_seconds: toIntOrNull(configRaw.ttl_seconds),
      max_total_bytes: toIntOrNull(configRaw.max_total_bytes),
      max_files: toIntOrNull(configRaw.max_files),
      sweep_min_interval_seconds: toIntOrNull(configRaw.sweep_min_interval_seconds),
      enabled: typeof configRaw.enabled === 'boolean' ? configRaw.enabled : null,
    },
    last_sweep_at: toFloatOrZero(obj.last_sweep_at),
    last_sweep_report: reportRaw
      ? {
          scanned_files: toIntOrNull(reportRaw.scanned_files),
          removed_files: toIntOrNull(reportRaw.removed_files),
          removed_bytes: toIntOrNull(reportRaw.removed_bytes),
          kept_files: toIntOrNull(reportRaw.kept_files),
          total_bytes_after: toIntOrNull(reportRaw.total_bytes_after),
          elapsed_ms: toIntOrNull(reportRaw.elapsed_ms),
          triggers: Array.isArray(reportRaw.triggers)
            ? reportRaw.triggers.filter((s): s is string => typeof s === 'string')
            : null,
        }
      : null,
  };
}

function toIntOrZero(value: unknown): number {
  const n = toIntOrNull(value);
  return n ?? 0;
}

function toIntOrNull(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.round(value);
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return Math.round(parsed);
  }
  return null;
}

function toFloatOrZero(value: unknown): number {
  const n = toFloatOrNull(value);
  return n ?? 0;
}

function toFloatOrNull(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

/** 派生 UI 展示用的"下一次 sweep 预计时刻"——last_sweep_at + sweep_min_interval_seconds。
 * 不可计算（任一字段缺失/为 0）→ null。 */
export function deriveNextSweepAt(stats: ContextStoreStatsResponse): number | null {
  const interval = stats.config.sweep_min_interval_seconds;
  if (typeof interval !== 'number' || interval <= 0) return null;
  if (!Number.isFinite(stats.last_sweep_at) || stats.last_sweep_at <= 0) return null;
  return stats.last_sweep_at + interval;
}

/** 派生 UI 展示用的 "X 天前最旧" 年龄（秒）。oldest_mtime 非有限 → null。 */
export function deriveOldestAgeSeconds(stats: ContextStoreStatsResponse, nowMs: number = Date.now()): number | null {
  if (typeof stats.oldest_mtime !== 'number' || !Number.isFinite(stats.oldest_mtime)) return null;
  const ageMs = nowMs - stats.oldest_mtime * 1000;
  if (!Number.isFinite(ageMs) || ageMs < 0) return 0;
  return Math.floor(ageMs / 1000);
}
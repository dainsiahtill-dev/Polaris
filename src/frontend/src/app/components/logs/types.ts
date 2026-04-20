/**
 * Unified Log Pipeline Types
 *
 * Type definitions for the CanonicalLogEventV2 schema and related types.
 * These types mirror the backend CanonicalLogEventV2 model.
 */

// Channel types - fixed three channels
export type LogChannel = 'system' | 'process' | 'llm';

// Severity levels
export type LogSeverity = 'debug' | 'info' | 'warn' | 'error' | 'critical';

// Event kinds
export type LogKind = 'state' | 'action' | 'observation' | 'output' | 'error';

// Domain types
export type LogDomain = 'system' | 'process' | 'llm' | 'user';

// LLM enrichment result
export interface LogEnrichment {
  signal_score: number;
  summary: string;
  normalized_fields: Record<string, unknown>;
  noise: boolean;
  status: 'pending' | 'success' | 'failed';
  error?: string;
}

// Canonical log event (matches backend CanonicalLogEventV2)
export interface CanonicalLogEvent {
  // Schema version
  schema_version: 2;

  // Core identifiers
  event_id: string;
  run_id: string;
  seq: number;

  // Timestamps
  ts: string;
  ts_epoch: number;

  // Channel and domain
  channel: LogChannel;
  domain: LogDomain;

  // Event classification
  severity: LogSeverity;
  kind: LogKind;

  // Source information
  actor: string;
  source: string;

  // Message content
  message: string;
  refs: Record<string, unknown>;
  tags: string[];

  // Raw original data
  raw?: Record<string, unknown>;

  // Deduplication
  fingerprint: string;
  dedupe_count: number;

  // LLM enrichment
  enrichment?: LogEnrichment;

  // Legacy compatibility
  legacy_name?: string;
  legacy_output?: Record<string, unknown>;
  legacy_input?: Record<string, unknown>;
}

// Query parameters for log events
export interface LogQueryParams {
  channel?: LogChannel;
  severity?: LogSeverity;
  actor?: string;
  source?: string;
  run_id?: string;
  task_id?: string;
  cursor?: string;
  limit?: number;
  include_raw?: boolean;
  include_enriched?: boolean;
  high_signal_only?: boolean;
}

// Query result from backend
export interface LogQueryResult {
  events: CanonicalLogEvent[];
  next_cursor: string | null;
  total_count: number;
  has_more: boolean;
}

// WebSocket event message
export interface LogEventMessage {
  type: 'event';
  action: 'query' | 'subscribe';
  channel?: LogChannel;
  run_id?: string;
  severity?: LogSeverity;
  limit?: number;
  cursor?: string;
  high_signal_only?: boolean;
}

// WebSocket event response
export interface LogEventResponse {
  type: 'event';
  action: 'query_result' | 'subscription';
  events: CanonicalLogEvent[];
  next_cursor: string | null;
  has_more: boolean;
  total_count: number;
}

// Channel metadata for UI
export interface ChannelMetadata {
  id: LogChannel;
  label: string;
  description: string;
  icon: string;
  color: string;
}

// Channel metadata configuration
export const CHANNEL_METADATA: Record<LogChannel, ChannelMetadata> = {
  system: {
    id: 'system',
    label: '系统',
    description: '系统事件（运行时、引擎状态、PM报告）',
    icon: 'Cpu',
    color: 'blue',
  },
  process: {
    id: 'process',
    label: '进程',
    description: '进程输出（子进程 stdout/stderr）',
    icon: 'Terminal',
    color: 'green',
  },
  llm: {
    id: 'llm',
    label: 'LLM',
    description: 'LLM 交互事件',
    icon: 'Brain',
    color: 'purple',
  },
};

// Severity styling
export const SEVERITY_STYLES: Record<LogSeverity, { bg: string; text: string; label: string }> = {
  debug: { bg: 'bg-gray-500/20', text: 'text-gray-400', label: '调试' },
  info: { bg: 'bg-blue-500/20', text: 'text-blue-400', label: '信息' },
  warn: { bg: 'bg-yellow-500/20', text: 'text-yellow-400', label: '警告' },
  error: { bg: 'bg-red-500/20', text: 'text-red-400', label: '错误' },
  critical: { bg: 'bg-red-600/30', text: 'text-red-300', label: '严重' },
};

// Kind styling
export const KIND_STYLES: Record<LogKind, { bg: string; text: string; label: string }> = {
  state: { bg: 'bg-gray-500/20', text: 'text-gray-400', label: '状态' },
  action: { bg: 'bg-amber-500/20', text: 'text-amber-400', label: '动作' },
  observation: { bg: 'bg-emerald-500/20', text: 'text-emerald-400', label: '观察' },
  output: { bg: 'bg-cyan-500/20', text: 'text-cyan-400', label: '输出' },
  error: { bg: 'bg-red-500/20', text: 'text-red-400', label: '错误' },
};

// ---------------------------------------------------------------------------
// Canonical LlmEvent types — matches backend emit_llm_event() schema v1
// ---------------------------------------------------------------------------

export interface LlmEventBase {
  schema_version: number;
  event_id: string;
  run_id: string;
  iteration: number;
  role: string;
  ts: string;
  seq: number;
  source: 'api' | 'cli' | 'system';
  event: string;
}

export interface ConfigData { tag: string; message: string }
export interface IterationData {
  iteration: number;
  timestamp: string;
  backend: string;
  stage: 'started' | 'llm_calling' | 'parsing' | 'completed' | 'failed';
  exit_code?: number;
  task_count?: number;
}
export interface LlmCallData {
  provider: string;
  model: string;
  provider_kind?: string;
  provider_type?: string;
  prompt_chars: number;
}
export interface LlmResultData {
  provider: string;
  model: string;
  provider_kind?: string;
  provider_type?: string;
  ok: boolean;
  thinking: string;
  output: string;
  output_preview?: string;
  output_json?: unknown;
  output_parse_error?: string;
  output_chars: number;
  content_type?: 'json' | 'markdown' | 'text';
  truncated?: boolean;
  preview_chars?: number;
  prompt_chars: number;
  tokens: { prompt: number; completion: number; total: number };
  duration_ms: number;
  estimated: boolean;
  error: string;
}
export interface InfoData { tag: string; message: string; level?: 'info' | 'warn' | 'error' }

export type LlmEvent =
  | (LlmEventBase & { event: 'config';    data: ConfigData })
  | (LlmEventBase & { event: 'iteration'; data: IterationData })
  | (LlmEventBase & { event: 'llm_call';  data: LlmCallData })
  | (LlmEventBase & { event: 'llm_result'; data: LlmResultData })
  | (LlmEventBase & { event: 'info';      data: InfoData })
  | (LlmEventBase & { event: string;      data: Record<string, unknown> });

let _parseSeq = 0;

function normalizeSource(value: unknown): 'api' | 'cli' | 'system' {
  const token = String(value || '').trim().toLowerCase();
  if (token === 'api' || token === 'cli' || token === 'system') {
    return token;
  }
  return 'system';
}

function toRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function parseLlmEventLine(line: string): LlmEvent | null {
  if (!line || !line.trim()) return null;
  try {
    const raw = JSON.parse(line) as Record<string, unknown>;
    if (!raw || typeof raw !== 'object') return null;

    if (raw.event) {
      if (!raw.event_id) raw.event_id = `fe-${++_parseSeq}`;
      return raw as unknown as LlmEvent;
    }

    const rawObj = toRecord(raw.raw);
    const streamEvent = String(rawObj?.stream_event || rawObj?.event || raw.kind || '').trim();
    if (!streamEvent) return null;

    const refs = toRecord(raw.refs);
    const role = String(raw.actor || raw.role || rawObj?.role || 'assistant').trim() || 'assistant';
    const fallbackData: Record<string, unknown> = {
      message: String(rawObj?.content || raw.message || '').trim(),
      tool: String(rawObj?.tool || '').trim(),
      args: toRecord(rawObj?.args) || {},
      success: rawObj?.success,
      result: toRecord(rawObj?.result) || rawObj?.result || {},
      error: String(rawObj?.error || '').trim(),
      kind: String(raw.kind || '').trim(),
      channel: String(raw.channel || '').trim(),
    };

    const converted: LlmEvent = {
      schema_version: Number(raw.schema_version || 2),
      event_id: String(raw.event_id || `fe-${++_parseSeq}`),
      run_id: String(raw.run_id || ''),
      iteration: Number(refs?.iteration || 0),
      role,
      ts: String(raw.ts || new Date().toISOString()),
      seq: Number(raw.seq || 0),
      source: normalizeSource(raw.source),
      event: streamEvent,
      data: fallbackData,
    };
    return converted;
  } catch {
    return null;
  }
}

export function parseLlmEventLines(lines: string[]): LlmEvent[] {
  const events: LlmEvent[] = [];
  for (const line of lines) {
    const ev = parseLlmEventLine(line);
    if (ev) events.push(ev);
  }
  return events;
}

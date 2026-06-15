import { describe, it, expect } from 'vitest';

import {
  parseObservationLog,
  telemetryRoleTokens,
  telemetryRoleEvents,
  EMPTY_TELEMETRY,
} from './contextOSTelemetry';

/**
 * 真实 schema 夹具：取自后端 io_events.emit_event 写入 llm.observations.jsonl 的记录形态。
 *  - prompt_context  = ProjectionEngine 投影事件（携带 context_snapshot 回执）
 *  - llm 调用        = 携带 output.usage + duration_ms
 *  - error           = kind:"error" / ok:false / error 文本
 */
const PROJECTION_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:00Z',
  ts_epoch: 1781856000.0,
  seq: 11,
  event_id: 'e11',
  kind: 'observation',
  actor: 'PM',
  name: 'prompt_context',
  refs: { run_id: 'r1', step: 2 },
  summary: 'Prompt Context Injection',
  ok: true,
  output: { context_hash: 'abc123', context_snapshot: 'runtime/snap/abc123.json' },
});

const PM_CALL_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:01Z',
  ts_epoch: 1781856001.5,
  seq: 12,
  event_id: 'e12',
  kind: 'observation',
  actor: 'PM',
  name: 'llm_call',
  refs: { run_id: 'r1', step: 2, mode: 'pm.planning' },
  summary: 'PM planning call',
  ok: true,
  output: { usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 } },
  duration_ms: 2400,
});

const DIRECTOR_ERROR_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:05Z',
  ts_epoch: 1781856005.0,
  seq: 13,
  event_id: 'e13',
  kind: 'error',
  actor: 'Director',
  name: 'llm_failed',
  refs: { mode: 'director.execution' },
  summary: 'provider exploded',
  ok: false,
  error: 'provider 500',
});

const DIRECTOR_CALL_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:06Z',
  ts_epoch: 1781856006.0,
  seq: 14,
  event_id: 'e14',
  kind: 'observation',
  actor: 'Director',
  name: 'llm_call',
  refs: { mode: 'director.execution' },
  ok: true,
  output: { usage: { prompt_tokens: 800, completion_tokens: 200, total_tokens: 1000 } },
  duration_ms: 1800,
});

const FIXTURE_LOG = [
  PROJECTION_LINE,
  PM_CALL_LINE,
  '   ', // blank line tolerated
  '{not valid json', // malformed line skipped
  DIRECTOR_ERROR_LINE,
  DIRECTOR_CALL_LINE,
  '',
].join('\n');

describe('parseObservationLog', () => {
  it('returns EMPTY_TELEMETRY for empty / whitespace content', () => {
    expect(parseObservationLog('')).toBe(EMPTY_TELEMETRY);
    expect(parseObservationLog('   \n  \n')).toBe(EMPTY_TELEMETRY);
    expect(parseObservationLog(null)).toBe(EMPTY_TELEMETRY);
  });

  it('skips malformed lines and parses the valid ones', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.hasData).toBe(true);
    // 4 valid JSON records (blank + malformed skipped).
    expect(t.events).toHaveLength(4);
    expect(t.parsedLines).toBe(4);
  });

  it('aggregates real tokens, calls, projections, receipts and errors', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.totalCalls).toBe(2); // two usage-bearing observations
    expect(t.totalTokens).toBe(2500);
    expect(t.promptTokens).toBe(2000);
    expect(t.completionTokens).toBe(500);
    expect(t.projectionCount).toBe(1); // prompt_context
    expect(t.receiptCount).toBe(1); // context_snapshot present
    expect(t.errorCount).toBe(1);
  });

  it('computes latency aggregates from duration_ms', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.avgLatencyMs).toBe(2100); // (2400 + 1800) / 2
    // Newest event with a duration is the Director call (10:00:06, 1800ms).
    expect(t.lastLatencyMs).toBe(1800);
  });

  it('orders events strictly newest-first by epoch', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.events.map((e) => e.id)).toEqual(['e14', 'e13', 'e12', 'e11']);
  });

  it('flags projection and receipt fields on the right events', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    const projection = t.events.find((e) => e.id === 'e11');
    expect(projection?.isProjection).toBe(true);
    expect(projection?.hasReceipt).toBe(true);
    expect(projection?.category).toBe('projection');
    const error = t.events.find((e) => e.id === 'e13');
    expect(error?.category).toBe('error');
    expect(error?.error).toBe('provider 500');
    const call = t.events.find((e) => e.id === 'e12');
    expect(call?.hasUsage).toBe(true);
    expect(call?.totalTokens).toBe(1500);
    expect(call?.durationMs).toBe(2400);
    expect(call?.mode).toBe('pm.planning');
  });

  it('aggregates by mode and by actor', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.byMode['pm.planning']).toEqual({ totalTokens: 1500, calls: 1 });
    expect(t.byMode['director.execution']).toEqual({ totalTokens: 1000, calls: 1 });
    expect(t.byActor['PM']).toEqual({ totalTokens: 1500, calls: 1, events: 2 });
    expect(t.byActor['Director']).toEqual({ totalTokens: 1000, calls: 1, events: 2 });
  });
});

describe('telemetry role helpers', () => {
  const t = parseObservationLog(FIXTURE_LOG);

  it('maps actor tokens onto role ids', () => {
    expect(telemetryRoleTokens(t, 'pm')).toBe(1500);
    expect(telemetryRoleTokens(t, 'director')).toBe(1000);
    expect(telemetryRoleTokens(t, 'qa')).toBe(0);
  });

  it('maps actor event counts onto role ids', () => {
    expect(telemetryRoleEvents(t, 'pm')).toBe(2);
    expect(telemetryRoleEvents(t, 'director')).toBe(2);
    expect(telemetryRoleEvents(t, 'architect')).toBe(0);
  });
});

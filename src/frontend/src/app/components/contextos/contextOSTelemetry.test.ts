import { describe, it, expect } from 'vitest';

import {
  parseObservationLog,
  telemetryRoleTokens,
  telemetryRoleEvents,
  telemetryRoleHasUsageChannel,
  EMPTY_TELEMETRY,
} from './contextOSTelemetry';

/**
 * 真实 schema 夹具：取自后端 io_events.emit_event 写入 llm.observations.jsonl 的记录形态。
 *  - context.build   = ContextEngine 装配 ContextPack（全角色，actor=System，含 items_count/total_tokens）
 *  - context.snapshot= 落盘上下文快照（actor=System，output.snapshot_path）—— 唯一的回执事实来源
 *  - prompt_context  = PM 规划路径的提示注入（actor=PM）
 *  - llm_invoke      = 携带 output.usage（含 estimated 标志）+ output.duration_ms 的 LLM 调用
 *  - error           = kind:"error" / ok:false / error 文本
 */
const CONTEXT_BUILD_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:00.0Z',
  ts_epoch: 1781856000.0,
  seq: 10,
  event_id: 'e10',
  kind: 'observation',
  actor: 'System',
  name: 'context.build',
  refs: { run_id: 'r1', step: 2, phase: 'director.execution' },
  summary: 'ContextPack built (7 items)',
  ok: true,
  output: { request_hash: 'rh1', items_count: 7, total_tokens: 3400, snapshot_path: 'runtime/snap/abc123.json' },
});

const PROJECTION_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:00.5Z',
  ts_epoch: 1781856000.5,
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

const SNAPSHOT_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:00.8Z',
  ts_epoch: 1781856000.8,
  seq: 12,
  event_id: 'e11b',
  kind: 'observation',
  actor: 'System',
  name: 'context.snapshot',
  refs: { run_id: 'r1', step: 2 },
  summary: 'Context snapshot stored',
  ok: true,
  output: { request_hash: 'rh1', snapshot_path: 'runtime/snap/abc123.json', snapshot_hash: 'sh1' },
});

const PM_CALL_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:01.5Z',
  ts_epoch: 1781856001.5,
  seq: 13,
  event_id: 'e12',
  kind: 'observation',
  actor: 'PM',
  name: 'llm_invoke',
  refs: { run_id: 'r1', step: 2, mode: 'pm.planning' },
  summary: 'PM planning call',
  ok: true,
  // 真实 llm_invoke：时延在 output.duration_ms（非顶层），usage 为真实（estimated 缺省 false）。
  output: { usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 }, duration_ms: 2400 },
});

const DIRECTOR_ERROR_LINE = JSON.stringify({
  schema_version: 1,
  ts: '2026-06-15T10:00:05Z',
  ts_epoch: 1781856005.0,
  seq: 14,
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
  seq: 15,
  event_id: 'e14',
  kind: 'observation',
  actor: 'Director',
  name: 'llm_invoke',
  refs: { mode: 'director.execution' },
  ok: true,
  // 字符估算的 usage：后端打 estimated=true，UI 应据此计入 estimatedCalls。
  output: { usage: { prompt_tokens: 800, completion_tokens: 200, total_tokens: 1000, estimated: true }, duration_ms: 1800 },
});

const FIXTURE_LOG = [
  CONTEXT_BUILD_LINE,
  PROJECTION_LINE,
  SNAPSHOT_LINE,
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
    // 6 valid JSON records (blank + malformed skipped).
    expect(t.events).toHaveLength(6);
    expect(t.parsedLines).toBe(6);
  });

  it('aggregates real tokens, calls, projections, receipts and errors', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.totalCalls).toBe(2); // two usage-bearing observations
    expect(t.estimatedCalls).toBe(1); // director call is char-estimated
    expect(t.totalTokens).toBe(2500);
    expect(t.promptTokens).toBe(2000);
    expect(t.completionTokens).toBe(500);
    expect(t.projectionCount).toBe(2); // context.build + prompt_context
    expect(t.receiptCount).toBe(1); // only the canonical context.snapshot
    expect(t.errorCount).toBe(1);
  });

  it('surfaces the latest context.build assembly size (items + tokens)', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.contextItemsCount).toBe(7);
    expect(t.contextTokensLatest).toBe(3400);
  });

  it('computes latency aggregates from output.duration_ms (llm_invoke)', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.avgLatencyMs).toBe(2100); // (2400 + 1800) / 2
    // Newest event with a duration is the Director call (10:00:06, 1800ms).
    expect(t.lastLatencyMs).toBe(1800);
  });

  it('orders events strictly newest-first by epoch', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.events.map((e) => e.id)).toEqual(['e14', 'e13', 'e12', 'e11b', 'e11', 'e10']);
  });

  it('flags projection / receipt / context-build fields on the right events', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    const build = t.events.find((e) => e.id === 'e10');
    expect(build?.isProjection).toBe(true); // context.build counts as a projection/assembly
    expect(build?.category).toBe('projection');
    expect(build?.contextItems).toBe(7);
    expect(build?.hasReceipt).toBe(false); // build is not a snapshot receipt

    const promptCtx = t.events.find((e) => e.id === 'e11');
    expect(promptCtx?.isProjection).toBe(true);
    // PM prompt_context carries context_snapshot key, but receipts are counted only from
    // the canonical context.snapshot event to avoid double-counting → hasReceipt is false here.
    expect(promptCtx?.hasReceipt).toBe(false);

    const snapshot = t.events.find((e) => e.id === 'e11b');
    expect(snapshot?.hasReceipt).toBe(true); // canonical context.snapshot

    const error = t.events.find((e) => e.id === 'e13');
    expect(error?.category).toBe('error');
    expect(error?.error).toBe('provider 500');

    const call = t.events.find((e) => e.id === 'e12');
    expect(call?.hasUsage).toBe(true);
    expect(call?.estimatedTokens).toBe(false);
    expect(call?.totalTokens).toBe(1500);
    expect(call?.durationMs).toBe(2400); // recovered from output.duration_ms
    expect(call?.mode).toBe('pm.planning');

    const dirCall = t.events.find((e) => e.id === 'e14');
    expect(dirCall?.estimatedTokens).toBe(true);
  });

  it('aggregates by mode and by actor', () => {
    const t = parseObservationLog(FIXTURE_LOG);
    expect(t.byMode['pm.planning']).toEqual({ totalTokens: 1500, calls: 1 });
    expect(t.byMode['director.execution']).toEqual({ totalTokens: 1000, calls: 1 });
    expect(t.byActor['PM']).toEqual({ totalTokens: 1500, calls: 1, events: 2 });
    expect(t.byActor['Director']).toEqual({ totalTokens: 1000, calls: 1, events: 2 });
    expect(t.byActor['System']).toEqual({ totalTokens: 0, calls: 0, events: 2 });
  });

  it('marks the read as windowed only when parsed lines reach the read cap', () => {
    expect(parseObservationLog(FIXTURE_LOG).windowed).toBe(false); // no cap passed
    expect(parseObservationLog(FIXTURE_LOG, 100).windowed).toBe(false); // 6 < 100
    expect(parseObservationLog(FIXTURE_LOG, 6).windowed).toBe(true); // 6 >= 6 → tail window
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

  it('reports a real usage channel only for roles that emit usage-bearing observations', () => {
    expect(telemetryRoleHasUsageChannel(t, 'pm')).toBe(true);
    expect(telemetryRoleHasUsageChannel(t, 'director')).toBe(true);
    // architect/qa/chief_engineer have no usage-bearing observation in the real backend.
    expect(telemetryRoleHasUsageChannel(t, 'architect')).toBe(false);
    expect(telemetryRoleHasUsageChannel(t, 'qa')).toBe(false);
  });
});

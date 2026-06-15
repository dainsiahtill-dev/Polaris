import { describe, it, expect } from 'vitest';

import type { LogEntry } from '@/types/log';
import {
  buildTelemetryFromStream,
  telemetryRoleTokens,
  telemetryRoleEvents,
  telemetryRoleHasUsageChannel,
  EMPTY_TELEMETRY,
} from './contextOSTelemetry';

/**
 * 夹具取自 useRuntime 经 WebSocket 推送、再由 parseLlmStreamLine / parseRuntimeEvent 解析出的
 * LogEntry 形态（source/message/level/details/meta）。buildTelemetryFromStream 从这些**实时**流
 * 派生 ContextOS 遥测——无任何文件轮询。
 */
function logEntry(over: Partial<LogEntry> & { id: string; timestamp: string }): LogEntry {
  return {
    level: 'info',
    source: 'System',
    message: '',
    ...over,
  };
}

// LLM 流（channel=llm）
const INVOKE_DONE: LogEntry = logEntry({
  id: 'llm-done-1',
  timestamp: '2026-06-15T10:00:03Z',
  level: 'success',
  source: 'PM',
  message: 'LLM 响应已返回',
  details: 'backend=minimax chars=120 2400ms',
  meta: { channel: 'llm', streamEvent: 'invoke_done', role: 'PM' },
  tags: ['invoke_done'],
});
const INVOKE_ERROR: LogEntry = logEntry({
  id: 'llm-err-1',
  timestamp: '2026-06-15T10:00:05Z',
  level: 'error',
  source: 'Director',
  message: 'LLM 调用失败: provider 500',
  details: 'backend=local',
  meta: { channel: 'llm', streamEvent: 'invoke_error', role: 'Director' },
  tags: ['invoke_error'],
});
const TOOL_CALL: LogEntry = logEntry({
  id: 'llm-tool-1',
  timestamp: '2026-06-15T10:00:04Z',
  level: 'thinking',
  source: 'Director',
  message: '调用工具: write_file',
  meta: { channel: 'llm', streamEvent: 'tool_call', role: 'Director' },
  tags: ['tool_call'],
});
const THINKING_CHUNK: LogEntry = logEntry({
  id: 'llm-chunk-1',
  timestamp: '2026-06-15T10:00:02Z',
  level: 'thinking',
  source: 'PM',
  message: '正在思考…',
  meta: { channel: 'llm', streamEvent: 'thinking_chunk', role: 'PM' },
  tags: ['thinking_chunk'],
});

// 运行时事件流（channel=runtime_events，emit_event 经总线推送）
const CONTEXT_BUILD: LogEntry = logEntry({
  id: 'rt-build-1',
  timestamp: '2026-06-15T10:00:01Z',
  level: 'info',
  source: 'System',
  message: 'context.build',
  meta: { channel: 'runtime_events' },
});
const RUNTIME_ERROR: LogEntry = logEntry({
  id: 'rt-fail-1',
  timestamp: '2026-06-15T10:00:06Z',
  level: 'error',
  source: 'Director',
  message: '任务执行失败',
  meta: { channel: 'runtime_events' },
});

// 进程流（channel=process）
const PROCESS_LINE: LogEntry = logEntry({
  id: 'proc-1',
  timestamp: '2026-06-15T10:00:00Z',
  level: 'info',
  source: 'Process',
  message: 'director process spawned',
  meta: { channel: 'process' },
});

const LLM_STREAM = [INVOKE_DONE, INVOKE_ERROR, TOOL_CALL, THINKING_CHUNK];
const EXECUTION = [CONTEXT_BUILD, RUNTIME_ERROR];
const PROCESS = [PROCESS_LINE];

describe('buildTelemetryFromStream', () => {
  it('returns EMPTY_TELEMETRY when every stream is empty / nullish', () => {
    expect(buildTelemetryFromStream([], [], [])).toBe(EMPTY_TELEMETRY);
    expect(buildTelemetryFromStream(null, undefined, null)).toBe(EMPTY_TELEMETRY);
  });

  it('derives real telemetry from the live WS streams', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.hasData).toBe(true);
    // thinking_chunk 是流式噪声，被排除；其余 6 条进入聚合。
    expect(t.events).toHaveLength(6);
    expect(t.parsedLines).toBe(6);
  });

  it('counts discrete LLM calls from invoke_done / invoke_error (not chunks)', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.totalCalls).toBe(2); // invoke_done + invoke_error
  });

  it('counts errors from error-level / invoke_error events', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.errorCount).toBe(2); // invoke_error + runtime 任务执行失败
  });

  it('counts context-assembly events as projections', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.projectionCount).toBe(1); // context.build
  });

  it('recovers latency from invoke_done details', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.avgLatencyMs).toBe(2400);
    expect(t.lastLatencyMs).toBe(2400);
  });

  it('keeps tokens at zero — the WS stream carries no precise per-call usage (honest)', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.totalTokens).toBe(0);
    expect(t.estimatedCalls).toBe(0);
    expect(t.receiptCount).toBe(0);
    expect(t.contextItemsCount).toBeNull();
  });

  it('recovers structured signals (items_count / snapshot) from runtime_events meta', () => {
    // parseRuntimeEvent 把事件 name 覆盖成 summary，但 meta = data/output 仍保真携带结构化字段。
    const build = logEntry({
      id: 'b1',
      timestamp: '2026-06-15T10:00:01Z',
      source: 'System',
      message: 'ContextPack built (5 items)', // 注意：文本里没有 "context.build"
      meta: { channel: 'runtime_events', request_hash: 'rh', items_count: 5, total_tokens: 3200, snapshot_path: 'runtime/snap/rh.json' },
    });
    const snap = logEntry({
      id: 's1',
      timestamp: '2026-06-15T10:00:02Z',
      source: 'System',
      message: 'Context snapshot stored',
      meta: { channel: 'runtime_events', request_hash: 'rh', snapshot_path: 'runtime/snap/rh.json', snapshot_hash: 'sh1' },
    });
    const t = buildTelemetryFromStream([], [build, snap], []);
    expect(t.projectionCount).toBe(1); // build via items_count; snapshot is a receipt, not a projection
    expect(t.receiptCount).toBe(1); // snapshot_hash signature
    expect(t.contextItemsCount).toBe(5);
    expect(t.contextTokensLatest).toBe(3200);
  });

  it('classifies each event into the right category', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    const byId = Object.fromEntries(t.events.map((e) => [e.id, e]));
    expect(byId['llm-done-1'].category).toBe('call');
    expect(byId['llm-done-1'].isCall).toBe(true);
    expect(byId['llm-done-1'].durationMs).toBe(2400);
    expect(byId['llm-err-1'].category).toBe('error');
    expect(byId['llm-tool-1'].category).toBe('tool');
    expect(byId['rt-build-1'].category).toBe('projection');
    expect(byId['rt-build-1'].isProjection).toBe(true);
    expect(byId['llm-chunk-1']).toBeUndefined(); // chunk excluded
  });

  it('orders events strictly newest-first by epoch', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.events[0].id).toBe('rt-fail-1'); // 10:00:06 newest
    expect(t.events[t.events.length - 1].id).toBe('proc-1'); // 10:00:00 oldest
  });

  it('aggregates events by actor', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.byActor['PM']).toEqual({ totalTokens: 0, calls: 1, events: 1 });
    expect(t.byActor['Director']).toEqual({ totalTokens: 0, calls: 1, events: 3 });
    expect(t.byActor['System']).toEqual({ totalTokens: 0, calls: 0, events: 1 });
  });

  it('flags windowed when a stream reaches its ring-buffer cap', () => {
    expect(buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS).windowed).toBe(false);
    const bigExecution = Array.from({ length: 100 }, (_, i) =>
      logEntry({ id: `rt-${i}`, timestamp: '2026-06-15T10:00:00Z', source: 'System', message: 'tick', meta: { channel: 'runtime_events' } }),
    );
    expect(buildTelemetryFromStream([], bigExecution, []).windowed).toBe(true);
  });
});

describe('telemetry role helpers', () => {
  const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);

  it('maps actor event counts onto role ids', () => {
    expect(telemetryRoleEvents(t, 'pm')).toBe(1);
    expect(telemetryRoleEvents(t, 'director')).toBe(3);
    expect(telemetryRoleEvents(t, 'qa')).toBe(0);
  });

  it('reports zero role tokens — WS stream has no precise usage', () => {
    expect(telemetryRoleTokens(t, 'pm')).toBe(0);
    expect(telemetryRoleTokens(t, 'director')).toBe(0);
  });

  it('reports no usage channel for any role over the realtime stream', () => {
    expect(telemetryRoleHasUsageChannel(t, 'pm')).toBe(false);
    expect(telemetryRoleHasUsageChannel(t, 'director')).toBe(false);
    expect(telemetryRoleHasUsageChannel(t, 'qa')).toBe(false);
  });
});

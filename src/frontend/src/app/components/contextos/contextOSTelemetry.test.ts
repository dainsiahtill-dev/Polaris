import { describe, it, expect } from 'vitest';

import type { LogEntry } from '@/types/log';
import {
  buildTelemetryFromStream,
  filterEventsForRole,
  telemetryRoleTokens,
  telemetryRoleEvents,
  telemetryRoleCalls,
  telemetryRoleHasUsageChannel,
  EMPTY_TELEMETRY,
} from './contextOSTelemetry';

/**
 * 夹具取自 useRuntime 经 WebSocket 推送、再由 parseLlmStreamLine / parseRuntimeEvent 解析出的
 * LogEntry 形态（source/message/level/details/meta）。buildTelemetryFromStream 从这些**实时**流
 * 派生 ContextOS 遥测——无任何文件轮询。
 *
 * 关键：journal `llm` 通道（CanonicalLogEventV2）的真实词汇是 llm_waiting / llm_completed /
 * llm_failed，且真实 per-call usage（prompt/completion_tokens）与时延（metadata.elapsed_ms）经
 * parseLlmStreamLine 注入 LogEntry.meta（promptTokens/completionTokens/totalTokens/durationMs）。
 * 这些夹具据此构造，验证实时遥测据实呈现真实 token 与时延。
 */
function logEntry(over: Partial<LogEntry> & { id: string; timestamp: string }): LogEntry {
  return {
    level: 'info',
    source: 'System',
    message: '',
    ...over,
  };
}

// LLM 流（channel=llm）— 规范 journal 词汇 + 真实 meta token / 时延。
const LLM_COMPLETED: LogEntry = logEntry({
  id: 'llm-done-1',
  timestamp: '2026-06-15T10:00:03Z',
  level: 'success',
  source: 'PM',
  message: 'llm response completed | completion_tokens=1454',
  details: 'model=MiniMax-M3 prompt=1932 completion=1454 71431ms',
  meta: {
    channel: 'llm',
    streamEvent: 'llm_completed',
    role: 'PM',
    model: 'MiniMax-M3',
    promptTokens: 1932,
    completionTokens: 1454,
    totalTokens: 3386,
    contextTokens: 1932,
    durationMs: 71431,
  },
  tags: ['llm_completed'],
});
const LLM_FAILED: LogEntry = logEntry({
  id: 'llm-err-1',
  timestamp: '2026-06-15T10:00:05Z',
  level: 'error',
  source: 'Director',
  message: 'LLM 调用失败: provider 500',
  details: 'model=local 1200ms',
  meta: { channel: 'llm', streamEvent: 'llm_failed', role: 'Director', model: 'local', durationMs: 1200 },
  tags: ['llm_failed'],
});
const LLM_WAITING: LogEntry = logEntry({
  id: 'llm-wait-1',
  timestamp: '2026-06-15T10:00:02Z',
  level: 'thinking',
  source: 'PM',
  message: '正在请求 MiniMax-M3 响应…',
  meta: { channel: 'llm', streamEvent: 'llm_waiting', role: 'PM' },
  tags: ['llm_waiting'],
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
  timestamp: '2026-06-15T10:00:01Z',
  level: 'thinking',
  source: 'PM',
  message: '正在思考…',
  meta: { channel: 'llm', streamEvent: 'thinking_chunk', role: 'PM' },
  tags: ['thinking_chunk'],
});

// 运行时事件流（channel=runtime_events，emit_event 经总线推送）。真实弱模型 run 发 prompt_context。
const PROMPT_CONTEXT: LogEntry = logEntry({
  id: 'rt-build-1',
  timestamp: '2026-06-15T10:00:00Z',
  level: 'info',
  source: 'PM',
  // 真实形态：parseRuntimeEvent 把事件 name=prompt_context 覆盖为 summary「Prompt Context Injection」，
  // 但 meta(=output) 保真携带 persona_id / strategy 等投影签名（真实 run 的 prompt_context 输出）。
  message: 'Prompt Context Injection',
  meta: {
    channel: 'runtime_events',
    run_id: 'pm-00001',
    phase: 'pm.planning',
    persona_id: 'pm.v1',
    strategy: 'combined_ranking',
    token_usage_estimate: 0,
  },
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
  timestamp: '2026-06-15T09:59:59Z',
  level: 'info',
  source: 'Process',
  message: 'director process spawned',
  meta: { channel: 'process' },
});

const LLM_STREAM = [LLM_COMPLETED, LLM_FAILED, LLM_WAITING, TOOL_CALL, THINKING_CHUNK];
const EXECUTION = [PROMPT_CONTEXT, RUNTIME_ERROR];
const PROCESS = [PROCESS_LINE];

describe('buildTelemetryFromStream', () => {
  it('returns EMPTY_TELEMETRY when every stream is empty / nullish', () => {
    expect(buildTelemetryFromStream([], [], [])).toBe(EMPTY_TELEMETRY);
    expect(buildTelemetryFromStream(null, undefined, null)).toBe(EMPTY_TELEMETRY);
  });

  it('derives real telemetry from the live WS streams', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.hasData).toBe(true);
    // thinking_chunk 与 llm_waiting 是流式/状态噪声，被排除；其余 6 条进入聚合。
    // (llm_completed, llm_failed, tool_call, prompt_context, runtime_error, process_line)
    expect(t.events).toHaveLength(6);
    expect(t.parsedLines).toBe(6);
  });

  it('counts discrete LLM calls from the canonical journal vocabulary (llm_completed / llm_failed)', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.totalCalls).toBe(2); // llm_completed + llm_failed
  });

  it('still counts legacy invoke_done / invoke_error calls (back-compat)', () => {
    const legacyDone = logEntry({
      id: 'legacy-done',
      timestamp: '2026-06-15T10:00:03Z',
      level: 'success',
      source: 'PM',
      message: 'LLM 响应已返回',
      details: 'backend=minimax chars=120 2400ms',
      meta: { channel: 'llm', streamEvent: 'invoke_done', role: 'PM' },
      tags: ['invoke_done'],
    });
    const t = buildTelemetryFromStream([legacyDone], [], []);
    expect(t.totalCalls).toBe(1);
    expect(t.lastLatencyMs).toBe(2400); // recovered from details
  });

  it('counts errors from error-level / llm_failed events', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.errorCount).toBe(2); // llm_failed + runtime 任务执行失败
  });

  it('counts context-assembly events (prompt_context) as projections', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.projectionCount).toBe(1); // prompt_context
  });

  it('aggregates ContextOS main tokens from final/context request size while preserving usage split', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.totalTokens).toBe(1932); // contextTokens is the request-side context size
    expect(t.promptTokens).toBe(1932);
    expect(t.completionTokens).toBe(1454);
    expect(t.estimatedCalls).toBe(0); // journal usage is real, never char-estimated
  });

  it('deduplicates repeated copies of the same LLM completion before aggregating', () => {
    const duplicated = Array.from({ length: 5 }, (_, index) => ({
      ...LLM_COMPLETED,
      id: `duplicate-id-${index}`,
    }));
    const t = buildTelemetryFromStream(duplicated, [], []);

    expect(t.events).toHaveLength(1);
    expect(t.parsedLines).toBe(1);
    expect(t.totalCalls).toBe(1);
    expect(t.totalTokens).toBe(1932);
    expect(t.promptTokens).toBe(1932);
    expect(t.completionTokens).toBe(1454);
    expect(filterEventsForRole(t.events, 'pm')).toHaveLength(1);
  });

  it('recovers real latency from meta.durationMs (raw.data.metadata.elapsed_ms)', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    // latencies present: completed 71431ms, failed 1200ms → avg 36316 (rounded)
    expect(t.avgLatencyMs).toBe(36316);
    // newest event WITH latency is llm_failed (10:00:05) → 1200ms
    expect(t.lastLatencyMs).toBe(1200);
  });

  it('surfaces context size (context_tokens_after) on completed calls', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.contextTokensLatest).toBe(1932);
  });

  it('recovers runtime_events role LLM usage from event_type and snake_case fields', () => {
    const pmRoleCall = logEntry({
      id: 'rt-pm-llm-end',
      timestamp: '2026-06-21T22:16:12Z',
      level: 'success',
      source: 'pm',
      message: 'llm_call_end',
      meta: {
        channel: 'runtime_events',
        event_type: 'llm_call_end',
        role: 'pm',
        model: 'kimi-for-coding',
        prompt_tokens: 2732,
        completion_tokens: 1954,
        context_tokens_after: 2732,
        call_id: 'call-pm-1',
        metadata: {
          elapsed_ms: 19177.76,
          context_snapshot_ref: 'e3db3551d74e5741fd664b7b',
          final_request_context_audit: {
            final_request_token_estimate: 4096,
            tool_schema_token_estimate: 1200,
            coverage: {
              has_pm_contract: true,
              has_chief_engineer_blueprint: true,
              has_target_files: true,
            },
          },
        },
      },
    });
    const t = buildTelemetryFromStream([], [pmRoleCall], []);

    expect(t.totalCalls).toBe(1);
    expect(t.totalTokens).toBe(4096);
    expect(t.promptTokens).toBe(2732);
    expect(t.completionTokens).toBe(1954);
    expect(t.contextTokensLatest).toBe(4096);
    expect(t.lastLatencyMs).toBe(19178);
    expect(telemetryRoleTokens(t, 'pm')).toBe(4096);
    expect(telemetryRoleHasUsageChannel(t, 'pm')).toBe(true);
    expect(t.events[0].contextSnapshotRef).toBe('e3db3551d74e5741fd664b7b');
    expect(t.events[0].callId).toBe('call-pm-1');
    expect(t.events[0].finalRequestTokenEstimate).toBe(4096);
    expect(t.events[0].finalRequestContextAudit).toMatchObject({
      final_request_token_estimate: 4096,
      tool_schema_token_estimate: 1200,
      coverage: { has_chief_engineer_blueprint: true },
    });
  });

  it('recovers provider-native usage aliases from nested usage payloads', () => {
    const directorCall = logEntry({
      id: 'rt-director-usage-alias',
      timestamp: '2026-06-21T22:18:12Z',
      level: 'success',
      source: 'director',
      message: 'llm_call_end',
      meta: {
        channel: 'runtime_events',
        event_type: 'llm_call_end',
        role: 'director',
        usage: {
          input_tokens: 3210,
          output_tokens: 456,
          total_tokens: 3666,
        },
        context_tokens_after: 3210,
      },
    });
    const t = buildTelemetryFromStream([], [directorCall], []);

    expect(t.totalCalls).toBe(1);
    expect(t.promptTokens).toBe(3210);
    expect(t.completionTokens).toBe(456);
    expect(t.totalTokens).toBe(3210);
    expect(telemetryRoleTokens(t, 'director')).toBe(3210);
    expect(telemetryRoleHasUsageChannel(t, 'director')).toBe(true);
  });

  it('does not count content_preview completion tokens as final provider usage', () => {
    const preview = logEntry({
      id: 'rt-pm-preview',
      timestamp: '2026-06-21T22:16:11Z',
      level: 'info',
      source: 'pm',
      message: 'content_preview',
      meta: {
        channel: 'runtime_events',
        event_type: 'content_preview',
        role: 'pm',
        completion_tokens: 1954,
        metadata: {
          context_snapshot_ref: 'e3db3551d74e5741fd664b7b',
        },
      },
    });
    const done = logEntry({
      id: 'rt-pm-done',
      timestamp: '2026-06-21T22:16:12Z',
      level: 'success',
      source: 'pm',
      message: 'llm_call_end',
      meta: {
        channel: 'runtime_events',
        event_type: 'llm_call_end',
        role: 'pm',
        prompt_tokens: 2732,
        completion_tokens: 1954,
        context_tokens_after: 2732,
      },
    });
    const t = buildTelemetryFromStream([], [preview, done], []);

    expect(t.totalCalls).toBe(1);
    expect(t.promptTokens).toBe(2732);
    expect(t.completionTokens).toBe(1954);
    expect(t.totalTokens).toBe(2732);
    expect(telemetryRoleTokens(t, 'pm')).toBe(2732);
  });

  it('does not count llm_call_start prompt tokens as final provider usage while preserving context size', () => {
    const start = logEntry({
      id: 'rt-pm-start',
      timestamp: '2026-06-21T22:16:10Z',
      level: 'info',
      source: 'pm',
      message: 'llm_call_start',
      meta: {
        channel: 'runtime_events',
        event_type: 'llm_call_start',
        role: 'pm',
        prompt_tokens: 2732,
        context_tokens_before: 2732,
        call_id: 'call-start-1',
      },
    });
    const t = buildTelemetryFromStream([], [start], []);

    expect(t.totalCalls).toBe(0);
    expect(t.promptTokens).toBe(0);
    expect(t.completionTokens).toBe(0);
    expect(t.totalTokens).toBe(0);
    expect(t.contextTokensLatest).toBe(2732);
    expect(t.events[0].hasUsage).toBe(false);
    expect(t.events[0].callId).toBe('call-start-1');
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
    expect(t.totalCalls).toBe(0);
    expect(t.totalTokens).toBe(0); // context.build total_tokens is context size, not LLM usage

    const nestedBuild = logEntry({
      id: 'b2',
      timestamp: '2026-06-15T10:00:03Z',
      source: 'System',
      message: 'ContextPack built',
      meta: {
        channel: 'runtime_events',
        output: {
          request_hash: 'rh2',
          items_count: 7,
          total_tokens: 4100,
          snapshot_path: 'runtime/snap/rh2.json',
        },
      },
    });
    const nested = buildTelemetryFromStream([], [nestedBuild], []);
    expect(nested.projectionCount).toBe(1);
    expect(nested.contextItemsCount).toBe(7);
    expect(nested.contextTokensLatest).toBe(4100);
  });

  it('surfaces contextSnapshotRef, promptHash, and turnId from meta fields', () => {
    const entry = logEntry({
      id: 'llm-ctx-1',
      timestamp: '2026-06-19T10:00:00Z',
      level: 'success',
      source: 'PM',
      message: 'llm response completed',
      meta: {
        channel: 'llm',
        streamEvent: 'llm_completed',
        role: 'PM',
        promptTokens: 1000,
        completionTokens: 500,
        totalTokens: 1500,
        contextSnapshotRef: 'a1b2c3d4e5f6a7b8c9d0e1f2',
        promptHash: 'f2e1d0c9b8a7',
        turnId: 'turn-42',
        callId: 'call-42',
      },
    });
    const t = buildTelemetryFromStream([entry], [], []);
    expect(t.events).toHaveLength(1);
    const event = t.events[0];
    expect(event.contextSnapshotRef).toBe('a1b2c3d4e5f6a7b8c9d0e1f2');
    expect(event.promptHash).toBe('f2e1d0c9b8a7');
    expect(event.turnId).toBe('turn-42');
    expect(event.callId).toBe('call-42');
  });

  it('accepts snake_case meta aliases for context snapshot fields and call id', () => {
    const entry = logEntry({
      id: 'llm-ctx-2',
      timestamp: '2026-06-19T10:00:01Z',
      level: 'success',
      source: 'Director',
      message: 'llm response completed',
      meta: {
        channel: 'llm',
        streamEvent: 'llm_completed',
        role: 'Director',
        context_snapshot_ref: 'abc123def456',
        prompt_hash: 'hash789',
        turn_id: 'turn-99',
        call_id: 'call-99',
      },
    });
    const t = buildTelemetryFromStream([entry], [], []);
    const event = t.events[0];
    expect(event.contextSnapshotRef).toBe('abc123def456');
    expect(event.promptHash).toBe('hash789');
    expect(event.turnId).toBe('turn-99');
    expect(event.callId).toBe('call-99');
  });

  it('surfaces context snapshot degraded evidence without leaking objects', () => {
    const entry = logEntry({
      id: 'llm-ctx-degraded',
      timestamp: '2026-06-19T10:00:01Z',
      level: 'success',
      source: 'Director',
      message: 'llm response completed',
      meta: {
        channel: 'llm',
        streamEvent: 'llm_completed',
        role: 'Director',
        promptTokens: 100,
        completionTokens: 50,
        totalTokens: 150,
        context_snapshot_degraded: {
          code: 'CONTEXT_STORE_WRITE_FAILED',
          reason: 'context_snapshot_store_failure',
          message: 'disk full',
          exception_type: 'OSError',
        },
      },
    });
    const t = buildTelemetryFromStream([entry], [], []);
    const event = t.events[0];
    expect(event.contextSnapshotRef).toBeNull();
    expect(event.contextSnapshotDegraded).toEqual({
      code: 'CONTEXT_STORE_WRITE_FAILED',
      reason: 'context_snapshot_store_failure',
      message: 'disk full',
      exceptionType: 'OSError',
    });
  });

  it('defaults contextSnapshotRef, promptHash, turnId, and callId to null when absent', () => {
    const entry = logEntry({
      id: 'llm-plain',
      timestamp: '2026-06-19T10:00:02Z',
      level: 'success',
      source: 'PM',
      message: 'llm response completed',
      meta: {
        channel: 'llm',
        streamEvent: 'llm_completed',
        role: 'PM',
        promptTokens: 100,
        completionTokens: 50,
      },
    });
    const t = buildTelemetryFromStream([entry], [], []);
    const event = t.events[0];
    expect(event.contextSnapshotRef).toBeNull();
    expect(event.promptHash).toBeNull();
    expect(event.turnId).toBeNull();
    expect(event.callId).toBeNull();
    expect(event.contextSnapshotDegraded).toBeNull();
  });

  it('uses callId as the strongest dedupe key for repeated completion envelopes', () => {
    const repeatedA = logEntry({
      id: 'rt-call-a',
      timestamp: '2026-06-21T22:17:00Z',
      level: 'success',
      source: 'pm',
      message: 'llm_call_end',
      meta: {
        channel: 'runtime_events',
        event_type: 'llm_call_end',
        role: 'pm',
        call_id: 'stable-call-1',
        prompt_tokens: 1000,
        completion_tokens: 250,
      },
    });
    const repeatedB = logEntry({
      ...repeatedA,
      id: 'rt-call-b',
      timestamp: '2026-06-21T22:17:01Z',
    });
    const t = buildTelemetryFromStream([], [repeatedA, repeatedB], []);

    expect(t.events).toHaveLength(1);
    expect(t.totalCalls).toBe(1);
    expect(t.totalTokens).toBe(1250);
  });

  it('classifies each event into the right category', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    const byId = Object.fromEntries(t.events.map((e) => [e.id, e]));
    expect(byId['llm-done-1'].category).toBe('call');
    expect(byId['llm-done-1'].isCall).toBe(true);
    expect(byId['llm-done-1'].hasUsage).toBe(true);
    expect(byId['llm-done-1'].durationMs).toBe(71431);
    expect(byId['llm-err-1'].category).toBe('error');
    expect(byId['llm-err-1'].isCall).toBe(true); // a failed call is still a discrete call
    expect(byId['llm-tool-1'].category).toBe('tool');
    expect(byId['rt-build-1'].category).toBe('projection');
    expect(byId['rt-build-1'].isProjection).toBe(true);
    expect(byId['llm-chunk-1']).toBeUndefined(); // thinking_chunk excluded
    expect(byId['llm-wait-1']).toBeUndefined(); // llm_waiting (start marker) excluded as state noise
  });

  it('orders events strictly newest-first by epoch', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.events[0].id).toBe('rt-fail-1'); // 10:00:06 newest
    expect(t.events[t.events.length - 1].id).toBe('proc-1'); // 09:59:59 oldest
  });

  it('aggregates events + real tokens by actor', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    // PM: llm_completed (call, 1932 context tok) + prompt_context (projection). llm_waiting/thinking filtered.
    expect(t.byActor['PM']).toEqual({ totalTokens: 1932, calls: 1, events: 2 });
    // Director: llm_failed (call+error) + tool_call + runtime_error
    expect(t.byActor['Director']).toEqual({ totalTokens: 0, calls: 1, events: 3 });
    expect(t.byActor['Process']).toEqual({ totalTokens: 0, calls: 0, events: 1 });
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
    expect(telemetryRoleEvents(t, 'pm')).toBe(2); // llm_completed + prompt_context
    expect(telemetryRoleEvents(t, 'director')).toBe(4); // actor Director + director process hint
    expect(telemetryRoleEvents(t, 'qa')).toBe(0);
  });

  it('attributes Factory Bench events to role signal planes from structured hints', () => {
    const factoryEvents: LogEntry[] = [
      logEntry({
        id: 'bench-start',
        timestamp: '2026-06-18T14:15:19Z',
        source: 'Factory Bench',
        title: 'factory_bench.project.started',
        message: 'L1-01 CLI 科学计算器 starting',
        meta: { channel: 'process', bench_event_type: 'factory_bench.project.started', project_id: 'L1-01', level: 1 },
        tags: ['bench'],
      }),
      logEntry({
        id: 'bench-blueprint',
        timestamp: '2026-06-18T14:18:25Z',
        source: 'Factory Bench',
        title: 'factory_bench.gate.evaluated',
        message: 'L1-01 gate:blueprint_artifact_present=ok',
        meta: { channel: 'process', bench_event_type: 'factory_bench.gate.evaluated', gate: 'blueprint_artifact_present', project_id: 'L1-01', level: 1 },
        tags: ['bench'],
      }),
      logEntry({
        id: 'bench-completed',
        timestamp: '2026-06-18T14:18:26Z',
        source: 'Factory Bench',
        title: 'factory_bench.project.completed',
        message: 'L1-01 exit=1 dur=185.2s',
        meta: { channel: 'process', bench_event_type: 'factory_bench.project.completed', exit_code: 1, project_id: 'L1-01', level: 1 },
        tags: ['bench'],
      }),
      logEntry({
        id: 'bench-qa',
        timestamp: '2026-06-18T14:18:27Z',
        source: 'Factory Bench',
        title: 'factory_bench.gate.evaluated',
        message: 'L1-01 gate:integration_qa_passed=FAIL',
        meta: { channel: 'process', bench_event_type: 'factory_bench.gate.evaluated', gate: 'integration_qa_passed', ok: false, project_id: 'L1-01', level: 1 },
        tags: ['bench'],
      }),
    ];
    const factoryTelemetry = buildTelemetryFromStream([], [], factoryEvents);

    expect(filterEventsForRole(factoryTelemetry.events, 'pm').map((event) => event.id)).toContain('bench-start');
    expect(filterEventsForRole(factoryTelemetry.events, 'chief_engineer').map((event) => event.id)).toContain('bench-blueprint');
    expect(filterEventsForRole(factoryTelemetry.events, 'director').map((event) => event.id)).toContain('bench-completed');
    expect(filterEventsForRole(factoryTelemetry.events, 'qa').map((event) => event.id)).toContain('bench-qa');
    expect(telemetryRoleEvents(factoryTelemetry, 'chief_engineer')).toBe(1);
    expect(telemetryRoleEvents(factoryTelemetry, 'director')).toBe(1);
    expect(telemetryRoleEvents(factoryTelemetry, 'qa')).toBe(1);
  });

  it('reports REAL per-role tokens from the journal llm usage channel', () => {
    expect(telemetryRoleTokens(t, 'pm')).toBe(1932);
    expect(telemetryRoleTokens(t, 'director')).toBe(0); // its call failed, no usage
  });

  it('keeps per-role usage aggregates complete even when display events are truncated', () => {
    const manyCalls: LogEntry[] = Array.from({ length: 130 }, (_, index) =>
      logEntry({
        id: `pm-usage-${index}`,
        timestamp: `2026-06-15T10:${String(Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}Z`,
        level: 'success',
        source: 'PM',
        message: 'llm response completed',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'PM',
          callId: `pm-call-${index}`,
          promptTokens: 10,
          completionTokens: 1,
          totalTokens: 11,
        },
      }),
    );
    const t = buildTelemetryFromStream(manyCalls, [], []);

    expect(t.events).toHaveLength(120);
    expect(t.parsedLines).toBe(130);
    expect(telemetryRoleEvents(t, 'pm')).toBe(130);
    expect(telemetryRoleCalls(t, 'pm')).toBe(130);
    expect(telemetryRoleTokens(t, 'pm')).toBe(1430);
  });

  it('reports a usage channel for roles that produced token-bearing calls', () => {
    expect(telemetryRoleHasUsageChannel(t, 'pm')).toBe(true);
    expect(telemetryRoleHasUsageChannel(t, 'director')).toBe(false);
    expect(telemetryRoleHasUsageChannel(t, 'qa')).toBe(false);
  });
});

describe('Real token verification — not hardcoded estimates', () => {
  it('aggregates real per-call tokens that vary by event, never fixed at 1200', () => {
    // Two calls with DIFFERENT token counts
    const call1 = logEntry({
      id: 'real-1',
      timestamp: '2026-06-15T10:00:01Z',
      level: 'success',
      source: 'PM',
      message: 'llm response completed',
      meta: { channel: 'llm', streamEvent: 'llm_completed', role: 'PM', promptTokens: 5000, completionTokens: 2500, totalTokens: 7500, durationMs: 3000 },
      tags: ['llm_completed'],
    });
    const call2 = logEntry({
      id: 'real-2',
      timestamp: '2026-06-15T10:00:03Z',
      level: 'success',
      source: 'Director',
      message: 'llm response completed',
      meta: { channel: 'llm', streamEvent: 'llm_completed', role: 'Director', promptTokens: 200, completionTokens: 100, totalTokens: 300, durationMs: 800 },
      tags: ['llm_completed'],
    });
    const t = buildTelemetryFromStream([call1, call2], [], []);

    // Total = 7500 + 300 = 7800, NOT 1200
    expect(t.totalTokens).toBe(7800);
    expect(t.totalTokens).not.toBe(1200);

    // Per-role tokens also vary
    expect(telemetryRoleTokens(t, 'pm')).toBe(7500);
    expect(telemetryRoleTokens(t, 'director')).toBe(300);

    // estimatedCalls = 0 because journal usage is real, never char-estimated
    expect(t.estimatedCalls).toBe(0);
  });

  it('reports zero tokens when no event carries usage (not a fake 1.2k)', () => {
    const noUsage = logEntry({
      id: 'no-use',
      timestamp: '2026-06-15T10:00:01Z',
      level: 'success',
      source: 'PM',
      message: 'invoke done',
      meta: { channel: 'llm', streamEvent: 'invoke_done', role: 'PM' },
      tags: ['invoke_done'],
    });
    const t = buildTelemetryFromStream([noUsage], [], []);
    // No usage in meta → totalTokens = 0, NOT 1200
    expect(t.totalTokens).toBe(0);
    expect(t.totalTokens).not.toBe(1200);
    expect(telemetryRoleTokens(t, 'pm')).toBe(0);
  });
});

describe('Phase 3+ multi-worker LLM tracking', () => {
  it('reports hasWorkers=false and an empty byWorker map when no event carries worker_id', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    expect(t.hasWorkers).toBe(false);
    expect(t.byWorker).toEqual({});
  });

  it('extracts workerId from meta.worker_id and meta.workerId, aggregating tokens/calls/latency', () => {
    const w1Call1 = logEntry({
      id: 'w1-call-1',
      timestamp: '2026-06-19T11:00:00Z',
      level: 'success',
      source: 'Director',
      message: 'worker-1 call',
      meta: {
        channel: 'llm',
        streamEvent: 'llm_completed',
        role: 'Director',
        worker_id: 'worker-1',
        promptTokens: 100,
        completionTokens: 50,
        totalTokens: 150,
        durationMs: 800,
      },
    });
    const w1Call2 = logEntry({
      id: 'w1-call-2',
      timestamp: '2026-06-19T11:00:05Z',
      level: 'success',
      source: 'Director',
      message: 'worker-1 second call',
      meta: {
        channel: 'llm',
        streamEvent: 'llm_completed',
        role: 'Director',
        workerId: 'worker-1', // alternate key
        promptTokens: 200,
        completionTokens: 80,
        totalTokens: 280,
        durationMs: 1200,
      },
    });
    const w2Call = logEntry({
      id: 'w2-call-1',
      timestamp: '2026-06-19T11:00:02Z',
      level: 'success',
      source: 'Director',
      message: 'worker-2 call',
      meta: {
        channel: 'llm',
        streamEvent: 'llm_completed',
        role: 'Director',
        worker_id: 'worker-2',
        promptTokens: 60,
        completionTokens: 30,
        totalTokens: 90,
        durationMs: 500,
      },
    });
    const t = buildTelemetryFromStream([w1Call1, w1Call2, w2Call], [], []);
    expect(t.hasWorkers).toBe(true);
    expect(Object.keys(t.byWorker).sort()).toEqual(['worker-1', 'worker-2']);
    expect(t.byWorker['worker-1'].calls).toBe(2);
    expect(t.byWorker['worker-1'].totalTokens).toBe(430);
    expect(t.byWorker['worker-2'].calls).toBe(1);
    expect(t.byWorker['worker-2'].totalTokens).toBe(90);
    // lastLatencyMs tracks the most recent event's duration
    expect(t.byWorker['worker-1'].lastLatencyMs).toBe(1200);
    expect(t.byWorker['worker-2'].lastLatencyMs).toBe(500);
  });

  it('attaches workerId to each derived event so downstream UI can filter', () => {
    const wEvent = logEntry({
      id: 'w-evt-1',
      timestamp: '2026-06-19T11:00:00Z',
      level: 'success',
      source: 'Director',
      message: 'worker-1 call',
      meta: { channel: 'llm', streamEvent: 'llm_completed', worker_id: 'worker-1', promptTokens: 10, completionTokens: 5 },
    });
    const t = buildTelemetryFromStream([wEvent], [], []);
    expect(t.events[0].workerId).toBe('worker-1');
  });

  it('exposes workerId=null for events without meta.worker_id, never fabricates it', () => {
    const t = buildTelemetryFromStream(LLM_STREAM, EXECUTION, PROCESS);
    for (const event of t.events) {
      expect(event.workerId).toBeNull();
    }
  });
});

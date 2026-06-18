import { describe, it, expect } from 'vitest';

import {
  buildContextOSModel,
  decisionMatchesRole,
  contextOSFormat,
  NOMINAL_CONTEXT_WINDOW,
} from './contextOSData';
import { buildTelemetryFromStream } from './contextOSTelemetry';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState } from '@/app/hooks/useLlmRuntimeGate';
import type { SnapshotPayload } from '@/app/types/appContracts';

const READY_LLM: LlmRuntimeGateState = {
  state: 'READY',
  blockedRoles: [],
  requiredRoles: ['pm', 'director'],
  lastUpdated: null,
};

function baseInput(overrides: Partial<Parameters<typeof buildContextOSModel>[0]> = {}) {
  return {
    usageStats: null,
    dialogueEvents: [] as DialogueEvent[],
    executionLogs: [] as LogEntry[],
    snapshot: null,
    llmRuntimeState: READY_LLM,
    currentPhase: 'idle',
    pmRunning: false,
    directorRunning: false,
    ...overrides,
  };
}

describe('buildContextOSModel', () => {
  it('produces a fully idle model from empty inputs without throwing', () => {
    const model = buildContextOSModel(baseInput());
    expect(model.running).toBe(false);
    expect(model.dataIdle).toBe(true);
    expect(model.totalTokens).toBe(0);
    expect(model.pipeline).toHaveLength(8);
    expect(model.components).toHaveLength(7);
    expect(model.roles).toHaveLength(5);
    expect(model.roles.map((r) => r.id)).toEqual(['pm', 'architect', 'chief_engineer', 'director', 'qa']);
    expect(model.decisions).toHaveLength(0);
    // Window occupancy must stay within [0,1].
    expect(model.windowOccupancy).toBeGreaterThanOrEqual(0);
    expect(model.windowOccupancy).toBeLessThanOrEqual(1);
  });

  it('derives real token totals and a prompt/completion budget split', () => {
    const usageStats: UsageStats = {
      totals: { prompt_tokens: 8000, completion_tokens: 2000, total_tokens: 10000 },
      calls: 5,
      estimated_calls: 0,
      by_mode: {
        pm: { total_tokens: 6000, calls: 3 },
        director: { total_tokens: 4000, calls: 2 },
      },
    };
    const model = buildContextOSModel(baseInput({ usageStats, pmRunning: true, currentPhase: 'planning' }));
    expect(model.totalTokens).toBe(10000);
    expect(model.avgPerCall).toBe(2000);
    expect(model.running).toBe(true);
    expect(model.dataIdle).toBe(false);

    const prompt = model.budget.find((b) => b.key === 'prompt');
    const completion = model.budget.find((b) => b.key === 'completion');
    expect(prompt?.ratio).toBeCloseTo(0.8, 5);
    expect(completion?.ratio).toBeCloseTo(0.2, 5);

    // by_mode slices are real, sorted desc by tokens.
    expect(model.byModeSlices[0].key).toBe('pm');
    expect(model.byModeSlices[0].tokens).toBe(6000);

    // Active stage during planning is ProjectionEngine.
    const projection = model.pipeline.find((s) => s.id === 'projection');
    expect(projection?.state).toBe('active');
  });

  it('maps by_mode tokens onto role cards via aliases', () => {
    const usageStats: UsageStats = {
      totals: { prompt_tokens: 100, completion_tokens: 100, total_tokens: 200 },
      calls: 2,
      estimated_calls: 0,
      by_mode: { director: { total_tokens: 200, calls: 2 } },
    };
    const model = buildContextOSModel(baseInput({ usageStats, directorRunning: true }));
    const director = model.roles.find((r) => r.id === 'director');
    expect(director?.tokens).toBe(200);
    expect(director?.state).toBe('active');
  });

  it('flags blocked roles in both the role card and the role-signal stage', () => {
    const blockedLlm: LlmRuntimeGateState = {
      state: 'BLOCKED',
      blockedRoles: ['director'],
      requiredRoles: ['director'],
      lastUpdated: null,
    };
    const model = buildContextOSModel(baseInput({ llmRuntimeState: blockedLlm, directorRunning: true }));
    const director = model.roles.find((r) => r.id === 'director');
    expect(director?.state).toBe('blocked');
    const roleSignal = model.pipeline.find((s) => s.id === 'role_signal');
    expect(roleSignal?.state).toBe('blocked');
    // LLM + Prompt stages go blocked when llmRuntimeState is BLOCKED.
    expect(model.pipeline.find((s) => s.id === 'llm')?.state).toBe('blocked');
  });

  it('counts errors and parses last latency from execution logs', () => {
    const logs: LogEntry[] = [
      { id: 'l1', timestamp: '2026-06-15T10:00:00Z', level: 'info', source: 'director', message: 'invoke done in 1234 ms' },
      { id: 'l2', timestamp: '2026-06-15T10:00:01Z', level: 'error', source: 'director', message: 'boom' },
    ];
    const model = buildContextOSModel(baseInput({ executionLogs: logs }));
    expect(model.errorCount).toBe(1);
    expect(model.lastLatencyMs).toBe(1234);
    // Telemetry component reflects the error.
    expect(model.components.find((c) => c.id === 'telemetry')?.state).toBe('blocked');
  });

  it('builds newest-first decision rows from dialogue + logs', () => {
    const dialogue: DialogueEvent[] = [
      { speaker: 'PM', content: 'plan ready', timestamp: '2026-06-15T10:00:00Z', type: 'message' },
      { speaker: 'Director', content: 'executing task', timestamp: '2026-06-15T10:00:05Z', type: 'progress' },
    ];
    const model = buildContextOSModel(baseInput({ dialogueEvents: dialogue }));
    expect(model.decisions.length).toBeGreaterThan(0);
    // Newest first.
    expect(model.decisions[0].actor).toBe('Director');
  });

  it('orders the decision log strictly newest-first when dialogue and logs interleave by time', () => {
    // A log timestamped BETWEEN two dialogue events must sort between them,
    // not above both (regression guard for the old `.reverse()` ordering bug).
    const dialogueEvents: DialogueEvent[] = [
      { speaker: 'PM', content: 'oldest dialogue', timestamp: '2026-06-15T10:00:00Z', type: 'message' },
      { speaker: 'Director', content: 'newest dialogue', timestamp: '2026-06-15T10:00:30Z', type: 'message' },
    ];
    const executionLogs: LogEntry[] = [
      { id: 'lg', timestamp: '2026-06-15T10:00:15Z', level: 'info', source: 'runtime', message: 'middle log' },
    ];
    const model = buildContextOSModel(baseInput({ dialogueEvents, executionLogs }));
    expect(model.decisions.map((d) => d.summary)).toEqual(['newest dialogue', 'middle log', 'oldest dialogue']);
  });

  it('derives iteration and task count from the runtime snapshot', () => {
    const snapshot = {
      tasks: [{ id: 't1' }, { id: 't2' }, { id: 't3' }],
      pm_state: { pm_iteration: 7 },
    } as unknown as SnapshotPayload;
    const model = buildContextOSModel(baseInput({ snapshot }));
    expect(model.taskCount).toBe(3);
    expect(model.iteration).toBe(7);
    expect(model.pipeline.find((s) => s.id === 'request')?.metric).toContain('3 任务');
  });
});

describe('buildContextOSModel with real WS telemetry', () => {
  // 夹具取自 useRuntime 经 WebSocket 推送、再解析出的 LogEntry 流（无文件轮询）。
  function wsLog(over: Partial<LogEntry> & { id: string; timestamp: string }): LogEntry {
    return { level: 'info', source: 'System', message: '', ...over };
  }
  // 真实 journal `llm` 通道形态：llm_completed + meta 携带真实 per-call usage / 时延。
  const LLM_STREAM: LogEntry[] = [
    wsLog({ id: 'c1', timestamp: '2026-06-15T10:00:02Z', level: 'success', source: 'PM', message: 'llm response completed', details: 'model=m prompt=1932 completion=1454 2400ms', meta: { channel: 'llm', streamEvent: 'llm_completed', role: 'PM', promptTokens: 1932, completionTokens: 1454, totalTokens: 3386, durationMs: 2400 }, tags: ['llm_completed'] }),
    wsLog({ id: 'c2', timestamp: '2026-06-15T10:00:04Z', level: 'success', source: 'Director', message: 'llm response completed', details: 'model=m prompt=800 completion=200 1800ms', meta: { channel: 'llm', streamEvent: 'llm_completed', role: 'Director', promptTokens: 800, completionTokens: 200, totalTokens: 1000, durationMs: 1800 }, tags: ['llm_completed'] }),
  ];
  // 退化夹具：遥测有活动但无 token（如旧版 invoke_done 或仅 llm_waiting）→ 用于验证 token 退回用量统计。
  const TOKENLESS_STREAM: LogEntry[] = [
    wsLog({ id: 'l1', timestamp: '2026-06-15T10:00:02Z', level: 'success', source: 'PM', message: 'LLM 响应已返回', details: 'chars=120 2400ms', meta: { channel: 'llm', streamEvent: 'invoke_done', role: 'PM' }, tags: ['invoke_done'] }),
    wsLog({ id: 'l2', timestamp: '2026-06-15T10:00:04Z', level: 'success', source: 'Director', message: 'LLM 响应已返回', details: 'chars=80 1800ms', meta: { channel: 'llm', streamEvent: 'invoke_done', role: 'Director' }, tags: ['invoke_done'] }),
  ];
  const EXECUTION: LogEntry[] = [
    wsLog({ id: 'cb1', timestamp: '2026-06-15T10:00:00Z', source: 'System', message: 'context.build', meta: { channel: 'runtime_events' } }),
    wsLog({ id: 'p1', timestamp: '2026-06-15T10:00:01Z', source: 'PM', message: 'prompt_context', meta: { channel: 'runtime_events' } }),
  ];
  const telemetryOf = (llm = LLM_STREAM, exec = EXECUTION, proc: LogEntry[] = []) =>
    buildTelemetryFromStream(llm, exec, proc);

  it('drives activity (calls / projections / latency) from the live WS stream', () => {
    const model = buildContextOSModel(baseInput({ telemetry: telemetryOf() }));

    expect(model.telemetryActive).toBe(true);
    expect(model.calls).toBe(2); // two llm_completed
    expect(model.projectionCount).toBe(2); // context.build + prompt_context
    expect(model.realLatencyMs).toBe(1800); // newest call's real latency (meta.durationMs)
    // tokens ARE on the realtime stream (journal llm channel raw.data) → real aggregation.
    expect(model.totalTokens).toBe(4386); // 3386 + 1000
    expect(model.tokensRealtime).toBe(true);
    expect(model.receiptCount).toBe(0); // no context.snapshot in this stream
    expect(model.contextItemsCount).toBeNull(); // context.build fixture carries no items_count

    // Pipeline projection node shows the real (all-role) projection count.
    expect(model.pipeline.find((s) => s.id === 'projection')?.metric).toBe('2 投影');

    // Decision log is the real WS event stream (newest first).
    expect(model.decisions[0].source).toBe('telemetry');
    expect(model.decisions[0].actor).toBe('Director');

    // Observed activity even though pmRunning/directorRunning are false.
    expect(model.dataIdle).toBe(false);
  });

  it('sources real per-call tokens from the realtime journal llm channel', () => {
    const model = buildContextOSModel(baseInput({ telemetry: telemetryOf() }));
    expect(model.totalTokens).toBe(4386);
    expect(model.promptTokens).toBe(2732); // 1932 + 800
    expect(model.completionTokens).toBe(1654); // 1454 + 200
    expect(model.tokensRealtime).toBe(true);
    expect(model.avgPerCall).toBe(Math.round(4386 / 2));
  });

  it('falls back to the usage-stats channel only when the realtime stream carries no tokens (honest)', () => {
    const usageStats: UsageStats = {
      totals: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 },
      calls: 7,
      estimated_calls: 1,
      by_mode: {},
    };
    // telemetry active (invoke_done activity) but NO per-call usage → token degrades to usage-stats.
    const model = buildContextOSModel(baseInput({ usageStats, telemetry: telemetryOf(TOKENLESS_STREAM) }));
    expect(model.totalTokens).toBe(1500);
    expect(model.tokensRealtime).toBe(false); // not realtime — labelled accordingly in the UI
    expect(model.estimatedCalls).toBe(1);
    // ...but calls / projections stay realtime from the WS stream.
    expect(model.calls).toBe(2);
    expect(model.projectionCount).toBe(2);
  });

  it('derives a real event-type distribution from WS categories', () => {
    const model = buildContextOSModel(baseInput({ telemetry: telemetryOf() }));
    // 4 events: 2 projection (context.build + prompt_context) + 2 call (invoke_done).
    expect(model.eventTypesTotal).toBe(4);
    const byKey = Object.fromEntries(model.eventTypes.map((s) => [s.key, s.count]));
    expect(byKey['projection']).toBe(2);
    expect(byKey['call']).toBe(2);
    const ratioSum = model.eventTypes.reduce((acc, s) => acc + s.ratio, 0);
    expect(ratioSum).toBeCloseTo(1, 5);
  });

  it('attributes REAL per-role tokens for roles with usage; others show event counts', () => {
    const model = buildContextOSModel(baseInput({ telemetry: telemetryOf() }));
    const pm = model.roles.find((r) => r.id === 'pm');
    const director = model.roles.find((r) => r.id === 'director');
    const qa = model.roles.find((r) => r.id === 'qa');
    expect(pm?.state).toBe('active'); // produced WS events (llm_completed + prompt_context)
    expect(pm?.tokensReal).toBe(true); // journal llm channel carries real usage
    expect(pm?.tokens).toBe(3386);
    expect(pm?.detail).toBe('3.4k tok');
    expect(director?.state).toBe('active');
    expect(director?.tokensReal).toBe(true);
    expect(director?.tokens).toBe(1000);
    expect(qa?.state).toBe('idle');
    expect(qa?.tokensReal).toBe(false); // no events, no usage
  });

  it('flags windowed when a WS stream reaches its ring-buffer cap', () => {
    expect(buildContextOSModel(baseInput({ telemetry: telemetryOf() })).telemetryWindowed).toBe(false);
    const bigExec = Array.from({ length: 100 }, (_, i) =>
      wsLog({ id: `rt-${i}`, timestamp: '2026-06-15T10:00:00Z', message: 'tick', meta: { channel: 'runtime_events' } }),
    );
    expect(buildContextOSModel(baseInput({ telemetry: telemetryOf([], bigExec) })).telemetryWindowed).toBe(true);
  });

  it('populates RoleInternalContext per role from live WS telemetry', () => {
    const model = buildContextOSModel(baseInput({ telemetry: telemetryOf() }));
    const pm = model.roles.find((r) => r.id === 'pm');
    const director = model.roles.find((r) => r.id === 'director');
    const qa = model.roles.find((r) => r.id === 'qa');

    expect(pm?.internalContext).toBeDefined();
    expect(pm?.internalContext.eventCount).toBe(2); // llm_completed + prompt_context
    expect(pm?.internalContext.calls).toBe(1);
    expect(pm?.internalContext.totalTokens).toBe(3386);
    expect(pm?.internalContext.promptTokens).toBe(1932);
    expect(pm?.internalContext.completionTokens).toBe(1454);
    expect(pm?.internalContext.state).toBe('active');
    expect(pm?.internalContext.events).toHaveLength(2);
    expect(pm?.internalContext.lastEventAt).toBeGreaterThan(0);
    expect(pm?.internalContext.currentTaskId).toBeNull();
    expect(pm?.internalContext.currentTaskTitle).toBeNull();
    // EXECUTION fixture 的 context.build 没有 items_count / total_tokens → 诚实为 null。
    expect(pm?.internalContext.contextItemsCount).toBeNull();
    expect(pm?.internalContext.contextTokensLatest).toBeNull();

    expect(director?.internalContext.eventCount).toBe(1);
    expect(director?.internalContext.totalTokens).toBe(1000);

    expect(qa?.internalContext.eventCount).toBe(0);
    expect(qa?.internalContext.state).toBe('idle');
  });

  it('keeps RoleInternalContext.state consistent with RoleCard.state when a role is running but has no events', () => {
    const model = buildContextOSModel(baseInput({ pmRunning: true, telemetry: telemetryOf() }));
    const pm = model.roles.find((r) => r.id === 'pm');
    expect(pm?.state).toBe('active');
    expect(pm?.internalContext.state).toBe('active');
  });

  it('reports per-role context items / tokens when context.build carries structured signals', () => {
    const build = wsLog({
      id: 'b1',
      timestamp: '2026-06-15T10:00:00Z',
      source: 'PM',
      message: 'ContextPack built',
      meta: { channel: 'runtime_events', items_count: 5, total_tokens: 3200 },
    });
    const snap = wsLog({
      id: 's1',
      timestamp: '2026-06-15T10:00:01Z',
      source: 'PM',
      message: 'Context snapshot stored',
      meta: { channel: 'runtime_events', snapshot_hash: 'sh1' },
    });
    const telemetry = telemetryOf([], [build, snap]);
    const model = buildContextOSModel(baseInput({ telemetry }));
    const pm = model.roles.find((r) => r.id === 'pm');

    expect(pm?.internalContext.projectionCount).toBe(1); // items_count signature
    expect(pm?.internalContext.receiptCount).toBe(1); // snapshot_hash signature
    expect(pm?.internalContext.contextItemsCount).toBe(5);
    expect(pm?.internalContext.contextTokensLatest).toBe(3200);
  });

  it('truncates RoleInternalContext.events to MAX_ROLE_EVENTS while preserving newest-first order', () => {
    const manyPmEvents: LogEntry[] = Array.from({ length: 12 }, (_, i) =>
      wsLog({
        id: `pm-${i}`,
        timestamp: `2026-06-15T10:00:${10 + i}Z`,
        source: 'PM',
        message: `pm event ${i}`,
        meta: { channel: 'llm', streamEvent: 'tool_call', role: 'PM' },
      }),
    );
    const telemetry = telemetryOf(manyPmEvents, []);
    const model = buildContextOSModel(baseInput({ telemetry }));
    const pm = model.roles.find((r) => r.id === 'pm');

    expect(pm?.internalContext.eventCount).toBe(12);
    expect(pm?.internalContext.events).toHaveLength(8);
    // 最新的事件 timestamp 最大，应排在首位。
    expect(pm?.internalContext.events[0].id).toBe('pm-11');
  });

  it('marks blocked role internal context as blocked', () => {
    const blockedLlm: LlmRuntimeGateState = {
      state: 'BLOCKED',
      blockedRoles: ['director'],
      requiredRoles: ['director'],
      lastUpdated: null,
    };
    const model = buildContextOSModel(baseInput({ llmRuntimeState: blockedLlm, telemetry: telemetryOf() }));
    const director = model.roles.find((r) => r.id === 'director');
    expect(director?.internalContext.state).toBe('blocked');
  });
});

describe('decisionMatchesRole', () => {
  it('returns true for null role (no filter)', () => {
    expect(decisionMatchesRole('PM', null)).toBe(true);
  });
  it('matches QA against Reviewer alias', () => {
    expect(decisionMatchesRole('Reviewer', 'qa')).toBe(true);
    expect(decisionMatchesRole('QA', 'qa')).toBe(true);
  });
  it('does not match unrelated actor', () => {
    expect(decisionMatchesRole('Director', 'pm')).toBe(false);
  });
});

describe('contextOSFormat', () => {
  it('formats token magnitudes compactly', () => {
    expect(contextOSFormat.tokens(999)).toBe('999');
    expect(contextOSFormat.tokens(1500)).toBe('1.5k');
    expect(contextOSFormat.tokens(12000)).toBe('12k');
  });
  it('exposes a nominal context window constant', () => {
    expect(NOMINAL_CONTEXT_WINDOW).toBe(128_000);
  });
});

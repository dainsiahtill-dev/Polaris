import { describe, it, expect } from 'vitest';

import {
  buildContextOSModel,
  decisionMatchesRole,
  contextOSFormat,
  NOMINAL_CONTEXT_WINDOW,
} from './contextOSData';
import { parseObservationLog } from './contextOSTelemetry';
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

describe('buildContextOSModel with real telemetry', () => {
  const TELEMETRY_LOG = [
    JSON.stringify({
      ts: '2026-06-15T10:00:00Z', ts_epoch: 1781856000.0, seq: 1, event_id: 'cb1',
      kind: 'observation', actor: 'System', name: 'context.build', refs: { run_id: 'r1', step: 1 },
      summary: 'ContextPack built (6 items)', ok: true,
      output: { request_hash: 'rh1', items_count: 6, total_tokens: 3000, snapshot_path: 'runtime/snap/h1.json' },
    }),
    JSON.stringify({
      ts: '2026-06-15T10:00:01Z', ts_epoch: 1781856001.0, seq: 2, event_id: 'p1',
      kind: 'observation', actor: 'PM', name: 'prompt_context', refs: { run_id: 'r1', step: 1 },
      summary: 'Prompt Context Injection', ok: true,
      output: { context_hash: 'h1', context_snapshot: 'runtime/snap/h1.json' },
    }),
    JSON.stringify({
      ts: '2026-06-15T10:00:01.5Z', ts_epoch: 1781856001.5, seq: 3, event_id: 's1',
      kind: 'observation', actor: 'System', name: 'context.snapshot', refs: { run_id: 'r1', step: 1 },
      summary: 'Context snapshot stored', ok: true,
      output: { request_hash: 'rh1', snapshot_path: 'runtime/snap/h1.json', snapshot_hash: 'sh1' },
    }),
    JSON.stringify({
      ts: '2026-06-15T10:00:02Z', ts_epoch: 1781856002.0, seq: 4, event_id: 'c1',
      kind: 'observation', actor: 'PM', name: 'llm_invoke', refs: { mode: 'pm.planning' },
      summary: 'pm call', ok: true,
      // real llm_invoke: duration lives in output.duration_ms (not top-level).
      output: { usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 }, duration_ms: 2400 },
    }),
    JSON.stringify({
      ts: '2026-06-15T10:00:04Z', ts_epoch: 1781856004.0, seq: 5, event_id: 'c2',
      kind: 'observation', actor: 'Director', name: 'llm_invoke', refs: { mode: 'director.execution' },
      summary: 'director call', ok: true,
      // char-estimated usage → estimated:true.
      output: { usage: { prompt_tokens: 800, completion_tokens: 200, total_tokens: 1000, estimated: true }, duration_ms: 1800 },
    }),
  ].join('\n');

  it('prefers real telemetry for tokens, projections, receipts and decisions', () => {
    const telemetry = parseObservationLog(TELEMETRY_LOG);
    const model = buildContextOSModel(baseInput({ telemetry }));

    expect(model.telemetryActive).toBe(true);
    // Telemetry-derived totals (no usageStats prop supplied).
    expect(model.totalTokens).toBe(2500);
    expect(model.calls).toBe(2);
    expect(model.estimatedCalls).toBe(1); // director call is char-estimated
    expect(model.projectionCount).toBe(2); // context.build + prompt_context (all-role)
    expect(model.receiptCount).toBe(1); // canonical context.snapshot only
    expect(model.contextItemsCount).toBe(6); // real context.build items_count
    expect(model.realLatencyMs).toBe(1800); // recovered from output.duration_ms

    // Pipeline projection node shows the real (all-role) projection count.
    expect(model.pipeline.find((s) => s.id === 'projection')?.metric).toBe('2 投影');
    // WorkingMem stage shows the real in-window item count (not the estimate path).
    expect(model.pipeline.find((s) => s.id === 'working_mem')?.metric).toBe('6 项在窗');

    // Decision log is the real observation stream (newest first), with token/receipt enrichment.
    expect(model.decisions[0].source).toBe('telemetry');
    expect(model.decisions[0].actor).toBe('Director');
    const snapshotRow = model.decisions.find((d) => d.id === 's1');
    expect(snapshotRow?.receipt).toBe(true); // canonical context.snapshot is the receipt
    const callRow = model.decisions.find((d) => d.id === 'c1');
    expect(callRow?.tokens).toBe(1500);

    // Not idle even though pmRunning/directorRunning are false — telemetry is observed activity.
    expect(model.dataIdle).toBe(false);
  });

  it('derives a real event-type distribution from observation categories', () => {
    const telemetry = parseObservationLog(TELEMETRY_LOG);
    const model = buildContextOSModel(baseInput({ telemetry }));
    // TELEMETRY_LOG = context.build + prompt_context (2 projection) + context.snapshot (1 event) + 2 llm_invoke (call).
    expect(model.eventTypesTotal).toBe(5);
    const byKey = Object.fromEntries(model.eventTypes.map((s) => [s.key, s.count]));
    expect(byKey['projection']).toBe(2);
    expect(byKey['call']).toBe(2);
    expect(byKey['event']).toBe(1); // context.snapshot
    // ratios sum to 1 over the present categories.
    const ratioSum = model.eventTypes.reduce((acc, s) => acc + s.ratio, 0);
    expect(ratioSum).toBeCloseTo(1, 5);
  });

  it('maps telemetry actor tokens onto role cards and marks them active', () => {
    const telemetry = parseObservationLog(TELEMETRY_LOG);
    const model = buildContextOSModel(baseInput({ telemetry }));
    const pm = model.roles.find((r) => r.id === 'pm');
    const director = model.roles.find((r) => r.id === 'director');
    expect(pm?.tokens).toBe(1500);
    expect(pm?.tokensReal).toBe(true); // PM has a real usage channel
    expect(pm?.state).toBe('active'); // produced telemetry events
    expect(director?.tokens).toBe(1000);
    expect(director?.tokensReal).toBe(true);
    const qa = model.roles.find((r) => r.id === 'qa');
    expect(qa?.state).toBe('idle');
    expect(qa?.tokensReal).toBe(false); // no usage channel for QA
  });

  it('marks token totals as windowed when the read hit the tail cap', () => {
    // 5 parsed lines; a cap of 5 means we only saw the tail window.
    const telemetry = parseObservationLog(TELEMETRY_LOG, 5);
    const model = buildContextOSModel(baseInput({ telemetry }));
    expect(model.telemetryWindowed).toBe(true);
    const wide = buildContextOSModel(baseInput({ telemetry: parseObservationLog(TELEMETRY_LOG, 800) }));
    expect(wide.telemetryWindowed).toBe(false);
  });

  it('falls back to proxy derivation when telemetry is empty', () => {
    const model = buildContextOSModel(baseInput());
    expect(model.telemetryActive).toBe(false);
    expect(model.decisions).toHaveLength(0);
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

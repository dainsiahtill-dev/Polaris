import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ContextOSWorkspace } from './ContextOSWorkspace';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState } from '@/app/hooks/useLlmRuntimeGate';

// ContextOS 现在直接消费 useRuntime 经 WebSocket 实时推送的运行时流（props），不再轮询任何文件。
// 这些 LogEntry 夹具取自 parseLlmStreamLine / parseRuntimeEvent 的输出形态。
// 真实 journal `llm` 通道形态：llm_completed + meta 携带真实 per-call usage / 时延。
const LLM_STREAM: LogEntry[] = [
  {
    id: 'c1',
    timestamp: new Date().toISOString(),
    level: 'success',
    source: 'PM',
    message: 'pm planning call returned',
    details: 'model=MiniMax-M3 prompt=1932 completion=1454 2400ms',
    meta: {
      channel: 'llm',
      streamEvent: 'llm_completed',
      role: 'PM',
      model: 'MiniMax-M3',
      promptTokens: 1932,
      completionTokens: 1454,
      totalTokens: 3386,
      durationMs: 2400,
    },
    tags: ['llm_completed'],
  },
];
const EXECUTION_STREAM: LogEntry[] = [
  {
    id: 'cb1',
    timestamp: new Date().toISOString(),
    level: 'info',
    source: 'System',
    message: 'context.build',
    meta: { channel: 'runtime_events' },
  },
];

const READY_LLM: LlmRuntimeGateState = {
  state: 'READY',
  blockedRoles: [],
  requiredRoles: ['pm', 'director'],
  lastUpdated: null,
};

function baseProps() {
  return {
    workspace: '/tmp/demo',
    onBackToMain: vi.fn(),
    onRefresh: vi.fn(),
    live: true,
    reconnecting: false,
    usageStats: null as UsageStats | null,
    currentPhase: 'idle',
    pmRunning: false,
    directorRunning: false,
    llmRuntimeState: READY_LLM,
    dialogueEvents: [] as DialogueEvent[],
    executionLogs: [] as LogEntry[],
    llmStreamEvents: [] as LogEntry[],
    processStreamEvents: [] as LogEntry[],
    snapshot: null,
    qualityGate: null,
  };
}

describe('ContextOSWorkspace', () => {
  it('renders the dashboard shell + all 8 pipeline stages with empty props', () => {
    render(<ContextOSWorkspace {...baseProps()} />);
    expect(screen.getByTestId('contextos-workspace')).toBeTruthy();
    for (const id of ['request', 'truthlog', 'working_mem', 'projection', 'role_signal', 'budget', 'prompt', 'llm']) {
      expect(screen.getByTestId(`contextos-stage-${id}`)).toBeTruthy();
    }
    // 7 component-health cards
    for (const id of ['truthlog', 'working_mem', 'projection', 'role_signal', 'budget', 'prompt', 'telemetry']) {
      expect(screen.getByTestId(`contextos-component-${id}`)).toBeTruthy();
    }
    // 5 role cards (pm/architect/chief_engineer/director/qa — the real 5-role system)
    for (const id of ['pm', 'architect', 'chief_engineer', 'director', 'qa']) {
      expect(screen.getByTestId(`contextos-role-${id}`)).toBeTruthy();
    }
  });

  it('invokes onBackToMain when the back button is clicked', () => {
    const props = baseProps();
    render(<ContextOSWorkspace {...props} />);
    fireEvent.click(screen.getByTestId('contextos-back'));
    expect(props.onBackToMain).toHaveBeenCalledTimes(1);
  });

  it('renders token totals from the usage-stats channel and stays crash-free', () => {
    const usageStats: UsageStats = {
      totals: { prompt_tokens: 8000, completion_tokens: 2000, total_tokens: 10000 },
      calls: 5,
      estimated_calls: 0,
      by_mode: { pm: { total_tokens: 6000, calls: 3 }, director: { total_tokens: 4000, calls: 2 } },
    };
    render(
      <ContextOSWorkspace
        {...baseProps()}
        usageStats={usageStats}
        pmRunning
        currentPhase="planning"
      />,
    );
    // total tokens appears (header chip + budget headline) — sourced from the usage-stats channel.
    expect(screen.getAllByText('10,000').length).toBeGreaterThan(0);
  });

  it('surfaces REAL ContextOS telemetry from the live WebSocket stream props', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={LLM_STREAM}
        executionLogs={EXECUTION_STREAM}
      />,
    );

    // The telemetry source badge appears once the live WS stream has events.
    const sourceBadge = screen.getByTestId('contextos-telemetry-source');
    expect(sourceBadge.textContent).toContain('REAL');
    expect(sourceBadge.textContent).toContain('1 调用'); // one invoke_done
    expect(sourceBadge.textContent).toContain('1 投影'); // context.build

    // Real-time freshness badge flips to live telemetry (events are timestamped "now").
    expect(screen.getByTestId('contextos-telemetry-freshness').textContent).toContain('实时遥测');

    // The realtime activity chip reflects the live call count + latency.
    expect(screen.getByTestId('contextos-activity-chip').textContent).toContain('2400ms');

    // Real per-call tokens (journal llm channel raw.data) are surfaced as realtime, not degraded.
    expect(screen.getAllByText('3,386').length).toBeGreaterThan(0);
    expect(screen.getByText(/tokens · 实时/)).toBeTruthy();
    // The honest "waiting / unavailable" empty-state is NOT shown once real tokens arrive.
    expect(screen.queryByTestId('contextos-tokens-unavailable')).toBeNull();

    // The real WS event appears in the decision log.
    expect(screen.getByText('pm planning call returned')).toBeTruthy();
  });

  it('cross-filters the decision stream when a role tab is selected', () => {
    const dialogueEvents: DialogueEvent[] = [
      { speaker: 'PM', content: 'pm decision', timestamp: '2026-06-15T10:00:00Z', type: 'message' },
      { speaker: 'Director', content: 'director decision', timestamp: '2026-06-15T10:00:05Z', type: 'message' },
    ];
    render(<ContextOSWorkspace {...baseProps()} dialogueEvents={dialogueEvents} />);
    // Before filter: both decisions visible.
    expect(screen.getByText('pm decision')).toBeTruthy();
    expect(screen.getByText('director decision')).toBeTruthy();
    // Filter to PM.
    fireEvent.click(screen.getByTestId('contextos-roletab-pm'));
    expect(screen.getByText('pm decision')).toBeTruthy();
    expect(screen.queryByText('director decision')).toBeNull();
  });
});

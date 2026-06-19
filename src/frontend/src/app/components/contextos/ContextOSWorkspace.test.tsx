import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ContextOSWorkspace } from './ContextOSWorkspace';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState } from '@/app/hooks/useLlmRuntimeGate';
vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => ({
    subscribeChannels: () => () => {},
    registerMessageHandler: () => () => {},
  }),
}));

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

const READY_LLM_WITH_WINDOWS: LlmRuntimeGateState = {
  state: 'READY',
  blockedRoles: [],
  requiredRoles: ['pm', 'director'],
  lastUpdated: null,
  roleDetails: {
    pm: {
      providerId: 'kimi',
      providerName: 'Kimi Coding',
      providerType: 'anthropic_compat',
      model: 'kimi-for-coding',
      maxContextTokens: 262_144,
      maxOutputTokens: 16_384,
      bindings: [],
      ready: true,
      runtimeSupported: true,
      runtimeIssue: '',
      readinessIssue: '',
      readinessSource: 'role_index',
      testedProviderId: 'kimi',
      testedModel: 'kimi-for-coding',
      testedTimestamp: null,
      timestamp: null,
    },
    director: {
      providerId: 'qwen-a',
      providerName: 'Qwen A',
      providerType: 'openai_compat',
      model: 'qwen3.6-27b-gpu0',
      maxContextTokens: 32_768,
      maxOutputTokens: 8_192,
      bindings: [
        {
          providerId: 'qwen-a',
          providerName: 'Qwen A',
          providerType: 'openai_compat',
          model: 'qwen3.6-27b-gpu0',
          profile: '',
          maxContextTokens: 32_768,
          maxOutputTokens: 8_192,
        },
        {
          providerId: 'qwen-b',
          providerName: 'Qwen B',
          providerType: 'openai_compat',
          model: 'qwen3.6-27b-gpu1',
          profile: '',
          maxContextTokens: 65_536,
          maxOutputTokens: 8_190,
        },
      ],
      ready: true,
      runtimeSupported: true,
      runtimeIssue: '',
      readinessIssue: '',
      readinessSource: 'role_index',
      testedProviderId: 'qwen-a',
      testedModel: 'qwen3.6-27b-gpu0',
      testedTimestamp: null,
      timestamp: null,
    },
  },
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
    // Bench strip should not pollute the ContextOS view.
    expect(screen.queryByTestId('bench-status-strip')).toBeNull();
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

  it('renders Context Budget against the actual selected role window', () => {
    const usageStats: UsageStats = {
      totals: { prompt_tokens: 16_384, completion_tokens: 1024, total_tokens: 17_408 },
      calls: 1,
      estimated_calls: 0,
      by_mode: {},
    };
    render(
      <ContextOSWorkspace
        {...baseProps()}
        usageStats={usageStats}
        llmRuntimeState={READY_LLM_WITH_WINDOWS}
      />,
    );

    const source = screen.getByTestId('contextos-window-source');
    expect(source.textContent).toContain('32.8k');
    expect(source.textContent).toContain('最小绑定窗口');
    expect(source.textContent).not.toContain('128k');

    fireEvent.click(screen.getByTestId('contextos-role-pm'));
    expect(screen.getByTestId('contextos-window-source').textContent).toContain('262k');
    expect(screen.getByTestId('contextos-window-source').textContent).toContain('PM');
  });

  it('renders missing role usage as missing while keeping each role window visible', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={READY_LLM_WITH_WINDOWS}
      />,
    );

    const source = screen.getByTestId('contextos-window-source');
    expect(source).toHaveAttribute('data-usage-state', 'none');
    expect(source.textContent).toContain('无 usage');
    expect(source.textContent).toContain('32.8k');

    expect(screen.getByTestId('contextos-role-occupancy-pm').textContent).toContain('无 usage');
    expect(screen.getByTestId('contextos-role-window-pm').textContent).toContain('262k');
    expect(screen.getByTestId('contextos-role-occupancy-director').textContent).toContain('无 usage');
    expect(screen.getByTestId('contextos-role-window-director').textContent).toContain('32.8k');
  });

  it('uses the selected role occupancy numerator instead of the global average', () => {
    const mixedRoleStream: LogEntry[] = [
      ...LLM_STREAM,
      {
        id: 'director-call',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'Director',
        message: 'director implementation call returned',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'Director',
          promptTokens: 800,
          completionTokens: 200,
          totalTokens: 1000,
          durationMs: 1800,
        },
        tags: ['llm_completed'],
      },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={READY_LLM_WITH_WINDOWS}
        llmStreamEvents={mixedRoleStream}
      />,
    );

    const source = screen.getByTestId('contextos-window-source');
    expect(source.textContent).toContain('~1.4k'); // global average prompt: (1932 + 800) / 2
    expect(source).toHaveAttribute('data-usage-state', 'observed');
    expect(screen.getByTestId('contextos-role-occupancy-pm').textContent).toContain('~1.9k');
    expect(screen.getByTestId('contextos-role-window-pm').textContent).toContain('262k');
    expect(screen.getByTestId('contextos-role-occupancy-director').textContent).toContain('~800');
    expect(screen.getByTestId('contextos-role-window-director').textContent).toContain('32.8k');

    fireEvent.click(screen.getByTestId('contextos-role-director'));
    expect(source.textContent).toContain('~800');
    expect(source.textContent).toContain('Director');
    expect(source.textContent).toContain('平均提示');

    fireEvent.click(screen.getByTestId('contextos-role-director'));
    fireEvent.click(screen.getByTestId('contextos-role-pm'));
    expect(source.textContent).toContain('~1.9k');
    expect(source.textContent).toContain('PM');
    expect(source.textContent).toContain('平均提示');
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

    // The consolidated resource chip reflects the live call count + tokens + latency.
    const resourceChip = screen.getByTestId('contextos-resource-chip');
    expect(resourceChip.textContent).toContain('2400ms');
    expect(resourceChip.textContent).toContain('3,386');
    expect(resourceChip.textContent).toContain('调用');

    // Real per-call tokens (journal llm channel raw.data) are surfaced as realtime, not degraded.
    expect(screen.getAllByText('3,386').length).toBeGreaterThan(0);
    expect(screen.getByText(/tokens · 实时/)).toBeTruthy();
    // The honest "waiting / unavailable" empty-state is NOT shown once real tokens arrive.
    expect(screen.queryByTestId('contextos-tokens-unavailable')).toBeNull();

    // The real WS event appears in the decision log.
    expect(screen.getByText('pm planning call returned')).toBeTruthy();
  });

  it('opens the real ContextOS structure view from an explicit entry point', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={LLM_STREAM}
        executionLogs={EXECUTION_STREAM}
      />,
    );

    expect(screen.queryByTestId('contextos-structure-panel')).toBeNull();

    fireEvent.click(screen.getByTestId('contextos-structure-toggle'));

    const panel = screen.getByTestId('contextos-structure-panel');
    expect(panel).toBeTruthy();
    expect(panel.textContent).toContain('TruthLog');
    expect(panel.textContent).toContain('WorkingMem');
    expect(panel.textContent).toContain('ProjectionEngine');
    expect(panel.textContent).toContain('ReceiptStore');
    expect(panel.textContent).toContain('角色上下文窗口');
    expect(panel.textContent).toContain('最近结构事件');
    expect(panel.textContent).toContain('pm planning call returned');
  });

  it('shows role context windows in the structure panel without requiring usage events', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={READY_LLM_WITH_WINDOWS}
      />,
    );

    fireEvent.click(screen.getByTestId('contextos-structure-toggle'));

    const panel = screen.getByTestId('contextos-structure-panel');
    expect(panel.textContent).toContain('无 usage');
    expect(panel.textContent).toContain('262k');
    expect(panel.textContent).toContain('32.8k');
  });

  it('opens the per-role internal ContextOS panel when a role card is selected', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={LLM_STREAM}
        executionLogs={EXECUTION_STREAM}
      />,
    );

    // PM role card exists.
    const pmCard = screen.getByTestId('contextos-role-pm');
    expect(pmCard).toBeTruthy();

    // Internal panel is not rendered before selection.
    expect(screen.queryByTestId('contextos-role-panel-pm')).toBeNull();

    // Select PM.
    fireEvent.click(pmCard);
    const panel = screen.getByTestId('contextos-role-panel-pm');
    expect(panel).toBeTruthy();
    expect(panel.textContent).toContain('TruthLog');
    expect(panel.textContent).toContain('ProjectionEngine');
    expect(panel.textContent).toContain('ReceiptStore');

    // The internal pipeline stages carry data-state and reflect derived metrics.
    const truthlogStage = screen.getByTestId('contextos-role-panel-stage-pm-truthlog');
    expect(truthlogStage).toHaveAttribute('data-state', 'active');
    expect(truthlogStage.textContent).toContain('1 事件');

    // Token / duration badges appear on the PM llm_completed event row.
    expect(panel.textContent).toContain('3,386');
    expect(panel.textContent).toContain('2400ms');

    // Token header chip appears for the PM role (totalTokens > 0).
    expect(screen.getByTestId('contextos-role-panel-tokens-pm')).toBeTruthy();

    // Toggle off.
    fireEvent.click(pmCard);
    expect(screen.queryByTestId('contextos-role-panel-pm')).toBeNull();
  });

  it('does not repeat the same recent LLM call when duplicated stream entries arrive', () => {
    const duplicatedCall: LogEntry = {
      ...LLM_STREAM[0],
      meta: {
        ...LLM_STREAM[0].meta,
        contextSnapshotRef: 'same-context-snapshot-ref',
        promptHash: 'same-prompt-hash',
        turnId: 'same-turn',
      },
    };
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={Array.from({ length: 5 }, (_, index) => ({
          ...duplicatedCall,
          id: `duplicated-call-${index}`,
        }))}
      />,
    );

    fireEvent.click(screen.getByTestId('contextos-role-pm'));
    const panel = screen.getByTestId('contextos-role-panel-pm');
    expect(panel.textContent).toContain('最近 LLM 调用');
    expect(screen.getAllByText('查看完整上下文')).toHaveLength(1);
    expect(screen.getByTestId('contextos-role-occupancy-pm').textContent).toContain('~1.9k');
  });

  it('does not show a token header for a role with zero tokens', () => {
    const tokenlessStream: LogEntry[] = [
      {
        id: 'c-no-usage',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'Director',
        message: 'director call returned',
        details: 'model=local 1200ms',
        meta: { channel: 'llm', streamEvent: 'llm_failed', role: 'Director', durationMs: 1200 },
        tags: ['llm_failed'],
      },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={tokenlessStream}
      />,
    );

    fireEvent.click(screen.getByTestId('contextos-role-director'));
    const panel = screen.getByTestId('contextos-role-panel-director');
    expect(panel).toBeTruthy();
    // No token chip since totalTokens === 0.
    expect(screen.queryByTestId('contextos-role-panel-tokens-director')).toBeNull();
  });

  it('renders the multi-worker LLM tracking panel when WS events carry worker_id', () => {
    const workerStream: LogEntry[] = [
      {
        id: 'w1c',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'Director',
        message: 'worker-1 call',
        details: 'model=local 800ms',
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
        tags: ['llm_completed'],
      },
      {
        id: 'w2c',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'Director',
        message: 'worker-2 call',
        details: 'model=local 500ms',
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
        tags: ['llm_completed'],
      },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={workerStream}
        directorRunning
      />,
    );

    const panel = screen.getByTestId('contextos-worker-panel');
    expect(panel).toBeTruthy();
    expect(screen.getByTestId('contextos-worker-worker-1')).toBeTruthy();
    expect(screen.getByTestId('contextos-worker-worker-2')).toBeTruthy();
    expect(screen.getByTestId('contextos-worker-count').textContent).toContain('2');
  });

  it('does not render the multi-worker panel when no event carries worker_id', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={LLM_STREAM}
        executionLogs={EXECUTION_STREAM}
      />,
    );
    expect(screen.queryByTestId('contextos-worker-panel')).toBeNull();
  });
});

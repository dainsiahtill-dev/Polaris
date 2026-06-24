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
  it('renders the dashboard shell + all pipeline stages with empty props', () => {
    render(<ContextOSWorkspace {...baseProps()} />);
    expect(screen.getByTestId('contextos-workspace')).toBeTruthy();
    for (const id of ['request', 'truthlog', 'working_mem', 'projection', 'role_signal', 'prompt', 'budget', 'llm', 'receipt']) {
      expect(screen.getByTestId(`contextos-stage-${id}`)).toBeTruthy();
    }
    // Bench strip should not pollute the ContextOS view.
    expect(screen.queryByTestId('bench-status-strip')).toBeNull();
    // 5 role cards (pm/architect/chief_engineer/director/qa — the real 5-role system)
    for (const id of ['pm', 'architect', 'chief_engineer', 'director', 'qa']) {
      expect(screen.getByTestId(`contextos-role-${id}`)).toBeTruthy();
    }
  });

  it('renders control-plane projection from run ledger snapshot when provided', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        controlPlaneProjection={{
          schema_version: 1,
          source: 'run_ledger_projection',
          available: true,
          ok: true,
          status: 'ready',
          audit_path: '/tmp/demo/runtime/control_plane/ledger/run-1.ndjson',
          compat_ledgers_included: false,
          total: 2,
          projected: 2,
          missing: 0,
          failed: 0,
          projects: [],
          detail: 'run ledger projection 2/2 project(s) ready',
        }}
      />,
    );

    const chip = screen.getByTestId('contextos-control-plane-projection');
    expect(chip.textContent).toContain('账本一致');
    expect(chip.textContent).toContain('2/2 投影');
    expect(chip.textContent).toContain('source=run_ledger_projection');
    expect(chip.textContent).not.toContain('compat=factory-ledger');
  });

  it('shows when control-plane projection includes migration compatibility ledgers', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        controlPlaneProjection={{
          schema_version: 1,
          source: 'run_ledger_projection',
          available: true,
          ok: true,
          status: 'ready',
          audit_path: '/tmp/demo/runtime/control_plane/ledger/run-1.ndjson',
          compat_ledgers_included: true,
          total: 1,
          projected: 1,
          missing: 0,
          failed: 0,
          projects: [],
          detail: 'run ledger projection 1 project(s), 0 failed',
        }}
      />,
    );

    const chip = screen.getByTestId('contextos-control-plane-projection');
    expect(chip.textContent).toContain('source=run_ledger_projection');
    expect(chip.textContent).toContain('compat=factory-ledger');
  });

  it('uses Run Ledger state for the quality gate marker before stale qualityGate data', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        qualityGate={{
          score: 100,
          passed: true,
          attempt: 1,
          maxAttempts: 1,
          issues: [],
        }}
        controlPlaneProjection={{
          schema_version: 1,
          source: 'run_ledger_projection',
          available: true,
          ok: false,
          status: 'failed',
          audit_path: '/tmp/demo/runtime/control_plane/ledger/run-1.ndjson',
          compat_ledgers_included: false,
          total: 1,
          projected: 1,
          missing: 0,
          failed: 1,
          projects: [
            {
              project_id: 'project-1',
              ok: false,
              integrity_ok: true,
              outcome_ok: false,
              gate_count: 2,
              failed_gate_count: 1,
              latest_token_id: 'token-failed',
              detail: 'qa gate failed',
              missing: [],
            },
          ],
          detail: 'run ledger projection 1 project(s), 1 failed',
        }}
      />,
    );

    expect(screen.getByTitle('质量门 HOLD · Run Ledger')).toBeTruthy();
    expect(screen.queryByTitle('质量门 PASS · quality gate')).toBeNull();
  });

  it('distinguishes enabled verifier capabilities from hard evidence requirements', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        controlPlaneProjection={{
          schema_version: 1,
          source: 'run_ledger_projection',
          available: true,
          ok: true,
          status: 'ready',
          audit_path: '/tmp/demo/run_ledger.ndjson',
          compat_ledgers_included: false,
          total: 1,
          projected: 1,
          missing: 0,
          failed: 0,
          projects: [],
          detail: 'run ledger projection ready',
          evidence_policy: {
            ok: true,
            enabled_modalities: ['browser', 'visual'],
            required_modalities: [],
            missing_required_modalities: [],
          },
        }}
      />,
    );

    const policy = screen.getByTestId('contextos-evidence-policy');
    expect(policy.textContent).toContain('可选验证已启用');
    expect(policy.textContent).toContain('可选启用 browser, visual');
    expect(policy.textContent).toContain('未作为硬门禁');
  });

  it('opens a tailored detail modal for every ContextOS pipeline node', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={READY_LLM_WITH_WINDOWS}
        llmStreamEvents={LLM_STREAM}
        executionLogs={EXECUTION_STREAM}
      />,
    );

    const expectedDetails: Array<[string, string]> = [
      ['request', '入口摘要'],
      ['truthlog', '事件类型分布'],
      ['working_mem', '角色工作记忆'],
      ['projection', 'ProjectionEngine 解释'],
      ['role_signal', '角色信号面'],
      ['prompt', '提示构成'],
      ['budget', 'CompressionEngine 判定'],
      ['llm', 'LLM 调用事件'],
      ['receipt', '回执与快照证据'],
    ];

    for (const [stageId, detailTitle] of expectedDetails) {
      fireEvent.click(screen.getByTestId(`contextos-stage-${stageId}`));
      const modal = screen.getByTestId('contextos-pipeline-detail-modal');
      expect(modal.textContent).toContain(detailTitle);
      expect(screen.getByTestId(`contextos-pipeline-detail-${stageId}`)).toBeTruthy();
      fireEvent.click(screen.getByTestId('contextos-pipeline-detail-close'));
      expect(screen.queryByTestId('contextos-pipeline-detail-modal')).toBeNull();
    }
  });

  it('renders legacy summarized LLM payloads as readable summaries inside the LLM detail view', () => {
    const redactedStream: LogEntry[] = [
      {
        id: 'redacted-director-call',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'Director',
        message: '{"redacted":true,"type":"str","chars":127}',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'Director',
          model: 'qwen3.6-27b-gpu1',
          providerName: 'Qwen B',
          promptTokens: 2700,
          completionTokens: 100,
          totalTokens: 2800,
          contextTokens: 2700,
          durationMs: 61620,
          contextSnapshotRef: '1234567890abcdef12345678',
        },
        tags: ['llm_completed'],
      },
    ];

    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={READY_LLM_WITH_WINDOWS}
        llmStreamEvents={redactedStream}
      />,
    );

    fireEvent.click(screen.getByTestId('contextos-stage-llm'));
    const modal = screen.getByTestId('contextos-pipeline-detail-llm');
    expect(modal.textContent).toContain('LLM 响应已完成');
    expect(modal.textContent).toContain('历史事件仅有摘要');
    expect(modal.textContent).toContain('qwen3.6-27b-gpu1');
    expect(modal.textContent).not.toContain('{"redacted"');
  });

  it('surfaces context snapshot degradation in the role detail panel', () => {
    const degradedCall: LogEntry = {
      id: 'degraded-call',
      timestamp: new Date().toISOString(),
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
      tags: ['llm_completed'],
    };

    render(<ContextOSWorkspace {...baseProps()} llmStreamEvents={[degradedCall]} />);
    fireEvent.click(screen.getByTestId('contextos-role-director'));

    expect(screen.getByTestId('contextos-role-panel-director')).toBeTruthy();
    expect(screen.getAllByText('快照未落盘').length).toBeGreaterThan(0);
    expect(screen.queryByText('[object Object]')).toBeNull();
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

  it('renders provider usage source and cache/tool budget chips from live telemetry', () => {
    const protocolUsageStream: LogEntry[] = [
      {
        id: 'director-protocol-usage',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'Director',
        message: 'director provider call',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'Director',
          providerId: 'qwen-a',
          providerName: 'Qwen A',
          model: 'qwen3.6-27b-gpu0',
          usage: {
            input_tokens: 1000,
            cache_read_input_tokens: 500,
            output_tokens: 100,
          },
          final_request_context_audit: {
            final_request_token_estimate: 1800,
            tool_schema_token_estimate: 150,
            response_format_token_estimate: 25,
          },
          context_tokens_after: 1800,
        },
        tags: ['llm_completed'],
      },
    ];

    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={READY_LLM_WITH_WINDOWS}
        llmStreamEvents={protocolUsageStream}
      />,
    );

    expect(screen.getByText('provider usage · 实时')).toBeTruthy();
    expect(screen.getAllByText(/cache read/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/tools/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/provider usage/).length).toBeGreaterThan(0);
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

  it('renders each Director provider/model as its own context budget row', () => {
    const multiDirectorStream: LogEntry[] = [
      {
        id: 'director-gpu0',
        timestamp: new Date(Date.now() - 1000).toISOString(),
        level: 'success',
        source: 'Director',
        message: 'director gpu0 call returned',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'Director',
          providerId: 'qwen-a',
          providerName: 'Qwen A',
          model: 'qwen3.6-27b-gpu0',
          promptTokens: 400,
          completionTokens: 100,
          totalTokens: 500,
          durationMs: 1200,
        },
        tags: ['llm_completed'],
      },
      {
        id: 'director-gpu1',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'Director',
        message: 'director gpu1 call returned',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'Director',
          providerId: 'qwen-b',
          providerName: 'Qwen B',
          model: 'qwen3.6-27b-gpu1',
          promptTokens: 900,
          completionTokens: 300,
          totalTokens: 1200,
          durationMs: 2200,
        },
        tags: ['llm_completed'],
      },
    ];

    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={READY_LLM_WITH_WINDOWS}
        llmStreamEvents={multiDirectorStream}
      />,
    );

    fireEvent.click(screen.getByTestId('contextos-role-director'));

    const budgetPanel = screen.getByTestId('contextos-binding-budgets');
    expect(budgetPanel.textContent).toContain('Director 模型预算');
    expect(budgetPanel.textContent).toContain('2 路');
    expect(budgetPanel.textContent).toContain('qwen3.6-27b-gpu0');
    expect(budgetPanel.textContent).toContain('qwen3.6-27b-gpu1');
    expect(screen.getByTestId('contextos-binding-budget-director-qwen-a-qwen3-6-27b-gpu0-0').textContent).toContain('~400');
    expect(screen.getByTestId('contextos-binding-budget-director-qwen-b-qwen3-6-27b-gpu1-1').textContent).toContain('~900');
    expect(screen.getByTestId('contextos-role-window-director').textContent).toContain('2 路绑定');
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
    expect(screen.getByText('provider usage · 实时')).toBeTruthy();
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
          timestamp: new Date(Date.parse(duplicatedCall.timestamp) + index).toISOString(),
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

  it('renders provider latency from WS stream in the resource chip', () => {
    const latencyStream: LogEntry[] = [
      {
        id: 'lat1',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'PM',
        message: 'pm call returned',
        details: 'model=m prompt=100 completion=50 3200ms',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'PM',
          promptTokens: 100,
          completionTokens: 50,
          totalTokens: 150,
          durationMs: 3200,
        },
        tags: ['llm_completed'],
      },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={latencyStream}
      />,
    );
    const resourceChip = screen.getByTestId('contextos-resource-chip');
    expect(resourceChip.textContent).toContain('3200ms');
  });

  it('renders newest latency when multiple calls have different durations', () => {
    const multiLatencyStream: LogEntry[] = [
      {
        id: 'lat1',
        timestamp: new Date(Date.now() - 5000).toISOString(),
        level: 'success',
        source: 'PM',
        message: 'first call',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'PM',
          promptTokens: 100,
          completionTokens: 50,
          totalTokens: 150,
          durationMs: 1200,
        },
        tags: ['llm_completed'],
      },
      {
        id: 'lat2',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'Director',
        message: 'second call',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'Director',
          promptTokens: 200,
          completionTokens: 80,
          totalTokens: 280,
          durationMs: 4500,
        },
        tags: ['llm_completed'],
      },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={multiLatencyStream}
      />,
    );
    const resourceChip = screen.getByTestId('contextos-resource-chip');
    expect(resourceChip.textContent).toContain('4500ms');
  });

  it('renders provider error from llm_failed events in the telemetry badge', () => {
    const errorStream: LogEntry[] = [
      {
        id: 'err1',
        timestamp: new Date().toISOString(),
        level: 'error',
        source: 'PM',
        message: 'provider timeout',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_failed',
          role: 'PM',
          error: 'timeout',
        },
        tags: ['llm_failed'],
      },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={errorStream}
      />,
    );
    // Error appears in the decision log
    expect(screen.getByText('provider timeout')).toBeTruthy();
  });

  it('renders mixed success and error events from the WS stream', () => {
    const mixedStream: LogEntry[] = [
      {
        id: 'err1',
        timestamp: new Date(Date.now() - 3000).toISOString(),
        level: 'error',
        source: 'PM',
        message: 'rate limit exceeded',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_failed',
          role: 'PM',
          error: 'rate_limit',
        },
        tags: ['llm_failed'],
      },
      {
        id: 'ok1',
        timestamp: new Date().toISOString(),
        level: 'success',
        source: 'PM',
        message: 'pm call succeeded',
        meta: {
          channel: 'llm',
          streamEvent: 'llm_completed',
          role: 'PM',
          promptTokens: 100,
          completionTokens: 50,
          totalTokens: 150,
          durationMs: 2000,
        },
        tags: ['llm_completed'],
      },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={mixedStream}
      />,
    );
    // Both events appear in the decision log
    expect(screen.getByText('rate limit exceeded')).toBeTruthy();
    expect(screen.getByText('pm call succeeded')).toBeTruthy();
    // Telemetry source shows activity
    const sourceBadge = screen.getByTestId('contextos-telemetry-source');
    expect(sourceBadge.textContent).toContain('REAL');
  });

  it('renders without crashing when all props are null/empty', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        usageStats={null}
        llmRuntimeState={{
          state: 'UNKNOWN',
          blockedRoles: [],
          requiredRoles: [],
          lastUpdated: null,
        }}
        dialogueEvents={[]}
        executionLogs={[]}
        llmStreamEvents={[]}
        processStreamEvents={[]}
        snapshot={null}
        qualityGate={null}
      />,
    );
    expect(screen.getByTestId('contextos-workspace')).toBeTruthy();
    // All pipeline stages render
    for (const id of ['request', 'truthlog', 'working_mem', 'projection', 'role_signal', 'budget', 'prompt', 'llm']) {
      expect(screen.getByTestId(`contextos-stage-${id}`)).toBeTruthy();
    }
  });

  it('renders without crashing when llmRuntimeState has no roleDetails', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={{
          state: 'READY',
          blockedRoles: [],
          requiredRoles: ['pm'],
          lastUpdated: null,
        }}
      />,
    );
    expect(screen.getByTestId('contextos-workspace')).toBeTruthy();
    // Window source shows unknown state
    const source = screen.getByTestId('contextos-window-source');
    expect(source.textContent).toContain('未知');
  });

  it('renders without crashing when llmStreamEvents contains events with missing meta', () => {
    const sparseStream: LogEntry[] = [
      {
        id: 'sparse1',
        timestamp: new Date().toISOString(),
        level: 'info',
        source: 'System',
        message: 'event without meta',
      },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmStreamEvents={sparseStream}
      />,
    );
    expect(screen.getByTestId('contextos-workspace')).toBeTruthy();
  });

  it('renders blocked role cards when llmRuntimeState is BLOCKED', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        llmRuntimeState={{
          state: 'BLOCKED',
          blockedRoles: ['director'],
          requiredRoles: ['director'],
          lastUpdated: null,
        }}
        directorRunning
      />,
    );
    // The workspace still renders
    expect(screen.getByTestId('contextos-workspace')).toBeTruthy();
    // Director role card exists
    expect(screen.getByTestId('contextos-role-director')).toBeTruthy();
  });

  it('renders correctly when live=false and reconnecting=true', () => {
    render(
      <ContextOSWorkspace
        {...baseProps()}
        live={false}
        reconnecting
      />,
    );
    expect(screen.getByTestId('contextos-workspace')).toBeTruthy();
  });
});

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ContextOSWorkspace } from './ContextOSWorkspace';
import { readFile } from '@/services/fileService';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState } from '@/app/hooks/useLlmRuntimeGate';

// The telemetry hook polls runtime/events/llm.observations.jsonl via readFile.
// Mock it so tests are deterministic (no real backend/network).
vi.mock('@/services/fileService', () => ({
  readFile: vi.fn(async () => ({ ok: true, data: { content: '' } })),
}));
const mockReadFile = vi.mocked(readFile);

const TELEMETRY_FIXTURE = [
  JSON.stringify({
    ts: '2026-06-15T10:00:00Z', ts_epoch: 1781856000.0, seq: 1, event_id: 'p1',
    kind: 'observation', actor: 'PM', name: 'prompt_context', refs: { run_id: 'r1', step: 1 },
    summary: 'Prompt Context Injection', ok: true,
    output: { context_hash: 'h1', context_snapshot: 'runtime/snap/h1.json' },
  }),
  JSON.stringify({
    ts: '2026-06-15T10:00:02Z', ts_epoch: 1781856002.0, seq: 2, event_id: 'c1',
    kind: 'observation', actor: 'PM', name: 'llm_call', refs: { mode: 'pm.planning' },
    summary: 'pm planning call', ok: true,
    output: { usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 } },
    duration_ms: 2400,
  }),
].join('\n');

beforeEach(() => {
  mockReadFile.mockReset();
  mockReadFile.mockResolvedValue({ ok: true, data: { content: '' } } as Awaited<ReturnType<typeof readFile>>);
});

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
    snapshot: null,
    qualityGate: null,
  };
}

describe('ContextOSWorkspace', () => {
  it('renders the dashboard shell + all 8 pipeline stages with empty props', () => {
    render(<ContextOSWorkspace {...baseProps()} />);
    expect(screen.getByTestId('contextos-workspace')).toBeTruthy();
    for (const id of ['request', 'turflog', 'working_mem', 'projection', 'role_signal', 'budget', 'prompt', 'llm']) {
      expect(screen.getByTestId(`contextos-stage-${id}`)).toBeTruthy();
    }
    // 7 component-health cards
    for (const id of ['turflog', 'working_mem', 'projection', 'role_signal', 'budget', 'prompt', 'telemetry']) {
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

  it('renders real token totals and stays crash-free with populated data', () => {
    const usageStats: UsageStats = {
      totals: { prompt_tokens: 8000, completion_tokens: 2000, total_tokens: 10000 },
      calls: 5,
      estimated_calls: 0,
      by_mode: { pm: { total_tokens: 6000, calls: 3 }, director: { total_tokens: 4000, calls: 2 } },
    };
    const dialogueEvents: DialogueEvent[] = [
      { speaker: 'PM', content: 'plan ready', timestamp: '2026-06-15T10:00:00Z', type: 'message' },
      { speaker: 'Director', content: 'executing', timestamp: '2026-06-15T10:00:05Z', type: 'progress' },
    ];
    render(
      <ContextOSWorkspace
        {...baseProps()}
        usageStats={usageStats}
        dialogueEvents={dialogueEvents}
        pmRunning
        currentPhase="planning"
      />,
    );
    // total tokens appears (header chip + budget headline)
    expect(screen.getAllByText('10,000').length).toBeGreaterThan(0);
  });

  it('surfaces REAL ContextOS telemetry from the observation log', async () => {
    mockReadFile.mockResolvedValue({
      ok: true,
      data: { content: TELEMETRY_FIXTURE },
    } as Awaited<ReturnType<typeof readFile>>);

    render(<ContextOSWorkspace {...baseProps()} />);

    // The telemetry source badge appears once real observations are parsed.
    const sourceBadge = await screen.findByTestId('contextos-telemetry-source');
    expect(sourceBadge.textContent).toContain('REAL');
    expect(sourceBadge.textContent).toContain('1 投影');
    expect(sourceBadge.textContent).toContain('1 快照');

    // Real-time freshness badge flips to live telemetry.
    await waitFor(() => {
      expect(screen.getByTestId('contextos-telemetry-freshness').textContent).toContain('实时遥测');
    });

    // The real observation events appear in the decision log.
    expect(screen.getByText('Prompt Context Injection')).toBeTruthy();
    expect(screen.getByText('pm planning call')).toBeTruthy();

    // Real token total (1500) is reflected in the budget headline.
    expect(screen.getAllByText('1,500').length).toBeGreaterThan(0);
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

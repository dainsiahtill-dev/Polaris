import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ContextOSWorkspace } from './ContextOSWorkspace';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';
import type { LlmRuntimeGateState } from '@/app/hooks/useLlmRuntimeGate';

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
    // 4 role cards
    for (const id of ['pm', 'architect', 'director', 'qa']) {
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

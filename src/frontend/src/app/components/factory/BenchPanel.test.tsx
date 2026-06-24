/**
 * Smoke tests for BenchPanel — verifies the panel renders the bench session
 * list, shows the live event stream, and reacts to status changes without
 * crashing. Transport wiring is covered by the Nats-JetStream factoryService
 * contract tests — this test is about the visual layer.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { BenchPanel } from './BenchPanel';
import { useFactoryBench } from '@/hooks/useFactoryBench';
import type {
  FactoryBenchSessionDetail,
  FactoryBenchSessionSummary,
} from '@/services/benchService';

const mockHookValue = {
  sessions: [] as FactoryBenchSessionSummary[],
  currentSession: null as FactoryBenchSessionDetail | null,
  events: [],
  isStreaming: false,
  isLoading: false,
  error: null as string | null,
  refresh: vi.fn().mockResolvedValue(undefined),
  select: vi.fn().mockResolvedValue(undefined),
  disconnect: vi.fn(),
};

vi.mock('@/hooks/useFactoryBench', () => ({
  useFactoryBench: vi.fn(() => mockHookValue),
}));

describe('BenchPanel', () => {
  beforeEach(() => {
    mockHookValue.sessions = [];
    mockHookValue.currentSession = null;
    mockHookValue.events = [];
    mockHookValue.isStreaming = false;
    mockHookValue.isLoading = false;
    mockHookValue.error = null;
    vi.mocked(useFactoryBench).mockClear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing and does not subscribe when internal bench mode is not enabled', () => {
    render(<BenchPanel />);
    expect(screen.queryByTestId('bench-panel')).not.toBeInTheDocument();
    expect(useFactoryBench).not.toHaveBeenCalled();
  });

  it('shows the empty-state when no sessions exist', () => {
    render(<BenchPanel enabled />);
    expect(screen.getByTestId('bench-panel')).toBeTruthy();
    expect(screen.getByText(/暂无 bench session/)).toBeTruthy();
    expect(useFactoryBench).toHaveBeenCalledWith({
      autoSelect: 'newest',
      onWorkspaceChange: undefined,
    });
  });

  it('shows error message when hook reports one', () => {
    mockHookValue.error = 'backend offline';
    render(<BenchPanel enabled />);
    expect(screen.getByText('backend offline')).toBeTruthy();
  });

  it('renders the selected session with progress and live event stream', () => {
    mockHookValue.sessions = [
      {
        session_id: 'bench-20260617-1',
        work_dir: '/tmp/ws',
        project_ids: ['L1-01', 'L2-07'],
        total: 2,
        completed: 1,
        failed: 0,
        status: 'running',
        created_at: '2026-06-17T10:00:00Z',
        updated_at: '2026-06-17T10:01:00Z',
        metadata: {},
      },
    ];
    mockHookValue.currentSession = {
      ...mockHookValue.sessions[0],
      events_path: '/tmp/sessions/bench-20260617-1/events.jsonl',
      events: [
        {
          type: 'factory_bench.project.started',
          name: 'L1-01',
          actor: 'factory-bench',
          summary: 'L1-01 starting',
          ts: '2026-06-17T10:00:30Z',
          meta: { project_id: 'L1-01' },
        },
        {
          type: 'factory_bench.project.completed',
          name: 'L1-01',
          actor: 'factory-bench',
          summary: 'L1-01 done',
          ok: true,
          ts: '2026-06-17T10:01:00Z',
          meta: { project_id: 'L1-01' },
        },
      ],
    };
    mockHookValue.events = mockHookValue.currentSession.events;
    mockHookValue.isStreaming = true;

    render(<BenchPanel enabled />);
    const panel = screen.getByTestId('bench-panel');
    expect(within(panel).getAllByText('运行中').length).toBeGreaterThan(0);
    // Progress bar reflects 1/2 = 50%.
    const progress = within(panel).getByTestId('bench-progress');
    expect(progress.getAttribute('data-progress')).toBe('50');
    // Event lines (rendered in reverse order, so .completed is at the top).
    const eventLines = within(panel).getAllByText(/factory_bench\./);
    expect(eventLines.length).toBeGreaterThanOrEqual(2);
  });

  it('displays terminal status badges correctly', () => {
    mockHookValue.sessions = [
      {
        session_id: 'bench-done',
        work_dir: '/tmp/ws',
        project_ids: ['L1-01'],
        total: 1,
        completed: 1,
        failed: 0,
        status: 'completed',
        created_at: '2026-06-17T10:00:00Z',
        updated_at: '2026-06-17T10:05:00Z',
        metadata: {},
      },
    ];
    render(<BenchPanel enabled />);
    expect(screen.getAllByText('已完成').length).toBeGreaterThanOrEqual(1);
  });
});

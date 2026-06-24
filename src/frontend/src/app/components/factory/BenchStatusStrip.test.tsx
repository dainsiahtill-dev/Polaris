/**
 * BenchStatusStrip — cross-page bench indicator. Smoke tests verify the
 * strip auto-hides when no session is active and renders session + last
 * event metadata when one is.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { BenchStatusStrip } from './BenchStatusStrip';
import { useFactoryBench } from '@/hooks/useFactoryBench';

const mockHookValue = {
  sessions: [] as Array<Record<string, unknown>>,
  currentSession: null as Record<string, unknown> | null,
  events: [] as Array<Record<string, unknown>>,
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

describe('BenchStatusStrip', () => {
  beforeEach(() => {
    mockHookValue.sessions = [];
    mockHookValue.currentSession = null;
    mockHookValue.events = [];
    mockHookValue.isStreaming = false;
    vi.mocked(useFactoryBench).mockClear();
  });

  it('renders nothing and does not subscribe when internal bench mode is not enabled', () => {
    const { container } = render(<BenchStatusStrip />);
    expect(container.firstChild).toBeNull();
    expect(useFactoryBench).not.toHaveBeenCalled();
  });

  it('renders nothing when no bench session is active', () => {
    const { container } = render(<BenchStatusStrip enabled />);
    expect(container.firstChild).toBeNull();
    expect(useFactoryBench).toHaveBeenCalledWith({ autoSelect: 'newest' });
  });

  it('shows a running session with progress and last event metadata', () => {
    mockHookValue.sessions = [
      {
        session_id: 'bench-running-1',
        work_dir: '/tmp/ws',
        project_ids: ['L1-01', 'L2-07', 'L3-15'],
        total: 3,
        completed: 1,
        failed: 0,
        status: 'running',
        created_at: '2026-06-17T10:00:00Z',
        updated_at: '2026-06-17T10:05:00Z',
        metadata: {},
      },
    ];
    mockHookValue.currentSession = mockHookValue.sessions[0];
    mockHookValue.events = [
      {
        type: 'factory_bench.project.started',
        name: 'L2-07',
        summary: 'L2-07 starting',
        meta: { project_id: 'L2-07' },
        ts: '2026-06-17T10:04:00Z',
      },
      {
        type: 'factory_bench.gate.evaluated',
        name: 'L2-07',
        summary: 'L2-07 chain_clean ok',
        meta: { project_id: 'L2-07' },
        ts: '2026-06-17T10:05:00Z',
      },
    ];
    mockHookValue.isStreaming = true;
    render(<BenchStatusStrip enabled />);
    const strip = screen.getByTestId('bench-status-strip');
    expect(strip.getAttribute('data-bench-session')).toBe('bench-running-1');
    expect(strip.getAttribute('data-bench-status')).toBe('running');
    const progress = within(strip).getByTestId('bench-strip-progress');
    expect(progress.getAttribute('data-progress')).toBe('33');
    const last = within(strip).getByTestId('bench-strip-last-event');
    expect(last.getAttribute('title')).toContain('factory_bench.gate.evaluated');
    expect(last.getAttribute('title')).toContain('L2-07');
  });

  it('reflects a completed session in the status tone', () => {
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
    mockHookValue.currentSession = mockHookValue.sessions[0];
    render(<BenchStatusStrip enabled />);
    const strip = screen.getByTestId('bench-status-strip');
    expect(strip.getAttribute('data-bench-status')).toBe('completed');
  });

  it('prefers the refreshed terminal session over a stale running detail snapshot', () => {
    mockHookValue.sessions = [
      {
        session_id: 'bench-cancelled',
        work_dir: '/tmp/ws',
        project_ids: ['L1-01', 'L1-02'],
        total: 2,
        completed: 0,
        failed: 1,
        status: 'cancelled',
        created_at: '2026-06-18T07:08:00Z',
        updated_at: '2026-06-18T07:10:45Z',
        metadata: {},
      },
    ];
    mockHookValue.currentSession = {
      ...mockHookValue.sessions[0],
      completed: 0,
      failed: 0,
      status: 'running',
    };

    render(<BenchStatusStrip enabled />);

    const strip = screen.getByTestId('bench-status-strip');
    expect(strip.getAttribute('data-bench-status')).toBe('cancelled');
    expect(strip).toHaveTextContent('已取消');
    expect(within(strip).getByTestId('bench-strip-progress').getAttribute('data-progress')).toBe('50');
  });

  it('does not resurrect an older running session after the newest session is terminal', () => {
    mockHookValue.sessions = [
      {
        session_id: 'bench-newest-failed',
        work_dir: '/tmp/new',
        project_ids: ['L1-01', 'L1-02'],
        total: 2,
        completed: 0,
        failed: 2,
        status: 'failed',
        created_at: '2026-06-18T07:05:00Z',
        updated_at: '2026-06-18T07:10:00Z',
        metadata: {},
      },
      {
        session_id: 'bench-old-stale-running',
        work_dir: '/tmp/old',
        project_ids: ['L1-01'],
        total: 1,
        completed: 0,
        failed: 0,
        status: 'running',
        created_at: '2026-06-17T07:05:00Z',
        updated_at: '2026-06-17T07:05:00Z',
        metadata: {},
      },
    ];
    mockHookValue.currentSession = { ...mockHookValue.sessions[0] };

    render(<BenchStatusStrip enabled />);

    const strip = screen.getByTestId('bench-status-strip');
    expect(strip.getAttribute('data-bench-session')).toBe('bench-newest-failed');
    expect(strip.getAttribute('data-bench-status')).toBe('failed');
  });
});

  it('reflects live per-project counters in progress percentage', () => {
    mockHookValue.sessions = [
      {
        session_id: 'bench-progress',
        work_dir: '/tmp/ws',
        project_ids: ['L1-01', 'L2-07', 'L3-15', 'L4-23'],
        total: 4,
        completed: 2,
        failed: 1,
        status: 'running',
        created_at: '2026-06-17T10:00:00Z',
        updated_at: '2026-06-17T10:05:00Z',
        metadata: {},
      },
    ];
    mockHookValue.currentSession = mockHookValue.sessions[0];
    render(<BenchStatusStrip enabled />);
    const progress = screen.getByTestId('bench-strip-progress');
    // (2 completed + 1 failed) / 4 total = 75%.
    expect(progress.getAttribute('data-progress')).toBe('75');
  });

  it('falls back to project_ids length when total is missing', () => {
    mockHookValue.sessions = [
      {
        session_id: 'bench-no-total',
        work_dir: '/tmp/ws',
        project_ids: ['L1-01', 'L2-07'],
        // total is 0/undefined; should fall back to project_ids.length.
        total: 0,
        completed: 1,
        failed: 0,
        status: 'running',
        created_at: '2026-06-17T10:00:00Z',
        updated_at: '2026-06-17T10:05:00Z',
        metadata: {},
      },
    ];
    mockHookValue.currentSession = mockHookValue.sessions[0];
    render(<BenchStatusStrip enabled />);
    const progress = screen.getByTestId('bench-strip-progress');
    expect(progress.getAttribute('data-progress')).toBe('50');
  });

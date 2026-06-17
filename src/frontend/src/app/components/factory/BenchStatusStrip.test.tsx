/**
 * BenchStatusStrip — cross-page bench indicator. Smoke tests verify the
 * strip auto-hides when no session is active and renders session + last
 * event metadata when one is.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { BenchStatusStrip } from './BenchStatusStrip';

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
  useFactoryBench: () => mockHookValue,
}));

describe('BenchStatusStrip', () => {
  beforeEach(() => {
    mockHookValue.sessions = [];
    mockHookValue.currentSession = null;
    mockHookValue.events = [];
    mockHookValue.isStreaming = false;
  });

  it('renders nothing when no bench session is active', () => {
    const { container } = render(<BenchStatusStrip />);
    expect(container.firstChild).toBeNull();
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
    render(<BenchStatusStrip />);
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
    render(<BenchStatusStrip />);
    const strip = screen.getByTestId('bench-status-strip');
    expect(strip.getAttribute('data-bench-status')).toBe('completed');
  });
});

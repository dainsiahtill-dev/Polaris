import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api', () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from '@/api';

import { ContextStoreStatsPanel } from './ContextStoreStatsPanel';

const mockedApiFetch = apiFetch as unknown as ReturnType<typeof vi.fn>;

function mockJsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Err',
    text: async () => JSON.stringify(body),
    json: async () => body,
    headers: new Headers(),
  } as unknown as Response;
}

function mockAdminDisabled() {
  mockedApiFetch.mockResolvedValueOnce(mockJsonResponse(404, {
    code: 'ADMIN_DISABLED',
    message: 'Context admin surface is disabled',
  }));
}

function mockAdminReady(overrides: Partial<{
  file_count: number;
  total_bytes: number;
  enabled: boolean;
  oldest_mtime: number;
  last_sweep_at: number;
  last_sweep_report: unknown;
}> = {}) {
  mockedApiFetch.mockResolvedValueOnce(mockJsonResponse(200, {
    workspace: '/repo',
    contexts_root: '/repo/runtime/contexts',
    file_count: overrides.file_count ?? 1500,
    total_bytes: overrides.total_bytes ?? 104857600,
    oldest_mtime: overrides.oldest_mtime ?? 1718000000.0,
    newest_mtime: 1718500000.0,
    config: {
      ttl_seconds: 604800,
      max_total_bytes: 524288000,
      max_files: 20000,
      sweep_min_interval_seconds: 300,
      enabled: overrides.enabled ?? true,
    },
    last_sweep_at: overrides.last_sweep_at ?? 1718400000.0,
    last_sweep_report: overrides.last_sweep_report ?? {
      scanned_files: 1500,
      removed_files: 250,
      removed_bytes: 10485760,
      kept_files: 1250,
      total_bytes_after: 94371840,
      elapsed_ms: 42,
      triggers: ['ttl', 'max_total_bytes'],
    },
  }));
}

function mockSweepOk() {
  mockedApiFetch.mockResolvedValueOnce(mockJsonResponse(200, {
    scanned_files: 1500,
    removed_files: 0,
    removed_bytes: 0,
    kept_files: 1500,
    total_bytes_after: 104857600,
    elapsed_ms: 7,
    triggers: ['admin-ui'],
  }));
}

describe('ContextStoreStatsPanel', () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders disabled hint when admin endpoint returns 404 ADMIN_DISABLED and basic endpoint also fails', async () => {
    // Admin endpoint returns 404
    mockAdminDisabled();
    // Basic endpoint also returns 404 (not available)
    mockedApiFetch.mockResolvedValueOnce(mockJsonResponse(404, { code: 'NOT_FOUND' }));
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-disabled')).toBeTruthy();
    });
    expect(within(screen.getByTestId('contextos-store-stats-disabled')).getByText(/管理员端点未启用/)).toBeTruthy();
    expect(screen.queryByTestId('contextos-store-stats-ready')).toBeNull();
  });

  it('renders ready view with capacity/utilization/last-sweep when admin stats endpoint returns 200', async () => {
    mockAdminReady();
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-ready')).toBeTruthy();
    });
    // utilization bar present
    expect(screen.getByText(/文件数利用比/)).toBeTruthy();
    expect(screen.getByText(/字节利用比/)).toBeTruthy();
    // last sweep report card with removed_files=250
    const lastSweep = screen.getByTestId('contextos-store-stats-last-sweep');
    expect(within(lastSweep).getByText('250')).toBeTruthy();
    // sweep button should be visible when admin endpoint is available
    expect(screen.getByTestId('contextos-store-stats-sweep')).toBeTruthy();
    // should not show read-only badge
    expect(screen.queryByText('只读')).toBeNull();
  });

  it('refreshes stats when a WebSocket snapshot signal changes', async () => {
    mockAdminReady({ file_count: 1, total_bytes: 1024, last_sweep_report: null });
    const { rerender } = render(<ContextStoreStatsPanel workspace="/repo" refreshSignal={null} />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-ready')).toBeTruthy();
    });
    expect(mockedApiFetch).toHaveBeenCalledTimes(1);

    mockAdminReady({ file_count: 2, total_bytes: 2048, last_sweep_report: null });
    rerender(<ContextStoreStatsPanel workspace="/repo" refreshSignal="ctx-ref-1:100:1" />);

    await waitFor(() => {
      expect(mockedApiFetch).toHaveBeenCalledTimes(2);
    });
    rerender(<ContextStoreStatsPanel workspace="/repo" refreshSignal="ctx-ref-1:100:1" />);
    expect(mockedApiFetch).toHaveBeenCalledTimes(2);
  });

  it('renders ready view without sweep button when basic stats endpoint returns 200', async () => {
    // Admin endpoint returns 404
    mockAdminDisabled();
    // Basic endpoint returns 200 with stats
    mockedApiFetch.mockResolvedValueOnce(mockJsonResponse(200, {
      workspace: '/repo',
      contexts_root: '/repo/runtime/contexts',
      file_count: 1500,
      total_bytes: 104857600,
      oldest_mtime: 1718000000.0,
      newest_mtime: 1718500000.0,
      config: {
        ttl_seconds: 604800,
        max_total_bytes: 524288000,
        max_files: 20000,
        sweep_min_interval_seconds: 300,
        enabled: true,
      },
      last_sweep_at: 1718400000.0,
      last_sweep_report: null,
    }));
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-ready')).toBeTruthy();
    });
    // utilization bar present
    expect(screen.getByText(/文件数利用比/)).toBeTruthy();
    expect(screen.getByText(/字节利用比/)).toBeTruthy();
    // sweep button should NOT be visible when using basic endpoint
    expect(screen.queryByTestId('contextos-store-stats-sweep')).toBeNull();
    // should show read-only badge
    expect(screen.getByText('只读')).toBeTruthy();
  });

  it('shows healthy status when under 70% utilization', async () => {
    mockAdminReady({ file_count: 100, total_bytes: 1024 });
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-ready')).toBeTruthy();
    });
    const ready = screen.getByTestId('contextos-store-stats-ready');
    expect(within(ready).getAllByText('健康').length).toBeGreaterThan(0);
  });

  it('shows warning status when either ratio >= 70%', async () => {
    mockAdminReady({ file_count: 15_000, total_bytes: 1024 });
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      const ready = screen.getByTestId('contextos-store-stats-ready');
      expect(within(ready).getAllByText('接近上限').length).toBeGreaterThan(0);
    });
  });

  it('shows critical status when either ratio >= 95%', async () => {
    mockAdminReady({ file_count: 19_500, total_bytes: 1024 });
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      const ready = screen.getByTestId('contextos-store-stats-ready');
      expect(within(ready).getAllByText('超限').length).toBeGreaterThan(0);
    });
  });

  it('shows disabled status pill when retention enabled=false', async () => {
    mockAdminReady({ enabled: false, last_sweep_report: null });
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      const ready = screen.getByTestId('contextos-store-stats-ready');
      expect(within(ready).getAllByText('已禁用').length).toBeGreaterThan(0);
    });
    // sweep button should be disabled when retention disabled
    const sweepButton = screen.getByTestId('contextos-store-stats-sweep') as HTMLButtonElement;
    expect(sweepButton.disabled).toBe(true);
  });

  it('renders nothing when enabled=false (fail-closed silent)', () => {
    const { container } = render(<ContextStoreStatsPanel workspace="/repo" enabled={false} />);
    expect(container.firstChild).toBeNull();
    // apiFetch should not be called when component is disabled
    expect(mockedApiFetch).not.toHaveBeenCalled();
  });

  it('triggers POST /v2/context/admin/sweep when sweep button is clicked', async () => {
    mockAdminReady();
    mockSweepOk();
    // After sweep, hook calls fetchOnce() to refresh stats
    mockAdminReady();
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-ready')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('contextos-store-stats-sweep'));
    await waitFor(() => {
      expect(mockedApiFetch).toHaveBeenCalledWith(
        '/v2/context/admin/sweep',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ triggers: ['admin-ui'] }),
        }),
      );
    });
  });

  it('shows error state with retry on initial 500 without previous data', async () => {
    mockedApiFetch.mockResolvedValueOnce(mockJsonResponse(500, 'boom'));
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-error')).toBeTruthy();
    });
    expect(within(screen.getByTestId('contextos-store-stats-error')).getByText(/HTTP 500/)).toBeTruthy();
  });

  it('retries the stats request from the initial error state', async () => {
    mockedApiFetch.mockResolvedValueOnce(mockJsonResponse(500, 'boom'));
    mockAdminReady({ file_count: 42, total_bytes: 2048, last_sweep_report: null });
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-error')).toBeTruthy();
    });

    fireEvent.click(within(screen.getByTestId('contextos-store-stats-error')).getByText('重试'));

    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-ready')).toBeTruthy();
    });
    expect(screen.queryByTestId('contextos-store-stats-error')).toBeNull();
    expect(screen.getByText('42')).toBeTruthy();
    expect(mockedApiFetch).toHaveBeenCalledTimes(2);
  });

  it('does not surface aborted stats reads as user-visible errors', async () => {
    mockedApiFetch.mockRejectedValueOnce(new Error('signal is aborted without reason'));
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(mockedApiFetch).toHaveBeenCalledWith('/v2/context/admin/stats', expect.any(Object));
    });
    expect(screen.queryByTestId('contextos-store-stats-error')).toBeNull();
    expect(screen.queryByText(/读取统计失败/)).toBeNull();
  });

  it('falls back to last successful data on subsequent error and surfaces it as warning', async () => {
    mockAdminReady();
    render(<ContextStoreStatsPanel workspace="/repo" />);
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-ready')).toBeTruthy();
    });
    // simulate a subsequent failing fetch
    mockedApiFetch.mockResolvedValueOnce(mockJsonResponse(500, 'boom'));
    fireEvent.click(screen.getByTestId('contextos-store-stats-refresh'));
    await waitFor(() => {
      expect(screen.getByTestId('contextos-store-stats-freshness-warning')).toBeTruthy();
    });
    // still renders the previous data
    expect(screen.getByTestId('contextos-store-stats-ready')).toBeTruthy();
  });
});

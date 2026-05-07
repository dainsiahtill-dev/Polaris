import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RuntimeDiagnosticsWorkspace } from './RuntimeDiagnosticsWorkspace';
import { apiFetchFresh } from '@/api';

vi.mock('@/api', () => ({
  apiFetchFresh: vi.fn(),
}));

const mockedApiFetchFresh = vi.mocked(apiFetchFresh);

function jsonResponse(payload: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  } as Response;
}

describe('RuntimeDiagnosticsWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApiFetchFresh.mockResolvedValue(jsonResponse({
      generated_at: '2026-05-07T10:20:30Z',
      nats: {
        ok: true,
        enabled: true,
        required: true,
        connected: true,
        lifecycle: [{ state: 'connected', message: 'NATS connected', timestamp: '2026-05-07T10:20:00Z' }],
      },
      websocket: {
        reconnect_attempts: 2,
        events: [{ state: 'reconnect', message: 'consumer restored', timestamp: '2026-05-07T10:19:00Z' }],
      },
      rate_limit: {
        status: 'normal',
        limit: 120,
        remaining: 118,
        retry_after_ms: 0,
      },
    }));
  });

  it('renders diagnostics cards from the endpoint and current websocket props', async () => {
    render(
      <RuntimeDiagnosticsWorkspace
        workspace="C:/Temp/Product"
        connectionState={{ live: true, reconnecting: false, attemptCount: 1 }}
        onBackToMain={vi.fn()}
      />,
    );

    expect(await screen.findByText('运行诊断')).toBeInTheDocument();
    expect(screen.getByText('NATS lifecycle')).toBeInTheDocument();
    expect(screen.getByText('WebSocket reconnect')).toBeInTheDocument();
    expect(screen.getByText('Rate limit')).toBeInTheDocument();
    expect(screen.getByText('WS LIVE')).toBeInTheDocument();
    expect(screen.getByText('NATS connected')).toBeInTheDocument();
    expect(mockedApiFetchFresh).toHaveBeenCalledWith('/v2/runtime/diagnostics');
  });

  it('refreshes diagnostics when the refresh button is clicked', async () => {
    mockedApiFetchFresh
      .mockResolvedValueOnce(jsonResponse({
        nats: { status: 'connecting' },
        websocket: { reconnect_attempts: 1 },
        rate_limit: { status: 'normal', remaining: 10 },
      }))
      .mockResolvedValueOnce(jsonResponse({
        nats: { status: 'connected', events: [{ state: 'connected', message: 'NATS restored' }] },
        websocket: { reconnect_attempts: 0 },
        rate_limit: { status: 'limited', remaining: 0 },
      }));

    render(
      <RuntimeDiagnosticsWorkspace
        workspace="C:/Temp/Product"
        connectionState={{ live: false, reconnecting: true, attemptCount: 3 }}
        onBackToMain={vi.fn()}
      />,
    );

    await waitFor(() => expect(mockedApiFetchFresh).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('runtime-diagnostics-refresh'));

    expect(await screen.findByText('NATS restored')).toBeInTheDocument();
    expect(mockedApiFetchFresh).toHaveBeenCalledTimes(2);
  });

  it('renders nested backend diagnostics contract fields', async () => {
    mockedApiFetchFresh.mockResolvedValueOnce(jsonResponse({
      schema_version: 'runtime_diagnostics.v1',
      generated_at: '2026-05-07T10:20:30Z',
      nats: {
        state: 'server_reachable',
        ok: true,
        details: {
          enabled: true,
          required: true,
          client: { is_connected: false },
          managed_server: { managed: true, tcp_reachable: true, process_pid: 32220 },
        },
      },
      websocket: {
        state: 'active',
        ok: true,
        details: { active_connections: 1, total_connections: 4 },
      },
      rate_limit: {
        state: 'blocking',
        ok: false,
        details: {
          requests_per_second: 10,
          burst_size: 20,
          store: { blocked_count: 2, total_violations: 3, entry_count: 5 },
        },
      },
    }));

    render(
      <RuntimeDiagnosticsWorkspace
        workspace="C:/Temp/Product"
        connectionState={{ live: true, reconnecting: false, attemptCount: 1 }}
        onBackToMain={vi.fn()}
      />,
    );

    expect(await screen.findByText('SERVER_REACHABLE')).toBeInTheDocument();
    expect(screen.getByText('32220')).toBeInTheDocument();
    expect(screen.getByText('BLOCKING')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });
});

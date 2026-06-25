import { render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  listInstances: vi.fn(),
  registerMessageHandler: vi.fn(() => vi.fn()),
  subscribeChannels: vi.fn(() => vi.fn()),
  noopAction: vi.fn(),
}));

vi.mock('@/runtime/transport', () => ({
  useConnectionState: () => ({ connected: true, reconnecting: false, error: null, attemptCount: 0 }),
  useMessageHandler: () => ({ registerMessageHandler: mocks.registerMessageHandler }),
  useTransportActions: () => ({ subscribeChannels: mocks.subscribeChannels }),
}));

vi.mock('@/services/instances', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/instances')>();
  return {
    ...actual,
    listInstances: mocks.listInstances,
    startInstance: mocks.noopAction,
    stopInstance: mocks.noopAction,
    restartInstance: mocks.noopAction,
    deleteInstance: mocks.noopAction,
    getInstanceLogs: mocks.noopAction,
  };
});

import {
  LauncherWorkspace,
  instanceSubtitle,
  isCurrentControlInstance,
  isLauncherBackendOpenable,
  isLauncherBackendReady,
  launcherInstanceStatusTone,
} from './LauncherWorkspace';
import type { PolarisInstance } from '@/services/instances';

function instance(overrides: Partial<PolarisInstance> = {}): PolarisInstance {
  return {
    schema_version: 1,
    instance_id: 'bench-l1-01',
    name: 'L1-01',
    kind: 'bench_project',
    polaris_root: '/repo',
    workspace: '/tmp/factory/L1-01',
    runtime_root: '/tmp/factory/L1-01/runtime',
    backend_port: 50017,
    frontend_port: 5178,
    backend_url: 'http://127.0.0.1:50017',
    frontend_url: 'http://127.0.0.1:5178',
    token: 'instance-token',
    backend_reload: false,
    frontend_vite: true,
    start_frontend: true,
    status: 'running',
    backend_pid: 101,
    frontend_pid: 102,
    backend_alive: true,
    frontend_alive: true,
    created_at: '',
    updated_at: '',
    last_started_at: '',
    last_stopped_at: '',
    bench: {},
    metadata: { backend_health: 'ok', frontend_health: 'ok' },
    ...overrides,
  };
}

beforeEach(() => {
  mocks.listInstances.mockReset();
  mocks.listInstances.mockResolvedValue({ ok: true, data: { instances: [] } });
  mocks.noopAction.mockReset();
  mocks.noopAction.mockResolvedValue({ ok: true, data: {} });
  mocks.registerMessageHandler.mockClear();
  mocks.subscribeChannels.mockClear();
});

describe('Launcher instance readiness display', () => {
  it('treats HTTP health as the backend readiness source', () => {
    expect(isLauncherBackendReady(instance())).toBe(true);
    expect(isLauncherBackendReady(instance({ metadata: { backend_health: 'starting' } }))).toBe(false);
    expect(isLauncherBackendReady(instance({ metadata: {} }))).toBe(false);
  });

  it('shows success for a running projected process that is openable', () => {
    const running = instance({
      metadata: { backend_health: 'starting', frontend_health: 'ok' },
      backend_alive: true,
    });

    expect(isLauncherBackendOpenable(running)).toBe(true);
    expect(launcherInstanceStatusTone(running)).toBe('success');
  });

  it('allows opening process-projected running instances and marks them as success', () => {
    const projected = instance({
      metadata: { backend_health: 'process', frontend_health: 'disabled' },
      backend_alive: true,
      frontend_alive: false,
      start_frontend: false,
      status: 'running',
    });

    expect(isLauncherBackendReady(projected)).toBe(false);
    expect(isLauncherBackendOpenable(projected)).toBe(true);
    expect(launcherInstanceStatusTone(projected)).toBe('success');
  });

  it('keeps a running dedicated frontend instance in warning state before frontend is alive', () => {
    const noFrontend = instance({
      metadata: { backend_health: 'process', frontend_health: 'stopped' },
      backend_alive: true,
      frontend_alive: false,
      start_frontend: true,
      status: 'running',
    });

    expect(isLauncherBackendOpenable(noFrontend)).toBe(false);
    expect(launcherInstanceStatusTone(noFrontend)).toBe('warning');
  });

  it('allows opening a main-style instance that uses the shared frontend', () => {
    const main = instance({
      instance_id: 'main',
      kind: 'development',
      metadata: { backend_health: 'process', frontend_health: 'disabled' },
      backend_alive: true,
      frontend_alive: false,
      start_frontend: false,
      status: 'running',
    });

    expect(isLauncherBackendOpenable(main)).toBe(true);
  });

  it('shows success only after running backend health is ok', () => {
    expect(launcherInstanceStatusTone(instance())).toBe('success');
  });

  it('shows failed instances as error', () => {
    expect(
      launcherInstanceStatusTone(
        instance({
          status: 'failed',
          backend_alive: false,
          frontend_alive: false,
          backend_pid: null,
          frontend_pid: null,
          metadata: { backend_health: 'stopped', frontend_health: 'stopped' },
        }),
      ),
    ).toBe('error');
  });

  it('adds bench project and work-dir identity to the card subtitle', () => {
    expect(
      instanceSubtitle(
        instance({
          instance_id: 'factory-bench-l1-05',
          bench: {
            project_id: 'L1-05',
            bench_workspace: '/tmp/factory-bench-L1-05-r06',
          },
        }),
      ),
    ).toBe('factory-bench-l1-05 · bench_project · L1-05 · factory-bench-L1-05-r06');
  });

  it('identifies only the current control backend instance as self-managed', () => {
    expect(isCurrentControlInstance(instance({ instance_id: 'main' }), 'main')).toBe(true);
    expect(isCurrentControlInstance(instance({ instance_id: 'factory-bench-l1-05' }), 'main')).toBe(false);
    expect(isCurrentControlInstance(instance({ instance_id: 'factory-bench-l1-05' }), 'factory-bench-l1-05')).toBe(true);
  });

  it('keeps the launcher and instance list scrollable when many instances are registered', async () => {
    const manyInstances = Array.from({ length: 36 }, (_, index) =>
      instance({
        instance_id: `factory-bench-l1-${String(index + 1).padStart(2, '0')}`,
        name: `L1-${String(index + 1).padStart(2, '0')}`,
        workspace: `/tmp/factory-bench-l1-${String(index + 1).padStart(2, '0')}/L1-${String(index + 1).padStart(2, '0')}`,
        backend_port: 49978 + index,
        frontend_port: 5174 + index,
      }),
    );
    mocks.listInstances.mockResolvedValue({ ok: true, data: { instances: manyInstances } });

    render(createElement(LauncherWorkspace));

    expect(await screen.findByText('L1-36')).toBeInTheDocument();
    expect(screen.getByText('共 36 个')).toBeInTheDocument();
    expect(screen.getByTestId('launcher-scroll-root')).toHaveClass('overflow-y-auto');
    expect(screen.getByTestId('launcher-instance-panel')).toHaveClass('overflow-hidden');
    expect(screen.getByTestId('launcher-instance-list')).toHaveClass('overflow-y-auto');
    expect(screen.getByTestId('launcher-instance-list')).toHaveClass('flex-1');
  });
});

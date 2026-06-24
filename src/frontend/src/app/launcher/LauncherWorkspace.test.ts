import { describe, expect, it } from 'vitest';
import {
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

describe('Launcher instance readiness display', () => {
  it('treats HTTP health as the backend readiness source', () => {
    expect(isLauncherBackendReady(instance())).toBe(true);
    expect(isLauncherBackendReady(instance({ metadata: { backend_health: 'starting' } }))).toBe(false);
    expect(isLauncherBackendReady(instance({ metadata: {} }))).toBe(false);
  });

  it('does not show success for a running process before backend HTTP is ready', () => {
    const starting = instance({
      metadata: { backend_health: 'starting', frontend_health: 'ok' },
      backend_alive: true,
    });

    expect(launcherInstanceStatusTone(starting)).toBe('warning');
  });

  it('allows opening process-projected running instances without showing HTTP success', () => {
    const projected = instance({
      metadata: { backend_health: 'process', frontend_health: 'disabled' },
      backend_alive: true,
      frontend_alive: false,
      start_frontend: false,
      status: 'running',
    });

    expect(isLauncherBackendReady(projected)).toBe(false);
    expect(isLauncherBackendOpenable(projected)).toBe(true);
    expect(launcherInstanceStatusTone(projected)).toBe('warning');
  });

  it('does not allow opening a dedicated frontend instance before frontend is alive', () => {
    const noFrontend = instance({
      metadata: { backend_health: 'process', frontend_health: 'stopped' },
      backend_alive: true,
      frontend_alive: false,
      start_frontend: true,
      status: 'running',
    });

    expect(isLauncherBackendOpenable(noFrontend)).toBe(false);
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
});

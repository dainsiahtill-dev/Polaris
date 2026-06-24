import { describe, expect, it } from 'vitest';
import {
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

  it('shows success only after running backend health is ok', () => {
    expect(launcherInstanceStatusTone(instance())).toBe('success');
  });
});

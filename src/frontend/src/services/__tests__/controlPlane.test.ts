import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockApiGet = vi.hoisted(() => vi.fn());
const mockApiPost = vi.hoisted(() => vi.fn());

vi.mock('../apiClient', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../apiClient')>()),
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));

import {
  controlPlaneProjectionFromRuntimeMessage,
  getControlPlaneProjection,
  getVerifierPolicy,
  updateVerifierPolicy,
} from '../controlPlane';

describe('controlPlane service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiGet.mockResolvedValue({ ok: true, data: { source: 'run_ledger_projection' } });
    mockApiPost.mockResolvedValue({ ok: true, data: { source: 'control_plane.verifier_policy' } });
  });

  it('reads the platform run-ledger projection without bench service coupling', async () => {
    await getControlPlaneProjection({
      workspace: '/tmp/polaris-workspace',
      runId: 'run-1',
      maxRuns: 3,
    });

    const [path, fallback] = mockApiGet.mock.calls[0] as [string, string];
    expect(path).toBe(
      '/v2/control-plane/ledger/projection?workspace=%2Ftmp%2Fpolaris-workspace&run_id=run-1&max_runs=3',
    );
    expect(path).not.toContain('bench');
    expect(path).not.toContain('factory');
    expect(fallback).toBe('获取 Control Plane 账本投影失败');
  });

  it('reads platform verifier policy through control-plane namespace', async () => {
    await getVerifierPolicy({ workspace: '/tmp/polaris-workspace' });

    const [path, fallback] = mockApiGet.mock.calls[0] as [string, string];
    expect(path).toBe('/v2/control-plane/verifier-policy?workspace=%2Ftmp%2Fpolaris-workspace');
    expect(path).not.toContain('bench');
    expect(path).not.toContain('factory');
    expect(fallback).toBe('获取 Control Plane 验收策略失败');
  });

  it('saves optional verifier policy without bench service coupling', async () => {
    await updateVerifierPolicy(
      {
        browser_enabled: true,
        visual_enabled: true,
        required_modalities: ['browser'],
      },
      { workspace: '/tmp/polaris-workspace' }
    );

    const [path, body, fallback] = mockApiPost.mock.calls[0] as [string, unknown, string];
    expect(path).toBe('/v2/control-plane/verifier-policy?workspace=%2Ftmp%2Fpolaris-workspace');
    expect(path).not.toContain('bench');
    expect(path).not.toContain('factory');
    expect(body).toEqual({
      browser_enabled: true,
      visual_enabled: true,
      required_modalities: ['browser'],
    });
    expect(fallback).toBe('保存 Control Plane 验收策略失败');
  });

  it('extracts control-plane projection from runtime.v2 wrapped events', () => {
    const projection = {
      schema_version: 1,
      source: 'run_ledger_projection',
      available: true,
      ok: true,
      status: 'ready',
      audit_path: '/tmp/workspace/runtime/control_plane/ledger',
      compat_ledgers_included: false,
      total: 1,
      projected: 1,
      missing: 0,
      failed: 0,
      detail: 'run ledger projection 1 project(s), 0 failed',
      projects: [
        {
          project_id: 'P1',
          ok: true,
          integrity_ok: true,
          outcome_ok: true,
          gate_count: 1,
          failed_gate_count: 0,
          latest_token_id: 'token-1',
          detail: 'gate passed',
          missing: [],
        },
      ],
    };

    const parsed = controlPlaneProjectionFromRuntimeMessage({
      type: 'EVENT',
      protocol: 'runtime.v2',
      cursor: 42,
      event: {
        schema_version: 'runtime.v2',
        channel: 'status.control_plane',
        kind: 'control_plane_ledger_projection_update',
        payload: { projection },
      },
    });

    expect(parsed).toEqual(projection);
  });

  it('rejects non-control-plane runtime events as projection sources', () => {
    const parsed = controlPlaneProjectionFromRuntimeMessage({
      type: 'EVENT',
      protocol: 'runtime.v2',
      event: {
        schema_version: 'runtime.v2',
        channel: 'event.bench',
        kind: 'bench_session_updated',
        payload: {
          projection: {
            source: 'run_ledger_projection',
            projects: [],
          },
        },
      },
    });

    expect(parsed).toBeNull();
  });
});

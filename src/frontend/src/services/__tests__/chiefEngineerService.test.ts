import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockApiGet = vi.fn();
const mockApiPost = vi.fn();

vi.mock('@/services/apiClient', () => ({
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));

import {
  generateChiefEngineerBlueprint,
  getChiefEngineerDiagnostics,
  getChiefEngineerBlueprint,
  getChiefEngineerBlueprintStatus,
  listChiefEngineerBlueprints,
} from '../chiefEngineerService';

describe('chiefEngineerService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads Chief Engineer diagnostics through the v2 backend route', async () => {
    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        role: 'chief_engineer',
        generated_at: '2026-05-23T08:00:00Z',
        workspace: {
          ok: true,
          status: 'ok',
          workspace: 'C:/Temp/Product',
          exists: true,
          error: null,
        },
        blueprints: {
          ok: true,
          status: 'ready',
          source: 'runtime/blueprints',
          total: 1,
          loadable: 1,
          invalid_payloads: 0,
          director_handoff_ready: true,
          latest_updated_at: '2026-05-23T08:00:00Z',
          error: null,
        },
        issues: [],
      },
    });

    const result = await getChiefEngineerDiagnostics();

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/diagnostics',
      'Failed to load Chief Engineer diagnostics',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.blueprints.director_handoff_ready).toBe(true);
  });

  it('generates a Chief Engineer blueprint through the v2 command route', async () => {
    mockApiPost.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        task_id: 'PM-42',
        workspace: 'C:/Temp/Product',
        status: 'generated',
        blueprint_id: 'ce_PM-42',
        blueprint_path: 'runtime/blueprints/ce_PM-42.json',
        source: 'runtime/blueprints',
        summary: 'Generated blueprint',
        recommendations: ['Verify acceptance'],
        risks: [],
        blueprint: { task_id: 'PM-42' },
      },
    });

    const payload = {
      task_id: 'PM-42',
      objective: 'Build Director task board',
      context: { target_files: ['src/app.tsx'] },
    };
    const result = await generateChiefEngineerBlueprint(payload);

    expect(mockApiPost).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints',
      payload,
      'Failed to generate Chief Engineer blueprint',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.blueprint_id).toBe('ce_PM-42');
  });

  it('loads Chief Engineer blueprint status with encoded query params', async () => {
    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: false,
        task_id: 'PM 42',
        workspace: 'C:/Temp/Product',
        status: 'missing',
        blueprint_id: null,
        blueprint_path: null,
        source: 'runtime/blueprints',
        summary: 'No blueprint',
        recommendations: [],
        risks: [],
        blueprint: {},
      },
    });

    const result = await getChiefEngineerBlueprintStatus('PM 42', 'run/1');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints/status?task_id=PM+42&run_id=run%2F1',
      'Failed to load Chief Engineer blueprint status',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.status).toBe('missing');
  });

  it('lists Chief Engineer blueprints through the v2 backend route', async () => {
    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: {
        total: 1,
        blueprints: [{ blueprint_id: 'bp-1', title: 'Plan', source: 'runtime/blueprints' }],
      },
    });

    const result = await listChiefEngineerBlueprints();

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints',
      'Failed to list Chief Engineer blueprints',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.blueprints[0].blueprint_id).toBe('bp-1');
  });

  it('loads a Chief Engineer blueprint detail with an encoded id', async () => {
    mockApiGet.mockResolvedValueOnce({
      ok: true,
      data: {
        blueprint_id: 'bp 1',
        source: 'runtime/blueprints',
        blueprint: { summary: 'Director work package' },
      },
    });

    const result = await getChiefEngineerBlueprint('bp 1');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints/bp%201',
      'Failed to load Chief Engineer blueprint',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.blueprint.summary).toBe('Director work package');
  });
});

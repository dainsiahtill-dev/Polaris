import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockApiGet = vi.fn();
const mockApiPost = vi.fn();
const mockApiDelete = vi.fn();

vi.mock('@/services/apiClient', () => ({
  apiDelete: (...args: unknown[]) => mockApiDelete(...args),
  apiGet: (...args: unknown[]) => mockApiGet(...args),
  apiPost: (...args: unknown[]) => mockApiPost(...args),
}));

import {
  deleteChiefEngineerBlueprint,
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
        can_handoff: true,
        can_generate: true,
        generated_at: '2026-05-23T08:00:00Z',
        workspace: {
          ok: true,
          status: 'ok',
          workspace: 'C:/Temp/Product',
          exists: true,
          error: null,
        },
        llm: {
          ok: true,
          state: 'ready',
          role: 'chief_engineer',
          blocked_roles: [],
          unsupported_roles: [],
          required_ready_roles: ['chief_engineer'],
          provider_id: 'qwen',
          model: 'Qwen3-Max',
          error: null,
          details: {},
        },
        blueprints: {
          ok: true,
          status: 'ready',
          source: 'runtime/blueprints',
          total: 1,
          loadable: 1,
          invalid_payloads: 0,
          planned_tasks: 2,
          covered_tasks: 2,
          missing_task_ids: [],
          director_handoff_ready: true,
          latest_updated_at: '2026-05-23T08:00:00Z',
          error: null,
        },
        issues: [],
        generate_blockers: [],
        handoff_blockers: [],
      },
    });

    const result = await getChiefEngineerDiagnostics();

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/diagnostics',
      'Failed to load Chief Engineer diagnostics',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.llm.ok).toBe(true);
    expect(result.data?.can_generate).toBe(true);
    expect(result.data?.blueprints.director_handoff_ready).toBe(true);
    expect(result.data?.blueprints.covered_tasks).toBe(2);
  });

  it('passes explicit workspace to Chief Engineer diagnostics', async () => {
    mockApiGet.mockResolvedValueOnce({ ok: true, data: { ok: true } });

    await getChiefEngineerDiagnostics('C:/Temp/Product');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/diagnostics?workspace=C%3A%2FTemp%2FProduct',
      'Failed to load Chief Engineer diagnostics',
    );
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

  it('passes explicit workspace when generating a Chief Engineer blueprint', async () => {
    mockApiPost.mockResolvedValueOnce({ ok: true, data: { ok: true } });
    const payload = {
      task_id: 'PM-42',
      objective: 'Build Director task board',
    };

    await generateChiefEngineerBlueprint(payload, 'C:/Temp/Product');

    expect(mockApiPost).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints?workspace=C%3A%2FTemp%2FProduct',
      payload,
      'Failed to generate Chief Engineer blueprint',
    );
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

  it('passes explicit workspace to Chief Engineer blueprint status', async () => {
    mockApiGet.mockResolvedValueOnce({ ok: true, data: { ok: true } });

    await getChiefEngineerBlueprintStatus('PM 42', 'run/1', 'C:/Temp/Product');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints/status?task_id=PM+42&run_id=run%2F1&workspace=C%3A%2FTemp%2FProduct',
      'Failed to load Chief Engineer blueprint status',
    );
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

  it('passes explicit workspace when listing Chief Engineer blueprints', async () => {
    mockApiGet.mockResolvedValueOnce({ ok: true, data: { blueprints: [], total: 0 } });

    await listChiefEngineerBlueprints('C:/Temp/Product');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints?workspace=C%3A%2FTemp%2FProduct',
      'Failed to list Chief Engineer blueprints',
    );
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

  it('passes explicit workspace when loading Chief Engineer blueprint detail', async () => {
    mockApiGet.mockResolvedValueOnce({ ok: true, data: { blueprint_id: 'bp 1', blueprint: {} } });

    await getChiefEngineerBlueprint('bp 1', 'C:/Temp/Product');

    expect(mockApiGet).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints/bp%201?workspace=C%3A%2FTemp%2FProduct',
      'Failed to load Chief Engineer blueprint',
    );
  });

  it('deletes a Chief Engineer blueprint with an encoded id', async () => {
    mockApiDelete.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        blueprint_id: 'bp 1',
        deleted: true,
        source: 'runtime/blueprints',
      },
    });

    const result = await deleteChiefEngineerBlueprint('bp 1');

    expect(mockApiDelete).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints/bp%201',
      'Failed to delete Chief Engineer blueprint',
    );
    expect(result.ok).toBe(true);
    expect(result.data?.deleted).toBe(true);
  });

  it('passes explicit workspace when deleting a Chief Engineer blueprint', async () => {
    mockApiDelete.mockResolvedValueOnce({ ok: true, data: { ok: true, deleted: true } });

    await deleteChiefEngineerBlueprint('bp 1', 'C:/Temp/Product');

    expect(mockApiDelete).toHaveBeenCalledWith(
      '/v2/chief-engineer/blueprints/bp%201?workspace=C%3A%2FTemp%2FProduct',
      'Failed to delete Chief Engineer blueprint',
    );
  });
});

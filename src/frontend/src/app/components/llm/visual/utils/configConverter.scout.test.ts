import { getRoleDisplayLabel } from '@/app/constants/roleLabels';
import { describe, expect, it } from 'vitest';
import type { VisualGraphConfig } from '../types/visual';
import {
  buildVisualGraph,
  clearRoleAssignment,
  removeRoleBinding,
  updateProviderConcurrency,
  updateRoleBindingConcurrency,
  updateRoleAssignment,
  updateRoleConcurrency,
  validateRoleAssignments,
  visualToRuntimeConfig,
} from './configConverter';
import { getRoleLabel } from './validation';

// Scout (探子) was previously absent from the LLM visual config editor: it is an
// auxiliary read-only reconnaissance role, so it was never added to the editor's
// hardcoded role set. These tests lock in that Scout is now a first-class,
// OPTIONAL configurable role in the editor (rendered + labelled + round-trips),
// without becoming a *required* role that would block an otherwise-valid config.

describe('LLM visual config editor — Scout role', () => {
  it('renders a Scout role node in the visual graph', () => {
    const { nodes } = buildVisualGraph({ providers: {}, roles: {} });
    const scoutNode = nodes.find((node) => node.id === 'role:scout');
    expect(scoutNode).toBeDefined();
    expect(scoutNode?.type).toBe('role');
    expect(String((scoutNode?.data as { label?: string }).label || '')).toBe('Scout');
  });

  it('labels scout consistently across the editor helpers', () => {
    expect(getRoleLabel('scout')).toBe('Scout');
    expect(getRoleDisplayLabel('scout')).toBe('Scout');
  });

  it('persists a scout provider/model assignment and round-trips to runtime config', () => {
    const base: VisualGraphConfig = {
      providers: { openai: { type: 'openai', name: 'OpenAI' } },
      roles: {},
    };

    const next = updateRoleAssignment(base, 'scout', 'openai', 'gpt-5');
    expect(next.roles?.scout).toEqual(expect.objectContaining({ provider_id: 'openai', model: 'gpt-5' }));

    // The assignment survives conversion to the backend runtime config as a real
    // 'scout' binding — NOT coerced to 'architect' (the fallback for unknown roles).
    const runtime = visualToRuntimeConfig(next);
    const scoutAssignment = runtime.roleAssignments.find((assignment) => assignment.roleId === 'scout');
    expect(scoutAssignment).toBeDefined();
    expect(scoutAssignment?.providerId).toBe('openai');
    expect(scoutAssignment?.model).toBe('gpt-5');
  });

  it('treats scout as OPTIONAL — an unconfigured scout never invalidates the config', () => {
    const result = validateRoleAssignments({ providers: {}, roles: {} });
    expect(result.missing).not.toContain('scout');
    expect(result.incomplete).not.toContain('scout');
  });
});

describe('LLM visual config editor — multi-binding concurrency', () => {
  it('adds multiple model bindings to one role without replacing the first binding', () => {
    const base: VisualGraphConfig = {
      providers: {
        kimi: { type: 'kimi', name: 'Kimi', max_concurrency: 20 },
      },
      roles: {
        director: { max_concurrency: 5 },
      },
    };

    const first = updateRoleAssignment(base, 'director', 'kimi', 'kimi-k2', { maxConcurrency: 3 });
    const second = updateRoleAssignment(first, 'director', 'kimi', 'kimi-k1', { maxConcurrency: 2 });

    expect(second.roles.director.provider_id).toBe('kimi');
    expect(second.roles.director.model).toBe('kimi-k2');
    expect(second.roles.director.max_concurrency).toBe(5);
    expect(second.roles.director.bindings).toEqual([
      { provider_id: 'kimi', model: 'kimi-k2', max_concurrency: 3 },
      { provider_id: 'kimi', model: 'kimi-k1', max_concurrency: 2 },
    ]);
  });

  it('renders one model-to-role edge per binding, including same-provider bindings', () => {
    const config: VisualGraphConfig = {
      providers: { kimi: { type: 'kimi', name: 'Kimi' } },
      roles: {
        director: {
          max_concurrency: 4,
          bindings: [
            { provider_id: 'kimi', model: 'kimi-k2', max_concurrency: 2 },
            { provider_id: 'kimi', model: 'kimi-k1', max_concurrency: 2 },
          ],
        },
      },
    };

    const { nodes, edges } = buildVisualGraph(config);
    const modelRoleEdges = edges.filter((edge) => edge.data?.kind === 'model-to-role');
    const assignedModels = nodes
      .filter((node) => node.type === 'model' && node.data.kind === 'model')
      .filter((node) => node.data.assignedRoles?.includes('director'))
      .map((node) => node.data.model)
      .sort();

    expect(modelRoleEdges).toHaveLength(2);
    expect(assignedModels).toEqual(['kimi-k1', 'kimi-k2']);
  });

  it('removes only the selected binding when a model-role edge is deleted', () => {
    const config: VisualGraphConfig = {
      providers: { kimi: { type: 'kimi' } },
      roles: {
        director: {
          provider_id: 'kimi',
          model: 'kimi-k2',
          bindings: [
            { provider_id: 'kimi', model: 'kimi-k2', max_concurrency: 2 },
            { provider_id: 'kimi', model: 'kimi-k1', max_concurrency: 2 },
          ],
        },
      },
    };

    const next = removeRoleBinding(config, 'director', 'kimi', 'kimi-k2');

    expect(next.roles.director.provider_id).toBe('kimi');
    expect(next.roles.director.model).toBe('kimi-k1');
    expect(next.roles.director.bindings).toEqual([
      { provider_id: 'kimi', model: 'kimi-k1', max_concurrency: 2 },
    ]);
  });

  it('converts every role binding to runtime assignments with concurrency metadata', () => {
    const config: VisualGraphConfig = {
      providers: { kimi: { type: 'kimi', max_concurrency: 20 } },
      roles: {
        director: {
          max_concurrency: 5,
          bindings: [
            { provider_id: 'kimi', model: 'kimi-k2', max_concurrency: 3 },
            { provider_id: 'kimi', model: 'kimi-k1', max_concurrency: 2 },
          ],
        },
      },
    };

    const runtime = visualToRuntimeConfig(config);

    expect(runtime.roleAssignments.filter((assignment) => assignment.roleId === 'director')).toEqual([
      {
        roleId: 'director',
        providerId: 'kimi',
        model: 'kimi-k2',
        profile: 'default',
        maxConcurrency: 3,
        roleMaxConcurrency: 5,
      },
      {
        roleId: 'director',
        providerId: 'kimi',
        model: 'kimi-k1',
        profile: 'default',
        maxConcurrency: 2,
        roleMaxConcurrency: 5,
      },
    ]);
  });

  it('clears every binding for a role when clearing the role assignment', () => {
    const config: VisualGraphConfig = {
      providers: { kimi: { type: 'kimi' } },
      roles: {
        director: {
          provider_id: 'kimi',
          model: 'kimi-k2',
          bindings: [{ provider_id: 'kimi', model: 'kimi-k2' }],
        },
      },
    };

    const next = clearRoleAssignment(config, 'director');

    expect(next.roles.director.provider_id).toBeUndefined();
    expect(next.roles.director.model).toBeUndefined();
    expect(next.roles.director.bindings).toEqual([]);
  });

  it('updates provider, role, and binding concurrency knobs independently', () => {
    const config: VisualGraphConfig = {
      providers: { kimi: { type: 'kimi' } },
      roles: {
        director: {
          bindings: [{ provider_id: 'kimi', model: 'kimi-k2' }],
        },
      },
    };

    const providerCapped = updateProviderConcurrency(config, 'kimi', 20);
    const roleCapped = updateRoleConcurrency(providerCapped, 'director', 5);
    const bindingCapped = updateRoleBindingConcurrency(roleCapped, 'director', 'kimi', 'kimi-k2', 3);

    expect(bindingCapped.providers.kimi).toEqual(expect.objectContaining({ max_concurrency: 20 }));
    expect(bindingCapped.roles.director.max_concurrency).toBe(5);
    expect(bindingCapped.roles.director.bindings).toEqual([
      { provider_id: 'kimi', model: 'kimi-k2', max_concurrency: 3 },
    ]);
  });
});

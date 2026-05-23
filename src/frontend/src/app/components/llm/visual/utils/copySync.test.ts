import { describe, expect, it } from 'vitest';
import { buildVisualGraph, getConfigSummary, validateRoleAssignments } from './configConverter';
import { getRoleLabel, validateVisualGraph } from './validation';
import type { VisualGraphConfig } from '../types/visual';

describe('visual copy sync', () => {
  it('uses updated role labels in visual graph nodes', () => {
    const config: VisualGraphConfig = {
      providers: {},
      roles: {},
    };

    const { nodes } = buildVisualGraph(config);
    const roleLabels = new Map(
      nodes
        .filter((node) => node.type === 'role')
        .map((node) => [node.id, String((node.data as { label?: string }).label || '')])
    );

    expect(roleLabels.get('role:pm')).toBe('PM');
    expect(roleLabels.get('role:director')).toBe('Director');
    expect(roleLabels.get('role:chief_engineer')).toBe('Chief Engineer');
    expect(roleLabels.get('role:qa')).toBe('QA');
    expect(roleLabels.get('role:architect')).toBe('Architect');
  });

  it('uses updated role and provider wording in validation copy', () => {
    const config: VisualGraphConfig = {
      providers: {},
      roles: {},
    };

    const { issues } = validateVisualGraph(config);
    expect(issues.length).toBeGreaterThan(0);
    expect(issues.some((issue) => issue.message.includes('Provider'))).toBe(false);
    expect(issues.some((issue) => issue.message.includes('PM'))).toBe(true);
    expect(issues.some((issue) => issue.message.includes('提供商'))).toBe(true);

    expect(getRoleLabel('pm')).toBe('PM');
    expect(getRoleLabel('director')).toBe('Director');
    expect(getRoleLabel('qa')).toBe('QA');
    expect(getRoleLabel('architect')).toBe('Architect');
    expect(getRoleLabel('docs')).toBe('Architect');
  });

  it('requires Chief Engineer in role assignment validation and summary', () => {
    const config: VisualGraphConfig = {
      providers: {
        openai_compat: { type: 'openai_compat' },
      },
      roles: {
        pm: { provider_id: 'openai_compat', model: 'qwen3-max' },
        director: { provider_id: 'openai_compat', model: 'qwen3-max' },
        qa: { provider_id: 'openai_compat', model: 'qwen3-max' },
        architect: { provider_id: 'openai_compat', model: 'qwen3-max' },
      },
    };

    const validation = validateRoleAssignments(config);
    expect(validation.missing).toContain('chief_engineer');
    expect(getConfigSummary(config)).toContain('chief_engineer: [未配置]');
  });
});

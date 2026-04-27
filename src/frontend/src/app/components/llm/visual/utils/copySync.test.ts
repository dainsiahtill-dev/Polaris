import { buildVisualGraph } from './configConverter';
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
});

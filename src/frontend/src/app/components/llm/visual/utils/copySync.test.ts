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

    expect(roleLabels.get('role:pm')).toBe('尚书令');
    expect(roleLabels.get('role:director')).toBe('工部侍郎');
    expect(roleLabels.get('role:qa')).toBe('门下侍中');
    expect(roleLabels.get('role:architect')).toBe('中书令');
  });

  it('uses updated role and provider wording in validation copy', () => {
    const config: VisualGraphConfig = {
      providers: {},
      roles: {},
    };

    const { issues } = validateVisualGraph(config);
    expect(issues.length).toBeGreaterThan(0);
    expect(issues.some((issue) => issue.message.includes('Provider'))).toBe(false);
    expect(issues.some((issue) => issue.message.includes('尚书令'))).toBe(true);
    expect(issues.some((issue) => issue.message.includes('提供商'))).toBe(true);

    expect(getRoleLabel('pm')).toBe('尚书令');
    expect(getRoleLabel('director')).toBe('工部侍郎');
    expect(getRoleLabel('qa')).toBe('门下侍中');
    expect(getRoleLabel('architect')).toBe('中书令');
    expect(getRoleLabel('docs')).toBe('中书令');
  });
});

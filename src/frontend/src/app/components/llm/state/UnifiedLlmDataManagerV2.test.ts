import { describe, expect, it } from 'vitest';

import { createInitialState } from './canonicalState';
import { ListViewAdapter, VisualGraphViewAdapter } from './UnifiedLlmDataManagerV2';

describe('UnifiedLlmDataManagerV2 role labels', () => {
  it('projects Director and Chief Engineer labels into list view without swapping them', () => {
    const state = createInitialState();

    const view = new ListViewAdapter().adaptToView(state);
    const labels = new Map(view.roles.map((role) => [role.id, role.label]));

    expect(labels.get('director')).toBe('Director');
    expect(labels.get('chief_engineer')).toBe('Chief Engineer');
  });

  it('projects Director and Chief Engineer labels into visual graph view without swapping them', () => {
    const state = createInitialState();

    const view = new VisualGraphViewAdapter().adaptToView(state);
    const labels = new Map(view.nodes.filter((node) => node.kind === 'role').map((node) => [node.id, node.label]));

    expect(labels.get('director')).toBe('Director');
    expect(labels.get('chief_engineer')).toBe('Chief Engineer');
  });

  it('initializes Chief Engineer as a first-class configurable role', () => {
    const state = createInitialState();

    expect(state.entities.roleAssignments.chief_engineer).toEqual({
      roleId: 'chief_engineer',
      ready: false,
    });
    expect(state.entities.roleRequirements.chief_engineer).toMatchObject({
      roleId: 'chief_engineer',
      requiresThinking: true,
    });
  });
});

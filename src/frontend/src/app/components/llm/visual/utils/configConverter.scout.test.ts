import { getRoleDisplayLabel } from '@/app/constants/roleLabels';
import { describe, expect, it } from 'vitest';
import type { VisualGraphConfig } from '../types/visual';
import { buildVisualGraph, updateRoleAssignment, validateRoleAssignments, visualToRuntimeConfig } from './configConverter';
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

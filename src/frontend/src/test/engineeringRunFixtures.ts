import { vi } from 'vitest';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import { newEngineeringProjection } from '@/runtime/engineeringRunProjection';
export function makeEngineeringBinding(workspace = '/work'): EngineeringRunBinding {
  return { projection: newEngineeringProjection(workspace, ''), sessionId: '', loading: false, pending: null, error: null, discussion: '', discussionMetadata: null, connected: true, reconnecting: false, connectionError: null, start: vi.fn(async () => true), cancel: vi.fn(async () => true), resume: vi.fn(async () => true), refresh: vi.fn(async () => { }), discuss: vi.fn(async () => true) };
}

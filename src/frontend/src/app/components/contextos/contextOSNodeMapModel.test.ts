import { describe, expect, it } from 'vitest';
import { buildContextNodes, foldStateCounts } from './contextOSNodeMapModel';
import type { ContextPayload } from './ContextViewerModal';
import type { EngineeringEvent } from '@/services/api.types';
import type { FinalProviderRequestPayload } from './finalProviderRequestProtocol';

const snapshot: ContextPayload = {
  schema_version: 1,
  hash: 'abc',
  trace_id: null,
  call_id: 'run:qa:9',
  stored_at: null,
  message_count: 2,
  total_chars: 42,
  messages: [
    { role: 'system', content: 'QA working state' },
    { role: 'user', content: '{"workspace_revision":7}' },
  ],
};

const finalRequest: FinalProviderRequestPayload = {
  call_id: 'run:qa:9',
  provider_request: { body: { messages: [{ role: 'tool', tool_call_id: 'current-call' }] } },
};

function event(seq: number, kind: string, payload: Record<string, unknown>): EngineeringEvent {
  return { session_id: 'run', workspace: '/ws', seq, role: 'qa', kind, payload };
}

describe('ContextOS node folding model', () => {
  it('distinguishes active, referenced, evicted and RunCard folded nodes', () => {
    const nodes = buildContextNodes(snapshot, finalRequest, [
      event(1, 'tool_started', { call_id: 'current-call', tool: 'execute_command', turn_seq: 4 }),
      event(2, 'tool_observed', { call_id: 'old-call', tool: 'execute_command', ok: true, content: 'done' }),
      event(3, 'run_updated', { workspace_revision: 7, verification: 'passed' }),
      event(4, 'message', { message: { role: 'assistant', content: 'old exploration' } }),
    ]);
    expect(nodes.find((node) => node.id === 'event-1')?.foldState).toBe('ACTIVE');
    expect(nodes.find((node) => node.id === 'event-2')?.foldState).toBe('REFERENCE');
    expect(nodes.find((node) => node.id === 'event-3')?.foldState).toBe('FOLDED');
    expect(nodes.find((node) => node.id === 'event-4')?.foldState).toBe('EVICTED');
    expect(nodes.filter((node) => node.id.startsWith('request-')).every((node) => node.foldState === 'ACTIVE')).toBe(true);
    expect(foldStateCounts(nodes).ACTIVE).toBe(3);
  });

  it('keeps unresolved folding states unknown when final request is unavailable', () => {
    const nodes = buildContextNodes(snapshot, null, [
      event(8, 'message', { message: { role: 'assistant', content: 'unproven' } }),
    ]);
    expect(nodes[0].foldState).toBe('UNKNOWN');
  });
});

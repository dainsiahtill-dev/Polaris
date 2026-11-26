import { describe, expect, it } from 'vitest';
import { buildContextNodes, foldStateCounts, nodesFromManifest } from './contextOSNodeMapModel';
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

  it('projects deterministic and epoch memory cards into the node map', () => {
    const nodes = nodesFromManifest({
      schema_version: 'context.projection_manifest.v1',
      context_snapshot_ref: null,
      workspace: '/ws',
      run_id: 'run',
      call_id: null,
      nodes: [],
      run_cards: [],
      memory_cards: [
        { kind: 'failure_cluster', id: 'failure-1', title: 'root is not defined', summary: '8 次 · 未解决', evidence_refs: ['verify-1'], unresolved: true },
        { kind: 'epoch_card', id: 'epoch-0001-0010', title: 'epoch-0001-0010', summary: '完成基础模块', evidence_refs: [] },
        { kind: 'known_facts', id: 'known-facts', title: '文件事实索引', summary: 'a.py', wire_entry_count: 0,
          entries: [{ path: 'a.py', content_ref: 'filecard:' + 'a'.repeat(64), lines: 2, symbols: [], version: 'observed', candidate_check: 'unknown' }] },
      ],
      summary: { original_tokens: 0, projected_tokens: 0, compression_ratio: null, fold_counts: {}, kind_counts: {}, run_card_count: 0, scope: 'test' },
    });
    expect(nodes[0].kind).toBe('failure_cluster');
    expect(nodes[0].evidenceRef).toBe('verify-1');
    expect(nodes[0].parentId).toBe('memory-cards');
    expect(nodes[1].kind).toBe('epoch_card');
    expect(nodes[2].foldReason).toContain('0 / 1');
  });
});

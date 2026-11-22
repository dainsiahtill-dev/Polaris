import { describe, expect, it } from 'vitest';
import type { EngineeringEvent } from '@/services/api.types';
import {
  buildLiveContextGraph,
  layoutLiveContextNodes,
  liveContextMetrics,
  liveMemoryCards,
} from './contextOSNavigatorModel';

function event(seq: number, kind: string, payload: Record<string, unknown>, role = 'director'): EngineeringEvent {
  return { session_id: 'run', workspace: '/ws', seq, role, kind, payload, created_at: seq };
}

const fileCard = JSON.stringify({
  file_card: {
    path: 'src/app.ts',
    content_ref: 'filecard:src/app.ts',
    fold_state: 'FOLDED',
    fold_reason: 'superseded_by_newer_card',
    summary: '入口文件',
    content_preview: 'x'.repeat(400),
  },
});

const journal: EngineeringEvent[] = [
  event(1, 'run_updated', { workspace_revision: 3, verification: 'not_run', status: 'running' }),
  event(2, 'resident_agi_decision', { project_type: 'node', reason: 'package.json 与 tsconfig 指向 Node' }, 'resident_agi'),
  event(3, 'cartographer_completed', { phase: 'EDIT', intent: '实现入口' }, 'cartographer'),
  event(4, 'request_prepared', { turn_seq: 8, evidence_kind: 'auxiliary_prepared_request', request: { context: { phase: 'EDIT' } } }),
  event(5, 'llm_request_observed', {
    turn_seq: 8,
    model: 'kimi-for-coding',
    phase: 'EDIT',
    context_snapshot_ref: 'a'.repeat(24),
    status: 'ok',
    audit: { final_request_token_estimate: 6335, context_window_tokens: 1000000, context_window_utilization: 0.0063 },
  }),
  event(6, 'tool_observed', { turn_seq: 8, call_id: 'call_1', tool: 'read_file', ok: true, content: fileCard }),
  event(7, 'context_projection_folded', { projection_ref: 'filecard:src/app.ts', node_kind: 'file_card', path: 'src/app.ts', fold_reason: 'superseded_by_newer_card' }),
  event(8, 'verification_completed', { turn_seq: 8, verification: 'passed', feedback: 'ok' }),
];

describe('buildLiveContextGraph', () => {
  it('derives layered nodes and truthful edges from the live journal', () => {
    const graph = buildLiveContextGraph(journal);
    expect(graph.truncated).toBe(false);
    const kinds = graph.nodes.map((node) => node.kind);
    expect(kinds).toContain('working_state');
    expect(kinds).toContain('agent');
    expect(kinds).toContain('run_card');
    expect(kinds).toContain('request');
    expect(kinds).toContain('model_call');
    expect(kinds).toContain('file_card');
    expect(kinds).toContain('verification');
    expect(kinds).toContain('fold');

    const card = graph.nodes.find((node) => node.kind === 'file_card');
    expect(card?.foldState).toBe('FOLDED');
    expect(card?.evidenceRef).toBe('filecard:src/app.ts');
    expect(card?.tokens).toBeGreaterThan(0);

    const fold = graph.nodes.find((node) => node.kind === 'fold');
    expect(graph.edges.some((edge) => edge.source === fold?.id && edge.target === card?.id && edge.label === '折叠自')).toBe(true);
    expect(graph.edges.some((edge) => edge.source === 'working_state' && edge.target === 'run_card:3')).toBe(true);
    expect(graph.edges.some((edge) => edge.source === 'request:8' && edge.target === 'model:8' && edge.animated)).toBe(true);
    expect(graph.edges.some((edge) => edge.source === 'model:8' && edge.target === 'card:call_1')).toBe(true);
  });

  it('keeps unknown fold state when the observation carries no card', () => {
    const graph = buildLiveContextGraph([event(1, 'tool_observed', { call_id: 'c', tool: 'repo_tree', ok: true, content: '' })]);
    expect(graph.nodes).toHaveLength(1);
    expect(graph.nodes[0]?.foldState).toBe('UNKNOWN');
    expect(graph.nodes[0]?.foldReason).toBe('观察内容不含卡片');
  });

  it('reports truncation honestly instead of dropping the whole history silently', () => {
    const many = Array.from({ length: 30 }, (_, index) => event(index + 1, 'request_prepared', { turn_seq: index, request: { context: { phase: 'EDIT' } } }));
    const graph = buildLiveContextGraph(many, 10);
    expect(graph.truncated).toBe(true);
    expect(graph.total).toBe(30);
    expect(graph.nodes.length).toBeLessThanOrEqual(10);
  });
});

describe('liveContextMetrics', () => {
  it('carries the latest snapshot, cost and structure counts', () => {
    const metrics = liveContextMetrics(journal);
    expect(metrics.lastSnapshotRef).toBe('a'.repeat(24));
    expect(metrics.lastTokenEstimate).toBe(6335);
    expect(metrics.contextWindowTokens).toBe(1000000);
    expect(metrics.contextUtilization).toBeCloseTo(0.0063);
    expect(metrics.requestCount).toBe(1);
    expect(metrics.modelCallCount).toBe(1);
    expect(metrics.toolCallCount).toBe(1);
    expect(metrics.verificationCount).toBe(1);
    expect(metrics.agentDecisionCount).toBe(1);
    expect(metrics.foldCounts.FOLDED).toBeGreaterThan(0);
  });
});

describe('liveMemoryCards', () => {
  it('surfaces epoch, resident and RunCard memories newest first', () => {
    const cards = liveMemoryCards([
      event(1, 'resident_agi_decision', { project_type: 'node', reason: 'Node 项目' }, 'resident_agi'),
      event(2, 'cartographer_completed', { phase: 'EDIT', intent: '实现入口' }, 'cartographer'),
      event(3, 'epoch_card_completed', { id: 'epoch-0001', title: 'epoch-0001', summary: '阶段记忆', validation_result: 'VALID', evidence_refs: ['e1'] }, 'devourer'),
    ]);
    expect(cards.map((card) => card.kind)).toEqual(['epoch_card', 'run_card', 'agent_decision']);
    expect(cards[0]?.status).toBe('VALID');
    expect(cards[0]?.evidenceRefs).toEqual(['e1']);
  });
});

describe('layoutLiveContextNodes', () => {
  it('is deterministic and separates layers on x and arrival order on y', () => {
    const graph = buildLiveContextGraph(journal);
    const first = layoutLiveContextNodes(graph.nodes);
    const second = layoutLiveContextNodes(graph.nodes);
    expect(first).toEqual(second);
    const state = graph.nodes.find((node) => node.kind === 'working_state');
    const card = graph.nodes.find((node) => node.kind === 'file_card');
    expect(state && card ? first.get(state.id)!.x < first.get(card.id)!.x : false).toBe(true);
  });
});

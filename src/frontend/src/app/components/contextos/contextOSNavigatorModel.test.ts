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
    summary_kind: 'model_inference',
    content_preview: 'x'.repeat(400),
  },
});

const compressionMarker = '【上下文已压缩】为控制在有界上下文预算（约 32000 tokens）内，省略了 69 条较早的工具轮次；这些轮次的完整记录仍在工程运行日志中，可按证据引用取回。以下保留最近的关键轮次。';

const journal: EngineeringEvent[] = [
  event(1, 'run_updated', { workspace_revision: 3, verification: 'not_run', status: 'running' }),
  event(2, 'resident_agi_decision', { project_type: 'node', reason: 'package.json 与 tsconfig 指向 Node' }, 'resident_agi'),
  event(3, 'cartographer_completed', { phase: 'EDIT', intent: '实现入口' }, 'cartographer'),
  event(4, 'request_prepared', {
    turn_seq: 8,
    evidence_kind: 'auxiliary_prepared_request',
    request: {
      context: { phase: 'EDIT' },
      options: {
        messages: [
          { role: 'user', content: compressionMarker },
          {
            role: 'tool',
            content: JSON.stringify({
              call_id: 'call_1',
              tool: 'read_file',
              content: JSON.stringify({
                file_card: {
                  path: 'src/app.ts',
                  content_ref: 'filecard:src/app.ts',
                  fold_state: 'FOLDED',
                  fold_reason: 'superseded_by_newer_card',
                  summary: '入口文件',
                  summary_kind: 'model_inference',
                },
              }),
            }),
          },
        ],
      },
    },
  }),
  event(5, 'llm_request_observed', {
    turn_seq: 8,
    model: 'kimi-for-coding',
    phase: 'EDIT',
    context_snapshot_ref: 'a'.repeat(24),
    snapshot_status: 'available',
    status: 'ok',
    audit: { final_request_token_estimate: 6335, context_window_tokens: 1000000, context_window_utilization: 0.0063 },
  }),
  event(6, 'tool_observed', { turn_seq: 8, call_id: 'call_1', tool: 'read_file', ok: true, content: fileCard }),
  event(7, 'context_projection_folded', { projection_ref: 'filecard:src/app.ts', node_kind: 'file_card', path: 'src/app.ts', fold_reason: 'superseded_by_newer_card' }),
  event(8, 'verification_completed', { turn_seq: 8, verification: 'passed', feedback: 'ok' }),
  // The distiller event carries its summaries at the top level.
  event(9, 'semantic_advice_completed', {
    agent_role: 'distiller',
    fingerprint: 'fp-1',
    validation_result: 'VALID',
    summaries: [
      {
        endpoint_index: 0,
        validation_result: 'VALID',
        result: { node_id: 'src/app.ts', summary: '入口文件摘要' },
      },
    ],
  }, 'distiller'),
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
    expect(kinds).toContain('distill');

    const card = graph.nodes.find((node) => node.kind === 'file_card');
    const distill = graph.nodes.find((node) => node.kind === 'distill');
    expect(distill?.title).toBe('蒸馏 1');
    expect(distill?.summary).toBe('入口文件摘要');
    expect(graph.edges.some((edge) => edge.source === distill?.id && edge.target === card?.id && edge.label === '蒸馏')).toBe(true);
    expect(card?.foldState).toBe('FOLDED');
    expect(card?.foldReason).toBe('superseded_by_newer_card');
    expect(card?.summaryKind).toBe('model_inference');
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
    expect(metrics.lastSnapshot).toEqual({ ref: 'a'.repeat(24), role: 'director' });
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

  it('parses the host compression marker and the folded-card summary provenance', () => {
    const metrics = liveContextMetrics(journal);
    expect(metrics.compression?.droppedTurns).toBe(69);
    expect(metrics.compression?.budgetTokens).toBe(32000);
    expect(metrics.compression?.dropUnit).toBe('tool_turns');
    expect(metrics.compression?.sourceSeq).toBe(4);
    expect(metrics.compression?.text).toContain('省略了 69 条较早的工具轮次');
    expect(metrics.cardFoldCounts.folded).toBe(1);
    expect(metrics.cardFoldCounts.active).toBe(0);
    expect(metrics.summaryKindCounts.model_inference).toBe(1);
    expect(metrics.summaryKindCounts.card_default).toBe(0);
    expect(metrics.summaryKindCounts.structural).toBe(0);
    expect(metrics.workingSetBudgetTokens).toBe(32000);
    expect(metrics.workingSetBudgetSource).toBe('compression_note');
    expect(metrics.workingSetUsedTokens).toBe(6335);
    expect(metrics.workingSetUtilization).toBeCloseTo(6335 / 32000);
  });

  it('falls back to the model window when the host states no prompt budget', () => {
    const marker = '【上下文已压缩】为适配模型窗口，省略了 11 条较早的工具轮次；这些轮次的完整记录仍在工程运行日志中。以下保留最近的关键轮次。';
    const metrics = liveContextMetrics([
      event(1, 'request_prepared', { request: { options: { messages: [{ role: 'user', content: marker }] } } }),
      event(2, 'llm_request_observed', {
        turn_seq: 1,
        audit: { final_request_token_estimate: 35058, context_window_tokens: 1000000, context_window_utilization: 0.0351 },
      }),
    ]);
    expect(metrics.compression?.droppedTurns).toBe(11);
    expect(metrics.compression?.budgetTokens).toBeNull();
    expect(metrics.workingSetBudgetTokens).toBe(1000000);
    expect(metrics.workingSetBudgetSource).toBe('model_window');
    expect(metrics.workingSetUsedTokens).toBe(35058);
    expect(metrics.workingSetUtilization).toBeCloseTo(0.035058);
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

/** Live ContextOS structure derived from the realtime engineering journal.
 *
 * The backend owns the facts; this module only projects them. Nothing here reads
 * a snapshot or invents fold state: every node carries the seq of the event that
 * produced it, and unknown fold state stays UNKNOWN.
 */
import type { EngineeringEvent } from '@/services/api.types';
import { engineeringContextRecords, engineeringSnapshotRef } from '@/runtime/contextSnapshot';
import {
  compressionFromEvents,
  preparedRequestMessages,
  type ContextCompressionFact,
  type ContextMessageLike,
} from './contextOSCompression';

export type LiveNodeKind =
  | 'working_state'
  | 'agent'
  | 'epoch'
  | 'run_card'
  | 'request'
  | 'model_call'
  | 'file_card'
  | 'command_card'
  | 'verification'
  | 'fold'
  | 'distill';

export type LiveFoldState = 'ACTIVE' | 'FOLDED' | 'REFERENCE' | 'EVICTED' | 'UNKNOWN';

/**
 * Who produced the summary a folded card displays. `model_inference` is the
 * distiller, `card_default` is the card's own summary, and `structural` is the
 * deterministic fallback. A folded card without an explicit kind still shows
 * the card's own summary, so it is reported as `card_default`.
 */
export type LiveSummaryKind = 'model_inference' | 'card_default' | 'structural';

export const LIVE_SUMMARY_KIND_LABELS: Record<LiveSummaryKind, string> = {
  model_inference: '蒸馏折叠',
  card_default: '卡片自带',
  structural: '结构性兜底',
};

export const LIVE_SUMMARY_KIND_ORDER: LiveSummaryKind[] = ['model_inference', 'card_default', 'structural'];

export interface LiveContextNode {
  id: string;
  seq: number;
  kind: LiveNodeKind;
  title: string;
  summary: string;
  role: string;
  foldState: LiveFoldState;
  foldReason: string;
  /** Summary provenance for cards; null for node kinds without a card summary. */
  summaryKind: LiveSummaryKind | null;
  tokens: number | null;
  layer: number;
  turn: number | null;
  evidenceRef: string | null;
  phase: string | null;
  ok: boolean | null;
  createdAt: number;
}

export interface LiveContextEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  animated: boolean;
}

export interface LiveContextGraph {
  nodes: LiveContextNode[];
  edges: LiveContextEdge[];
  truncated: boolean;
  total: number;
}

export interface LiveContextMetrics {
  nodeCount: number;
  edgeCount: number;
  foldCounts: Record<LiveFoldState, number>;
  kindCounts: Partial<Record<LiveNodeKind, number>>;
  requestCount: number;
  modelCallCount: number;
  toolCallCount: number;
  verificationCount: number;
  foldEventCount: number;
  distillCount: number;
  agentDecisionCount: number;
  epochCount: number;
  lastSnapshot: { ref: string; role: string } | null;
  lastTokenEstimate: number | null;
  peakTokenEstimate: number | null;
  contextWindowTokens: number | null;
  contextUtilization: number | null;
  /** Folded / unfolded card counts, so the rail can explain the projection. */
  cardFoldCounts: { folded: number; active: number };
  /** Summary provenance over folded cards: distiller / card / structural. */
  summaryKindCounts: Record<LiveSummaryKind, number>;
  /** Latest host compression marker parsed from the prepared request. */
  compression: ContextCompressionFact | null;
  /** Working-set budget: the host-stated prompt budget, else the model window. */
  workingSetBudgetTokens: number | null;
  workingSetBudgetSource: 'compression_note' | 'model_window' | null;
  workingSetUsedTokens: number | null;
  workingSetUtilization: number | null;
}

export interface LiveProjectedCard {
  /** Card identity (content_ref / path / command) used to match a graph node. */
  nodeId: string;
  path: string;
  foldState: LiveFoldState;
  foldReason: string;
  summaryKind: LiveSummaryKind;
  summary: string;
}

export interface LiveMemoryCard {
  id: string;
  kind: string;
  title: string;
  summary: string;
  evidenceRefs: string[];
  status: string;
  createdAt: number;
}

/** Layer order is the projection order: state/memory, run card, request, model, evidence, fold. */
export const LIVE_LAYERS: Record<LiveNodeKind, number> = {
  working_state: 0,
  agent: 0,
  epoch: 0,
  run_card: 1,
  request: 1,
  model_call: 2,
  file_card: 3,
  command_card: 3,
  verification: 3,
  fold: 4,
  distill: 0,
};

export const LIVE_LAYER_LABELS: Record<number, string> = {
  0: '状态与长期记忆',
  1: 'RunCard 与请求',
  2: '模型请求',
  3: '证据与验证',
  4: '折叠投影',
};

export const LIVE_KIND_LABELS: Record<LiveNodeKind, string> = {
  working_state: 'WorkingState',
  agent: 'Resident AGI',
  epoch: 'Epoch 记忆',
  run_card: 'RunCard',
  request: '请求',
  model_call: '模型请求',
  file_card: 'FileCard',
  command_card: 'CommandCard',
  verification: '验证',
  fold: '折叠节点',
  distill: '蒸馏节点',
};

export const LIVE_FOLD_LABELS: Record<LiveFoldState, string> = {
  ACTIVE: 'ACTIVE · 当前请求',
  FOLDED: 'FOLDED · 已折叠',
  REFERENCE: 'REFERENCE · 证据引用',
  EVICTED: 'EVICTED · 已移出',
  UNKNOWN: 'UNKNOWN · 未证明',
};

const RECORD = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value));

function text(value: unknown, limit = 160): string {
  if (typeof value !== 'string') return '';
  return value.replace(/\s+/g, ' ').trim().slice(0, limit);
}

function numberOr(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function turnOf(payload: Record<string, unknown>): number | null {
  return numberOr(payload.turn_seq);
}

function parseObject(value: unknown): Record<string, unknown> | null {
  if (!RECORD(value)) return null;
  return value;
}

function parseJsonString(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed.startsWith('{')) return null;
  try {
    return parseObject(JSON.parse(trimmed));
  } catch {
    return null;
  }
}

/** FileCard / CommandCard live inside a tool observation, sometimes nested once. */
function cardFromContent(content: unknown): Record<string, unknown> | null {
  const outer = parseJsonString(content);
  if (!outer) return null;
  for (const key of ['file_card', 'command_card']) {
    const card = parseObject(outer[key]);
    if (card) return card;
  }
  const nested = parseJsonString(outer.content);
  if (!nested) return null;
  for (const key of ['file_card', 'command_card']) {
    const card = parseObject(nested[key]);
    if (card) return card;
  }
  return null;
}

function cardKindOf(content: unknown, tool: string): 'file_card' | 'command_card' {
  const outer = parseJsonString(content);
  const nested = outer ? parseJsonString(outer.content) : null;
  if (parseObject(outer?.file_card) || parseObject(nested?.file_card)) return 'file_card';
  if (parseObject(outer?.command_card) || parseObject(nested?.command_card)) return 'command_card';
  return tool === 'execute_command' ? 'command_card' : 'file_card';
}

function foldStateOf(card: Record<string, unknown>): { state: LiveFoldState; reason: string } {
  const raw = typeof card.fold_state === 'string' ? card.fold_state.toUpperCase() : '';
  if (raw === 'FOLDED' || raw === 'ACTIVE' || raw === 'REFERENCE' || raw === 'EVICTED') {
    return { state: raw, reason: text(card.fold_reason) || text(card.summary) || '由宿主投影决定' };
  }
  return { state: 'UNKNOWN', reason: '卡片未声明折叠状态' };
}

/** Summary provenance, defaulting to the card's own summary when unannotated. */
function summaryKindOf(card: Record<string, unknown> | null): LiveSummaryKind {
  const raw = typeof card?.summary_kind === 'string' ? card.summary_kind : '';
  if (raw === 'model_inference' || raw === 'card_default' || raw === 'structural') return raw;
  return 'card_default';
}

function tokenEstimateOf(value: unknown): number | null {
  if (typeof value !== 'string' || !value) return null;
  return Math.max(1, Math.ceil(value.length / 4));
}

function auditOf(payload: Record<string, unknown>): Record<string, unknown> {
  return parseObject(payload.audit) ?? {};
}

export const LIVE_RELEVANT_KINDS = new Set([
  'run_started',
  'run_updated',
  'resident_agi_decision',
  'epoch_card_completed',
  'request_prepared',
  'llm_request_observed',
  'tool_observed',
  'context_projection_folded',
  'verification_completed',
  'cartographer_completed',
  'cartographer_rejected',
  'semantic_advice_completed',
]);

/** Distiller summaries carry the FileCard/CommandCard path as their node_id. */
interface DistillTarget {
  node_id: string;
  summary: string;
}

/** Summaries live at the top level of the event; older runs nested them under
 * result when the advisor also carried assessments. Each entry is
 * {validation_result, result:{node_id,summary}}.
 */
function distillTargets(payload: Record<string, unknown>): DistillTarget[] {
  const container = (
    Array.isArray(payload.summaries) ? payload : payload.result
  ) as Record<string, unknown> | undefined;
  const summaries = container?.summaries;
  if (!Array.isArray(summaries)) return [];
  const targets: DistillTarget[] = [];
  for (const item of summaries) {
    if (!item || typeof item !== 'object') continue;
    const record = item as Record<string, unknown>;
    if (record.validation_result && record.validation_result !== 'VALID') continue;
    const inner = (
      record.result && typeof record.result === 'object' ? record.result : record
    ) as Record<string, unknown>;
    const nodeId = typeof inner.node_id === 'string' ? inner.node_id : '';
    if (!nodeId) continue;
    targets.push({ node_id: nodeId, summary: typeof inner.summary === 'string' ? inner.summary : '' });
  }
  return targets;
}

export function buildLiveContextGraph(events: EngineeringEvent[], limit = 90): LiveContextGraph {
  const relevant = events.filter((event) => LIVE_RELEVANT_KINDS.has(event.kind));
  const truncated = relevant.length > limit;
  const window = relevant.slice(-limit);
  const nodes = new Map<string, LiveContextNode>();
  const edges: LiveContextEdge[] = [];
  const cardRefs = new Map<string, string>();
  const cardPaths = new Map<string, string>();
  const edgeKeys = new Set<string>();
  const addEdge = (source: string, target: string, label: string, animated = false) => {
    const key = `${source}->${target}`;
    if (edgeKeys.has(key)) return;
    edgeKeys.add(key);
    edges.push({ id: key, source, target, label, animated });
  };

  let stateSeq = 0;
  let runCardId: string | null = null;
  for (const event of window) {
    const payload = event.payload;
    const turn = turnOf(payload);
    const seq = event.seq;
    if (event.kind === 'run_started' || event.kind === 'run_updated') {
      stateSeq = seq;
      const revision = numberOr(payload.workspace_revision);
      nodes.set('working_state', {
        id: 'working_state',
        seq,
        kind: 'working_state',
        title: 'WorkingState',
        summary: `rev=${revision === null ? '—' : revision} · ${text(payload.verification ?? payload.status) || 'running'}`,
        role: event.role,
        foldState: 'ACTIVE',
        foldReason: '当前工作状态投影',
        summaryKind: null,
        tokens: null,
        layer: LIVE_LAYERS.working_state,
        turn: null,
        evidenceRef: null,
        phase: null,
        ok: null,
        createdAt: event.created_at ?? 0,
      });
      continue;
    }
    if (event.kind === 'resident_agi_decision') {
      const signals = Array.isArray(payload.signals) ? payload.signals.slice(0, 3).map((item) => text(item, 40)) : [];
      nodes.set('agent', {
        id: 'agent',
        seq,
        kind: 'agent',
        title: text(payload.project_type, 40) || 'Resident AGI',
        summary: text(payload.reason ?? payload.guidance) || signals.join(' · ') || '等待决策',
        role: event.role,
        foldState: 'REFERENCE',
        foldReason: '项目类型与能力绑定决策',
        summaryKind: null,
        tokens: null,
        layer: LIVE_LAYERS.agent,
        turn: null,
        evidenceRef: null,
        phase: null,
        ok: true,
        createdAt: event.created_at ?? 0,
      });
      continue;
    }
    if (event.kind === 'epoch_card_completed') {
      const id = `epoch:${text(payload.id, 80) || seq}`;
      nodes.set(id, {
        id,
        seq,
        kind: 'epoch',
        title: text(payload.title, 80) || 'Epoch 记忆',
        summary: text(payload.summary) || text(payload.error) || '阶段记忆已生成',
        role: event.role,
        foldState: 'REFERENCE',
        foldReason: '长期记忆卡 · 证据引用保留',
        summaryKind: null,
        tokens: null,
        layer: LIVE_LAYERS.epoch,
        turn: null,
        evidenceRef: Array.isArray(payload.evidence_refs) ? text(payload.evidence_refs[0]) : null,
        phase: null,
        ok: payload.validation_result === 'VALID',
        createdAt: event.created_at ?? 0,
      });
      continue;
    }
    if (event.kind === 'cartographer_completed' || event.kind === 'cartographer_rejected') {
      const rejected = event.kind === 'cartographer_rejected';
      const id = rejected ? `run_card:rejected:${seq}` : `run_card:${seq}`;
      const phase = text(payload.phase) || text(payload.observed_phase) || 'unknown';
      nodes.set(id, {
        id,
        seq,
        kind: 'run_card',
        title: rejected ? `RunCard 拒绝 · ${phase}` : `RunCard · ${phase}`,
        summary: rejected
          ? `reason=${text(payload.reason, 60) || 'unknown'}`
          : text(payload.intent) || text(payload.blocker) || '运行地图已更新',
        role: event.role,
        foldState: rejected ? 'EVICTED' : 'REFERENCE',
        foldReason: rejected ? '叙述与持久事实不一致，未进入投影' : '当前阶段 RunCard 投影',
        summaryKind: null,
        tokens: null,
        layer: LIVE_LAYERS.run_card,
        turn: null,
        evidenceRef: null,
        phase,
        ok: !rejected,
        createdAt: event.created_at ?? 0,
      });
      if (!rejected) runCardId = id;
      continue;
    }
    if (event.kind === 'request_prepared') {
      const request = parseObject(payload.request);
      const context = parseObject(request?.context);
      const phase = text(context?.phase) || text(payload.phase) || 'unknown';
      const id = `request:${turn ?? seq}`;
      nodes.set(id, {
        id,
        seq,
        kind: 'request',
        title: `请求 · ${phase}`,
        summary: text(payload.evidence_kind, 90) || '已装配的模型请求',
        role: event.role,
        foldState: 'ACTIVE',
        foldReason: '当前请求窗口',
        summaryKind: null,
        tokens: null,
        layer: LIVE_LAYERS.request,
        turn,
        evidenceRef: null,
        phase,
        ok: null,
        createdAt: event.created_at ?? 0,
      });
      if (runCardId) addEdge(runCardId, id, '投影');
      continue;
    }
    if (event.kind === 'llm_request_observed') {
      const audit = auditOf(payload);
      const id = `model:${turn ?? seq}`;
      nodes.set(id, {
        id,
        seq,
        kind: 'model_call',
        title: text(payload.model, 60) || '模型请求',
        summary: `约 ${numberOr(audit.final_request_token_estimate) ?? '—'} tok · 窗口 ${numberOr(audit.context_window_tokens) ?? '—'}`,
        role: event.role,
        foldState: 'ACTIVE',
        foldReason: '最终 provider 请求观察',
        summaryKind: null,
        tokens: numberOr(audit.final_request_token_estimate),
        layer: LIVE_LAYERS.model_call,
        turn,
        evidenceRef: engineeringSnapshotRef(payload),
        phase: text(payload.phase) || null,
        ok: text(payload.status) === 'ok' || text(payload.status) === 'observed' ? true : null,
        createdAt: event.created_at ?? 0,
      });
      const request = nodes.get(`request:${turn ?? seq}`);
      if (request) addEdge(request.id, id, '发送', true);
      continue;
    }
    if (event.kind === 'tool_observed') {
      const tool = text(payload.tool, 40) || 'tool';
      const callId = text(payload.call_id, 80) || `call:${seq}`;
      const card = cardFromContent(payload.content);
      const kind = cardKindOf(payload.content, tool);
      const fold = card ? foldStateOf(card) : { state: 'UNKNOWN' as LiveFoldState, reason: '观察内容不含卡片' };
      const id = `card:${callId}`;
      const path = text(card?.path) || text(card?.command) || tool;
      const reference = text(card?.content_ref) || text(card?.snapshot_ref) || null;
      nodes.set(id, {
        id,
        seq,
        kind,
        title: `${kind === 'file_card' ? 'FileCard' : 'CommandCard'} · ${path.slice(0, 60)}`,
        summary: text(card?.summary) || text(payload.content, 120) || tool,
        role: event.role,
        foldState: fold.state,
        foldReason: fold.reason,
        summaryKind: card && fold.state === 'FOLDED' ? summaryKindOf(card) : null,
        tokens: tokenEstimateOf(card?.content_preview) ?? tokenEstimateOf(payload.content),
        layer: LIVE_LAYERS[kind],
        turn,
        evidenceRef: reference,
        phase: null,
        ok: typeof payload.ok === 'boolean' ? payload.ok : null,
        createdAt: event.created_at ?? 0,
      });
      if (reference) cardRefs.set(reference, id);
      const cardPath = card?.path;
      if (typeof cardPath === 'string' && cardPath) cardPaths.set(cardPath, id);
      const model = nodes.get(`model:${turn ?? seq}`);
      if (model) addEdge(model.id, id, '观察');
      continue;
    }
    if (event.kind === 'semantic_advice_completed') {
      const targets = distillTargets(payload);
      const id = `distill:${text(payload.fingerprint, 64) || seq}`;
      nodes.set(id, {
        id,
        seq,
        kind: 'distill',
        title: `蒸馏 ${targets.length || ''}`.trim(),
        summary: targets
          .slice(0, 3)
          .map((target) => target.summary || target.node_id)
          .join('、') || text(payload.validation_result, 60) || '节点摘要',
        role: event.role,
        foldState: 'FOLDED',
        foldReason: '蒸馏摘要替代原始卡片进入投影',
        summaryKind: 'model_inference',
        tokens: null,
        layer: LIVE_LAYERS.distill,
        turn: null,
        evidenceRef: null,
        phase: null,
        ok: text(payload.validation_result) === 'VALID',
        createdAt: event.created_at ?? 0,
      });
      for (const target of targets) {
        const cardId = cardPaths.get(target.node_id) ?? cardRefs.get(target.node_id);
        if (cardId) addEdge(id, cardId, '蒸馏', true);
      }
      continue;
    }
    if (event.kind === 'context_projection_folded') {
      const reference = text(payload.projection_ref) || `fold:${seq}`;
      const id = `fold:${reference}`;
      nodes.set(id, {
        id,
        seq,
        kind: 'fold',
        title: `${text(payload.node_kind, 40) || '卡片'} · 折叠`,
        summary: text(payload.summary) || text(payload.fold_reason) || '已折叠为有界摘要',
        role: event.role,
        foldState: 'FOLDED',
        foldReason: text(payload.fold_reason) || '超出当前活动窗口',
        summaryKind: null,
        tokens: null,
        layer: LIVE_LAYERS.fold,
        turn: numberOr(payload.activity_seq),
        evidenceRef: reference,
        phase: null,
        ok: true,
        createdAt: event.created_at ?? 0,
      });
      const source = cardRefs.get(reference);
      if (source) addEdge(id, source, '折叠自', true);
      continue;
    }
    if (event.kind === 'verification_completed') {
      const id = `verify:${seq}`;
      nodes.set(id, {
        id,
        seq,
        kind: 'verification',
        title: `验证 · ${text(payload.verification, 40) || 'unknown'}`,
        summary: text(payload.feedback) || '独立验证已完成',
        role: event.role,
        foldState: 'REFERENCE',
        foldReason: '验证证据绑定当前源码',
        summaryKind: null,
        tokens: null,
        layer: LIVE_LAYERS.verification,
        turn,
        evidenceRef: null,
        phase: null,
        ok: payload.verification === 'passed',
        createdAt: event.created_at ?? 0,
      });
      const model = nodes.get(`model:${turn ?? seq}`);
      if (model) addEdge(model.id, id, '验证');
      continue;
    }
  }
  if (stateSeq === 0 && nodes.size === 0) return { nodes: [], edges: [], truncated, total: relevant.length };
  const state = nodes.get('working_state');
  if (state && runCardId) addEdge(state.id, runCardId, '当前');
  return { nodes: [...nodes.values()], edges, truncated, total: relevant.length };
}

/**
 * Cards as the host actually projected them in the most recent prepared
 * request that carried cards.
 *
 * `tool_observed` carries the raw observation; the fold decision and the
 * summary provenance are only in the projected request messages, so the fold
 * counts and the distiller/structural split must be read here. A prepared
 * request with no cards carries no fold evidence, so the last card-bearing
 * projection is used rather than reporting an empty current state.
 */
export function liveProjectedCards(events: EngineeringEvent[]): LiveProjectedCard[] {
  let messages: ContextMessageLike[] = [];
  for (const event of events) {
    if (event.kind !== 'request_prepared') continue;
    const parsed = preparedRequestMessages(event.payload);
    if (parsed.length > 0) messages = parsed;
  }
  const cards: LiveProjectedCard[] = [];
  for (const message of messages) {
    const card = cardFromContent(message.content);
    if (!card) continue;
    const fold = foldStateOf(card);
    cards.push({
      nodeId: text(card.content_ref) || text(card.path) || text(card.command),
      path: text(card.path) || text(card.command),
      foldState: fold.state,
      foldReason: fold.reason,
      summaryKind: summaryKindOf(card),
      summary: text(card.summary),
    });
  }
  return cards;
}

export function liveContextMetrics(events: EngineeringEvent[]): LiveContextMetrics {
  const graph = buildLiveContextGraph(events, Number.MAX_SAFE_INTEGER);
  const foldCounts: Record<LiveFoldState, number> = { ACTIVE: 0, FOLDED: 0, REFERENCE: 0, EVICTED: 0, UNKNOWN: 0 };
  const kindCounts: Partial<Record<LiveNodeKind, number>> = {};
  for (const node of graph.nodes) {
    foldCounts[node.foldState] += 1;
    kindCounts[node.kind] = (kindCounts[node.kind] ?? 0) + 1;
  }
  let foldedCards = 0;
  let activeCards = 0;
  const summaryKindCounts: Record<LiveSummaryKind, number> = {
    model_inference: 0,
    card_default: 0,
    structural: 0,
  };
  for (const card of liveProjectedCards(events)) {
    if (card.foldState === 'FOLDED') {
      foldedCards += 1;
      summaryKindCounts[card.summaryKind] += 1;
    } else if (card.foldState === 'ACTIVE') {
      activeCards += 1;
    }
  }
  let lastSnapshot: LiveContextMetrics['lastSnapshot'] = null;
  let lastTokenEstimate: number | null = null;
  let peakTokenEstimate: number | null = null;
  let contextWindowTokens: number | null = null;
  let contextUtilization: number | null = null;
  for (const { event, audit, contextSnapshotRef } of engineeringContextRecords(events).reverse()) {
    const estimate = numberOr(audit.final_request_token_estimate);
    if (estimate !== null) {
      lastTokenEstimate = estimate;
      peakTokenEstimate = peakTokenEstimate === null ? estimate : Math.max(peakTokenEstimate, estimate);
    }
    const window = numberOr(audit.context_window_tokens);
    if (window !== null) contextWindowTokens = window;
    const utilization = numberOr(audit.context_window_utilization);
    if (utilization !== null) contextUtilization = utilization;
    if (contextSnapshotRef) lastSnapshot = { ref: contextSnapshotRef, role: event.role };
  }
  const compression = compressionFromEvents(events);
  const workingSetUsedTokens = lastTokenEstimate;
  const workingSetBudgetTokens = compression?.budgetTokens ?? contextWindowTokens;
  const workingSetBudgetSource = compression?.budgetTokens
    ? 'compression_note'
    : contextWindowTokens !== null
      ? 'model_window'
      : null;
  const workingSetUtilization = workingSetBudgetTokens !== null && workingSetUsedTokens !== null
    ? workingSetUsedTokens / workingSetBudgetTokens
    : null;
  return {
    nodeCount: graph.nodes.length,
    edgeCount: graph.edges.length,
    foldCounts,
    kindCounts,
    requestCount: graph.nodes.filter((node) => node.kind === 'request').length,
    modelCallCount: graph.nodes.filter((node) => node.kind === 'model_call').length,
    toolCallCount: graph.nodes.filter((node) => node.kind === 'file_card' || node.kind === 'command_card').length,
    verificationCount: graph.nodes.filter((node) => node.kind === 'verification').length,
    foldEventCount: graph.nodes.filter((node) => node.kind === 'fold').length,
    distillCount: graph.nodes.filter((node) => node.kind === 'distill').length,
    agentDecisionCount: events.filter((event) => event.kind === 'resident_agi_decision').length,
    epochCount: events.filter((event) => event.kind === 'epoch_card_completed').length,
    lastSnapshot,
    lastTokenEstimate,
    peakTokenEstimate,
    contextWindowTokens,
    contextUtilization,
    cardFoldCounts: { folded: foldedCards, active: activeCards },
    summaryKindCounts,
    compression,
    workingSetBudgetTokens,
    workingSetBudgetSource,
    workingSetUsedTokens,
    workingSetUtilization,
  };
}

export function liveMemoryCards(events: EngineeringEvent[], limit = 8): LiveMemoryCard[] {
  const cards: LiveMemoryCard[] = [];
  for (const event of events) {
    if (event.kind === 'epoch_card_completed') {
      cards.push({
        id: `epoch:${text(event.payload.id, 80) || event.seq}`,
        kind: 'epoch_card',
        title: text(event.payload.title, 80) || 'Epoch 记忆',
        summary: text(event.payload.summary) || text(event.payload.error) || '阶段记忆',
        evidenceRefs: Array.isArray(event.payload.evidence_refs)
          ? event.payload.evidence_refs.filter((item): item is string => typeof item === 'string').slice(0, 6)
          : [],
        status: text(event.payload.validation_result) || 'unknown',
        createdAt: event.created_at ?? 0,
      });
      continue;
    }
    if (event.kind === 'resident_agi_decision') {
      cards.push({
        id: `agent:${event.seq}`,
        kind: 'agent_decision',
        title: text(event.payload.project_type, 60) || 'Resident AGI',
        summary: text(event.payload.reason) || text(event.payload.guidance) || '项目类型与能力决策',
        evidenceRefs: Array.isArray(event.payload.signals)
          ? event.payload.signals.filter((item): item is string => typeof item === 'string').slice(0, 6)
          : [],
        status: 'VALID',
        createdAt: event.created_at ?? 0,
      });
      continue;
    }
    if (event.kind === 'cartographer_completed') {
      cards.push({
        id: `run_card:${event.seq}`,
        kind: 'run_card',
        title: `RunCard · ${text(event.payload.phase, 40) || 'unknown'}`,
        summary: text(event.payload.intent) || text(event.payload.blocker) || '运行地图',
        evidenceRefs: [],
        status: 'VALID',
        createdAt: event.created_at ?? 0,
      });
    }
  }
  return cards.slice(-limit).reverse();
}

export interface LiveGraphNodePosition { x: number; y: number }

/** Deterministic layered layout: x by projection layer, y by arrival order in layer. */
export function layoutLiveContextNodes(nodes: LiveContextNode[]): Map<string, LiveGraphNodePosition> {
  const positions = new Map<string, LiveGraphNodePosition>();
  const perLayer = new Map<number, number>();
  const columns = [...new Set(nodes.map((node) => node.layer))].sort((a, b) => a - b);
  const nextColumn = new Map<number, number>(columns.map((layer, index) => [layer, index]));
  const layerHeight = new Map<number, number>();
  for (const node of [...nodes].sort((a, b) => a.seq - b.seq)) {
    const column = nextColumn.get(node.layer) ?? 0;
    const row = perLayer.get(node.layer) ?? 0;
    perLayer.set(node.layer, row + 1);
    const height = layerHeight.get(node.layer) ?? 0;
    layerHeight.set(node.layer, Math.max(height, row));
    positions.set(node.id, { x: column * 260, y: row * 72 });
  }
  return positions;
}

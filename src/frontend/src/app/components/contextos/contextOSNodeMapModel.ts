import type { EngineeringEvent } from '@/services/api.types';
import type { ContextPayload } from './ContextViewerModal';
import type { FinalProviderRequestPayload } from './finalProviderRequestProtocol';
import type { ContextProjectionManifest } from './contextProjectionManifest';

export type ContextNodeKind = 'goal' | 'message' | 'tool_call' | 'tool_log' | 'verification' | 'run_card' | 'artifact' | 'request';
export type ContextFoldState = 'ACTIVE' | 'FOLDED' | 'REFERENCE' | 'EVICTED' | 'UNKNOWN';

export interface ContextNode {
  id: string;
  seq: number;
  turn: number | null;
  kind: ContextNodeKind;
  title: string;
  summary: string;
  role: string;
  foldState: ContextFoldState;
  foldReason: string;
  evidenceRef: string | null;
  phase: string | null;
  ok: boolean | null;
  approxTokens: number | null;
  parentId?: string | null;
  projectionRef?: string | null;
  requestMessageIndex?: number | null;
  originalTokens?: number;
  projectedTokens?: number;
}

function text(value: unknown, limit = 160): string {
  if (typeof value !== 'string') return '';
  return value.replace(/\s+/g, ' ').trim().slice(0, limit);
}

function json(value: unknown): string {
  try {
    return JSON.stringify(value ?? null);
  } catch {
    return '';
  }
}

function approxTokens(value: unknown): number | null {
  return typeof value === 'string' && value.length > 0 ? Math.max(1, Math.ceil(value.length / 4)) : null;
}

function kindForEvent(event: EngineeringEvent): ContextNodeKind {
  if (event.kind === 'tool_started') return 'tool_call';
  if (event.kind === 'tool_observed') return 'tool_log';
  if (event.kind === 'verification_completed') return 'verification';
  if (event.kind === 'run_updated' || event.kind === 'request_prepared') return 'run_card';
  if (event.kind === 'tool_output_capture_completed') return 'artifact';
  return 'message';
}

function eventTitle(event: EngineeringEvent): string {
  const tool = typeof event.payload.tool === 'string' ? event.payload.tool : null;
  if (tool) return tool;
  if (event.kind === 'run_updated') return 'RunCard / run_updated';
  if (event.kind === 'request_prepared') return 'RunCard / provider request';
  if (event.kind === 'verification_completed') return 'verification';
  if (event.kind === 'tool_output_capture_completed') return 'artifact capture';
  return event.kind;
}

function eventSummary(event: EngineeringEvent): string {
  const payload = event.payload;
  if (event.kind === 'run_updated') {
    const revision = typeof payload.workspace_revision === 'number' ? payload.workspace_revision : '—';
    return `rev=${revision} · ${String(payload.verification ?? 'unknown')} · ${String(payload.status ?? '')}`;
  }
  if (event.kind === 'request_prepared') {
    const request = payload.request && typeof payload.request === 'object' ? payload.request as Record<string, unknown> : {};
    const context = request.context && typeof request.context === 'object' ? request.context as Record<string, unknown> : {};
    return `phase=${String(context.phase ?? 'unknown')} · ${text(payload.evidence_kind, 70)}`;
  }
  if (event.kind === 'verification_completed') return `${String(payload.verification ?? 'unknown')} · ${text(payload.feedback, 90)}`;
  return text(payload.content ?? payload.feedback ?? payload.error ?? payload.message ?? '', 150);
}

function eventRef(event: EngineeringEvent): string | null {
  for (const key of ['call_id', 'context_snapshot_ref', 'provider_request_id']) {
    const value = event.payload[key];
    if (typeof value === 'string' && value) return value;
  }
  return null;
}

function foldForEvent(
  event: EngineeringEvent,
  ref: string | null,
  activeWire: string,
  projectionText: string,
  hasFinalRequest: boolean,
): { state: ContextFoldState; reason: string } {
  if (ref && activeWire.includes(ref)) return { state: 'ACTIVE', reason: '当前最终请求仍包含该节点或证据引用' };
  if (event.kind === 'run_updated') {
    const revision = event.payload.workspace_revision;
    if (revision !== undefined && projectionText.includes('workspace_revision') && projectionText.includes(String(revision))) {
      return { state: 'FOLDED', reason: '已折叠为当前 WorkingState / RunCard 状态投影' };
    }
    return { state: 'REFERENCE', reason: '原始 RunCard 已退出当前请求，journal 仍保留' };
  }
  if (event.kind === 'request_prepared') {
    return { state: 'REFERENCE', reason: '该轮请求已退出当前窗口，快照引用仍保留' };
  }
  if (event.kind === 'tool_observed' || event.kind === 'verification_completed' || event.kind === 'tool_output_capture_completed') {
    return { state: 'REFERENCE', reason: '原始观察已退出当前请求，但 observation / artifact 引用仍保留' };
  }
  if (event.kind === 'tool_started') return { state: 'EVICTED', reason: '调用参数已不在当前请求；配对结果可能仍以引用形式保留' };
  if (!hasFinalRequest) return { state: 'UNKNOWN', reason: '最终请求尚未加载，无法判断折叠状态' };
  return { state: 'EVICTED', reason: '该历史节点不在当前最终请求中' };
}

export function buildContextNodes(
  content: ContextPayload | null,
  finalRequest: FinalProviderRequestPayload | null,
  events: EngineeringEvent[] = [],
): ContextNode[] {
  if (!content) return [];
  const activeWire = json(finalRequest) + json(content.messages);
  const projectionText = json(
    content.messages.filter((message) => {
      const body = typeof message.content === 'string' ? message.content : json(message.content);
      return message.role === 'system' || message.role === 'user';
    }),
  );
  const journal = events.map((event) => {
    const ref = eventRef(event);
    const fold = foldForEvent(event, ref, activeWire, projectionText, Boolean(finalRequest));
    const payload = event.payload;
    return {
      id: `event-${event.seq}`,
      seq: event.seq,
      turn: typeof payload.turn_seq === 'number' ? payload.turn_seq : null,
      kind: kindForEvent(event),
      title: eventTitle(event),
      summary: eventSummary(event),
      role: event.role,
      foldState: fold.state,
      foldReason: fold.reason,
      evidenceRef: ref,
      phase: event.kind === 'request_prepared' && payload.request && typeof payload.request === 'object'
        ? String(((payload.request as Record<string, unknown>).context as Record<string, unknown> | undefined)?.phase ?? 'unknown')
        : null,
      ok: typeof payload.ok === 'boolean' ? payload.ok : null,
      approxTokens: approxTokens(payload.content ?? payload.feedback ?? payload.message),
    } satisfies ContextNode;
  });
  const requestNodes = content.messages.map((message, index): ContextNode => ({
    id: `request-${index}`,
    seq: Number.MAX_SAFE_INTEGER - content.messages.length + index,
    turn: null,
    kind: message.role === 'tool' ? 'tool_log' : message.role === 'user' && index === 0 ? 'goal' : 'request',
    title: `${message.role} · 当前请求`,
    summary: text(message.content, 150),
    role: message.role,
    foldState: 'ACTIVE',
    foldReason: '该消息存在于当前最终请求快照',
    evidenceRef: null,
    phase: null,
    ok: null,
    approxTokens: approxTokens(message.content),
  }));
  return [...journal, ...requestNodes];
}

export function nodesFromManifest(manifest: ContextProjectionManifest): ContextNode[] {
  return manifest.nodes.map((node) => ({
    id: node.id,
    seq: node.seq ?? Number.MAX_SAFE_INTEGER,
    turn: node.turn,
    kind: node.kind,
    title: node.title,
    summary: node.summary,
    role: node.role,
    foldState: node.fold_state,
    foldReason: node.fold_reason,
    evidenceRef: node.evidence_ref,
    phase: node.phase,
    ok: node.ok,
    approxTokens: node.original_tokens || null,
    parentId: node.parent_id,
    projectionRef: node.projection_ref,
    requestMessageIndex: node.request_message_index,
    originalTokens: node.original_tokens,
    projectedTokens: node.projected_tokens,
  }));
}

export function foldStateCounts(nodes: ContextNode[]): Record<ContextFoldState, number> {
  const counts: Record<ContextFoldState, number> = { ACTIVE: 0, FOLDED: 0, REFERENCE: 0, EVICTED: 0, UNKNOWN: 0 };
  for (const node of nodes) counts[node.foldState] += 1;
  return counts;
}

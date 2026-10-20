/** One read model for new-engine facts; transport cursor is deliberately absent. */
import type { EngineeringEvent, EngineeringRole, EngineeringRunSnapshot } from '@/services/api.types';
import { normalizeRuntimeWorkspacePath } from '@/app/hooks/_runtimeEventFilter';
export const ENGINEERING_ROLES: EngineeringRole[] = ['pm', 'chief_engineer', 'director', 'qa'];
const ROLE_STATES = new Set(['not_called', 'running', 'completed', 'failed', 'cancelled', 'interrupted', 'unknown']);
const RUN_STATES = new Set(['pending', 'running', 'completed', 'failed', 'cancelled', 'interrupted']);
const VERIFICATION = new Set(['passed', 'failed', 'not_run', 'unknown']);
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
export function parseEngineeringSnapshot(value: unknown): EngineeringRunSnapshot {
  if (!record(value) || value.engine_version !== 'engineering_v2')
    throw new Error('Unsupported engineering engine');
  if (typeof value.session_id !== 'string' || value.run_id !== value.session_id || !value.session_id || typeof value.workspace !== 'string' || !value.workspace)
    throw new Error('Invalid engineering run identity');
  if (!Number.isSafeInteger(value.last_seq) || Number(value.last_seq) < 0)
    throw new Error('Engineering snapshot watermark unavailable');
  if (!RUN_STATES.has(String(value.status)) || !VERIFICATION.has(String(value.verification)) || !ENGINEERING_ROLES.includes(value.role as EngineeringRole) || !['direct', 'collaborative'].includes(String(value.workflow)))
    throw new Error('Unsupported engineering run state');
  if (value.require_qa !== undefined && typeof value.require_qa !== 'boolean' || value.qa_verdict !== undefined && !['not_run', 'approved', 'rejected', 'unknown'].includes(String(value.qa_verdict)) || value.project_outcome !== undefined && !['not_accepted', 'accepted', 'rejected', 'unknown'].includes(String(value.project_outcome)) || value.acceptance_basis !== undefined && value.acceptance_basis !== null && !['checks_only', 'independent_qa'].includes(String(value.acceptance_basis)))
    throw new Error('Engineering QA/outcome state unavailable');
  if (!record(value.roles) || ENGINEERING_ROLES.some((role) => !ROLE_STATES.has(String((value.roles as Record<string, unknown>)[role]))))
    throw new Error('Engineering role state unavailable');
  if (!Array.isArray(value.events) || !Array.isArray(value.checks) || !Array.isArray(value.active_roles) || !Array.isArray(value.required_checks) || typeof value.text !== 'string')
    throw new Error('Engineering snapshot evidence unavailable');
  if (value.events.some((event) => !record(event) || !Number.isSafeInteger(event.seq) || Number(event.seq) <= 0 || typeof event.role !== 'string' || typeof event.kind !== 'string' || !record(event.payload)))
    throw new Error('Engineering journal evidence unavailable');
  if (value.checks.some((check) => !record(check) || typeof check.call_id !== 'string' || typeof check.tool !== 'string' || typeof check.content !== 'string' || typeof check.ok !== 'boolean' || typeof check.interrupted !== 'boolean' || check.exit_code !== null && !Number.isInteger(check.exit_code)))
    throw new Error('Engineering verifier evidence unavailable');
  if (value.active_roles.some((role) => typeof role !== 'string') || value.required_checks.some((check) => typeof check !== 'string'))
    throw new Error('Engineering role/check evidence unavailable');
  if (value.verification_commands !== undefined && (!Array.isArray(value.verification_commands) || value.verification_commands.some((command) => typeof command !== 'string')))
    throw new Error('Engineering verification command evidence unavailable');
  return { ...value, verification_commands: value.verification_commands ?? value.required_checks } as unknown as EngineeringRunSnapshot;
}
export interface EngineeringProjection {
  workspace: string;
  sessionId: string;
  base: EngineeringRunSnapshot | null;
  snapshot: EngineeringRunSnapshot | null;
  events: EngineeringEvent[];
  buffered: EngineeringEvent[];
  lastSeq: number;
  historyWindowed: boolean;
  snapshotRequiredSeq: number | null;
  error: string | null;
}
export function newEngineeringProjection(workspace: string, sessionId: string): EngineeringProjection {
  return { workspace, sessionId, base: null, snapshot: null, events: [], buffered: [], lastSeq: 0, historyWindowed: false, snapshotRequiredSeq: null, error: null };
}
export function engineeringEventMatchesScope(state: Pick<EngineeringProjection, "workspace" | "sessionId">, event: EngineeringEvent): boolean {
  return typeof event.workspace === 'string' && Boolean(event.workspace) && normalizeRuntimeWorkspacePath(event.workspace) === normalizeRuntimeWorkspacePath(state.workspace) && event.session_id === state.sessionId && Number.isSafeInteger(event.seq) && event.seq > 0 && typeof event.kind === 'string' && record(event.payload);
}
function fold(saved: EngineeringRunSnapshot, event: EngineeringEvent): EngineeringRunSnapshot {
  const payload = event.payload;
  if (payload.snapshot_required === true) return saved;
  if (event.kind === 'run_updated') {
    // The backend supplies the same durable summary as GET, never text-derived status.
    if (payload.workspace !== undefined && normalizeRuntimeWorkspacePath(payload.workspace) !== normalizeRuntimeWorkspacePath(saved.workspace) || payload.session_id !== undefined && payload.session_id !== saved.session_id || payload.run_id !== undefined && payload.run_id !== saved.run_id)
      throw new Error('Engineering state update identity mismatch');
    return parseEngineeringSnapshot({ ...saved, ...payload, session_id: saved.session_id, run_id: saved.run_id, workspace: saved.workspace, last_seq: event.seq, events: saved.events });
  }
  if (event.kind === 'role_started' && ENGINEERING_ROLES.includes(event.role as EngineeringRole)) {
    return { ...saved, roles: { ...saved.roles, [event.role]: 'running' }, active_roles: [...new Set([...saved.active_roles, event.role])] };
  }
  if (event.kind === 'role_finished' && ENGINEERING_ROLES.includes(event.role as EngineeringRole)) {
    const status = ROLE_STATES.has(String(payload.status)) ? payload.status : 'unknown';
    return { ...saved, roles: { ...saved.roles, [event.role]: status }, active_roles: saved.active_roles.filter((role) => role !== event.role) } as EngineeringRunSnapshot;
  }
  if (event.kind === 'verification_started' || event.kind === 'verification_completed' && VERIFICATION.has(String(payload.verification))) {
    const inputBinding = record(payload.input_binding) ? payload.input_binding : {};
    const commands = payload.verification_commands !== undefined ? payload.verification_commands : inputBinding.commands !== undefined ? inputBinding.commands : saved.verification_commands ?? saved.required_checks;
    const acceptance = event.kind === 'verification_started' ? {
      qa_verdict: payload.qa_verdict !== undefined ? payload.qa_verdict : 'unknown',
      project_outcome: payload.project_outcome !== undefined ? payload.project_outcome : 'unknown',
      acceptance_basis: payload.acceptance_basis !== undefined ? payload.acceptance_basis : null,
    } : {};
    return parseEngineeringSnapshot({ ...saved, ...acceptance, verification_commands: commands, verification: event.kind === 'verification_started' ? 'not_run' : payload.verification, checks: event.kind === 'verification_started' ? [] : Array.isArray(payload.checks) ? payload.checks : saved.checks });
  }
  return saved;
}
function rebuild(state: EngineeringProjection): EngineeringProjection {
  if (!state.base)
    return { ...state, buffered: state.buffered.slice(-200) };
  try {
    let base = state.base;
    let live = state.buffered.filter((event) => event.seq > base.last_seq).sort((a, b) => a.seq - b.seq);
    if (live.length > 200) {
      const prefix = live.slice(0, -200);
      base = { ...prefix.reduce(fold, base), last_seq: prefix[prefix.length - 1].seq, events: [...base.events, ...prefix].slice(-200) };
      live = live.slice(-200);
    }
    const snapshot = live.reduce(fold, base);
    const events = [...base.events, ...live].filter((row, index, rows) => rows.findIndex((item) => item.seq === row.seq) === index).sort((a, b) => a.seq - b.seq).slice(-200);
    return { ...state, base, snapshot, events, buffered: live, lastSeq: Math.max(base.last_seq, ...live.map((event) => event.seq)), historyWindowed: Boolean(events[0] && events[0].seq > 1), snapshotRequiredSeq: Math.max(state.snapshotRequiredSeq || 0, ...live.filter((event) => event.payload.snapshot_required === true).map((event) => event.seq)) || null, error: null };
  }
  catch (error) {
    return { ...state, buffered: state.buffered.slice(-200), error: error instanceof Error ? error.message : 'Engineering state update unavailable' };
  }
}
export function hydrateEngineeringRun(state: EngineeringProjection, raw: EngineeringRunSnapshot): EngineeringProjection {
  let snapshot: EngineeringRunSnapshot;
  try {
    snapshot = parseEngineeringSnapshot(raw);
  }
  catch (error) {
    return { ...state, error: error instanceof Error ? error.message : 'Engineering snapshot unavailable' };
  }
  if (snapshot.session_id !== state.sessionId || normalizeRuntimeWorkspacePath(snapshot.workspace) !== normalizeRuntimeWorkspacePath(state.workspace))
    return { ...state, error: 'Engineering snapshot workspace/session mismatch' };
  if (state.base && snapshot.last_seq < state.base.last_seq)
    return state;
  const events = snapshot.events.map((event) => ({ ...event, session_id: event.session_id || snapshot.session_id, workspace: event.workspace || snapshot.workspace })).filter((event) => engineeringEventMatchesScope(state, event) && event.seq <= snapshot.last_seq);
  return rebuild({ ...state, base: { ...snapshot, events }, snapshotRequiredSeq: state.snapshotRequiredSeq && state.snapshotRequiredSeq > snapshot.last_seq ? state.snapshotRequiredSeq : null });
}
export function applyEngineeringEvent(state: EngineeringProjection, event: EngineeringEvent): EngineeringProjection {
  if (!engineeringEventMatchesScope(state, event) || event.seq <= (state.base?.last_seq ?? 0) || state.buffered.some((row) => row.seq === event.seq))
    return state;
  return rebuild({ ...state, snapshotRequiredSeq: event.payload.snapshot_required === true ? Math.max(state.snapshotRequiredSeq || 0, event.seq) : state.snapshotRequiredSeq, buffered: [...state.buffered, event] });
}
export function engineeringEventFromMessage(message: unknown): EngineeringEvent | null {
  if (!record(message))
    return null;
  const envelope = record(message.event) ? message.event : message;
  if (envelope.channel !== 'event.engineering' || !record(envelope.payload))
    return null;
  const value = envelope.payload;
  if (typeof value.session_id !== 'string' || typeof value.workspace !== 'string' || !Number.isSafeInteger(value.seq) || typeof value.kind !== 'string' || typeof value.role !== 'string' || !record(value.payload))
    return null;
  return value as unknown as EngineeringEvent;
}

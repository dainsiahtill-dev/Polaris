/** Pure presentation helpers for the Mission Control console. */
import { engineeringSnapshotRef } from '@/runtime/contextSnapshot';

import type { EngineeringEvent, EngineeringRole, EngineeringRoleStatus, EngineeringRunStatus, EngineeringVerification, EngineeringQAVerdict, EngineeringProjectOutcome } from '@/services/api.types';

export const ROLE_ORDER: EngineeringRole[] = ['pm', 'chief_engineer', 'director', 'qa'];

export const ROLE_LABELS: Record<EngineeringRole, string> = {
  pm: 'PM',
  chief_engineer: 'Chief Engineer',
  director: 'Director',
  qa: 'QA',
};

/** Console navigation labels (keep stable: referenced by App tests). */
export const ROLE_NAV_LABELS: Record<EngineeringRole, string> = {
  pm: 'PM 规划',
  chief_engineer: 'CE 设计',
  director: 'Director 工程',
  qa: 'QA 审阅',
};

export const RUN_STATUS_LABELS: Record<EngineeringRunStatus, string> = {
  pending: '等待执行',
  running: '运行中',
  completed: '执行结束',
  failed: '执行失败',
  cancelled: '已取消',
  interrupted: '已中断',
};

export const ROLE_STATUS_LABELS: Record<EngineeringRoleStatus, string> = {
  not_called: 'not_called',
  running: '进行中',
  completed: '执行结束',
  failed: '执行失败',
  cancelled: '已取消',
  interrupted: '已中断',
  unknown: '未知',
};

export const VERIFICATION_LABELS: Record<EngineeringVerification, string> = {
  passed: '已通过',
  failed: '未通过',
  not_run: '未运行验证',
  unknown: '未知',
};

export const QA_LABELS: Record<EngineeringQAVerdict, string> = {
  approved: '已批准',
  rejected: '已拒绝',
  not_run: '未调用',
  unknown: '未知',
};

export const OUTCOME_LABELS: Record<EngineeringProjectOutcome, string> = {
  accepted: '已验收',
  not_accepted: '未验收',
  rejected: '已拒绝',
  unknown: '未知',
};

export const OUTCOME_BASIS_LABELS: Record<'checks_only' | 'independent_qa', string> = {
  checks_only: '仅检查',
  independent_qa: '独立QA',
};

export type Tone = 'idle' | 'info' | 'run' | 'ok' | 'warn' | 'bad' | 'gold';

export function runStatusTone(status: EngineeringRunStatus | undefined): Tone {
  switch (status) {
    case 'running': return 'run';
    case 'completed': return 'ok';
    case 'failed': return 'bad';
    case 'cancelled':
    case 'interrupted': return 'warn';
    case 'pending': return 'info';
    default: return 'idle';
  }
}

export function roleStatusTone(status: EngineeringRoleStatus | undefined): Tone {
  switch (status) {
    case 'running': return 'run';
    case 'completed': return 'ok';
    case 'failed': return 'bad';
    case 'cancelled':
    case 'interrupted': return 'warn';
    case 'not_called': return 'idle';
    default: return 'idle';
  }
}

export function verificationTone(verification: EngineeringVerification | undefined): Tone {
  switch (verification) {
    case 'passed': return 'ok';
    case 'failed': return 'bad';
    case 'not_run': return 'info';
    default: return 'idle';
  }
}

export function qaTone(qa: EngineeringQAVerdict | undefined): Tone {
  switch (qa) {
    case 'approved': return 'ok';
    case 'rejected': return 'bad';
    case 'not_run': return 'idle';
    default: return 'idle';
  }
}

export function outcomeTone(outcome: EngineeringProjectOutcome | undefined): Tone {
  switch (outcome) {
    case 'accepted': return 'gold';
    case 'rejected':
    case 'not_accepted': return 'bad';
    default: return 'idle';
  }
}

/** Short clock label `mm:ss` from an epoch-ms duration. */
export function formatElapsed(startedAtMs: number | undefined, nowMs: number): string {
  if (!startedAtMs || startedAtMs <= 0) return '—';
  const elapsed = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
  const minutes = Math.floor(elapsed / 60);
  const seconds = elapsed % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function formatClock(timestampMs: number | undefined): string {
  if (!timestampMs || timestampMs <= 0) return '—';
  const date = new Date(timestampMs);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** Readable run short id: last 4 characters upper-cased, e.g. `RUN-0042`. */
export function runShortId(sessionId: string | undefined): string {
  const tail = (sessionId || '').replace(/[^a-zA-Z0-9]/g, '').slice(-4).toUpperCase();
  return tail ? `RUN-${tail}` : 'RUN-····';
}

/** A UI-usable context snapshot ref only when the backend marks it available. */
export function contextRefOf(event: EngineeringEvent): string | null {
  return engineeringSnapshotRef(event.payload);
}

export type EventTone = 'ok' | 'bad' | 'info';

export function eventTone(event: EngineeringEvent): EventTone {
  if (event.payload.ok === false || event.kind.endsWith('failed') || event.kind.endsWith('violation')) return 'bad';
  if (event.kind === 'tool_observed') return 'ok';
  return 'info';
}

/** Human label for a journal event kind. */
export function eventKindLabel(kind: string): string {
  switch (kind) {
    case 'tool_started': return '工具调用中';
    case 'tool_observed': return '工具结果';
    case 'role_started': return '角色开始';
    case 'role_finished': return '角色结束';
    case 'verification_started': return '验证开始';
    case 'verification_completed': return '验证结束';
    case 'run_started': return '运行开始';
    case 'run_updated': return '运行更新';
    case 'message': return '消息';
    default: return kind;
  }
}

/** Tool-name buckets map a tool/event name to the timeline glyph variant. */
export type ToolGlyph = 'folder' | 'file' | 'terminal' | 'browser' | 'search' | 'generic';

export function toolGlyph(tool: string | undefined): ToolGlyph {
  const name = (tool || '').toLowerCase();
  if (!name) return 'generic';
  if (name.includes('browser') || name.includes('render') || name.includes('screenshot')) return 'browser';
  if (name.includes('repo_tree') || name.includes('tree')) return 'folder';
  if (name.includes('rg') || name.includes('grep') || name.includes('search') || name.includes('read')) return 'search';
  if (name.includes('write') || name.includes('edit') || name.includes('patch')) return 'file';
  if (name.includes('execute') || name.includes('command') || name.includes('shell') || name.includes('run_')) return 'terminal';
  if (name.includes('list')) return 'folder';
  return 'generic';
}

/** Derive a one-line summary for a receipt from its payload. */
export function receiptSummary(event: EngineeringEvent): string {
  const payload = event.payload;
  const tool = typeof payload.tool === 'string' ? payload.tool : '';
  const path = typeof payload.path === 'string' ? payload.path : typeof payload.file_path === 'string' ? payload.file_path : '';
  const command = typeof payload.command === 'string' ? payload.command : Array.isArray(payload.argv) ? (payload.argv as unknown[]).join(' ') : '';
  if (tool && path) return `${tool} · ${path}`;
  if (tool && command) return `${tool} · ${command}`;
  if (tool) return tool;
  if (command) return command;
  if (path) return path;
  return event.kind;
}

/** Extract write-like tool names between verification boundaries for the gate meter. */
export function writesSinceVerification(events: EngineeringEvent[], gateThreshold: number): { count: number; threshold: number } {
  let writes = 0;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!event) continue;
    if (event.kind === 'verification_started' || event.kind === 'verification_completed') break;
    if (event.kind !== 'tool_started') continue;
    const glyph = toolGlyph(typeof event.payload.tool === 'string' ? event.payload.tool : '');
    if (glyph === 'file' || glyph === 'terminal') writes += 1;
  }
  return { count: writes, threshold: gateThreshold };
}

/** Count loop-intervention style events emitted by the engineering stage gate. */
export function loopInterventions(events: EngineeringEvent[]): number {
  return events.filter((event) => event.kind.includes('loop') || event.kind.includes('stage_gate') || event.payload.loop_enforced === true).length;
}

/** Chinese action label for a tool/event name (timeline second line). */
export function toolLabel(tool: string | undefined): string {
  const name = (tool || '').toLowerCase();
  if (name.includes('repo_tree')) return '扫描仓库结构';
  if (name.includes('write')) return '写入文件';
  if (name.includes('edit')) return '编辑文件';
  if (name.includes('read')) return '读取文件';
  if (name.includes('execute') || name.includes('command')) return '执行命令';
  if (name.includes('browser') || name.includes('render')) return '浏览器渲染';
  if (name.includes('rg') || name.includes('grep') || name.includes('search')) return '检索代码';
  if (name.includes('list') || name.includes('tree')) return '列出文件';
  return '工具调用';
}

/** Sequential receipt id for display, e.g. `rcpt_042`. */
export function receiptId(seqOrIndex: number): string {
  return `rcpt_${String(Math.max(1, seqOrIndex)).padStart(3, '0')}`;
}

/** Extract a workspace-relative file path from free text, when unambiguous. */
export function firstFilePath(content: string): string | null {
  const match = content.match(/(?:src|tests?|lib|app|pkg|cmd|internal)\/[\w\-./]+\.(?:ts|tsx|js|jsx|mjs|cjs|py|rs|go|java|kt|cs|cpp|c|h|vue|svelte|css|scss|html|json|toml|yaml|yml|md)/);
  return match ? match[0] : null;
}

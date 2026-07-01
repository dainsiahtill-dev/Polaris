import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';

export const PROCESS_EXECUTION_CHANNELS = [
  'system',
  'process',
] as const;

export const PROCESS_ARTIFACT_CHANNELS = [] as const;

export const PROCESS_STREAM_CHANNELS = [
  ...PROCESS_EXECUTION_CHANNELS,
  ...PROCESS_ARTIFACT_CHANNELS,
] as const;

export type RuntimeProcessStreamKind = 'execution' | 'artifact';

const HISTORICAL_ARTIFACT_LOG_SOURCES = new Set(['pm-report', 'planner', 'ollama', 'qa', 'runlog']);

export const LIVE_CHANNELS = [
  'status',
  'dialogue',
  ...PROCESS_STREAM_CHANNELS,
  'llm',
] as const;

function normalizeChannelToken(channel: string) {
  return channel.trim().toLowerCase();
}

export function getRuntimeProcessStreamKind(channel: string): RuntimeProcessStreamKind | null {
  const normalized = normalizeChannelToken(channel);
  if ((PROCESS_EXECUTION_CHANNELS as readonly string[]).includes(normalized)) {
    return 'execution';
  }
  if ((PROCESS_ARTIFACT_CHANNELS as readonly string[]).includes(normalized)) {
    return 'artifact';
  }
  return null;
}

export function isProcessStreamChannel(channel: string): boolean {
  return getRuntimeProcessStreamKind(channel) !== null;
}

export function isExecutionProcessChannel(channel: string): boolean {
  return getRuntimeProcessStreamKind(channel) === 'execution';
}

export function isArtifactProcessChannel(channel: string): boolean {
  return getRuntimeProcessStreamKind(channel) === 'artifact';
}

function getLogChannel(entry: LogEntry): string {
  const rawChannel = entry.meta?.channel;
  return typeof rawChannel === 'string' ? normalizeChannelToken(rawChannel) : '';
}

export function isExecutionActivityLog(entry: LogEntry): boolean {
  const channel = getLogChannel(entry);
  if (channel) {
    return isExecutionProcessChannel(channel);
  }
  return !HISTORICAL_ARTIFACT_LOG_SOURCES.has(entry.source.trim().toLowerCase());
}

export function filterExecutionActivityLogs(entries: LogEntry[]): LogEntry[] {
  return entries.filter(isExecutionActivityLog);
}

export function getLatestExecutionActivityLog(entries: LogEntry[]): LogEntry | null {
  const filtered = filterExecutionActivityLogs(entries);
  return filtered[filtered.length - 1] ?? null;
}

export function normalizeEngineRoleName(value: string): string {
  return value.replace(/[^a-z0-9]/gi, '').toLowerCase();
}

export function readEngineRoleDetail(
  roles: Record<string, { detail?: unknown }> | null | undefined,
  names: string[],
): string {
  if (!roles) return '';
  const targetNames = new Set(names.map(normalizeEngineRoleName));
  for (const [roleName, roleStatus] of Object.entries(roles)) {
    if (!targetNames.has(normalizeEngineRoleName(roleName))) {
      continue;
    }
    return String(roleStatus?.detail || '').trim();
  }
  return '';
}

export function appendLiveContent(prev: string, incoming: string, maxLines = 2000) {
  const combined = prev ? `${prev}\n${incoming}` : incoming;
  const lines = combined.split('\n');
  if (lines.length <= maxLines) {
    return combined;
  }
  return lines.slice(-maxLines).join('\n');
}

export function normalizeDialogueEvent(raw: Record<string, unknown>): DialogueEvent | null {
  if (!raw) return null;
  const eventId = String(raw.event_id ?? '').trim();
  const rawSpeakerValue = raw.speaker ?? 'System';
  const rawSpeaker = typeof rawSpeakerValue === 'string' ? rawSpeakerValue : String(rawSpeakerValue);
  const speaker = ['PM', 'Director', 'QA', 'Reviewer', 'System'].includes(rawSpeaker)
    ? (rawSpeaker as DialogueEvent['speaker'])
    : 'System';
  const content = String(raw.text ?? raw.summary ?? raw.content ?? '').trim();
  let timestamp = String(raw.timestamp ?? raw.ts ?? raw.time ?? '').trim();
  if (timestamp.includes('T')) {
    timestamp = timestamp.split('T')[1].replace('Z', '');
  }
  const seq = typeof raw.seq === 'number' ? raw.seq : undefined;
  const type = typeof raw.type === 'string' ? raw.type : undefined;
  const refs =
    raw.refs && typeof raw.refs === 'object'
      ? (raw.refs as DialogueEvent['refs'])
      : undefined;
  const meta =
    raw.meta && typeof raw.meta === 'object' && !Array.isArray(raw.meta)
      ? (raw.meta as Record<string, unknown>)
      : undefined;
  return {
    seq,
    eventId: eventId || undefined,
    speaker,
    type,
    content: content || '(empty)',
    timestamp,
    refs,
    meta,
  };
}

export function summarizeActionError(detail: string, maxLen = 160) {
  const trimmed = detail.trim();
  if (!trimmed) return 'Action failed';
  const firstLine = trimmed.split('\n').find((line) => line.trim()) || trimmed;
  let summary = firstLine;
  if (summary.length > maxLen) {
    summary = summary.slice(0, Math.max(1, maxLen - 3)) + '...';
  }
  if (trimmed.includes('\n')) {
    summary += ' (see logs)';
  }
  return summary;
}

export function trimLogPreview(text: string, maxLines = 20) {
  const lines = text.split('\n').filter((line) => line.trim().length > 0);
  if (lines.length <= maxLines) return lines.join('\n');
  return lines.slice(-maxLines).join('\n');
}

export function normalizeAgentsFeedback(content: string) {
  if (!content) return '';
  const lines = content.split('\n');
  if (lines[0]?.startsWith('## ')) {
    return lines.slice(1).join('\n').trimStart();
  }
  return content;
}

export function extractPmStopSummary(reportText: string) {
  const lines = reportText
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (!lines.length) return '';
  for (let idx = lines.length - 1; idx >= 0; idx -= 1) {
    const line = lines[idx];
    const lowered = line.toLowerCase();
    if (lowered.includes('halted') || lowered.startsWith('status:')) {
      return line;
    }
    if (lowered.startsWith('director exit')) {
      return line;
    }
  }
  const last = lines[lines.length - 1];
  if (last.startsWith('{') || last.startsWith('[')) {
    return '';
  }
  return last;
}

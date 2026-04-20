import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { LogEntry } from '@/types/log';

export const PROCESS_EXECUTION_CHANNELS = [
  'system',
  'process',
  'pm_subprocess',
  'director_console',
  'pm_log',
  'engine_status',
] as const;

export const PROCESS_ARTIFACT_CHANNELS = [
  'pm_report',
  'planner',
  'ollama',
  'qa',
  'runlog',
] as const;

export const PROCESS_STREAM_CHANNELS = [
  ...PROCESS_EXECUTION_CHANNELS,
  ...PROCESS_ARTIFACT_CHANNELS,
] as const;

export type RuntimeProcessStreamKind = 'execution' | 'artifact';

const ARTIFACT_LOG_SOURCES = new Set(['pm-report', 'planner', 'ollama', 'qa', 'runlog']);

export const LIVE_CHANNELS = [
  'status',
  'dialogue',
  ...PROCESS_STREAM_CHANNELS,
  'runtime_events',
  'llm',
] as const;

export const CHANNEL_TO_PATH: Record<string, string> = {
  dialogue: 'runtime/events/dialogue.transcript.jsonl',
  pm_report: 'runtime/results/pm.report.md',
  pm_log: 'runtime/events/pm.events.jsonl',
  pm_subprocess: 'runtime/logs/pm.process.log',
  director_console: 'runtime/logs/director.process.log',
  planner: 'runtime/results/planner.output.md',
  ollama: 'runtime/results/director_llm.output.md',
  qa: 'runtime/results/qa.review.md',
  runlog: 'runtime/logs/director.runlog.md',
};

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
  return !ARTIFACT_LOG_SOURCES.has(entry.source.trim().toLowerCase());
}

export function filterExecutionActivityLogs(entries: LogEntry[]): LogEntry[] {
  return entries.filter(isExecutionActivityLog);
}

export function getLatestExecutionActivityLog(entries: LogEntry[]): LogEntry | null {
  const filtered = filterExecutionActivityLogs(entries);
  return filtered[filtered.length - 1] ?? null;
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
  return {
    seq,
    eventId: eventId || undefined,
    speaker,
    type,
    content: content || '(empty)',
    timestamp,
    refs,
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


/** Parser for the host's bounded-context compression marker.
 *
 * When the host drops earlier turns to fit the prompt budget it inserts a
 * user-role marker into the prepared request. The wording has changed across
 * engine versions, so both known forms are accepted:
 *
 *   【上下文已压缩】为控制在有界上下文预算（约 32000 tokens）内，省略了 12 条较早的工具轮次；…
 *   【上下文已压缩】为适配模型窗口，省略了 69 条较早的工具轮次；…
 *
 * The parser only reports numbers the host actually wrote; it never invents a
 * budget or a dropped count, and it keeps the raw text so the UI can quote the
 * host instead of paraphrasing it.
 */
import type { EngineeringEvent } from '@/services/api.types';

export type CompressionDropUnit = 'tool_turns' | 'messages' | 'turns' | 'unknown';

export interface ContextCompressionFact {
  /** Raw host marker text, whitespace-normalized, quoted verbatim by the UI. */
  text: string;
  /** Number of earlier turns the host says it omitted. */
  droppedTurns: number;
  /** Bounded prompt budget from the marker, when the host stated one. */
  budgetTokens: number | null;
  /** What the dropped unit refers to, so the UI does not mislabel it. */
  dropUnit: CompressionDropUnit;
  /** Journal seq of the request_prepared event that carried the marker. */
  sourceSeq: number | null;
}

export interface ContextMessageLike {
  role?: unknown;
  content?: unknown;
}

const MARKER = '上下文已压缩';

function contentText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((block) => {
      if (typeof block === 'string') return block;
      if (block && typeof block === 'object') {
        const record = block as Record<string, unknown>;
        return typeof record.text === 'string' ? record.text : '';
      }
      return '';
    })
    .join('\n');
}

function dropUnitOf(unitText: string): CompressionDropUnit {
  if (unitText.includes('工具')) return 'tool_turns';
  if (unitText.includes('对话') || unitText.includes('消息')) return 'messages';
  if (unitText.includes('轮次')) return 'turns';
  return 'unknown';
}

/**
 * Return the last compression marker in a message list, or null when the host
 * never compressed. Message order is the request order, so the last marker is
 * the most recent compression the host applied.
 */
export function parseContextCompression(messages: readonly ContextMessageLike[]): ContextCompressionFact | null {
  let latest: ContextCompressionFact | null = null;
  for (const message of messages) {
    const body = contentText(message?.content);
    if (!body.includes(MARKER)) continue;
    const dropped = /省略了\s*(\d+)\s*条较早的([^；;。]+)/.exec(body);
    if (!dropped) continue;
    const budget = /约\s*([\d,]+)\s*tokens/i.exec(body);
    latest = {
      text: body.replace(/\s+/g, ' ').trim(),
      droppedTurns: Number.parseInt(dropped[1], 10),
      budgetTokens: budget ? Number.parseInt(budget[1].replace(/,/g, ''), 10) : null,
      dropUnit: dropUnitOf(dropped[2] ?? ''),
      sourceSeq: null,
    };
  }
  return latest;
}

/** Prepared request messages from a request_prepared engineering event. */
export function preparedRequestMessages(payload: Record<string, unknown>): ContextMessageLike[] {
  const request = payload.request;
  if (!request || typeof request !== 'object' || Array.isArray(request)) return [];
  const record = request as Record<string, unknown>;
  const options = record.options;
  const containers: unknown[] = [options, record];
  for (const container of containers) {
    if (!container || typeof container !== 'object' || Array.isArray(container)) continue;
    const messages = (container as Record<string, unknown>).messages;
    if (!Array.isArray(messages)) continue;
    return messages.filter((message): message is ContextMessageLike => Boolean(message && typeof message === 'object'));
  }
  return [];
}

/**
 * Latest compression fact carried by the engineering journal. The journal is
 * the only source the live view already has; no extra fetch is needed.
 */
export function compressionFromEvents(events: readonly EngineeringEvent[]): ContextCompressionFact | null {
  let latest: ContextCompressionFact | null = null;
  for (const event of events) {
    if (event.kind !== 'request_prepared') continue;
    const fact = parseContextCompression(preparedRequestMessages(event.payload));
    if (fact) latest = { ...fact, sourceSeq: event.seq };
  }
  return latest;
}

export const COMPRESSION_DROP_UNIT_LABELS: Record<CompressionDropUnit, string> = {
  tool_turns: '工具轮次',
  messages: '对话消息',
  turns: '轮次',
  unknown: '轮次',
};

export interface ChatStreamEventData {
  content?: string;
  response?: string;
  message?: string;
  complete?: string;
  error?: string;
}

export interface ChatStreamEvent {
  type: 'thinking_chunk' | 'content_chunk' | 'complete' | 'error';
  data?: ChatStreamEventData;
}

export interface JetstreamChatStartResponse {
  session_id?: string;
  status?: string;
  channel?: string;
  subject?: string;
  transport?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isChatStreamEventType(value: unknown): value is ChatStreamEvent['type'] {
  return value === 'thinking_chunk' || value === 'content_chunk' || value === 'complete' || value === 'error';
}

function normalizeEventData(value: unknown): ChatStreamEventData {
  if (!isRecord(value)) return {};
  return {
    content: typeof value.content === 'string' ? value.content : undefined,
    response: typeof value.response === 'string' ? value.response : undefined,
    message: typeof value.message === 'string' ? value.message : undefined,
    complete: typeof value.complete === 'string' ? value.complete : undefined,
    error: typeof value.error === 'string' ? value.error : undefined,
  };
}

export function normalizeRuntimeChatEvent(raw: unknown, channel: string): ChatStreamEvent | null {
  const message = isRecord(raw) ? raw : {};
  const event = message.type === 'EVENT' && isRecord(message.event)
    ? message.event
    : message;
  if (event.channel !== channel) return null;

  const payload = isRecord(event.payload) ? event.payload : {};
  if (!isChatStreamEventType(payload.type)) return null;
  return {
    type: payload.type,
    data: normalizeEventData(payload.data),
  };
}

export function chatCompleteContent(data: ChatStreamEventData | undefined): string {
  // Historical chat stream producers used response/complete for terminal text.
  // Keep those aliases only at this protocol boundary so UI components consume
  // a single normalized contract.
  return data?.content ?? data?.response ?? data?.complete ?? '';
}

export function chatErrorMessage(data: ChatStreamEventData | undefined): string {
  return data?.error ?? data?.message ?? '未知错误';
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { getBackendInfo } from '@/api';
import { useRuntimeTransport } from '@/runtime/transport';
import type { TestEvent } from '../test/types';

export interface StreamEvent {
  type: string;
  data: Record<string, unknown>;
}

export type RealtimeThinkingKind = 'reasoning' | 'command_execution' | 'agent_message';

export type StreamingTagEventType =
  | 'thinking_start'
  | 'thinking_chunk'
  | 'thinking_end'
  | 'answer_start'
  | 'answer_chunk'
  | 'answer_end';

export interface StreamingTagEvent {
  type: StreamingTagEventType;
  data: {
    content?: string;
    timestamp: string;
    isComplete?: boolean;
  };
}

export interface RealtimeThinkingEvent {
  id: string;
  kind: RealtimeThinkingKind;
  timestamp: string;
  text?: string;
  command?: string;
  output?: string;
  status?: string;
  exitCode?: number | null;
  thinking?: string | null;
  answer?: string | null;
  raw?: string;
}

export interface InterviewStreamResult {
  sessionId: string;
  answer: string;
  output?: string;
  thinking?: string;
  latencyMs?: number;
  ok?: boolean;
  error?: string | null;
}

export interface UseInterviewStreamOptions {
  onEvent?: (event: TestEvent) => void;
  onStart?: (sessionId: string) => void;
  onComplete?: (result: InterviewStreamResult) => void;
  onError?: (error: string) => void;
  onThinkingEvent?: (event: RealtimeThinkingEvent) => void;
  onTagEvent?: (event: StreamingTagEvent) => void;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

interface JetstreamStartResponse {
  ok?: boolean;
  session_id?: string;
  status?: string;
  channel?: string;
  subject?: string;
  transport?: string;
}

function createClientRunId(prefix: string): string {
  const randomId =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2, 12);
  return `${prefix}-${randomId}`;
}

function normalizeStreamId(value: string | null | undefined, prefix: string): string {
  const raw = String(value || '').trim() || createClientRunId(prefix);
  const safe = raw.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^[._-]+|[._-]+$/g, '');
  return safe.slice(0, 96) || createClientRunId(prefix);
}

function streamEventFromRuntimeMessage(message: unknown): StreamEvent | null {
  const msg = asRecord(message);
  const event = msg.type === 'EVENT' ? asRecord(msg.event) : msg;
  const payload = asRecord(event.payload);
  const eventType = typeof payload.type === 'string' ? payload.type : '';
  if (!eventType) return null;
  return {
    type: eventType,
    data: asRecord(payload.data),
  };
}

type StreamTagName = 'thinking' | 'answer';

const STREAM_TAG_ALIASES: Record<string, StreamTagName> = {
  thinking: 'thinking',
  think: 'thinking',
  reasoning: 'thinking',
  analysis: 'thinking',
  answer: 'answer',
  final: 'answer',
  response: 'answer',
};

const START_EVENT_BY_TAG: Record<StreamTagName, StreamingTagEventType> = {
  thinking: 'thinking_start',
  answer: 'answer_start',
};

const CHUNK_EVENT_BY_TAG: Record<StreamTagName, StreamingTagEventType> = {
  thinking: 'thinking_chunk',
  answer: 'answer_chunk',
};

const END_EVENT_BY_TAG: Record<StreamTagName, StreamingTagEventType> = {
  thinking: 'thinking_end',
  answer: 'answer_end',
};

const CLOSE_TAGS_BY_TAG: Record<StreamTagName, string[]> = {
  thinking: ['</thinking>', '</think>', '</reasoning>', '</analysis>'],
  answer: ['</answer>', '</final>', '</response>'],
};

const normalizeTagName = (rawTag: string): StreamTagName | null => {
  const normalized = rawTag
    .trim()
    .replace(/^\//, '')
    .split(/\s+/)[0]
    ?.toLowerCase();
  return normalized ? STREAM_TAG_ALIASES[normalized] || null : null;
};

const trailingCloseTagPrefixLength = (value: string, tag: StreamTagName) => {
  const lowerValue = value.toLowerCase();
  let bestLength = 0;
  for (const closeTag of CLOSE_TAGS_BY_TAG[tag]) {
    const maxLength = Math.min(closeTag.length - 1, lowerValue.length);
    for (let length = maxLength; length > bestLength; length -= 1) {
      if (closeTag.startsWith(lowerValue.slice(-length))) {
        bestLength = length;
        break;
      }
    }
  }
  return bestLength;
};

const findCloseTag = (value: string, tag: StreamTagName) => {
  const lowerValue = value.toLowerCase();
  let best: { index: number; length: number } | null = null;
  for (const closeTag of CLOSE_TAGS_BY_TAG[tag]) {
    const index = lowerValue.indexOf(closeTag);
    if (index >= 0 && (!best || index < best.index)) {
      best = { index, length: closeTag.length };
    }
  }
  return best;
};

export const createContentTagParser = () => {
  let buffer = '';
  let activeTag: StreamTagName | null = null;

  const emit = (
    type: StreamingTagEventType,
    content: string | undefined,
    timestamp: string,
    onTagEvent?: (event: StreamingTagEvent) => void
  ) => {
    onTagEvent?.({
      type,
      data: {
        content,
        timestamp,
        isComplete: type.endsWith('_end') ? true : undefined,
      },
    });
  };

  const consume = (
    chunk: string,
    timestamp: string,
    onTagEvent?: (event: StreamingTagEvent) => void
  ) => {
    if (!chunk) return;
    buffer += chunk;

    for (let guard = 0; guard < 1000 && buffer; guard += 1) {
      if (activeTag) {
        const closeMatch = findCloseTag(buffer, activeTag);

        if (!closeMatch) {
          const keepLength = trailingCloseTagPrefixLength(buffer, activeTag);
          const emitText = keepLength > 0 ? buffer.slice(0, -keepLength) : buffer;
          if (emitText) {
            emit(CHUNK_EVENT_BY_TAG[activeTag], emitText, timestamp, onTagEvent);
          }
          buffer = keepLength > 0 ? buffer.slice(-keepLength) : '';
          break;
        }

        const text = buffer.slice(0, closeMatch.index);
        if (text) {
          emit(CHUNK_EVENT_BY_TAG[activeTag], text, timestamp, onTagEvent);
        }
        emit(END_EVENT_BY_TAG[activeTag], undefined, timestamp, onTagEvent);
        buffer = buffer.slice(closeMatch.index + closeMatch.length);
        activeTag = null;
        continue;
      }

      const tagStart = buffer.indexOf('<');
      if (tagStart < 0) {
        buffer = buffer.slice(Math.max(0, buffer.length - 16));
        break;
      }
      if (tagStart > 0) {
        buffer = buffer.slice(tagStart);
      }

      const tagEnd = buffer.indexOf('>');
      if (tagEnd < 0) {
        break;
      }

      const rawTag = buffer.slice(1, tagEnd);
      buffer = buffer.slice(tagEnd + 1);
      if (rawTag.trim().startsWith('/')) {
        continue;
      }

      const nextTag = normalizeTagName(rawTag);
      if (nextTag) {
        activeTag = nextTag;
        emit(START_EVENT_BY_TAG[nextTag], undefined, timestamp, onTagEvent);
      }
    }
  };

  return { consume };
};

export function useInterviewStream(options: UseInterviewStreamOptions = {}) {
  const { onEvent, onStart, onComplete, onError, onThinkingEvent, onTagEvent } = options;
  const transport = useRuntimeTransport();
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  const unsubscribeStreamRef = useRef<(() => void) | null>(null);
  const unregisterHandlerRef = useRef<(() => void) | null>(null);

  const cleanupActiveStream = useCallback(() => {
    unregisterHandlerRef.current?.();
    unregisterHandlerRef.current = null;
    unsubscribeStreamRef.current?.();
    unsubscribeStreamRef.current = null;
  }, []);

  const requestCancel = useCallback(async (sessionId: string) => {
    try {
      const backendInfo = await getBackendInfo();
      if (!backendInfo.baseUrl) return;

      await fetch(`${backendInfo.baseUrl}/v2/llm/interview/cancel`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(backendInfo.token ? { Authorization: `Bearer ${backendInfo.token}` } : {}),
        },
        body: JSON.stringify({ session_id: sessionId }),
      });
    } catch {
      // ignore
    }
  }, []);

  const startStream = useCallback(async (payload: {
    roleId: string;
    providerId: string;
    model: string;
    question: string;
    expectedCriteria?: string[];
    expectsThinking?: boolean;
    sessionId?: string | null;
    context?: Array<{ question: string; answer: string }>;
    envOverrides?: Record<string, string>;
  }) => {
    if (isStreaming) return;

    cleanupActiveStream();
    const streamSessionId = normalizeStreamId(payload.sessionId, 'interactive');
    const channel = `llm-interview:${streamSessionId}`;
    const abortController = new AbortController();
    let finalResult: InterviewStreamResult | null = null;
    let streamError: string | null = null;
    let resolveDone: (() => void) | null = null;
    const streamDone = new Promise<void>((resolve) => {
      resolveDone = resolve;
    });
    const finishStream = () => {
      resolveDone?.();
      resolveDone = null;
    };

    setIsStreaming(true);
    activeSessionIdRef.current = streamSessionId;
    abortControllerRef.current = abortController;

    try {
      const backendInfo = await getBackendInfo();
      if (!backendInfo.baseUrl) {
        throw new Error('Backend baseUrl missing.');
      }

      const contentTagParser = createContentTagParser();

      const handleStreamEvent = (eventType: string, data: Record<string, unknown>) => {
        switch (eventType) {
          case 'start':
            if (typeof data.session_id === 'string' && data.session_id) {
              activeSessionIdRef.current = data.session_id;
              onStart?.(data.session_id);
            }
            onEvent?.({
              type: 'stdout',
              timestamp: new Date().toISOString(),
              content: `Stream started: ${String(data.session_id || '')}`,
              details: { kind: 'start', ...data },
            });
            break;

          case 'command':
            onEvent?.({
              type: 'command',
              timestamp: new Date().toISOString(),
              content: `${String(data.command || '')} ${Array.isArray(data.args) ? data.args.join(' ') : ''}`,
              details: data,
            });
            break;

          case 'stdout':
            onEvent?.({
              type: 'stdout',
              timestamp: new Date().toISOString(),
              content: String(data.line || ''),
            });
            break;

          case 'stderr':
            onEvent?.({
              type: 'stderr',
              timestamp: new Date().toISOString(),
              content: String(data.line || ''),
            });
            break;

          case 'content_chunk': {
            const content = typeof data.content === 'string' ? data.content : '';
            const timestamp = typeof data.timestamp === 'string' ? data.timestamp : new Date().toISOString();
            onEvent?.({
              type: 'stdout',
              timestamp,
              content: `[content_chunk] ${JSON.stringify(data)}`,
            });
            contentTagParser.consume(content, timestamp, onTagEvent);
            break;
          }

          case 'thinking':
          case 'command_execution':
          case 'agent_message': {
            const itemId =
              typeof data.item_id === 'string' || typeof data.item_id === 'number'
                ? String(data.item_id)
                : '';
            const event: RealtimeThinkingEvent = {
              id: itemId || `${eventType}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              kind:
                eventType === 'thinking'
                  ? 'reasoning'
                  : (eventType as RealtimeThinkingKind),
              timestamp: typeof data.timestamp === 'string' ? data.timestamp : new Date().toISOString(),
              text: typeof data.text === 'string' ? data.text : undefined,
              command: typeof data.command === 'string' ? data.command : undefined,
              output: typeof data.output === 'string' ? data.output : undefined,
              status: typeof data.status === 'string' ? data.status : undefined,
              exitCode:
                typeof data.exit_code === 'number'
                  ? data.exit_code
                  : typeof data.exit_code === 'string'
                    ? Number(data.exit_code)
                    : undefined,
              thinking: typeof data.thinking === 'string' ? data.thinking : undefined,
              answer: typeof data.answer === 'string' ? data.answer : undefined,
              raw: typeof data.raw === 'string' ? data.raw : undefined,
            };
            onThinkingEvent?.(event);
            break;
          }

          case 'complete':
            finalResult = {
              ...(data as unknown as InterviewStreamResult),
              sessionId:
                typeof data.sessionId === 'string'
                  ? data.sessionId
                  : typeof data.session_id === 'string'
                    ? data.session_id
                    : streamSessionId,
            };
            break;

          case 'error':
            streamError = String(data.error || 'Unknown error');
            onEvent?.({
              type: 'error',
              timestamp: new Date().toISOString(),
              content: streamError,
            });
            onError?.(streamError);
            break;

          case 'ping':
            break;

          case 'thinking_start':
          case 'thinking_chunk':
          case 'thinking_end':
          case 'answer_start':
          case 'answer_chunk':
          case 'answer_end':
            onTagEvent?.({
              type: eventType as StreamingTagEventType,
              data: {
                content: typeof data.content === 'string' ? data.content : undefined,
                timestamp: typeof data.timestamp === 'string' ? data.timestamp : new Date().toISOString(),
                isComplete: typeof data.is_complete === 'boolean' ? data.is_complete : undefined,
              },
            });
            break;

          default:
            onEvent?.({
              type: 'stdout',
              timestamp: new Date().toISOString(),
              content: `[${eventType}] ${JSON.stringify(data)}`,
            });
        }
      };

      const abortHandler = () => finishStream();
      abortController.signal.addEventListener('abort', abortHandler, { once: true });

      unregisterHandlerRef.current = transport.registerMessageHandler((message) => {
        const streamEvent = streamEventFromRuntimeMessage(message);
        if (!streamEvent) return;
        handleStreamEvent(streamEvent.type, streamEvent.data);
        if (streamEvent.type === 'complete' || streamEvent.type === 'error') {
          finishStream();
        }
      }, channel);
      unsubscribeStreamRef.current = transport.subscribeChannels([
        { channel, tailLines: 0 },
      ]);
      if (!transport.connected) {
        transport.reconnect();
      }

      const response = await fetch(`${backendInfo.baseUrl}/v2/llm/interview/jetstream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(backendInfo.token ? { Authorization: `Bearer ${backendInfo.token}` } : {}),
        },
        body: JSON.stringify({
          role: payload.roleId,
          provider_id: payload.providerId,
          model: payload.model,
          question: payload.question,
          criteria: payload.expectedCriteria,
          expects_thinking: payload.expectsThinking,
          session_id: streamSessionId,
          context: payload.context,
          env_overrides: payload.envOverrides,
        }),
        signal: abortController.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `HTTP ${response.status}`);
      }

      const startResponse = (await response.json()) as JetstreamStartResponse;
      if (startResponse.ok === false) {
        throw new Error('Failed to start interview stream.');
      }

      await streamDone;
      abortController.signal.removeEventListener('abort', abortHandler);

      if (finalResult && !streamError && !abortController.signal.aborted) {
        onComplete?.(finalResult);
      }

    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        onEvent?.({
          type: 'error',
          timestamp: new Date().toISOString(),
          content: error.message,
        });
        onError?.(error.message);
      }
    } finally {
      cleanupActiveStream();
      setIsStreaming(false);
      abortControllerRef.current = null;
      activeSessionIdRef.current = null;
    }
  }, [
    cleanupActiveStream,
    isStreaming,
    onEvent,
    onStart,
    onComplete,
    onError,
    onThinkingEvent,
    onTagEvent,
    transport,
  ]);

  const stopStream = useCallback((sessionIdOverride?: string | null) => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    cleanupActiveStream();
    const sessionId = sessionIdOverride ?? activeSessionIdRef.current;
    activeSessionIdRef.current = null;
    if (sessionId) {
      void requestCancel(sessionId);
    }
    setIsStreaming(false);
  }, [cleanupActiveStream, requestCancel]);

  // 组件卸载时清理资源
  useEffect(() => {
    return () => {
      // 强制停止所有进行中的流
      abortControllerRef.current?.abort();
      cleanupActiveStream();
      // 如果有活跃会话，通知后端取消
      const sessionId = activeSessionIdRef.current;
      if (sessionId) {
        void requestCancel(sessionId);
      }
    };
  }, [cleanupActiveStream, requestCancel]);

  return {
    isStreaming,
    startStream,
    stopStream,
  };
}

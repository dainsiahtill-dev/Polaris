import { useCallback, useEffect, useRef, useState } from 'react';
import { getBackendInfo } from '@/api';
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
  const [isStreaming, setIsStreaming] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);

  const requestCancel = useCallback(async (sessionId: string) => {
    try {
      const backendInfo = await getBackendInfo();
      if (!backendInfo.baseUrl) return;

      await fetch(`${backendInfo.baseUrl}/llm/interview/cancel`, {
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
    
    setIsStreaming(true);
    activeSessionIdRef.current = payload.sessionId ? String(payload.sessionId) : null;
    
    // Close any existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    
    // Use fetch with ReadableStream for POST request
    // EventSource doesn't support POST, so we use fetch + ReadableStream
    abortControllerRef.current = new AbortController();
    
    try {
      const backendInfo = await getBackendInfo();
      if (!backendInfo.baseUrl) {
        throw new Error('Backend baseUrl missing.');
      }
      const response = await fetch(`${backendInfo.baseUrl}/llm/interview/stream`, {
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
          session_id: payload.sessionId,
          context: payload.context,
          env_overrides: payload.envOverrides,
        }),
        signal: abortControllerRef.current.signal,
      });
      
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `HTTP ${response.status}`);
      }
      
      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No response body');
      }
      
      const decoder = new TextDecoder();
      let buffer = '';
      let finalResult: InterviewStreamResult | null = null;
      const contentTagParser = createContentTagParser();
      let currentEvent: string | null = null;
      let currentData = '';
      
      while (true) {
        const { done, value } = await reader.read();
        
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        
        // Process SSE messages
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        for (const rawLine of lines) {
          const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7);
          } else if (line.startsWith('data: ')) {
            currentData = currentData ? `${currentData}\n${line.slice(6)}` : line.slice(6);
          } else if (line === '' && currentEvent) {
            // End of event, process it
            try {
              const data = JSON.parse(currentData);
              
              switch (currentEvent) {
                case 'start':
                  if (typeof data.session_id === 'string' && data.session_id) {
                    activeSessionIdRef.current = data.session_id;
                    onStart?.(data.session_id);
                  }
                  onEvent?.({
                    type: 'stdout',
                    timestamp: new Date().toISOString(),
                    content: `Stream started: ${data.session_id}`,
                    details: { kind: 'start', ...data },
                  });
                  break;
                  
                case 'command':
                  onEvent?.({
                    type: 'command',
                    timestamp: new Date().toISOString(),
                    content: `${data.command} ${data.args?.join(' ') || ''}`,
                    details: data,
                  });
                  break;
                  
                case 'stdout':
                  onEvent?.({
                    type: 'stdout',
                    timestamp: new Date().toISOString(),
                    content: data.line || '',
                  });
                  break;
                  
                case 'stderr':
                  onEvent?.({
                    type: 'stderr',
                    timestamp: new Date().toISOString(),
                    content: data.line || '',
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
                    id: itemId || `${currentEvent}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    kind:
                      currentEvent === 'thinking'
                        ? 'reasoning'
                        : (currentEvent as RealtimeThinkingKind),
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
                  finalResult = data as InterviewStreamResult;
                  break;
                  
                case 'error':
                  onEvent?.({
                    type: 'error',
                    timestamp: new Date().toISOString(),
                    content: data.error || 'Unknown error',
                  });
                  onError?.(data.error || 'Unknown error');
                  break;
                  
                case 'ping':
                  // Heartbeat, ignore
                  break;

                case 'thinking_start':
                case 'thinking_chunk':
                case 'thinking_end':
                case 'answer_start':
                case 'answer_chunk':
                case 'answer_end':
                  onTagEvent?.({
                    type: currentEvent as StreamingTagEventType,
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
                    content: `[${currentEvent}] ${JSON.stringify(data)}`,
                  });
              }
            } catch (e) {
              // Invalid JSON, ignore
            }
            
            currentEvent = null;
            currentData = '';
          }
        }
      }
      
      if (finalResult) {
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
      setIsStreaming(false);
      abortControllerRef.current = null;
      eventSourceRef.current = null;
      activeSessionIdRef.current = null;
    }
  }, [isStreaming, onEvent, onStart, onComplete, onError, onThinkingEvent, onTagEvent]);

  const stopStream = useCallback((sessionIdOverride?: string | null) => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    const sessionId = sessionIdOverride ?? activeSessionIdRef.current;
    activeSessionIdRef.current = null;
    if (sessionId) {
      void requestCancel(sessionId);
    }
    setIsStreaming(false);
  }, [requestCancel]);

  // 组件卸载时清理资源
  useEffect(() => {
    return () => {
      // 强制停止所有进行中的流
      abortControllerRef.current?.abort();
      eventSourceRef.current?.close();
      // 如果有活跃会话，通知后端取消
      const sessionId = activeSessionIdRef.current;
      if (sessionId) {
        void requestCancel(sessionId);
      }
    };
  }, [requestCancel]);

  return {
    isStreaming,
    startStream,
    stopStream,
  };
}

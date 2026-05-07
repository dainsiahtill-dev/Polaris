import { useCallback, useEffect, useRef, useState } from 'react';
import { getBackendInfo } from '@/api';
import type { TestEvent } from '../../test/types';
import { devLogger } from '@/app/utils/devLogger';

export interface TestStreamEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface TestSuiteStartEvent {
  suite: string;
}

export interface TestSuiteCompleteEvent {
  suite: string;
  result: {
    ok: boolean;
    details?: Record<string, unknown>;
  };
}

export interface TestCompleteEvent {
  schema_version: number;
  test_run_id: string;
  timestamp: string;
  target: {
    role: string;
    provider_id: string;
    model: string;
  };
  suites: Record<string, unknown>;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    estimated: boolean;
  };
  final: {
    ready: boolean;
    grade: string;
    next_action: string;
  };
}

export interface UseTestStreamOptions {
  onEvent?: (event: TestEvent) => void;
  onSuiteStart?: (suite: string) => void;
  onSuiteComplete?: (suite: string, result: { ok: boolean }) => void;
  onComplete?: (report: TestCompleteEvent) => void;
  onError?: (error: string) => void;
}

// Extended payload for Scheme B (connectivity-only with direct config)
export interface TestStreamPayload {
  role: string;
  providerId: string;
  model: string;
  suites?: string[];
  testLevel?: string;
  evaluationMode?: string;
  apiKey?: string | null;
  envOverrides?: Record<string, string>;
  // Scheme B: direct config fields for connectivity-only tests
  providerType?: string;
  baseUrl?: string;
  apiPath?: string;
  timeout?: number;
}

export function useTestStream(options: UseTestStreamOptions = {}) {
  const { onEvent, onSuiteStart, onSuiteComplete, onComplete, onError } = options;
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const startStream = useCallback(async (payload: TestStreamPayload) => {
    if (isStreaming) {
      devLogger.debug('[useTestStream] Already streaming, ignoring');
      return;
    }

    devLogger.debug('[useTestStream] Starting stream', payload);
    setIsStreaming(true);

    // 立即通知开始
    onEvent?.({
      type: 'stdout',
      timestamp: new Date().toISOString(),
      content: '🔌 正在建立流式连接...',
    });

    abortControllerRef.current = new AbortController();

    try {
      const backendInfo = await getBackendInfo();
      if (!backendInfo.baseUrl) {
        throw new Error('Backend baseUrl missing');
      }

      onEvent?.({
        type: 'stdout',
        timestamp: new Date().toISOString(),
        content: `📍 服务器: ${backendInfo.baseUrl}`,
      });

      // Build request body
      const requestBody: Record<string, unknown> = {
        role: payload.role,
        provider_id: payload.providerId,
        model: payload.model,
        suites: payload.suites || ['connectivity', 'response'],
        test_level: payload.testLevel || 'quick',
        evaluation_mode: payload.evaluationMode || 'provider',
        api_key: payload.apiKey,
        env_overrides: payload.envOverrides,
      };

      // Scheme B: Add direct config fields for connectivity-only tests
      // When baseUrl is provided and suites is ['connectivity'], backend will bypass config loading
      if (payload.baseUrl) {
        requestBody.base_url = payload.baseUrl;
      }
      if (payload.providerType) {
        requestBody.provider_type = payload.providerType;
      }
      if (payload.apiPath) {
        requestBody.api_path = payload.apiPath;
      }
      if (payload.timeout !== undefined) {
        requestBody.timeout = payload.timeout;
      }

      const response = await fetch(`${backendInfo.baseUrl}/v2/llm/test/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(backendInfo.token ? { Authorization: `Bearer ${backendInfo.token}` } : {}),
        },
        body: JSON.stringify(requestBody),
        signal: abortControllerRef.current.signal,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `HTTP ${response.status}`);
      }

      onEvent?.({
        type: 'stdout',
        timestamp: new Date().toISOString(),
        content: '✅ 连接成功，接收数据中...',
      });

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('No response body');
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          devLogger.debug('[useTestStream] Stream done');
          break;
        }

        const chunk = decoder.decode(value, { stream: true });
        devLogger.debug('[useTestStream] Chunk:', chunk);
        buffer += chunk;

        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || '';

        let currentEvent: string | null = null;
        let currentData = '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7);
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6);
          } else if (line === '' && currentEvent) {
            try {
              const data = JSON.parse(currentData);
              devLogger.debug('[useTestStream] Event:', currentEvent, data);

              switch (currentEvent) {
                case 'start':
                  {
                    const runId =
                      typeof data.test_run_id === 'string' && data.test_run_id.trim()
                        ? data.test_run_id.trim()
                        : typeof data.run_id === 'string' && data.run_id.trim()
                          ? data.run_id.trim()
                          : '';
                  onEvent?.({
                    type: 'stdout',
                    timestamp: new Date().toISOString(),
                      content: runId ? `Test started: ${runId}` : 'Test started',
                    details: data,
                  });
                  }
                  break;

                case 'suite_start':
                  if (data.suite) {
                    onSuiteStart?.(data.suite);
                    onEvent?.({
                      type: 'command',
                      timestamp: new Date().toISOString(),
                      content: `Starting suite: ${data.suite}`,
                      details: data,
                    });
                  }
                  break;

                case 'suite_complete':
                  if (data.suite && data.result) {
                    onSuiteComplete?.(data.suite, data.result);
                    onEvent?.({
                      type: data.result.ok ? 'result' : 'error',
                      timestamp: new Date().toISOString(),
                      content: `Suite ${data.suite}: ${data.result.ok ? 'PASS' : 'FAIL'}`,
                      details: data,
                    });
                  }
                  break;

                case 'suite_error':
                  onEvent?.({
                    type: 'error',
                    timestamp: new Date().toISOString(),
                    content: `Suite error: ${data.error || 'Unknown error'}`,
                    details: data,
                  });
                  break;

                case 'complete':
                  onComplete?.(data as TestCompleteEvent);
                  onEvent?.({
                    type: 'result',
                    timestamp: new Date().toISOString(),
                    content: `Test completed: ${data.final?.grade || 'UNKNOWN'}`,
                    details: data,
                  });
                  break;

                case 'error':
                  onEvent?.({
                    type: 'error',
                    timestamp: new Date().toISOString(),
                    content: data.error || 'Unknown error',
                  });
                  onError?.(data.error || 'Unknown error');
                  break;

                case 'debug':
                  if (data.message) {
                    onEvent?.({
                      type: 'stdout',
                      timestamp: new Date().toISOString(),
                      content: `[DEBUG] ${data.message}`,
                      details: data.details,
                    });
                  }
                  break;

                case 'ping':
                  break;

                default:
                  onEvent?.({
                    type: 'stdout',
                    timestamp: new Date().toISOString(),
                    content: `[${currentEvent}] ${JSON.stringify(data)}`,
                  });
              }
            } catch (parseError) {
              devLogger.debug('[useTestStream] Parse error:', parseError);
            }

            currentEvent = null;
            currentData = '';
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        onEvent?.({
          type: 'error',
          timestamp: new Date().toISOString(),
          content: `❌ ${error.message}`,
        });
        onError?.(error.message);
      }
    } finally {
      setIsStreaming(false);
      abortControllerRef.current = null;
    }
  }, [isStreaming, onEvent, onSuiteStart, onSuiteComplete, onComplete, onError]);

  const stopStream = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setIsStreaming(false);
  }, []);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  return {
    isStreaming,
    startStream,
    stopStream,
  };
}

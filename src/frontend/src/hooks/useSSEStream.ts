import { useCallback, useEffect, useRef, useState } from 'react';
import { getBackendInfo } from '@/api';
import { devLogger } from '@/app/utils/devLogger';

export interface SSERawEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface UseSSEStreamOptions {
  onRawEvent?: (event: SSERawEvent) => void;
  onComplete?: (data: Record<string, unknown>) => void;
  onError?: (error: string) => void;
}

export function useSSEStream(options: UseSSEStreamOptions = {}) {
  const { onRawEvent, onComplete, onError } = options;
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const startStream = useCallback(
    async (path: string, body: Record<string, unknown>) => {
      if (isStreaming) return;
      setIsStreaming(true);
      abortControllerRef.current = new AbortController();

      try {
        const backendInfo = await getBackendInfo();
        if (!backendInfo.baseUrl) {
          throw new Error('Backend baseUrl missing.');
        }

        const response = await fetch(`${backendInfo.baseUrl}${path}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(backendInfo.token
              ? { Authorization: `Bearer ${backendInfo.token}` }
              : {}),
          },
          body: JSON.stringify(body),
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
        let currentEvent: string | null = null;
        let currentData = '';
        let sawTerminalEvent = false;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split(/\r?\n/);
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7);
            } else if (line.startsWith('data: ')) {
              currentData = line.slice(6);
            } else if (line === '' && currentEvent) {
              try {
                const data = JSON.parse(currentData) as Record<string, unknown>;
                const evt: SSERawEvent = { type: currentEvent, data };
                onRawEvent?.(evt);

                if (currentEvent === 'complete') {
                  sawTerminalEvent = true;
                  onComplete?.(data);
                } else if (currentEvent === 'error') {
                  sawTerminalEvent = true;
                  onError?.((data.error as string) || 'Unknown error');
                }
              } catch (err) {
                devLogger.warn('[useSSEStream] SSE event parse error:', err);
              }

              currentEvent = null;
              currentData = '';
            }
          }
        }

        if (!sawTerminalEvent && !abortControllerRef.current?.signal.aborted) {
          onError?.('SSE stream ended without a terminal event');
        }
      } catch (error) {
        if (error instanceof Error && error.name !== 'AbortError') {
          onError?.(error.message);
        }
      } finally {
        setIsStreaming(false);
        abortControllerRef.current = null;
      }
    },
    [isStreaming, onRawEvent, onComplete, onError],
  );

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

  return { isStreaming, startStream, stopStream };
}

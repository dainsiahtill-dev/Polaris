import { useCallback, useEffect, useRef, useState } from 'react';
import { getBackendInfo } from '@/api';
import { devLogger } from '@/app/utils/devLogger';

export interface NDJSONEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface UseNDJSONStreamOptions {
  onEvent?: (event: NDJSONEvent) => void;
  onComplete?: (data: Record<string, unknown>) => void;
  onError?: (error: string) => void;
}

/**
 * Replacement for useSSEStream: reads NDJSON (newline-delimited JSON) instead
 * of SSE wire format.  Backend returns one JSON object per line terminated
 * by a final `{type:"complete",…}` or `{type:"error",…}` line.
 */
export function useNDJSONStream(options: UseNDJSONStreamOptions = {}) {
  const { onEvent, onComplete, onError } = options;
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

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split(/\r?\n/);
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;

            try {
              const parsed = JSON.parse(trimmed) as NDJSONEvent;
              onEvent?.(parsed);

              if (parsed.type === 'complete') {
                onComplete?.(parsed.data);
              } else if (parsed.type === 'error') {
                onError?.((parsed.data.error as string) || 'Unknown error');
              }
            } catch {
              devLogger.warn('[useNDJSONStream] Parse error for line:', trimmed.slice(0, 120));
            }
          }
        }

        // Process remaining buffer
        if (buffer.trim()) {
          try {
            const parsed = JSON.parse(buffer.trim()) as NDJSONEvent;
            onEvent?.(parsed);
            if (parsed.type === 'complete') onComplete?.(parsed.data);
            else if (parsed.type === 'error') onError?.((parsed.data.error as string) || 'Unknown error');
          } catch { /* ignore trailing partial */ }
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
    [isStreaming, onEvent, onComplete, onError],
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

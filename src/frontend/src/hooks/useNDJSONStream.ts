import { useCallback, useEffect, useRef, useState } from 'react';
import { getBackendInfo } from '@/api';
import { useConnectionState, useMessageHandler, useTransportActions } from '@/runtime/transport';

export interface NDJSONEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface UseNDJSONStreamOptions {
  onEvent?: (event: NDJSONEvent) => void;
  onComplete?: (data: Record<string, unknown>) => void;
  onError?: (error: string) => void;
}

interface JetstreamStartResponse {
  ok?: boolean;
  session_id?: string;
  channel?: string;
  subject?: string;
  transport?: string;
}

function streamNameFromPath(path: string): 'dialogue' | 'preview' {
  return path.includes('/preview/') ? 'preview' : 'dialogue';
}

function createSessionId(streamName: string): string {
  const cryptoId = globalThis.crypto?.randomUUID?.();
  if (cryptoId) return `docs-${streamName}-${cryptoId}`;
  return `docs-${streamName}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function eventFromRuntimeMessage(message: unknown, channel: string): NDJSONEvent | null {
  const raw = message as Record<string, unknown> | null;
  const envelope = raw?.type === 'EVENT' ? (raw.event as Record<string, unknown> | undefined) : raw;
  if (!envelope || envelope.channel !== channel) return null;
  const payload = envelope.payload as Record<string, unknown> | undefined;
  if (!payload) return null;
  const data = payload.data;
  return {
    type: String(payload.type || 'message'),
    data: data && typeof data === 'object' ? data as Record<string, unknown> : {},
  };
}

/**
 * Starts docs-init generation over Nats-JetStream and consumes the matching
 * runtime WebSocket channel. The public callback shape remains NDJSON-like
 * because DocsInitDialog already models events as `{ type, data }`.
 */
export function useNDJSONStream(options: UseNDJSONStreamOptions = {}) {
  const { onEvent, onComplete, onError } = options;
  const { connected } = useConnectionState();
  const { subscribeChannels, reconnect } = useTransportActions();
  const { registerMessageHandler } = useMessageHandler();
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const unregisterHandlerRef = useRef<(() => void) | null>(null);

  const cleanupRuntimeSubscription = useCallback(() => {
    unregisterHandlerRef.current?.();
    unregisterHandlerRef.current = null;
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }, []);

  const stopStream = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    cleanupRuntimeSubscription();
    setIsStreaming(false);
  }, [cleanupRuntimeSubscription]);

  const startStream = useCallback(
    async (path: string, body: Record<string, unknown>) => {
      if (isStreaming) return;

      const streamName = streamNameFromPath(path);
      const requestBody = { ...body };
      const requestedSessionId =
        typeof requestBody.session_id === 'string' && requestBody.session_id.trim()
          ? requestBody.session_id.trim()
          : createSessionId(streamName);
      requestBody.session_id = requestedSessionId;
      const channel = `docs-init-${streamName}:${requestedSessionId}`;

      cleanupRuntimeSubscription();
      abortControllerRef.current = new AbortController();
      setIsStreaming(true);

      const finish = () => {
        cleanupRuntimeSubscription();
        abortControllerRef.current = null;
        setIsStreaming(false);
      };

      unregisterHandlerRef.current = registerMessageHandler((message) => {
        const event = eventFromRuntimeMessage(message, channel);
        if (!event) return;
        onEvent?.(event);
        if (event.type === 'complete') {
          onComplete?.(event.data);
          finish();
        } else if (event.type === 'error') {
          onError?.((event.data.error as string) || 'Unknown error');
          finish();
        }
      }, channel);
      unsubscribeRef.current = subscribeChannels([{ channel, tailLines: 0 }]);
      if (!connected) {
        reconnect();
      }

      try {
        const backendInfo = await getBackendInfo();
        if (!backendInfo.baseUrl) {
          throw new Error('Backend baseUrl missing.');
        }

        const response = await fetch(`${backendInfo.baseUrl}${path}`, {
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

        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
          throw new Error(`Unexpected stream start response content-type: ${contentType || 'unknown'}`);
        }
        const started = await response.json() as JetstreamStartResponse;
        if (started.ok === false) {
          throw new Error('Docs init stream failed to start.');
        }
      } catch (error) {
        if (error instanceof Error && error.name !== 'AbortError') {
          onError?.(error.message);
        }
        finish();
      }
    },
    [
      cleanupRuntimeSubscription,
      isStreaming,
      onComplete,
      onError,
      onEvent,
      connected,
      reconnect,
      registerMessageHandler,
      subscribeChannels,
    ],
  );

  useEffect(() => stopStream, [stopStream]);

  return { isStreaming, startStream, stopStream };
}

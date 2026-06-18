/**
 * Chat Stream Hook (WebSocket / NAT-JetStream)
 *
 * 替代 useChatStream 的 SSE 版本：
 *   1. POST /v2/role/{role}/chat/jetstream 立即返回 session_id
 *   2. 通过 runtime transport 订阅 chat:<session_id> 通道
 *   3. 解析 RuntimeEventEnvelope.kind=chat.chunk 事件，更新对话 UI
 *   4. complete / error 时自动取消订阅
 *
 * Wire 协议：和 useChatStream 的 SSE 版本完全兼容（thinking_chunk /
 * content_chunk / tool_call / tool_result / fingerprint / complete / error），
 * 只是底层从 EventSource / ReadableStream 换成了 NAT-JetStream WebSocket。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/api';

function appendWorkspaceQuery(path: string, workspace?: string): string {
  if (!workspace) return path;
  const sep = path.includes('?') ? '&' : '?';
  return `${path}${sep}workspace=${encodeURIComponent(workspace)}`;
}
import { useRuntimeTransport } from '@/runtime/transport';
import type { ChatStreamMessage } from './useChatStream';

export interface UseChatStreamWSOptions {
  role: string;
  workspace?: string;
  onChunk?: (chunk: { type: string; data: Record<string, unknown> }) => void;
}

interface JetstreamChatStartResponse {
  session_id: string;
  status: string;
  channel: string;
  subject: string;
  transport: string;
}

export interface UseChatStreamWSReturn {
  start: (message: string) => Promise<{ ok: boolean; sessionId?: string; error?: string }>;
  cancel: () => void;
  isStreaming: boolean;
  sessionId: string | null;
  chunks: Array<{ type: string; data: Record<string, unknown>; ts: number }>;
}

export function useChatStreamWS(options: UseChatStreamWSOptions): UseChatStreamWSReturn {
  const { role, workspace, onChunk } = options;
  const transport = useRuntimeTransport();
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [chunks, setChunks] = useState<UseChatStreamWSReturn['chunks']>([]);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const messageHandlerUnregisterRef = useRef<(() => void) | null>(null);

  const cleanup = useCallback(() => {
    if (unsubscribeRef.current) {
      try { unsubscribeRef.current(); } catch { /* noop */ }
      unsubscribeRef.current = null;
    }
    if (messageHandlerUnregisterRef.current) {
      try { messageHandlerUnregisterRef.current(); } catch { /* noop */ }
      messageHandlerUnregisterRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  const start = useCallback(
    async (message: string) => {
      if (!message.trim()) return { ok: false, error: 'empty message' };
      // Ensure WS is connected before we POST (otherwise the chunk publish
      // arrives before the SUBSCRIBE and we miss the stream).
      if (!transport.connected) {
        return { ok: false, error: 'runtime transport not connected' };
      }

      // 1) POST returns immediately with session_id
      let response: Response;
      try {
        response = await apiFetch(
          appendWorkspaceQuery(`/v2/role/${role}/chat/jetstream`, workspace),
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, max_tokens: 256 }),
          },
        );
      } catch (err) {
        return { ok: false, error: err instanceof Error ? err.message : 'POST failed' };
      }

      if (!response.ok) {
        const text = await response.text();
        return { ok: false, error: `HTTP ${response.status}: ${text.slice(0, 200)}` };
      }
      const ct = response.headers.get('content-type') || '';
      if (ct.includes('text/event-stream')) {
        return {
          ok: false,
          error: 'server returned SSE; chat/jetstream must return application/json',
        };
      }
      const payload = (await response.json()) as JetstreamChatStartResponse;
      const sid = payload.session_id;
      const channel = payload.channel;
      if (!sid || !channel) {
        return { ok: false, error: 'missing session_id/channel in response' };
      }
      setSessionId(sid);
      setChunks([]);
      setIsStreaming(true);

      // 2) Subscribe to chat:<sid>
      const unsubscribe = transport.subscribeChannels([{ channel, tailLines: 0 }]);
      unsubscribeRef.current = unsubscribe;

      // 3) Register a one-shot message handler that filters by channel
      const handler = (raw: unknown) => {
        // The runtime transport wraps each inner envelope and dispatches
        // by channel. We accept any message and check the channel field
        // ourselves to be safe across v1/v2 message shapes.
        const msg = raw as Record<string, unknown>;
        const event = msg?.type === 'EVENT'
          ? msg.event as Record<string, unknown> | undefined
          : msg;
        if (!event) return;
        if (event.channel !== channel) return;
        const p = (event.payload as Record<string, unknown>) || {};
        const chunkType = String(p.type || 'message');
        const chunkData = (p.data as Record<string, unknown>) || {};
        setChunks((prev) => [...prev, { type: chunkType, data: chunkData, ts: Date.now() }]);
        onChunk?.({ type: chunkType, data: chunkData });
        if (chunkType === 'complete' || chunkType === 'error') {
          cleanup();
        }
      };
      messageHandlerUnregisterRef.current = transport.registerMessageHandler(handler);

      return { ok: true, sessionId: sid };
    },
    [role, workspace, transport, onChunk, cleanup],
  );

  const cancel = useCallback(() => {
    cleanup();
    setSessionId(null);
  }, [cleanup]);

  return { start, cancel, isStreaming, sessionId, chunks };
}

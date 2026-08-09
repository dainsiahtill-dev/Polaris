/**
 * Chat Stream Hook (WebSocket / Nats-JetStream)
 *
 *   1. POST /v2/role/{role}/chat/jetstream 立即返回 session_id
 *   2. 通过 runtime transport 订阅 chat:<session_id> 通道
 *   3. 解析 RuntimeEventEnvelope.kind=chat.chunk 事件，更新对话 UI
 *   4. complete / error 时自动取消订阅
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/api';
import { useConnectionState, useMessageHandler, useTransportActions } from '@/runtime/transport';
function appendWorkspaceQuery(path, workspace) {
    if (!workspace)
        return path;
    const sep = path.includes('?') ? '&' : '?';
    return `${path}${sep}workspace=${encodeURIComponent(workspace)}`;
}
export function useChatStreamWS(options) {
    const { role, workspace, onChunk } = options;
    const { connected } = useConnectionState();
    const { subscribeChannels } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    const [isStreaming, setIsStreaming] = useState(false);
    const [sessionId, setSessionId] = useState(null);
    const [chunks, setChunks] = useState([]);
    const unsubscribeRef = useRef(null);
    const messageHandlerUnregisterRef = useRef(null);
    const cleanup = useCallback(() => {
        if (unsubscribeRef.current) {
            try {
                unsubscribeRef.current();
            }
            catch { /* noop */ }
            unsubscribeRef.current = null;
        }
        if (messageHandlerUnregisterRef.current) {
            try {
                messageHandlerUnregisterRef.current();
            }
            catch { /* noop */ }
            messageHandlerUnregisterRef.current = null;
        }
        setIsStreaming(false);
    }, []);
    useEffect(() => () => cleanup(), [cleanup]);
    const start = useCallback(async (message) => {
        if (!message.trim())
            return { ok: false, error: 'empty message' };
        // Ensure WS is connected before we POST (otherwise the chunk publish
        // arrives before the SUBSCRIBE and we miss the stream).
        if (!connected) {
            return { ok: false, error: 'runtime transport not connected' };
        }
        // 1) POST returns immediately with session_id
        let response;
        try {
            response = await apiFetch(appendWorkspaceQuery(`/v2/role/${role}/chat/jetstream`, workspace), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message, max_tokens: 256 }),
            });
        }
        catch (err) {
            return { ok: false, error: err instanceof Error ? err.message : 'POST failed' };
        }
        if (!response.ok) {
            const text = await response.text();
            return { ok: false, error: `HTTP ${response.status}: ${text.slice(0, 200)}` };
        }
        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
            return {
                ok: false,
                error: `unexpected chat start response content-type: ${contentType || 'unknown'}`,
            };
        }
        const payload = (await response.json());
        const sid = payload.session_id;
        const channel = payload.channel;
        if (!sid || !channel) {
            return { ok: false, error: 'missing session_id/channel in response' };
        }
        setSessionId(sid);
        setChunks([]);
        setIsStreaming(true);
        // 2) Subscribe to chat:<sid>
        const unsubscribe = subscribeChannels([{ channel, tailLines: 0 }]);
        unsubscribeRef.current = unsubscribe;
        // 3) Register a one-shot message handler that filters by channel
        const handler = (raw) => {
            // The runtime transport wraps each inner envelope and dispatches
            // by channel. We accept any message and check the channel field
            // ourselves to be safe across v1/v2 message shapes.
            const msg = raw;
            const event = msg?.type === 'EVENT'
                ? msg.event
                : msg;
            if (!event)
                return;
            if (event.channel !== channel)
                return;
            const p = event.payload || {};
            const chunkType = String(p.type || 'message');
            const chunkData = p.data || {};
            setChunks((prev) => [...prev, { type: chunkType, data: chunkData, ts: Date.now() }]);
            onChunk?.({ type: chunkType, data: chunkData });
            if (chunkType === 'complete' || chunkType === 'error') {
                cleanup();
            }
        };
        messageHandlerUnregisterRef.current = registerMessageHandler(handler);
        return { ok: true, sessionId: sid };
    }, [role, workspace, connected, subscribeChannels, registerMessageHandler, onChunk, cleanup]);
    const cancel = useCallback(() => {
        cleanup();
        setSessionId(null);
    }, [cleanup]);
    return { start, cancel, isStreaming, sessionId, chunks };
}

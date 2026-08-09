/**
 * RuntimeSocketManager - Singleton WebSocket Manager for Runtime Domain
 *
 * This module provides a single WebSocket connection owner for all runtime
 * communication, with:
 * - Channel subscription aggregation (ref-count based)
 * - Message routing to registered listeners
 * - Exponential backoff reconnection
 * - Connection state management
 *
 * Architecture:
 * - Singleton pattern ensures only ONE WebSocket connection exists
 * - Channel ref-counting: multiple consumers can subscribe to same channel
 * - Message router: routes messages to listeners based on channel/type
 * - Provider pattern: React context exposes state to components
 */
import { connectWebSocket } from "@/api";
import { devLogger } from "@/app/utils/devLogger";
// ============================================================================
// Configuration
// ============================================================================
const CONFIG = {
    maxRetries: Infinity,
    baseDelay: 1000,
    maxDelay: 30000,
    jitterMax: 500,
    // v2 protocol settings
    batchAckInterval: 500,
    batchAckThreshold: 20,
    // Heartbeat settings
    pingIntervalMs: 30000,
    pongTimeoutMs: 10000, // if no PONG within 10s, reconnect
};
const POLICY_VIOLATION_CLOSE_CODE = 1008;
const RUNTIME_OBSERVABLE_ROLES = [
    "pm",
    "architect",
    "chief_engineer",
    "director",
    "qa",
    "scout",
    "resident_agi",
];
const RUNTIME_OBSERVABLE_ROLE_SET = new Set(RUNTIME_OBSERVABLE_ROLES);
function isRuntimeRole(value) {
    return RUNTIME_OBSERVABLE_ROLE_SET.has(value);
}
// ============================================================================
// RuntimeSocketManager Singleton
// ============================================================================
class RuntimeSocketManager {
    // Private constructor for singleton
    constructor() {
        // WebSocket instance
        this.ws = null;
        this.connectInFlight = false;
        // Connection state
        this.state = {
            connected: false,
            reconnecting: false,
            error: null,
            attemptCount: 0,
        };
        // Reconnection
        this.reconnectTimer = null;
        this.closed = false;
        // Heartbeat
        this.pingTimer = null;
        this.pongTimer = null;
        // v2 protocol state
        this.lastCursor = 0;
        this.pendingAckCursors = [];
        this.batchAckTimer = null;
        this.protocolActivated = false;
        this.subscribedChannels = [];
        this.subscribedRoles = [];
        this.hasExplicitRoleFilter = false;
        // Channel subscriptions (ref-count based)
        this.channels = new Map(); // channel -> ref count
        this.channelTailLines = new Map(); // channel -> tail lines
        // Listeners
        this.messageListeners = new Map();
        this.stateListeners = new Set();
    }
    static getInstance() {
        if (!RuntimeSocketManager.instance) {
            RuntimeSocketManager.instance = new RuntimeSocketManager();
        }
        return RuntimeSocketManager.instance;
    }
    static destroy() {
        if (RuntimeSocketManager.instance) {
            RuntimeSocketManager.instance.close();
            RuntimeSocketManager.instance = null;
        }
    }
    // ==========================================================================
    // Public API
    // ==========================================================================
    /**
     * Start the connection (idempotent)
     */
    start() {
        if (this.closed) {
            this.closed = false;
        }
        if (!this.ws && !this.reconnectTimer) {
            this.connect();
        }
    }
    /**
     * Permanently close the connection
     */
    close() {
        this.closed = true;
        this.connectInFlight = false;
        this.clearReconnectTimer();
        this.clearBatchAckTimer();
        this.clearHeartbeat();
        this.ws?.close();
        this.ws = null;
        this.updateState({
            connected: false,
            reconnecting: false,
            error: null,
            attemptCount: 0,
        });
    }
    clearBatchAckTimer() {
        if (this.batchAckTimer) {
            clearTimeout(this.batchAckTimer);
            this.batchAckTimer = null;
        }
        this.pendingAckCursors = [];
    }
    // ==========================================================================
    // Heartbeat
    // ==========================================================================
    clearHeartbeat() {
        if (this.pingTimer) {
            clearInterval(this.pingTimer);
            this.pingTimer = null;
        }
        if (this.pongTimer) {
            clearTimeout(this.pongTimer);
            this.pongTimer = null;
        }
    }
    startHeartbeat() {
        this.clearHeartbeat();
        this.pingTimer = setInterval(() => this.sendPing(), CONFIG.pingIntervalMs);
    }
    sendPing() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN)
            return;
        this.ws.send(JSON.stringify({ type: "PING", protocol: "runtime.v2" }));
        // If no PONG within timeout, consider connection dead
        this.pongTimer = setTimeout(() => {
            this.pongTimer = null;
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.close();
                // onclose handler will trigger reconnect
            }
        }, CONFIG.pongTimeoutMs);
    }
    handlePong() {
        if (this.pongTimer) {
            clearTimeout(this.pongTimer);
            this.pongTimer = null;
        }
    }
    /**
     * Get current cursor position for reconnection
     */
    getLastCursor() {
        return this.lastCursor;
    }
    /**
     * Subscribe to channels (ref-counted)
     */
    subscribeChannels(subscriptions, roles) {
        let needsResubscribe = false;
        let rolesChanged = false;
        if (roles !== undefined) {
            const normalizedRoles = Array.from(new Set(roles));
            rolesChanged =
                !this.hasExplicitRoleFilter ||
                    !this.areRolesEqual(this.subscribedRoles, normalizedRoles);
            this.subscribedRoles = normalizedRoles;
            this.hasExplicitRoleFilter = true;
        }
        for (const { channel, tailLines = 0 } of subscriptions) {
            const currentCount = this.channels.get(channel) || 0;
            this.channels.set(channel, currentCount + 1);
            // Track max tail lines requested
            const currentTail = this.channelTailLines.get(channel) || 0;
            if (tailLines > currentTail) {
                this.channelTailLines.set(channel, tailLines);
            }
            // If this is a new channel, we need to resubscribe
            if (currentCount === 0) {
                needsResubscribe = true;
            }
        }
        if ((needsResubscribe || rolesChanged) && this.state.connected) {
            this.sendSubscribe();
        }
    }
    /**
     * Unsubscribe from channels (ref-counted)
     */
    unsubscribeChannels(channels) {
        for (const channel of channels) {
            const currentCount = this.channels.get(channel) || 0;
            if (currentCount <= 1) {
                this.channels.delete(channel);
                this.channelTailLines.delete(channel);
            }
            else {
                this.channels.set(channel, currentCount - 1);
            }
        }
    }
    /**
     * Send a command/message through the WebSocket
     */
    send(data) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            if (typeof data !== "string" &&
                data &&
                typeof data === "object" &&
                !Array.isArray(data)) {
                const payload = data;
                const msgType = String(payload.type || "")
                    .trim()
                    .toUpperCase();
                const protocol = String(payload.protocol || "")
                    .trim()
                    .toLowerCase();
                if (msgType === "SUBSCRIBE" && protocol === "runtime.v2") {
                    const channels = Array.isArray(payload.channels)
                        ? payload.channels
                            .map((value) => String(value || "").trim())
                            .filter((value) => value.length > 0)
                        : [];
                    if (channels.length > 0) {
                        this.subscribedChannels = channels;
                    }
                    if (Array.isArray(payload.roles)) {
                        const roles = payload.roles
                            .map((value) => String(value || "").trim())
                            .filter((value) => value.length > 0)
                            .filter((value) => isRuntimeRole(value));
                        this.subscribedRoles = Array.from(new Set(roles));
                        this.hasExplicitRoleFilter = true;
                    }
                }
            }
            this.ws.send(typeof data === "string" ? data : JSON.stringify(data));
            return true;
        }
        return false;
    }
    /**
     * Register a message listener
     */
    registerMessageListener(listener) {
        this.messageListeners.set(listener.id, listener);
        return () => {
            this.messageListeners.delete(listener.id);
        };
    }
    /**
     * Register a connection state listener
     */
    registerStateListener(listener) {
        this.stateListeners.add(listener);
        // Immediately notify current state
        listener(this.getState());
        return () => {
            this.stateListeners.delete(listener);
        };
    }
    /**
     * Get current connection state
     */
    getState() {
        return { ...this.state };
    }
    /**
     * Force reconnect
     */
    reconnect() {
        this.ws?.close();
        // Reconnect will be triggered by onclose handler
    }
    // ==========================================================================
    // Private Methods
    // ==========================================================================
    startBatchAckTimer() {
        if (this.batchAckTimer)
            return;
        this.batchAckTimer = setTimeout(() => {
            this.batchAckTimer = null;
            this.flushPendingAcks();
        }, CONFIG.batchAckInterval);
    }
    flushPendingAcks() {
        if (this.pendingAckCursors.length === 0)
            return;
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN)
            return;
        const maxCursor = Math.max(...this.pendingAckCursors);
        // Clear pending acks AFTER calculating maxCursor
        const cursorsToAck = [...this.pendingAckCursors];
        this.pendingAckCursors = [];
        if (maxCursor > this.lastCursor) {
            this.lastCursor = maxCursor;
            this.ws.send(JSON.stringify({
                type: "ACK",
                protocol: "runtime.v2",
                cursor: this.lastCursor,
            }));
        }
    }
    queueAck(cursor) {
        if (cursor <= this.lastCursor)
            return;
        this.pendingAckCursors.push(cursor);
        if (this.pendingAckCursors.length >= CONFIG.batchAckThreshold) {
            if (this.batchAckTimer) {
                clearTimeout(this.batchAckTimer);
                this.batchAckTimer = null;
            }
            this.flushPendingAcks();
        }
        else if (!this.batchAckTimer) {
            this.startBatchAckTimer();
        }
    }
    processV2Event(eventData) {
        // Update cursor from v2 event
        if (eventData.cursor) {
            this.queueAck(eventData.cursor);
        }
        // Route the canonical runtime.v2 EVENT wrapper to listeners. Downstream
        // hooks normalize the envelope from `event`, while preserving cursor/ACK
        // handling here in the transport layer.
        this.dispatchMessageToListeners(eventData);
    }
    connect() {
        if (this.connectInFlight) {
            return;
        }
        if (this.ws?.readyState === WebSocket.OPEN ||
            this.ws?.readyState === WebSocket.CONNECTING) {
            return;
        }
        this.connectInFlight = true;
        this.updateState({ reconnecting: true, error: null });
        connectWebSocket(this.state.attemptCount > 0).then((socket) => {
            this.connectInFlight = false;
            if (this.closed) {
                socket.close();
                return;
            }
            if (this.ws &&
                this.ws !== socket &&
                this.ws.readyState !== WebSocket.CLOSED) {
                socket.close();
                return;
            }
            this.ws = socket;
            this.setupSocketHandlers(socket);
        }, (error) => {
            this.connectInFlight = false;
            this.updateState({
                reconnecting: false,
                error: error instanceof Error ? error.message : "Failed to connect",
            });
            this.scheduleReconnect();
        });
    }
    handleSocketOpen(socket) {
        if (this.ws !== socket || this.state.connected) {
            return;
        }
        this.updateState({
            connected: true,
            reconnecting: false,
            error: null,
            attemptCount: 0,
        });
        this.startHeartbeat();
        this.sendSubscribe();
    }
    setupSocketHandlers(socket) {
        socket.onopen = () => {
            this.handleSocketOpen(socket);
        };
        socket.onmessage = (event) => {
            this.routeMessage(event.data);
        };
        socket.onclose = (event) => {
            this.clearHeartbeat();
            this.ws = null;
            this.updateState({ connected: false, reconnecting: false });
            if (this.closed)
                return;
            if (event.code === 1000 || event.code === 1001)
                return; // Normal close
            // 1008 means the server rejected this page's authentication or
            // workspace/instance binding. Retrying the same immutable URL binding
            // cannot recover. It only turns a stale Launcher tab plus reused ports
            // into an unbounded handshake and audit-write storm against a different
            // isolated instance. Stop here; an explicit start (normally page reload
            // after correcting the binding) may try again.
            if (event.code === POLICY_VIOLATION_CLOSE_CODE) {
                this.closed = true;
                this.clearReconnectTimer();
                this.updateState({
                    reconnecting: false,
                    error: "Runtime connection rejected by instance policy (1008)",
                });
                return;
            }
            this.scheduleReconnect();
        };
        socket.onerror = () => {
            socket.close();
        };
        if (socket.readyState === WebSocket.OPEN) {
            this.handleSocketOpen(socket);
        }
    }
    routeMessage(data) {
        let message;
        try {
            message = JSON.parse(data);
        }
        catch {
            message = { type: "raw", data };
        }
        // Handle v2 protocol EVENT message
        const msg = message;
        if (msg.type === "EVENT" && msg.protocol === "runtime.v2" && msg.event) {
            this.processV2Event({
                type: "EVENT",
                protocol: "runtime.v2",
                cursor: typeof msg.cursor === "number" ? msg.cursor : 0,
                event: msg.event,
            });
            return;
        }
        // Handle RESYNC_REQUIRED - reset cursor
        if (msg.type === "RESYNC_REQUIRED" && msg.protocol === "runtime.v2") {
            this.lastCursor = typeof msg.cursor === "number" ? msg.cursor : 0;
            return;
        }
        // Handle PONG - heartbeat response
        if (msg.type === "PONG") {
            this.handlePong();
            return;
        }
        this.dispatchMessageToListeners(message);
    }
    dispatchMessageToListeners(message) {
        const msg = message;
        // Get channel from message
        const channel = typeof msg.channel === "string" ? msg.channel : undefined;
        // Route to all listeners
        for (const listener of this.messageListeners.values()) {
            try {
                // If listener has specific channel filter, only route matching messages
                if (listener.channel && channel && listener.channel !== channel) {
                    continue;
                }
                listener.handler(message);
            }
            catch (error) {
                devLogger.error(`[RuntimeSocketManager] Listener ${listener.id} error:`, error);
            }
        }
    }
    sendSubscribe() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN)
            return;
        const channelList = Array.from(this.channels.keys());
        if (channelList.length === 0)
            return;
        // Calculate max tail lines across all subscriptions
        const maxTailLines = Math.max(...Array.from(this.channelTailLines.values()), 0);
        // Store subscribed channels for reconnection
        this.subscribedChannels = channelList;
        // Send v2 protocol subscription
        const payload = {
            type: "SUBSCRIBE",
            protocol: "runtime.v2",
            channels: channelList,
            tail: maxTailLines,
            cursor: this.lastCursor,
        };
        if (this.hasExplicitRoleFilter) {
            payload.roles = this.subscribedRoles;
        }
        this.ws.send(JSON.stringify(payload));
    }
    areRolesEqual(left, right) {
        if (left.length !== right.length) {
            return false;
        }
        for (const role of left) {
            if (!right.includes(role)) {
                return false;
            }
        }
        return true;
    }
    scheduleReconnect() {
        if (this.closed)
            return;
        if (this.state.attemptCount >= CONFIG.maxRetries) {
            this.updateState({
                reconnecting: false,
                error: "Max reconnection attempts reached",
            });
            return;
        }
        // Guard: clear existing timer to prevent double-schedule from onerror→onclose
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        const attempt = this.state.attemptCount + 1;
        this.updateState({ attemptCount: attempt, reconnecting: true });
        const jitter = Math.random() * CONFIG.jitterMax;
        const delay = Math.min(CONFIG.baseDelay * 2 ** (attempt - 1), CONFIG.maxDelay) + jitter;
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.connect();
        }, delay);
    }
    clearReconnectTimer() {
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
    }
    updateState(partial) {
        this.state = { ...this.state, ...partial };
        const stateCopy = this.getState();
        for (const listener of this.stateListeners) {
            try {
                listener(stateCopy);
            }
            catch (error) {
                devLogger.error("[RuntimeSocketManager] State listener error:", error);
            }
        }
    }
}
RuntimeSocketManager.instance = null;
// ============================================================================
// Export singleton instance
// ============================================================================
export const runtimeSocketManager = RuntimeSocketManager.getInstance();
// Types re-export for convenience
export { RUNTIME_OBSERVABLE_ROLES };

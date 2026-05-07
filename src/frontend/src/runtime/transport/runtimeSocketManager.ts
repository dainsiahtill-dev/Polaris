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

import { connectWebSocket } from '@/api';
import { devLogger } from '@/app/utils/devLogger';

// ============================================================================
// Configuration
// ============================================================================

const CONFIG = {
  maxRetries: Infinity,
  baseDelay: 1000,
  maxDelay: 30000,
  jitterMax: 500,
  // v2 protocol settings
  batchAckInterval: 500, // ms
  batchAckThreshold: 20,
  // Heartbeat settings
  pingIntervalMs: 30000, // send PING every 30s
  pongTimeoutMs: 10000,  // if no PONG within 10s, reconnect
} as const;

// ============================================================================
// Types
// ============================================================================

export interface ChannelSubscription {
  channel: string;
  tailLines?: number;
}

export interface MessageListener {
  id: string;
  channel?: string;
  handler: (message: unknown) => void;
}

export interface ConnectionState {
  connected: boolean;
  reconnecting: boolean;
  error: string | null;
  attemptCount: number;
}

export type ConnectionStateListener = (state: ConnectionState) => void;

// v2 protocol types
interface V2EventMessage {
  type: 'EVENT';
  protocol: 'runtime.v2';
  cursor: number;
  event: Record<string, unknown>;
}

type RuntimeRole = 'pm' | 'chief_engineer' | 'director' | 'qa';

// ============================================================================
// RuntimeSocketManager Singleton
// ============================================================================

class RuntimeSocketManager {
  private static instance: RuntimeSocketManager | null = null;

  // WebSocket instance
  private ws: WebSocket | null = null;

  // Connection state
  private state: ConnectionState = {
    connected: false,
    reconnecting: false,
    error: null,
    attemptCount: 0,
  };

  // Reconnection
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;

  // Heartbeat
  private pingTimer: ReturnType<typeof setTimeout> | null = null;
  private pongTimer: ReturnType<typeof setTimeout> | null = null;

  // v2 protocol state
  private lastCursor = 0;
  private pendingAckCursors: number[] = [];
  private batchAckTimer: ReturnType<typeof setTimeout> | null = null;
  private protocolActivated = false;
  private subscribedChannels: string[] = [];
  private subscribedRoles: RuntimeRole[] = [];
  private hasExplicitRoleFilter = false;

  // Channel subscriptions (ref-count based)
  private channels = new Map<string, number>(); // channel -> ref count
  private channelTailLines = new Map<string, number>(); // channel -> tail lines

  // Listeners
  private messageListeners = new Map<string, MessageListener>();
  private stateListeners = new Set<ConnectionStateListener>();

  // Private constructor for singleton
  private constructor() {}

  static getInstance(): RuntimeSocketManager {
    if (!RuntimeSocketManager.instance) {
      RuntimeSocketManager.instance = new RuntimeSocketManager();
    }
    return RuntimeSocketManager.instance;
  }

  static destroy(): void {
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
  start(): void {
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
  close(): void {
    this.closed = true;
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

  private clearBatchAckTimer(): void {
    if (this.batchAckTimer) {
      clearTimeout(this.batchAckTimer);
      this.batchAckTimer = null;
    }
    this.pendingAckCursors = [];
  }

  // ==========================================================================
  // Heartbeat
  // ==========================================================================

  private clearHeartbeat(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    if (this.pongTimer) {
      clearTimeout(this.pongTimer);
      this.pongTimer = null;
    }
  }

  private startHeartbeat(): void {
    this.clearHeartbeat();
    this.pingTimer = setInterval(() => this.sendPing(), CONFIG.pingIntervalMs);
  }

  private sendPing(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    this.ws.send(JSON.stringify({ type: 'PING', protocol: 'runtime.v2' }));

    // If no PONG within timeout, consider connection dead
    this.pongTimer = setTimeout(() => {
      this.pongTimer = null;
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.close();
        // onclose handler will trigger reconnect
      }
    }, CONFIG.pongTimeoutMs);
  }

  private handlePong(): void {
    if (this.pongTimer) {
      clearTimeout(this.pongTimer);
      this.pongTimer = null;
    }
  }

  /**
   * Get current cursor position for reconnection
   */
  getLastCursor(): number {
    return this.lastCursor;
  }

  /**
   * Subscribe to channels (ref-counted)
   */
  subscribeChannels(subscriptions: ChannelSubscription[], roles?: RuntimeRole[]): void {
    let needsResubscribe = false;
    let rolesChanged = false;

    if (roles !== undefined) {
      const normalizedRoles = Array.from(new Set(roles));
      rolesChanged =
        !this.hasExplicitRoleFilter || !this.areRolesEqual(this.subscribedRoles, normalizedRoles);
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
  unsubscribeChannels(channels: string[]): void {
    const removedChannels: string[] = [];

    for (const channel of channels) {
      const currentCount = this.channels.get(channel) || 0;
      if (currentCount <= 1) {
        if (currentCount > 0) {
          removedChannels.push(channel);
        }
        this.channels.delete(channel);
        this.channelTailLines.delete(channel);
      } else {
        this.channels.set(channel, currentCount - 1);
      }
    }

    if (removedChannels.length > 0) {
      this.sendUnsubscribe(removedChannels);
    }
  }

  /**
   * Send a command/message through the WebSocket
   */
  send(data: unknown): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      if (typeof data !== 'string' && data && typeof data === 'object' && !Array.isArray(data)) {
        const payload = data as Record<string, unknown>;
        const msgType = String(payload.type || '').trim().toUpperCase();
        const protocol = String(payload.protocol || '').trim().toLowerCase();
        if (msgType === 'SUBSCRIBE' && protocol === 'runtime.v2') {
          const channels = Array.isArray(payload.channels)
            ? payload.channels
                .map((value) => String(value || '').trim())
                .filter((value): value is string => value.length > 0)
            : [];
          if (channels.length > 0) {
            this.subscribedChannels = channels;
          }
          if (Array.isArray(payload.roles)) {
            const roles = payload.roles
              .map((value) => String(value || '').trim())
              .filter((value): value is string => value.length > 0)
              .filter(
                (value): value is RuntimeRole =>
                  value === 'pm' || value === 'chief_engineer' || value === 'director' || value === 'qa'
              );
            this.subscribedRoles = Array.from(new Set(roles));
            this.hasExplicitRoleFilter = true;
          }
        }
      }
      this.ws.send(typeof data === 'string' ? data : JSON.stringify(data));
      return true;
    }
    return false;
  }

  /**
   * Register a message listener
   */
  registerMessageListener(listener: MessageListener): () => void {
    this.messageListeners.set(listener.id, listener);
    return () => {
      this.messageListeners.delete(listener.id);
    };
  }

  /**
   * Register a connection state listener
   */
  registerStateListener(listener: ConnectionStateListener): () => void {
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
  getState(): ConnectionState {
    return { ...this.state };
  }

  /**
   * Force reconnect
   */
  reconnect(): void {
    this.ws?.close();
    // Reconnect will be triggered by onclose handler
  }

  // ==========================================================================
  // Private Methods
  // ==========================================================================

  private startBatchAckTimer(): void {
    if (this.batchAckTimer) return;
    this.batchAckTimer = setTimeout(() => {
      this.batchAckTimer = null;
      this.flushPendingAcks();
    }, CONFIG.batchAckInterval);
  }

  private flushPendingAcks(): void {
    if (this.pendingAckCursors.length === 0) return;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    const maxCursor = Math.max(...this.pendingAckCursors);
    // Clear pending acks AFTER calculating maxCursor
    const cursorsToAck = [...this.pendingAckCursors];
    this.pendingAckCursors = [];

    if (maxCursor > this.lastCursor) {
      this.lastCursor = maxCursor;
      this.ws.send(
        JSON.stringify({
          type: 'ACK',
          protocol: 'runtime.v2',
          cursor: this.lastCursor,
        })
      );
    }
  }

  private queueAck(cursor: number): void {
    if (cursor <= this.lastCursor) return;
    this.pendingAckCursors.push(cursor);

    if (this.pendingAckCursors.length >= CONFIG.batchAckThreshold) {
      if (this.batchAckTimer) {
        clearTimeout(this.batchAckTimer);
        this.batchAckTimer = null;
      }
      this.flushPendingAcks();
    } else if (!this.batchAckTimer) {
      this.startBatchAckTimer();
    }
  }

  private processV2Event(eventData: V2EventMessage): void {
    // Update cursor from v2 event
    if (eventData.cursor) {
      this.queueAck(eventData.cursor);
    }

    // Route to listeners
    this.routeMessage(JSON.stringify(eventData.event));
  }

  private connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      return;
    }

    this.updateState({ reconnecting: true, error: null });

    connectWebSocket(this.state.attemptCount > 0).then(
      (socket) => {
        if (this.closed) {
          socket.close();
          return;
        }

        this.ws = socket;
        this.setupSocketHandlers(socket);
      },
      (error) => {
        this.updateState({
          reconnecting: false,
          error: error instanceof Error ? error.message : 'Failed to connect',
        });
        this.scheduleReconnect();
      }
    );
  }

  private setupSocketHandlers(socket: WebSocket): void {
    socket.onopen = () => {
      this.updateState({
        connected: true,
        reconnecting: false,
        error: null,
        attemptCount: 0,
      });
      this.startHeartbeat();
      this.sendSubscribe();
    };

    socket.onmessage = (event) => {
      this.routeMessage(event.data);
    };

    socket.onclose = (event) => {
      this.clearHeartbeat();
      this.ws = null;
      this.updateState({ connected: false, reconnecting: false });

      if (this.closed) return;
      if (event.code === 1000 || event.code === 1001) return; // Normal close

      this.scheduleReconnect();
    };

    socket.onerror = () => {
      socket.close();
    };
  }

  private routeMessage(data: string): void {
    let message: unknown;
    try {
      message = JSON.parse(data);
    } catch {
      message = { type: 'raw', data };
    }

    // Handle v2 protocol EVENT message
    const msg = message as Record<string, unknown>;
    if (msg.type === 'EVENT' && msg.protocol === 'runtime.v2' && msg.event) {
      this.processV2Event({
        type: 'EVENT',
        protocol: 'runtime.v2',
        cursor: typeof msg.cursor === 'number' ? msg.cursor : 0,
        event: msg.event as Record<string, unknown>,
      });
      return;
    }

    // Handle RESYNC_REQUIRED - reset cursor
    if (msg.type === 'RESYNC_REQUIRED' && msg.protocol === 'runtime.v2') {
      this.lastCursor = typeof msg.cursor === 'number' ? msg.cursor : 0;
      return;
    }

    // Handle PONG - heartbeat response
    if (msg.type === 'PONG') {
      this.handlePong();
      return;
    }

    // Get channel from message
    const channel = typeof msg.channel === 'string' ? msg.channel : undefined;

    // Route to all listeners
    for (const listener of this.messageListeners.values()) {
      try {
        // If listener has specific channel filter, only route matching messages
        if (listener.channel && channel && listener.channel !== channel) {
          continue;
        }
        listener.handler(message);
      } catch (error) {
        devLogger.error(`[RuntimeSocketManager] Listener ${listener.id} error:`, error);
      }
    }
  }

  private sendSubscribe(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    const channelList = Array.from(this.channels.keys());
    if (channelList.length === 0) return;

    // Calculate max tail lines across all subscriptions
    const maxTailLines = Math.max(...Array.from(this.channelTailLines.values()), 0);

    // Store subscribed channels for reconnection
    this.subscribedChannels = channelList;

    // Send v2 protocol subscription
    const payload: Record<string, unknown> = {
      type: 'SUBSCRIBE',
      protocol: 'runtime.v2',
      channels: channelList,
      tail: maxTailLines,
      cursor: this.lastCursor,
    };
    if (this.hasExplicitRoleFilter) {
      payload.roles = this.subscribedRoles;
    }
    this.ws.send(JSON.stringify(payload));
  }

  private sendUnsubscribe(channels: string[]): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    this.ws.send(
      JSON.stringify({
        type: 'UNSUBSCRIBE',
        protocol: 'runtime.v2',
        channels,
      })
    );
  }

  private areRolesEqual(left: RuntimeRole[], right: RuntimeRole[]): boolean {
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

  private scheduleReconnect(): void {
    if (this.closed) return;
    if (this.state.attemptCount >= CONFIG.maxRetries) {
      this.updateState({
        reconnecting: false,
        error: 'Max reconnection attempts reached',
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

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private updateState(partial: Partial<ConnectionState>): void {
    this.state = { ...this.state, ...partial };
    const stateCopy = this.getState();
    for (const listener of this.stateListeners) {
      try {
        listener(stateCopy);
      } catch (error) {
        devLogger.error('[RuntimeSocketManager] State listener error:', error);
      }
    }
  }
}

// ============================================================================
// Export singleton instance
// ============================================================================

export const runtimeSocketManager = RuntimeSocketManager.getInstance();

// Types re-export for convenience
export type { RuntimeRole, RuntimeSocketManager };

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

  // v2 protocol state
  private lastCursor = 0;
  private pendingAckCursors: number[] = [];
  private batchAckTimer: ReturnType<typeof setTimeout> | null = null;
  private protocolActivated = false;
  private subscribedChannels: string[] = [];

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

  /**
   * Get current cursor position for reconnection
   */
  getLastCursor(): number {
    return this.lastCursor;
  }

  /**
   * Subscribe to channels (ref-counted)
   */
  subscribeChannels(subscriptions: ChannelSubscription[]): void {
    let needsResubscribe = false;

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

    if (needsResubscribe && this.state.connected) {
      this.sendSubscribe();
    }
  }

  /**
   * Unsubscribe from channels (ref-counted)
   */
  unsubscribeChannels(channels: string[]): void {
    for (const channel of channels) {
      const currentCount = this.channels.get(channel) || 0;
      if (currentCount <= 1) {
        this.channels.delete(channel);
        this.channelTailLines.delete(channel);
      } else {
        this.channels.set(channel, currentCount - 1);
      }
    }

    // Note: We don't send unsubscribe to server - we just stop listening
    // Server can handle client disconnect naturally
  }

  /**
   * Send a command/message through the WebSocket
   */
  send(data: unknown): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
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
      this.sendSubscribe();
    };

    socket.onmessage = (event) => {
      this.routeMessage(event.data);
    };

    socket.onclose = (event) => {
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
    this.ws.send(
      JSON.stringify({
        type: 'SUBSCRIBE',
        protocol: 'runtime.v2',
        channels: channelList,
        tail: maxTailLines,
        cursor: this.lastCursor,
      })
    );
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
export type { RuntimeSocketManager };

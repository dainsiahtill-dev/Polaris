/**
 * useWebSocketWithFallback Hook Tests
 *
 * 测试 WebSocket-only Hook 的核心功能：
 * - 状态转换
 * - 订阅管理
 * - 消息发送
 */

import { renderHook, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useWebSocketWithFallback } from '../useWebSocketWithFallback';

// Mock fetch
const mockFetch = vi.fn();

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  binaryType: BinaryType = 'blob';
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  static instances: MockWebSocket[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send = vi.fn();

  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
  });
}

const originalFetch = global.fetch;
const originalWebSocket = global.WebSocket;

describe('useWebSocketWithFallback', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockWebSocket.instances = [];
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ type: 'legacy_poll_removed', data: 'test' }),
    });
    global.fetch = mockFetch as unknown as typeof fetch;
    global.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    global.WebSocket = originalWebSocket;
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  describe('Initial State', () => {
    it('should start in disconnected state when no URL provided', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      expect(result.current.connectionState).toBe('disconnected');
      expect(result.current.isConnected).toBe(false);
      expect(result.current.isWebSocketConnected).toBe(false);
      expect(result.current.isFallbackActive).toBe(false);
      expect(result.current.reconnectAttempt).toBe(0);
      expect(result.current.fallbackAttempt).toBe(0);
      expect(result.current.error).toBeNull();
    });

    it('should have correct initial connection states', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      // Verify all state flags are correctly initialized
      expect(result.current.connectionState).toBe('disconnected');
      expect(result.current.isConnected).toBe(false);
      expect(result.current.isWebSocketConnected).toBe(false);
      expect(result.current.isFallbackActive).toBe(false);
      expect(result.current.reconnectAttempt).toBe(0);
      expect(result.current.fallbackAttempt).toBe(0);
    });
  });

  describe('Manual Controls', () => {
    it('should allow manual disconnect', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      expect(result.current.connectionState).toBe('disconnected');

      act(() => {
        result.current.disconnect();
      });

      expect(result.current.connectionState).toBe('disconnected');
      expect(result.current.isConnected).toBe(false);
    });

    it('should allow manual reconnect to start connecting state', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({
          url: 'ws://localhost:8080',
          autoConnect: false,
        })
      );

      // Initially disconnected without autoConnect
      expect(result.current.connectionState).toBe('disconnected');

      // Manual reconnect should attempt connection
      act(() => {
        result.current.reconnect();
      });

      // Will attempt to connect (but WebSocket mock won't auto-connect without manual simulation)
      expect(result.current.connectionState).toBe('connecting');
    });
  });

  describe('Subscribe/Unsubscribe', () => {
    it('should return a valid subscribe function', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      expect(typeof result.current.subscribe).toBe('function');

      // Should not throw when called
      act(() => {
        result.current.subscribe([{ channel: 'test_channel' }]);
      });
    });

    it('should return a valid unsubscribe function', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      expect(typeof result.current.unsubscribe).toBe('function');

      act(() => {
        result.current.unsubscribe(['test_channel']);
      });
    });
  });

  describe('Send', () => {
    it('should return false when sending while disconnected', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      const sent = result.current.send({ type: 'command', command: 'test' });
      expect(sent).toBe(false);
    });

    it('should accept string messages', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      const sent = result.current.send('string message');
      expect(sent).toBe(false); // Disconnected, so should fail
    });
  });

  describe('Legacy Fallback Configuration', () => {
    it('should ignore provided fallback configuration in WebSocket-only mode', () => {
      const fallbackEndpoint = '/api/poll';
      const fallbackInterval = 3000;

      const { result } = renderHook(() =>
        useWebSocketWithFallback({
          autoConnect: false,
          fallbackEndpoint,
          fallbackInterval,
        })
      );

      expect(result.current.connectionState).toBe('disconnected');
      expect(result.current.isFallbackActive).toBe(false);
    });

    it('should keep fallback attempt count at zero', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({
          autoConnect: false,
          fallbackEndpoint: '/api/poll',
        })
      );

      // Initially 0
      expect(result.current.fallbackAttempt).toBe(0);
    });

    it('should not start HTTP polling when reconnect retries are exhausted', () => {
      vi.useFakeTimers();
      const onFallbackStart = vi.fn();
      const { result } = renderHook(() =>
        useWebSocketWithFallback({
          url: 'ws://localhost:8080',
          fallbackEndpoint: '/api/poll',
          maxRetries: 0,
          onFallbackStart,
        })
      );

      const socket = MockWebSocket.instances[0];
      expect(socket).toBeDefined();

      act(() => {
        socket.onclose?.(new CloseEvent('close'));
      });

      expect(result.current.connectionState).toBe('disconnected');
      expect(result.current.isConnected).toBe(false);
      expect(result.current.isFallbackActive).toBe(false);
      expect(result.current.fallbackAttempt).toBe(0);
      expect(onFallbackStart).not.toHaveBeenCalled();

      act(() => {
        vi.advanceTimersByTime(5000);
      });
      expect(mockFetch).not.toHaveBeenCalled();
    });
  });

  describe('Reconnection Configuration', () => {
    it('should track reconnect attempt count', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({
          autoConnect: false,
          url: 'ws://localhost:8080',
        })
      );

      expect(result.current.reconnectAttempt).toBe(0);
    });

    it('should respect maxRetries configuration', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({
          autoConnect: false,
          maxRetries: 5,
        })
      );

      expect(result.current.reconnectAttempt).toBe(0);
    });

    it('should stop reconnecting after finite maxRetries across repeated closes', () => {
      vi.useFakeTimers();
      const { result } = renderHook(() =>
        useWebSocketWithFallback({
          url: 'ws://localhost:8080',
          maxRetries: 2,
          baseDelay: 1,
          maxDelay: 1,
        })
      );

      expect(MockWebSocket.instances).toHaveLength(1);

      act(() => {
        MockWebSocket.instances[0].onclose?.(new CloseEvent('close'));
      });
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(MockWebSocket.instances).toHaveLength(2);
      expect(result.current.reconnectAttempt).toBe(1);

      act(() => {
        MockWebSocket.instances[1].onclose?.(new CloseEvent('close'));
      });
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(MockWebSocket.instances).toHaveLength(3);
      expect(result.current.reconnectAttempt).toBe(2);

      act(() => {
        MockWebSocket.instances[2].onclose?.(new CloseEvent('close'));
      });
      act(() => {
        vi.advanceTimersByTime(10);
      });

      expect(MockWebSocket.instances).toHaveLength(3);
      expect(result.current.connectionState).toBe('disconnected');
      expect(result.current.error).toContain('reconnect exhausted');
    });
  });

  describe('Connection State Values', () => {
    it('should have valid connectionState values', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      const validStates = ['connected', 'connecting', 'disconnected', 'fallback'];
      expect(validStates).toContain(result.current.connectionState);
    });
  });

  describe('Derived State', () => {
    it('should derive isConnected from connectionState', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      // When disconnected, isConnected should be false
      expect(result.current.isConnected).toBe(result.current.connectionState === 'connected');
    });

    it('should correctly identify WebSocket connection status', () => {
      const { result } = renderHook(() =>
        useWebSocketWithFallback({ autoConnect: false })
      );

      // When disconnected, WebSocket should not be connected
      expect(result.current.isWebSocketConnected).toBe(result.current.connectionState === 'connected');
    });
  });
});

describe('useWebSocketWithFallback return interface', () => {
  it('should return all required methods and properties', () => {
    const { result } = renderHook(() =>
      useWebSocketWithFallback({ autoConnect: false })
    );

    // Required properties
    expect(result.current).toHaveProperty('connectionState');
    expect(result.current).toHaveProperty('isConnected');
    expect(result.current).toHaveProperty('isWebSocketConnected');
    expect(result.current).toHaveProperty('isFallbackActive');
    expect(result.current).toHaveProperty('reconnectAttempt');
    expect(result.current).toHaveProperty('fallbackAttempt');
    expect(result.current).toHaveProperty('error');

    // Required methods
    expect(result.current).toHaveProperty('subscribe');
    expect(result.current).toHaveProperty('unsubscribe');
    expect(result.current).toHaveProperty('send');
    expect(result.current).toHaveProperty('disconnect');
    expect(result.current).toHaveProperty('reconnect');

    // All methods should be functions
    expect(typeof result.current.subscribe).toBe('function');
    expect(typeof result.current.unsubscribe).toBe('function');
    expect(typeof result.current.send).toBe('function');
    expect(typeof result.current.disconnect).toBe('function');
    expect(typeof result.current.reconnect).toBe('function');
  });
});

describe('Specialized Hooks', () => {
  describe('useCourtWebSocketWithFallback', () => {
    it('should be importable and return expected shape', async () => {
      const { useCourtWebSocketWithFallback } = await import('../useWebSocketWithFallback');

      const { result } = renderHook(() =>
        useCourtWebSocketWithFallback({ autoConnect: false })
      );

      // Should have base hook properties plus courtState
      expect(result.current).toHaveProperty('courtState');
      expect(result.current.courtState).toBeNull();
    });
  });

  describe('useRuntimeWebSocketWithFallback', () => {
    it('should be importable and return expected shape', async () => {
      const { useRuntimeWebSocketWithFallback } = await import('../useWebSocketWithFallback');

      const { result } = renderHook(() =>
        useRuntimeWebSocketWithFallback({ autoConnect: false })
      );

      // Should have base hook properties plus sendCommand
      expect(result.current).toHaveProperty('sendCommand');
      expect(typeof result.current.sendCommand).toBe('function');
    });
  });
});

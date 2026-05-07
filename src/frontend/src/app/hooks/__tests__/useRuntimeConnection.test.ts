/**
 * useRuntimeConnection Hook Tests
 *
 * 测试运行时连接状态管理 Hook 的核心功能：
 * - 连接状态同步
 * - 订阅管理
 * - 重连逻辑
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import React from 'react';
import { useRuntimeConnection } from '../useRuntimeConnection';

const mockSubscribeChannels = vi.hoisted(() => vi.fn(() => vi.fn()));
const mockSendCommand = vi.hoisted(() => vi.fn());
const mockReconnect = vi.hoisted(() => vi.fn());
const mockRegisterMessageHandler = vi.hoisted(() => vi.fn(() => vi.fn()));
const mockGetLastCursor = vi.hoisted(() => vi.fn(() => 0));

// Mock dependencies
vi.mock('@/app/hooks/useRuntimeStore', () => ({
  useRuntimeStore: vi.fn((selector) => {
    const state = {
      live: false,
      error: null,
      reconnecting: false,
      attemptCount: 0,
      setConnectionState: vi.fn(),
      resetForWorkspace: vi.fn(),
    };
    return selector ? selector(state) : state;
  }),
}));

vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: vi.fn(() => ({
    connected: false,
    reconnecting: false,
    error: null,
    attemptCount: 0,
    subscribeChannels: mockSubscribeChannels,
    sendCommand: mockSendCommand,
    reconnect: mockReconnect,
    getLastCursor: mockGetLastCursor,
    registerMessageHandler: mockRegisterMessageHandler,
  })),
}));

vi.mock('@/hooks', () => ({
  useSettings: vi.fn(() => ({
    settings: { workspace: '/test/workspace' },
    load: vi.fn(),
  })),
}));

describe('useRuntimeConnection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('Initial State', () => {
    it('should initialize with disconnected state', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(result.current.live).toBe(false);
      expect(result.current.connected).toBe(false);
      expect(result.current.isConnected).toBe(false);
    });

    it('should initialize with null error', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(result.current.error).toBeNull();
    });

    it('should initialize with no reconnect attempts', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(result.current.reconnecting).toBe(false);
      expect(result.current.attemptCount).toBe(0);
    });
  });

  describe('Connection Actions', () => {
    it('should return connect function', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(typeof result.current.connect).toBe('function');
    });

    it('should return disconnect function', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(typeof result.current.disconnect).toBe('function');
    });

    it('should return reconnect function', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(typeof result.current.reconnect).toBe('function');
    });

    it('should call disconnect action', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      act(() => {
        result.current.disconnect();
      });

      // Disconnect should be callable without errors
      expect(result.current.live).toBe(false);
    });
  });

  describe('Subscription Management', () => {
    it('should provide updateSubscription function', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(typeof result.current.updateSubscription).toBe('function');
    });
  });

  describe('Transport Layer', () => {
    it('should expose transport layer state', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(result.current).toHaveProperty('transportConnected');
      expect(result.current).toHaveProperty('transportReconnecting');
      expect(result.current).toHaveProperty('transportError');
      expect(result.current).toHaveProperty('transportAttemptCount');
    });

    it('should expose transport actions', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(result.current).toHaveProperty('transportReconnect');
      expect(result.current).toHaveProperty('registerMessageHandler');
      expect(result.current).toHaveProperty('sendCommand');
      expect(typeof result.current.transportReconnect).toBe('function');
      expect(typeof result.current.registerMessageHandler).toBe('function');
      expect(typeof result.current.sendCommand).toBe('function');
    });
  });

  describe('Refs', () => {
    it('should provide workspaceRef', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(result.current.workspaceRef).toBeDefined();
      expect(result.current.workspaceRef.current).toBe('/test');
    });

    it('should provide rolesRef', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, roles: ['pm', 'director'], workspace: '/test' })
      );

      expect(result.current.rolesRef).toBeDefined();
      expect(result.current.rolesRef.current).toEqual(['director', 'pm']);
    });

    it('should provide activeRef', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      expect(result.current.activeRef).toBeDefined();
      expect(result.current.activeRef.current).toBe(true);
    });
  });

  describe('Role Subscription', () => {
    it('subscribes concrete runtime stream channels instead of a roles pseudo-channel', async () => {
      renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      await waitFor(() => {
        expect(mockSubscribeChannels).toHaveBeenCalled();
      });

      const subscriptions = mockSubscribeChannels.mock.calls[0]?.[0] ?? [];
      const channels = subscriptions.map((item: { channel: string }) => item.channel);
      expect(channels).toEqual(['system', 'process', 'llm', 'dialogue', 'runtime_events', 'event.file_edit']);
      expect(channels).not.toContain('roles:pm,director,qa');
    });

    it('passes roles to transport subscribe request', async () => {
      renderHook(() =>
        useRuntimeConnection({ autoConnect: false, roles: ['director'], workspace: '/test' })
      );

      await waitFor(() => {
        expect(mockSubscribeChannels).toHaveBeenCalled();
      });

      const calls = mockSubscribeChannels.mock.calls;
      const lastCall = calls.at(-1);
      expect(lastCall?.[1]).toEqual(['director']);
    });

    it('does not resubscribe when rerender keeps the same role set', async () => {
      const unsubscribe = vi.fn();
      mockSubscribeChannels.mockReturnValue(unsubscribe);

      const { rerender } = renderHook(
        ({
          roles,
        }: {
          roles: ('pm' | 'director' | 'qa')[];
        }) => useRuntimeConnection({ autoConnect: false, roles, workspace: '/test' }),
        {
          initialProps: { roles: ['pm', 'director'] },
        }
      );

      await waitFor(() => {
        expect(mockSubscribeChannels).toHaveBeenCalledTimes(1);
      });

      rerender({ roles: ['director', 'pm'] });

      await waitFor(() => {
        expect(mockSubscribeChannels).toHaveBeenCalledTimes(1);
      });

      expect(unsubscribe).not.toHaveBeenCalled();
    });

    it('should use default roles when not specified', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      // Default roles should be ['pm', 'director', 'qa']
      expect(result.current.rolesRef.current).toEqual(['director', 'pm', 'qa']);
    });

    it('should respect custom roles', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({
          autoConnect: false,
          roles: ['director'],
          workspace: '/test',
        })
      );

      expect(result.current.rolesRef.current).toEqual(['director']);
    });

    it('updateSubscription should send full v2 SUBSCRIBE payload', async () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      mockGetLastCursor.mockReturnValueOnce(128);
      act(() => {
        result.current.updateSubscription(['director', 'qa']);
      });

      expect(mockSendCommand).toHaveBeenCalledWith({
        type: 'SUBSCRIBE',
        protocol: 'runtime.v2',
        roles: ['director', 'qa'],
        tail: 100,
        channels: ['system', 'process', 'llm', 'dialogue', 'runtime_events', 'event.file_edit'],
        cursor: 128,
      });
    });

    it('updateSubscription should persist effective roles across rerender with unchanged props', async () => {
      const { result, rerender } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      await waitFor(() => {
        expect(mockSubscribeChannels).toHaveBeenCalledTimes(1);
      });

      act(() => {
        result.current.updateSubscription(['qa', 'director']);
      });

      await waitFor(() => {
        expect(result.current.rolesRef.current).toEqual(['director', 'qa']);
      });

      rerender();

      await waitFor(() => {
        expect(result.current.rolesRef.current).toEqual(['director', 'qa']);
      });
    });

    it('updateSubscription should switch roles without triggering unsubscribe cleanup', async () => {
      const unsubscribe = vi.fn();
      mockSubscribeChannels.mockReturnValue(unsubscribe);

      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      await waitFor(() => {
        expect(mockSubscribeChannels).toHaveBeenCalledTimes(1);
      });

      act(() => {
        result.current.updateSubscription(['director']);
      });

      await waitFor(() => {
        expect(result.current.rolesRef.current).toEqual(['director']);
      });

      expect(mockSubscribeChannels).toHaveBeenCalledTimes(1);
      expect(unsubscribe).not.toHaveBeenCalled();
      expect(mockSendCommand).toHaveBeenCalledWith({
        type: 'SUBSCRIBE',
        protocol: 'runtime.v2',
        roles: ['director'],
        tail: 100,
        channels: ['system', 'process', 'llm', 'dialogue', 'runtime_events', 'event.file_edit'],
        cursor: 0,
      });
    });

    it('role switch via props should avoid unsubscribe/subscribe churn', async () => {
      const unsubscribe = vi.fn();
      mockSubscribeChannels.mockReturnValue(unsubscribe);

      const { rerender } = renderHook(
        ({
          roles,
        }: {
          roles: ('pm' | 'director' | 'qa')[];
        }) => useRuntimeConnection({ autoConnect: false, roles, workspace: '/test' }),
        {
          initialProps: { roles: ['pm', 'director'] },
        }
      );

      await waitFor(() => {
        expect(mockSubscribeChannels).toHaveBeenCalledTimes(1);
      });

      rerender({ roles: ['qa'] });

      await waitFor(() => {
        expect(mockSendCommand).toHaveBeenCalledWith({
          type: 'SUBSCRIBE',
          protocol: 'runtime.v2',
          roles: ['qa'],
          tail: 100,
          channels: ['system', 'process', 'llm', 'dialogue', 'runtime_events', 'event.file_edit'],
          cursor: 0,
        });
      });

      expect(mockSubscribeChannels).toHaveBeenCalledTimes(1);
      expect(unsubscribe).not.toHaveBeenCalled();
    });
  });

  describe('Workspace Handling', () => {
    it('should use provided workspace', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/custom/workspace' })
      );

      expect(result.current.workspaceRef.current).toBe('/custom/workspace');
    });

    it('should handle empty workspace', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '' })
      );

      expect(result.current.workspaceRef.current).toBe('');
    });
  });
});

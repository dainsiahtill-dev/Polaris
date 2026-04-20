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
    subscribeChannels: vi.fn(() => vi.fn()),
    sendCommand: vi.fn(),
    reconnect: vi.fn(),
    registerMessageHandler: vi.fn(() => vi.fn()),
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
      expect(result.current.rolesRef.current).toEqual(['pm', 'director']);
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
    it('should use default roles when not specified', () => {
      const { result } = renderHook(() =>
        useRuntimeConnection({ autoConnect: false, workspace: '/test' })
      );

      // Default roles should be ['pm', 'director', 'qa']
      expect(result.current.rolesRef.current).toEqual(['pm', 'director', 'qa']);
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

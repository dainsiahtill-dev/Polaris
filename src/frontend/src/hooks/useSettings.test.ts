import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

const getMock = vi.fn();
const updateMock = vi.fn();

vi.mock('@/services/api', () => ({
  settingsService: {
    get: (...args: unknown[]) => getMock(...args),
    update: (...args: unknown[]) => updateMock(...args),
  },
}));

import { useSettings } from './useSettings';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

describe('useSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads settings on mount', async () => {
    getMock.mockResolvedValueOnce({
      ok: true,
      data: { workspace: 'X:/workspace' },
    });

    const { result } = renderHook(() => useSettings(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace');
    });

    expect(getMock).toHaveBeenCalledTimes(1);
  });

  it('updates settings optimistically', async () => {
    getMock.mockResolvedValueOnce({
      ok: true,
      data: { workspace: 'X:/workspace-initial' },
    });

    updateMock.mockResolvedValueOnce({
      ok: true,
      data: { workspace: 'X:/workspace-updated' },
    });

    const { result } = renderHook(() => useSettings(), { wrapper: createWrapper() });

    // Wait for initial load
    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-initial');
    });

    // Update settings
    await act(async () => {
      await result.current.update({ workspace: 'X:/workspace-updated' });
    });

    // Wait for React to re-render with optimistic update
    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-updated');
    });
  });

  it('reverts optimistic update on failure', async () => {
    getMock.mockResolvedValueOnce({
      ok: true,
      data: { workspace: 'X:/workspace-initial' },
    });

    updateMock.mockRejectedValueOnce(new Error('Update failed'));

    const { result } = renderHook(() => useSettings(), { wrapper: createWrapper() });

    // Wait for initial load
    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-initial');
    });

    // Try to update and expect it to fail
    await act(async () => {
      await expect(
        result.current.update({ workspace: 'X:/workspace-updated' })
      ).rejects.toThrow('Update failed');
    });

    // Settings should be reverted to initial value
    expect(result.current.settings?.workspace).toBe('X:/workspace-initial');
  });

  it('handles update with immediate server response', async () => {
    getMock.mockResolvedValueOnce({
      ok: true,
      data: { workspace: 'X:/workspace-initial' },
    });

    updateMock.mockResolvedValueOnce({
      ok: true,
      data: { workspace: 'X:/workspace-updated' },
    });

    const { result } = renderHook(() => useSettings(), { wrapper: createWrapper() });

    // Wait for initial load
    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-initial');
    });

    // Update settings
    const updateResult = await act(async () => {
      return result.current.update({ workspace: 'X:/workspace-updated' });
    });

    // Update function should return the new data
    expect(updateResult?.workspace).toBe('X:/workspace-updated');

    // Settings should reflect the update
    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-updated');
    });
  });
});

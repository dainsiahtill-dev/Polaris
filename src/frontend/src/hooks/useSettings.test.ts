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

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
  reject: (reason?: unknown) => void;
};

function createDeferred<T>(): Deferred<T> {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

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

  it('does not rollback newer successful update when older update fails', async () => {
    getMock.mockResolvedValueOnce({
      ok: true,
      data: { workspace: 'X:/workspace-initial' },
    });

    const olderUpdate = createDeferred<{ ok: true; data: { workspace: string } }>();
    const newerUpdate = createDeferred<{ ok: true; data: { workspace: string } }>();
    updateMock
      .mockImplementationOnce(() => olderUpdate.promise)
      .mockImplementationOnce(() => newerUpdate.promise);

    const { result } = renderHook(() => useSettings(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-initial');
    });

    let olderPromise!: Promise<unknown>;
    let newerPromise!: Promise<unknown>;
    act(() => {
      olderPromise = result.current.update({ workspace: 'X:/workspace-older' });
      newerPromise = result.current.update({ workspace: 'X:/workspace-newer' });
    });

    await act(async () => {
      newerUpdate.resolve({
        ok: true,
        data: { workspace: 'X:/workspace-newer' },
      });
      await newerPromise;
    });

    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-newer');
    });

    await act(async () => {
      olderUpdate.reject(new Error('Older update failed'));
      await expect(olderPromise).rejects.toThrow('Older update failed');
    });

    expect(result.current.settings?.workspace).toBe('X:/workspace-newer');
  });

  it('rolls back to last stable snapshot when latest update fails after delay', async () => {
    getMock.mockResolvedValueOnce({
      ok: true,
      data: { workspace: 'X:/workspace-initial' },
    });

    const delayedFailure = createDeferred<{ ok: true; data: { workspace: string } }>();
    updateMock.mockImplementationOnce(() => delayedFailure.promise);

    const { result } = renderHook(() => useSettings(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-initial');
    });

    let updatePromise!: Promise<unknown>;
    act(() => {
      updatePromise = result.current.update({ workspace: 'X:/workspace-failing' });
    });

    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-failing');
    });

    await act(async () => {
      delayedFailure.reject(new Error('Delayed failure'));
      await expect(updatePromise).rejects.toThrow('Delayed failure');
    });

    await waitFor(() => {
      expect(result.current.settings?.workspace).toBe('X:/workspace-initial');
    });
  });
});

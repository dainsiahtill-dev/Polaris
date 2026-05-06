import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSSEStream } from './useSSEStream';

const getBackendInfoMock = vi.fn();

vi.mock('@/api', () => ({
  getBackendInfo: (...args: unknown[]) => getBackendInfoMock(...args),
}));

function responseFromChunks(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    }),
    { status: 200 },
  );
}

describe('useSSEStream', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getBackendInfoMock.mockResolvedValue({
      baseUrl: 'http://127.0.0.1:49977',
      token: 'test-token',
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('calls onComplete for terminal complete events', async () => {
    const onComplete = vi.fn();
    const onError = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(responseFromChunks(['event: complete\ndata: {"ok":true}\n\n'])),
    );

    const { result } = renderHook(() => useSSEStream({ onComplete, onError }));

    await act(async () => {
      await result.current.startStream('/docs/init/preview/stream', { mode: 'manual' });
    });

    expect(onComplete).toHaveBeenCalledWith({ ok: true });
    expect(onError).not.toHaveBeenCalled();
    expect(result.current.isStreaming).toBe(false);
  });

  it('reports EOF without terminal event as an error and clears streaming state', async () => {
    const onComplete = vi.fn();
    const onError = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(responseFromChunks(['event: stage\ndata: {"step":"llm"}\n\n'])));

    const { result } = renderHook(() => useSSEStream({ onComplete, onError }));

    await act(async () => {
      await result.current.startStream('/docs/init/preview/stream', { mode: 'manual' });
    });

    await waitFor(() => {
      expect(onError).toHaveBeenCalledWith('SSE stream ended without a terminal event');
    });
    expect(onComplete).not.toHaveBeenCalled();
    expect(result.current.isStreaming).toBe(false);
  });
});

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createContentTagParser, useInterviewStream, type StreamingTagEvent } from './useInterviewStream';

const getBackendInfoMock = vi.fn();
const runtimeTransportMock = vi.hoisted(() => ({
  connected: true,
  reconnecting: false,
  error: null as string | null,
  attemptCount: 0,
  subscribeChannels: vi.fn(() => vi.fn()),
  sendCommand: vi.fn(() => true),
  getLastCursor: vi.fn(() => 0),
  reconnect: vi.fn(),
  registerMessageHandler: vi.fn(() => vi.fn()),
}));

vi.mock('@/api', () => ({
  getBackendInfo: () => getBackendInfoMock(),
}));

vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => runtimeTransportMock,
}));

function jsonResponse(payload: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? 'OK' : 'Error',
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  } as Response;
}

function emitInterviewChunk(channel: string, type: string, data: Record<string, unknown>) {
  const handler = runtimeTransportMock.registerMessageHandler.mock.calls.at(-1)?.[0];
  expect(handler).toBeTypeOf('function');
  act(() => {
    handler?.({
      channel,
      payload: { type, data, seq: 0 },
    });
  });
}

beforeEach(() => {
  getBackendInfoMock.mockResolvedValue({
    baseUrl: 'http://127.0.0.1:49977',
    token: 'test-token',
  });
  runtimeTransportMock.connected = true;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('createContentTagParser', () => {
  it('parses answer tags split across content chunks', () => {
    const parser = createContentTagParser();
    const events: StreamingTagEvent[] = [];

    ['<', 'answer', '>风险', '识别</', 'answer>'].forEach((chunk) => {
      parser.consume(chunk, '2026-06-02T00:00:00.000Z', (event) => events.push(event));
    });

    expect(events.map((event) => event.type)).toEqual([
      'answer_start',
      'answer_chunk',
      'answer_chunk',
      'answer_end',
    ]);
    expect(events.filter((event) => event.type === 'answer_chunk').map((event) => event.data.content).join('')).toBe(
      '风险识别'
    );
  });

  it('parses thinking aliases without leaking partial closing tags', () => {
    const parser = createContentTagParser();
    const events: StreamingTagEvent[] = [];

    ['<think>', 'step 1</thi', 'nk>'].forEach((chunk) => {
      parser.consume(chunk, '2026-06-02T00:00:00.000Z', (event) => events.push(event));
    });

    expect(events.map((event) => event.type)).toEqual(['thinking_start', 'thinking_chunk', 'thinking_end']);
    expect(events[1]?.data.content).toBe('step 1');
  });
});

describe('useInterviewStream', () => {
  it('starts interview over Nat-JetStream and completes from runtime WebSocket events', async () => {
    const report = {
      sessionId: 'interactive-1',
      answer: '实施计划',
      thinking: '分析',
      ok: true,
    };

    const fetchMock = vi.fn(async () => jsonResponse({
      ok: true,
      session_id: 'interactive-1',
      status: 'started',
      channel: 'llm-interview:interactive-1',
      subject: 'hp.runtime.llm.interview.interactive-1',
      transport: 'nat-jetstream',
    }));
    vi.stubGlobal('fetch', fetchMock);

    const onStart = vi.fn();
    const onComplete = vi.fn();
    const onError = vi.fn();
    const onTagEvent = vi.fn();
    const { result } = renderHook(() =>
      useInterviewStream({
        onStart,
        onComplete,
        onError,
        onTagEvent,
      })
    );

    let startPromise: Promise<void> | undefined;
    act(() => {
      startPromise = result.current.startStream({
        roleId: 'pm',
        providerId: 'anthropic_compat-1771249789301',
        model: 'kimi-for-coding',
        question: '请分析这个项目需求并制定实施计划。',
        sessionId: 'interactive-1',
      });
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:49977/v2/llm/interview/jetstream',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: 'Bearer test-token',
          'Content-Type': 'application/json',
        }),
      }),
    );
    expect(runtimeTransportMock.subscribeChannels).toHaveBeenCalledWith([
      { channel: 'llm-interview:interactive-1', tailLines: 0 },
    ]);

    emitInterviewChunk('llm-interview:interactive-1', 'start', { session_id: 'interactive-1' });
    emitInterviewChunk('llm-interview:interactive-1', 'content_chunk', {
      content: '<answer>实施',
      timestamp: '2026-06-18T00:00:00.000Z',
    });
    emitInterviewChunk('llm-interview:interactive-1', 'content_chunk', {
      content: '计划</answer>',
      timestamp: '2026-06-18T00:00:01.000Z',
    });
    emitInterviewChunk('llm-interview:interactive-1', 'complete', report);

    await act(async () => {
      await startPromise;
    });

    expect(onStart).toHaveBeenCalledWith('interactive-1');
    expect(onComplete).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'interactive-1' }));
    expect(onError).not.toHaveBeenCalled();
    expect(onTagEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'answer_start' }));
    expect(onTagEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'answer_end' }));
    await waitFor(() => expect(result.current.isStreaming).toBe(false));
  });

  it('emits untagged content_chunk text as live answer chunks', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({
      ok: true,
      session_id: 'interactive-plain',
      status: 'started',
      channel: 'llm-interview:interactive-plain',
      subject: 'hp.runtime.llm.interview.interactive-plain',
      transport: 'nat-jetstream',
    }));
    vi.stubGlobal('fetch', fetchMock);

    const onComplete = vi.fn();
    const onTagEvent = vi.fn();
    const { result } = renderHook(() =>
      useInterviewStream({
        onComplete,
        onTagEvent,
      })
    );

    let startPromise: Promise<void> | undefined;
    act(() => {
      startPromise = result.current.startStream({
        roleId: 'pm',
        providerId: 'anthropic_compat-1771249789301',
        model: 'kimi-for-coding',
        question: '请分析这个项目需求并制定实施计划。',
        sessionId: 'interactive-plain',
      });
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    emitInterviewChunk('llm-interview:interactive-plain', 'content_chunk', {
      content: ' This',
      timestamp: '2026-06-18T00:00:00.000Z',
    });
    emitInterviewChunk('llm-interview:interactive-plain', 'content_chunk', {
      content: ' allows',
      timestamp: '2026-06-18T00:00:01.000Z',
    });
    emitInterviewChunk('llm-interview:interactive-plain', 'complete', {
      sessionId: 'interactive-plain',
      answer: ' This allows',
      ok: true,
    });

    await act(async () => {
      await startPromise;
    });

    expect(onComplete).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'interactive-plain' }));
    expect(onTagEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'answer_start' }));
    expect(onTagEvent).toHaveBeenCalledWith(expect.objectContaining({
      type: 'answer_chunk',
      data: expect.objectContaining({ content: ' This' }),
    }));
    expect(onTagEvent).toHaveBeenCalledWith(expect.objectContaining({
      type: 'answer_chunk',
      data: expect.objectContaining({ content: ' allows' }),
    }));
    expect(onTagEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'answer_end' }));
  });
});

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useRuntime } from '../useRuntime';
import { useRuntimeStore } from '../useRuntimeStore';
import { TaskStatus } from '@/types/task';
import { buildTelemetryFromStream, telemetryRoleTokens } from '@/app/components/contextos/contextOSTelemetry';

type RuntimeMessageHandler = (message: unknown) => void;

const runtimeConnectionMock = vi.hoisted(() => {
  let handler: RuntimeMessageHandler | null = null;
  const registerMessageHandler = vi.fn((nextHandler: RuntimeMessageHandler) => {
    handler = nextHandler;
    return vi.fn(() => {
      if (handler === nextHandler) {
        handler = null;
      }
    });
  });
  const sendCommand = vi.fn();
  const connect = vi.fn();
  const disconnect = vi.fn();
  const reconnect = vi.fn();
  const updateSubscription = vi.fn();

  return {
    registerMessageHandler,
    sendCommand,
    connect,
    disconnect,
    reconnect,
    updateSubscription,
    getHandler: () => handler,
    reset: () => {
      handler = null;
      registerMessageHandler.mockClear();
      sendCommand.mockClear();
      connect.mockClear();
      disconnect.mockClear();
      reconnect.mockClear();
      updateSubscription.mockClear();
    },
  };
});

const settingsHookMock = vi.hoisted(() => {
  const load = vi.fn();

  return {
    load,
    reset: () => {
      load.mockClear();
    },
  };
});

vi.mock('../useRuntimeConnection', () => ({
  useRuntimeConnection: vi.fn(() => ({
    live: false,
    connected: false,
    isConnected: false,
    error: null,
    reconnecting: false,
    attemptCount: 0,
    connect: runtimeConnectionMock.connect,
    disconnect: runtimeConnectionMock.disconnect,
    reconnect: runtimeConnectionMock.reconnect,
    updateSubscription: runtimeConnectionMock.updateSubscription,
    transportConnected: false,
    transportReconnecting: false,
    transportError: null,
    transportAttemptCount: 0,
    transportReconnect: runtimeConnectionMock.reconnect,
    registerMessageHandler: runtimeConnectionMock.registerMessageHandler,
    sendCommand: runtimeConnectionMock.sendCommand,
    workspaceRef: { current: '/test/workspace' },
    rolesRef: {
      current: ['pm', 'chief_engineer', 'director', 'qa'] as ('pm' | 'chief_engineer' | 'director' | 'qa')[],
    },
    activeRef: { current: true },
  })),
}));

vi.mock('@/hooks', () => ({
  useSettings: vi.fn(() => ({
    settings: { workspace: '/test/workspace' },
    load: settingsHookMock.load,
  })),
}));

function emitRuntimeMessage(message: unknown): void {
  const handler = runtimeConnectionMock.getHandler();
  if (!handler) {
    throw new Error('runtime message handler not registered');
  }
  act(() => {
    handler(message);
  });
}

describe('useRuntime llm filtering and dedup', () => {
  beforeEach(() => {
    runtimeConnectionMock.reset();
    settingsHookMock.reset();
    act(() => {
      useRuntimeStore.getState().resetAll();
    });
  });

  afterEach(() => {
    act(() => {
      useRuntimeStore.getState().resetAll();
    });
  });

  it('accepts llm line when payload domain is llm even if nested channel is runtime_events', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        channel: 'runtime_events',
        domain: 'llm',
        event: 'invoke_done',
        data: {
          summary: 'LLM response accepted',
          output_chars: 32,
        },
      }),
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    expect(result.current.llmStreamEvents[0]?.message).toBe('LLM response accepted');
  });

  it('merges task_runtime execution claimed events into in-progress tasks', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'snapshot',
      channel: 'runtime_events',
      lines: [
        JSON.stringify({
          schema_version: 1,
          stream: 'task_runtime.execution',
          event_type: 'claimed',
          source: 'runtime.task_runtime',
          aggregate_id: '1',
          payload: {
            event_type: 'claimed',
            task_id: '1',
            status: 'in_progress',
            subject: '创建响应式简历网页核心实现',
            claimed_by: 'director',
            timestamp: '2026-06-19T21:42:22.858239+00:00',
          },
          metadata: {
            task_id: '1',
          },
        }),
      ],
    });

    expect(result.current.tasks).toHaveLength(1);
    expect(result.current.tasks[0]?.id).toBe('1');
    expect(result.current.tasks[0]?.status).toBe(TaskStatus.IN_PROGRESS);
    expect(result.current.tasks[0]?.done).toBe(false);
    expect(result.current.tasks[0]?.title).toBe('创建响应式简历网页核心实现');
  });

  it('loads event.factory snapshots into process stream events', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'snapshot',
      channel: 'event.factory',
      lines: [
        JSON.stringify({
          type: 'factory_bench.project.started',
          actor: 'factory-bench',
          message: 'Factory bench project L1-02 started',
          project_id: 'L1-02',
          level: 1,
        }),
      ],
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('Factory bench project L1-02 started');
    expect(result.current.processStreamEvents[0]?.meta?.channel).toBe('event.factory');
    expect(result.current.processStreamEvents[0]?.meta?.streamEvent).toBe('factory_bench.project.started');
  });

  it('appends live event.factory lines into process stream events', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'event.factory',
      text: JSON.stringify({
        type: 'factory_bench.gate.evaluated',
        actor: 'factory-bench',
        message: 'real_run_gate ok',
        gate: 'real_run_gate',
        ok: true,
      }),
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('real_run_gate ok');
    expect(result.current.processStreamEvents[0]?.meta?.channel).toBe('event.factory');
    expect(result.current.processStreamEvents[0]?.meta?.streamEvent).toBe('factory_bench.gate.evaluated');
  });

  it('serializes object-valued process summaries instead of rendering [object Object]', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'event.factory',
      text: JSON.stringify({
        type: 'factory_bench.project.updated',
        actor: 'factory-bench',
        message: {
          project_id: 'L1-01',
          phase: 'director_dispatch',
          status: 'running',
        },
      }),
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toContain('"phase":"director_dispatch"');
    expect(result.current.processStreamEvents[0]?.message).not.toContain('[object Object]');
  });

  it('serializes object-valued runtime summaries instead of rendering [object Object]', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'runtime_events',
      text: JSON.stringify({
        event_id: 'runtime-object-summary',
        name: 'llm_completed',
        actor: 'Director',
        summary: {
          state: 'llm_completed',
          provider: 'kimi',
        },
      }),
    });

    expect(result.current.executionLogs).toHaveLength(1);
    expect(result.current.executionLogs[0]?.message).toContain('"state":"llm_completed"');
    expect(result.current.executionLogs[0]?.message).not.toContain('[object Object]');
  });

  it('flattens runtime.v2 role LLM usage envelopes into execution log meta', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'event',
      event: {
        schema_version: 'runtime.v2',
        channel: 'runtime_events',
        kind: 'llm.state',
        ts: '2026-06-21T22:16:12Z',
        payload: {
          raw: {
            event_type: 'llm_call_end',
            role: 'pm',
            data: {
              event_type: 'llm_call_end',
              role: 'pm',
              model: 'kimi-for-coding',
              prompt_tokens: 2732,
              completion_tokens: 1954,
              context_tokens_after: 2732,
              metadata: {
                elapsed_ms: 19177.76,
                context_snapshot_ref: 'e3db3551d74e5741fd664b7b',
              },
            },
          },
        },
      },
    });

    expect(result.current.executionLogs).toHaveLength(1);
    const entry = result.current.executionLogs[0];
    expect(entry?.message).toBe('llm_call_end');
    expect(entry?.meta?.prompt_tokens).toBe(2732);
    expect(entry?.meta?.completion_tokens).toBe(1954);
    expect(entry?.meta?.context_tokens_after).toBe(2732);
    expect(entry?.meta?.elapsed_ms).toBe(19177.76);
    expect(entry?.meta?.context_snapshot_ref).toBe('e3db3551d74e5741fd664b7b');
  });

  it('routes runtime.v2 event.factory envelopes into process stream events', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'EVENT',
      protocol: 'runtime.v2',
      cursor: 42,
      event: {
        schema_version: 'runtime.v2',
        event_id: 'factory-live-1',
        workspace_key: 'test-workspace',
        run_id: 'run-42',
        channel: 'event.factory:run-42',
        kind: 'factory_bench.project.started',
        ts: '2026-06-20T08:56:47.770284+00:00',
        payload: {
          type: 'factory_bench.project.started',
          name: 'factory_bench.project.started',
          actor: 'factory-bench',
          message: 'Factory v2 live event visible',
        },
      },
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('Factory v2 live event visible');
    expect(result.current.processStreamEvents[0]?.source).toBe('factory-bench');
    expect(result.current.processStreamEvents[0]?.meta?.channel).toBe('event.factory:run-42');
    expect(result.current.processStreamEvents[0]?.meta?.streamEvent).toBe('factory_bench.project.started');
  });

  it('routes runtime.v2 event.bench envelopes into process stream events', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'EVENT',
      protocol: 'runtime.v2',
      cursor: 43,
      event: {
        schema_version: 'runtime.v2',
        event_id: 'bench-live-1',
        workspace_key: 'test-workspace',
        run_id: 'bench-1',
        channel: 'event.bench:bench-1',
        kind: 'factory_bench.run.started',
        ts: '2026-06-20T08:57:47.770284+00:00',
        payload: {
          type: 'factory_bench.run.started',
          name: 'factory_bench.run.started',
          actor: 'factory-bench',
          summary: 'Bench v2 live event visible',
        },
      },
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('Bench v2 live event visible');
    expect(result.current.processStreamEvents[0]?.source).toBe('factory-bench');
    expect(result.current.processStreamEvents[0]?.meta?.channel).toBe('event.bench:bench-1');
    expect(result.current.processStreamEvents[0]?.meta?.streamEvent).toBe('factory_bench.run.started');
  });

  it('parses the canonical journal llm_completed line: real tokens + latency into meta', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    // 真实 journal.norm.jsonl 形态（CanonicalLogEventV2）：raw.stream_event=llm_completed，
    // raw.data 携带真实 prompt/completion_tokens、context_tokens_after 与 metadata.elapsed_ms。
    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        schema_version: 2,
        channel: 'llm',
        domain: 'llm',
        kind: 'state',
        actor: 'pm',
        message: 'llm response completed | completion_tokens=1454',
        tags: ['llm_realtime_bridge', 'llm_event:llm_call_end', 'projection_event:llm_completed'],
        raw: {
          stream_event: 'llm_completed',
          event_type: 'llm_call_end',
          role: 'pm',
          data: {
            model: 'MiniMax-M3',
            prompt_tokens: 1932,
            completion_tokens: 1454,
            context_tokens_after: 1932,
            metadata: { elapsed_ms: 71431.06 },
          },
        },
      }),
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    const entry = result.current.llmStreamEvents[0];
    expect(entry?.level).toBe('success');
    expect(entry?.meta?.streamEvent).toBe('llm_completed');
    expect(entry?.meta?.model).toBe('MiniMax-M3');
    // 真实 per-call 用量经 raw.data 注入 meta（不再丢失）。
    expect(entry?.meta?.promptTokens).toBe(1932);
    expect(entry?.meta?.completionTokens).toBe(1454);
    expect(entry?.meta?.totalTokens).toBe(3386);
    expect(entry?.meta?.contextTokens).toBe(1932);
    // 真实时延来自 raw.data.metadata.elapsed_ms（四舍五入）。
    expect(entry?.meta?.durationMs).toBe(71431);
    expect(entry?.details).toContain('71431ms');
    expect(entry?.details).toContain('completion=1454');
  });

  it('preserves provider-native usage aliases through useRuntime into ContextOS telemetry', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        schema_version: 2,
        channel: 'llm',
        domain: 'llm',
        kind: 'state',
        actor: 'director',
        raw: {
          stream_event: 'llm_completed',
          event_type: 'llm_call_end',
          role: 'director',
          data: {
            model: 'qwen3.6-27b',
            usage: {
              input_tokens: 3210,
              output_tokens: 456,
              total_tokens: 3666,
            },
            context_tokens_after: 4096,
            call_id: 'provider-usage-1',
            metadata: {
              elapsed_ms: 2500,
              context_snapshot_ref: 'ctx-provider-usage',
              context_snapshot_degraded: {
                code: 'CONTEXT_STORE_WRITE_FAILED',
                reason: 'context_snapshot_store_failure',
                message: 'disk full',
                exception_type: 'OSError',
              },
            },
          },
        },
      }),
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    const entry = result.current.llmStreamEvents[0];
    expect(entry?.meta?.promptTokens).toBe(3210);
    expect(entry?.meta?.completionTokens).toBe(456);
    expect(entry?.meta?.totalTokens).toBe(3666);
    expect(entry?.meta?.contextTokens).toBe(4096);
    expect(entry?.meta?.callId).toBe('provider-usage-1');
    expect(entry?.meta?.contextSnapshotDegraded).toEqual({
      code: 'CONTEXT_STORE_WRITE_FAILED',
      reason: 'context_snapshot_store_failure',
      message: 'disk full',
      exception_type: 'OSError',
    });

    const telemetry = buildTelemetryFromStream(result.current.llmStreamEvents, [], []);
    expect(telemetry.totalTokens).toBe(4096);
    expect(telemetry.contextTokensLatest).toBe(4096);
    expect(telemetryRoleTokens(telemetry, 'director')).toBe(4096);
    expect(telemetry.events[0].contextSnapshotDegraded?.reason).toBe('context_snapshot_store_failure');
  });

  it('uses llm_completed response_content when no separate content preview is emitted', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'event',
      event: {
        schema_version: 'runtime.v2',
        channel: 'llm',
        kind: 'llm.state',
        ts: '2026-06-19T07:04:05.413082Z',
        payload: {
          message: 'LLM call_end',
          actor: 'director',
          severity: 'info',
          domain: 'llm',
          raw: {
            event_type: 'call_end',
            metadata: {
              elapsed_ms: 48630.82,
              response_content: 'I reviewed the files and will repair the missing stylesheet.',
              prompt_tokens: 2354,
              completion_tokens: 95,
            },
          },
        },
      },
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    const entry = result.current.llmStreamEvents[0];
    expect(entry?.message).toBe('I reviewed the files and will repair the missing stylesheet.');
    expect(entry?.meta?.streamEvent).toBe('call_end');
    expect(entry?.meta?.durationMs).toBe(48631);
  });

  it('parses the canonical journal llm_failed line as an error call', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        schema_version: 2,
        channel: 'llm',
        domain: 'llm',
        kind: 'error',
        actor: 'director',
        message: 'llm call failed',
        raw: {
          stream_event: 'llm_failed',
          event_type: 'llm_call_error',
          role: 'director',
          data: { model: 'local', error_message: 'provider 500', metadata: { elapsed_ms: 1200 } },
        },
      }),
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    const entry = result.current.llmStreamEvents[0];
    expect(entry?.level).toBe('error');
    expect(entry?.meta?.streamEvent).toBe('llm_failed');
    expect(entry?.message).toContain('provider 500');
    expect(entry?.meta?.durationMs).toBe(1200);
  });

  it('classifies runtime.v2 llm.action tool payloads as tool activity', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'event',
      event: {
        schema_version: 'runtime.v2',
        channel: 'llm',
        kind: 'llm.action',
        ts: '2026-06-19T07:02:03.754192Z',
        payload: {
          message: "[tool_call] {'tool': 'write_file'}",
          actor: 'director',
          severity: 'info',
          domain: 'llm',
          raw: {
            event_type: 'tool_call',
            payload: {
              tool: 'write_file',
              args: { file: 'index.html', content: '<html></html>' },
            },
          },
        },
      },
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    const entry = result.current.llmStreamEvents[0];
    expect(entry?.level).toBe('tool');
    expect(entry?.source).toBe('Director');
    expect(entry?.message).toBe('调用工具: write_file');
    expect(entry?.details).toContain('"file":"index.html"');
    expect(entry?.meta?.streamEvent).toBe('tool_call');
    expect(entry?.tags).toContain('tool_call');
  });

  it('parses tagged content_chunk lines emitted by streaming-compatible providers', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: '[16:55:00] > [content_chunk] {"content":" This","model":"kimi-for-coding"}',
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    const entry = result.current.llmStreamEvents[0];
    expect(entry?.message).toBe('This');
    expect(entry?.level).toBe('info');
    expect(entry?.meta?.streamEvent).toBe('content_chunk');
    expect(entry?.meta?.model).toBe('kimi-for-coding');
  });

  it('parses runtime.v2 content_preview metadata as visible LLM output', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'event',
      event: {
        schema_version: 'runtime.v2',
        channel: 'llm',
        kind: 'llm.output',
        ts: '2026-06-19T07:32:36.916196Z',
        payload: {
          actor: 'director',
          severity: 'info',
          domain: 'llm',
          raw: {
            stream_event: 'content_preview',
            event_type: 'content_preview',
            data: {
              model: 'qwen3.6-27b-gpu1',
              metadata: {
                content: '公开模型输出片段',
              },
            },
          },
        },
      },
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    const entry = result.current.llmStreamEvents[0];
    expect(entry?.message).toBe('公开模型输出片段');
    expect(entry?.level).toBe('info');
    expect(entry?.title).toBe('输出预览');
    expect(entry?.meta?.streamEvent).toBe('content_preview');
    expect(entry?.meta?.model).toBe('qwen3.6-27b-gpu1');
  });

  it('classifies process tool events as tool activity for role workspaces', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'process',
      text: JSON.stringify({
        channel: 'process',
        event: 'tool_result',
        role: 'director',
        data: {
          tool: 'repo_tree',
          success: true,
          result: { path: '.', total_entries: 6 },
        },
      }),
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    const entry = result.current.processStreamEvents[0];
    expect(entry?.level).toBe('tool');
    expect(entry?.source).toBe('Director');
    expect(entry?.message).toBe('工具结果: repo_tree (ok)');
    expect(entry?.meta?.streamEvent).toBe('tool_result');
  });

  it('processes EVENT query_result batches item-by-item and preserves v2 dedup', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'event',
      action: 'query_result',
      events: [
        {
          event_id: 'evt-1',
          channel: 'llm',
          domain: 'llm',
          event: 'invoke_done',
          data: {
            summary: 'query-result-item-1',
            output_chars: 10,
          },
        },
        {
          event_id: 'evt-1',
          channel: 'llm',
          domain: 'llm',
          event: 'invoke_done',
          data: {
            summary: 'query-result-item-duplicate',
            output_chars: 10,
          },
        },
        {
          event_id: 'evt-2',
          channel: 'llm',
          domain: 'llm',
          event: 'invoke_done',
          data: {
            summary: 'query-result-item-2',
            output_chars: 10,
          },
        },
      ],
    });

    expect(result.current.llmStreamEvents).toHaveLength(2);
    expect(result.current.llmStreamEvents[0]?.message).toBe('query-result-item-1');
    expect(result.current.llmStreamEvents[1]?.message).toBe('query-result-item-2');
  });

  it('dedups repeated llm line within same run but allows same payload after run switch', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    const repeatedLlmLine = {
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        event: 'invoke_done',
        data: {
          summary: 'same-line-across-runs',
          output_chars: 21,
        },
      }),
    };

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-1' },
    });

    emitRuntimeMessage(repeatedLlmLine);
    emitRuntimeMessage(repeatedLlmLine);
    expect(result.current.llmStreamEvents).toHaveLength(1);

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-2' },
    });

    emitRuntimeMessage(repeatedLlmLine);
    expect(result.current.llmStreamEvents).toHaveLength(2);
    expect(result.current.llmStreamEvents[0]?.message).toBe('same-line-across-runs');
    expect(result.current.llmStreamEvents[1]?.message).toBe('same-line-across-runs');
  });

  it('clears stale llm dedup scope when runtime returns to idle without run_id', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    const repeatedLlmLine = {
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        event: 'invoke_done',
        data: {
          summary: 'same-line-after-idle-boundary',
          output_chars: 14,
        },
      }),
    };

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-1' },
    });

    emitRuntimeMessage(repeatedLlmLine);
    emitRuntimeMessage(repeatedLlmLine);
    expect(result.current.llmStreamEvents).toHaveLength(1);

    emitRuntimeMessage({
      type: 'status',
      pm_status: { running: false },
      director_status: { running: false },
      snapshot: null,
    });

    emitRuntimeMessage(repeatedLlmLine);
    expect(result.current.llmStreamEvents).toHaveLength(2);
    expect(result.current.llmStreamEvents[0]?.message).toBe('same-line-after-idle-boundary');
    expect(result.current.llmStreamEvents[1]?.message).toBe('same-line-after-idle-boundary');
  });

  it('does not rollback dedup scope when late log from previous run arrives', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    const sharedLine = {
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        event: 'invoke_done',
        data: {
          summary: 'shared-cross-run-line',
          output_chars: 12,
        },
      }),
    };

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-1' },
    });
    emitRuntimeMessage(sharedLine);
    expect(result.current.llmStreamEvents).toHaveLength(1);

    emitRuntimeMessage({
      type: 'status',
      snapshot: { run_id: 'run-2' },
    });

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        run_id: 'run-1',
        event: 'invoke_done',
        data: {
          summary: 'late-run1-line',
          output_chars: 8,
        },
      }),
    });

    emitRuntimeMessage(sharedLine);
    expect(result.current.llmStreamEvents).toHaveLength(3);
    expect(result.current.llmStreamEvents[2]?.message).toBe('shared-cross-run-line');
  });

  it('reloads runtime settings when a v2 settings_changed event updates the workspace', () => {
    renderHook(() => useRuntime({ autoConnect: false }));

    emitRuntimeMessage({
      type: 'event',
      event: {
        event_id: 'settings-evt-1',
        event_name: 'settings_changed',
        category: 'system',
        payload: {
          workspace: '/new/workspace',
          previous_workspace: '/test/workspace',
          changed_fields: ['workspace'],
        },
      },
    });

    expect(settingsHookMock.load).toHaveBeenCalledTimes(1);
  });

  it('does not reload runtime settings for controlled workspace props', () => {
    renderHook(() => useRuntime({ autoConnect: false, workspace: '/test/workspace' }));

    emitRuntimeMessage({
      type: 'event',
      event: {
        event_id: 'settings-evt-2',
        event_name: 'settings_changed',
        category: 'system',
        payload: {
          workspace: '/new/workspace',
          previous_workspace: '/test/workspace',
          changed_fields: ['workspace'],
        },
      },
    });

    expect(settingsHookMock.load).not.toHaveBeenCalled();
  });

  it('populates file edit events from direct websocket file_edit messages', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'file_edit',
      timestamp: '2026-05-07T01:00:00.000Z',
      event: {
        file_path: 'src/new.ts',
        operation: 'create',
        content_size: 32,
        task_id: 'PM-1',
        added_lines: 2,
      },
    });

    expect(result.current.fileEditEvents).toHaveLength(1);
    expect(result.current.fileEditEvents[0]).toMatchObject({
      filePath: 'src/new.ts',
      operation: 'create',
      contentSize: 32,
      taskId: 'PM-1',
      addedLines: 2,
    });
  });

  it('populates file edit events from runtime.v2 event.file_edit messages', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'event',
      event: {
        schema_version: 'runtime.v2',
        event_id: 'file-edit-evt-1',
        channel: 'event.file_edit',
        kind: 'file_edit',
        timestamp: '2026-05-07T01:00:00.000Z',
        payload: {
          raw: {
            file_path: 'src/changed.ts',
            operation: 'modify',
            content_size: 64,
            task_id: 'PM-2',
            modified_lines: 1,
          },
        },
      },
    });

    expect(result.current.fileEditEvents).toHaveLength(1);
    expect(result.current.fileEditEvents[0]).toMatchObject({
      filePath: 'src/changed.ts',
      operation: 'modify',
      contentSize: 64,
      taskId: 'PM-2',
      modifiedLines: 1,
      schemaVersion: 'runtime.v2',
      sourceChannel: 'event.file_edit',
    });
  });

  it('populates file edit events from schema-tagged direct fanout messages', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'file_edit',
      protocol: 'runtime.v2',
      schema_version: 'runtime.v2',
      event_schema: 'runtime.event.file_edit.v1',
      channel: 'event.file_edit',
      kind: 'file_edit',
      source: 'process_local_fanout',
      timestamp: '2026-05-07T01:00:00.000Z',
      event: {
        file_path: 'src/fanout.ts',
        operation: 'modify',
        content_size: 96,
        task_id: 'PM-3',
        modified_lines: 4,
      },
    });

    expect(result.current.fileEditEvents).toHaveLength(1);
    expect(result.current.fileEditEvents[0]).toMatchObject({
      filePath: 'src/fanout.ts',
      operation: 'modify',
      contentSize: 96,
      taskId: 'PM-3',
      modifiedLines: 4,
      schemaVersion: 'runtime.v2',
      eventSchema: 'runtime.event.file_edit.v1',
      sourceChannel: 'event.file_edit',
      eventKind: 'file_edit',
      provenance: 'process_local_fanout',
    });
  });

  it('marks runtime tasks in progress from Director task lifecycle events', () => {
    act(() => {
      useRuntimeStore.getState().setTasks([
        {
          id: 'TASK-2',
          title: 'Implement checkout route',
          status: TaskStatus.PENDING,
          state: TaskStatus.PENDING,
          done: false,
          completed: false,
          priority: 3,
          acceptance: [],
        },
      ]);
    });

    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'runtime_event',
      event: {
        name: 'director_task_started',
        actor: 'Director',
        ts: '2026-06-19T12:00:00.000Z',
        data: {
          task_id: 'TASK-2',
          task_title: 'Implement checkout route',
          worker_id: 'worker-1',
        },
      },
    });

    expect(result.current.tasks[0]).toMatchObject({
      id: 'TASK-2',
      status: 'in_progress',
      state: 'in_progress',
      done: false,
      completed: false,
      worker_id: 'worker-1',
      started_at: '2026-06-19T12:00:00.000Z',
    });
  });
});

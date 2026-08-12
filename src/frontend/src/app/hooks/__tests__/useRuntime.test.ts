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

  it('accepts llm line when payload domain is llm', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        channel: 'system',
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
      channel: 'system',
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
          type: 'stage_started',
          actor: 'factory-run',
          message: 'Factory run stage started',
          run_id: 'factory-run-1',
          stage: 'pm',
        }),
      ],
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('Factory run stage started');
    expect(result.current.processStreamEvents[0]?.meta?.channel).toBe('event.factory');
    expect(result.current.processStreamEvents[0]?.meta?.streamEvent).toBe('stage_started');
  });

  it('keeps formal event.factory snapshots when internal bench mode is disabled', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'snapshot',
      channel: 'event.factory',
      lines: [
        JSON.stringify({
          type: 'stage_completed',
          actor: 'factory-run',
          message: 'Factory run stage completed',
          run_id: 'factory-run-1',
          stage: 'director',
        }),
      ],
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('Factory run stage completed');
    expect(result.current.processStreamEvents[0]?.meta?.channel).toBe('event.factory');
    expect(result.current.processStreamEvents[0]?.meta?.streamEvent).toBe('stage_completed');
  });

  it('merges task_runtime execution events from event.factory snapshots into in-progress tasks', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace', includeInternalBench: true })
    );

    emitRuntimeMessage({
      type: 'snapshot',
      channel: 'event.factory:factory-run-1',
      lines: [
        JSON.stringify({
          schema_version: 1,
          stream: 'task_runtime.execution',
          source: 'runtime.task_runtime',
          payload: {
            event_type: 'claimed',
            task_id: 'task-1',
            status: 'in_progress',
            subject: 'Execute L1-01 artifact materialization',
            claimed_by: 'director-1',
            timestamp: '2026-06-22T01:15:14.000000+00:00',
          },
          metadata: {
            task_id: 'task-1',
          },
        }),
      ],
    });

    expect(result.current.tasks).toHaveLength(1);
    expect(result.current.tasks[0]).toMatchObject({
      id: 'task-1',
      title: 'Execute L1-01 artifact materialization',
      status: TaskStatus.IN_PROGRESS,
      state: TaskStatus.IN_PROGRESS,
      done: false,
      completed: false,
      worker_id: 'director-1',
      started_at: '2026-06-22T01:15:14.000000+00:00',
    });
  });

  it('appends live event.factory lines into process stream events', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'event.factory',
      text: JSON.stringify({
        type: 'gate_evaluated',
        actor: 'factory-run',
        message: 'integration qa gate ok',
        gate: 'integration_qa',
        ok: true,
      }),
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('integration qa gate ok');
    expect(result.current.processStreamEvents[0]?.meta?.channel).toBe('event.factory');
    expect(result.current.processStreamEvents[0]?.meta?.streamEvent).toBe('gate_evaluated');
  });

  it('serializes object-valued process summaries instead of rendering [object Object]', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'event.factory',
      text: JSON.stringify({
        type: 'stage_progress',
        actor: 'factory-run',
        message: {
          run_id: 'factory-run-1',
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
      channel: 'system',
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

  it('routes runtime.v2 role LLM usage envelopes into the canonical llm stream', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'event',
      event: {
        schema_version: 'runtime.v2',
        channel: 'system',
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

    expect(result.current.llmStreamEvents).toHaveLength(1);
    const entry = result.current.llmStreamEvents[0];
    expect(entry?.message).toBe('llm.state');
    expect(entry?.meta?.promptTokens).toBe(2732);
    expect(entry?.meta?.completionTokens).toBe(1954);
    expect(entry?.meta?.contextTokens).toBe(2732);
    expect(entry?.meta?.durationMs).toBe(19178);
    expect(entry?.meta?.contextSnapshotRef).toBe('e3db3551d74e5741fd664b7b');
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
        kind: 'stage_started',
        ts: '2026-06-20T08:56:47.770284+00:00',
        payload: {
          type: 'stage_started',
          name: 'stage_started',
          actor: 'factory-run',
          message: 'Factory v2 live event visible',
        },
      },
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('Factory v2 live event visible');
    expect(result.current.processStreamEvents[0]?.source).toBe('factory-run');
    expect(result.current.processStreamEvents[0]?.meta?.channel).toBe('event.factory:run-42');
    expect(result.current.processStreamEvents[0]?.meta?.streamEvent).toBe('stage_started');
  });

  it('does not classify successful Factory stages containing error_code=none as errors', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'EVENT',
      protocol: 'runtime.v2',
      event: {
        schema_version: 'runtime.v2',
        event_id: 'factory-stage-success-1',
        workspace_key: 'test-workspace',
        run_id: 'factory-run-1',
        channel: 'event.factory:factory-run-1',
        kind: 'stage_completed',
        ts: '2026-08-11T23:14:16.530472+00:00',
        payload: {
          type: 'stage_completed',
          run_id: 'factory-run-1',
          stage: 'chief_engineer_review',
          message: 'Chief Engineer portfolio review generated 2/2 blueprints; error_code=none; root_cause_hint=none',
          result: {
            stage: 'chief_engineer_review',
            status: 'success',
          },
        },
      },
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.level).toBe('success');
    const telemetry = buildTelemetryFromStream([], [], result.current.processStreamEvents);
    expect(telemetry.events[0]?.category).not.toBe('error');
    expect(telemetry.errorCount).toBe(0);
  });

  it('keeps failed Factory stage results classified as errors', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'EVENT',
      protocol: 'runtime.v2',
      event: {
        schema_version: 'runtime.v2',
        event_id: 'factory-stage-failed-1',
        workspace_key: 'test-workspace',
        run_id: 'factory-run-1',
        channel: 'event.factory:factory-run-1',
        kind: 'stage_completed',
        ts: '2026-08-11T23:14:16.530472+00:00',
        payload: {
          type: 'stage_completed',
          run_id: 'factory-run-1',
          stage: 'director_dispatch',
          message: 'Director dispatch stopped',
          result: {
            stage: 'director_dispatch',
            status: 'failed',
          },
        },
      },
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.level).toBe('error');
    const telemetry = buildTelemetryFromStream([], [], result.current.processStreamEvents);
    expect(telemetry.events[0]?.category).toBe('error');
    expect(telemetry.errorCount).toBe(1);
  });

  it('merges runtime.v2 status.resident envelopes into snapshot.resident', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'EVENT',
      protocol: 'runtime.v2',
      cursor: 45,
      event: {
        schema_version: 'runtime.v2',
        event_id: 'resident-status-1',
        workspace_key: 'test-workspace',
        run_id: '',
        channel: 'status.resident',
        kind: 'resident_status_update',
        ts: '2026-06-25T08:00:00.000Z',
        payload: {
          action: 'resident_tick',
          resident: {
            workspace: '/test/workspace',
            runtime: {
              active: true,
              mode: 'propose',
              tick_count: 4,
            },
            agi_capability_surface: {
              role_id: 'resident_agi',
              runtime_foundation: 'roles.runtime + ContextOS + TurnEngine',
            },
          },
        },
      },
    });

    expect(result.current.snapshot?.resident?.workspace).toBe('/test/workspace');
    expect(result.current.snapshot?.resident?.runtime?.active).toBe(true);
    expect(result.current.snapshot?.resident?.runtime?.mode).toBe('propose');
    expect(result.current.snapshot?.resident?.agi_capability_surface?.role_id).toBe('resident_agi');
    expect(result.current.executionLogs).toHaveLength(0);
  });

  it('routes runtime.v2 event.bench envelopes into process stream events', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace', includeInternalBench: true })
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

  it('drops runtime.v2 event.bench envelopes when internal bench mode is disabled', () => {
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: '/test/workspace' })
    );

    emitRuntimeMessage({
      type: 'EVENT',
      protocol: 'runtime.v2',
      cursor: 44,
      event: {
        schema_version: 'runtime.v2',
        event_id: 'bench-live-disabled',
        workspace_key: 'test-workspace',
        run_id: 'bench-1',
        channel: 'event.bench:bench-1',
        kind: 'factory_bench.run.started',
        ts: '2026-06-20T08:57:47.770284+00:00',
        payload: {
          type: 'factory_bench.run.started',
          name: 'factory_bench.run.started',
          actor: 'factory-bench',
          summary: 'Bench v2 live event hidden',
        },
      },
    });

    expect(result.current.processStreamEvents).toHaveLength(0);
  });

  it('drops bench process events that belong to a different workspace', () => {
    const activeWorkspace = '/tmp/factory-bench-l1-10-r02/L1-10';
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: activeWorkspace, includeInternalBench: true })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'event.bench:bench-foreign',
      text: JSON.stringify({
        type: 'factory_bench.project.started',
        actor: 'factory-bench',
        summary: 'Foreign L1-05 event must not leak',
        meta: {
          project_id: 'L1-05',
          workspace: '/tmp/factory-bench-L1-05-r01/L1-05',
          project_workspace: '/tmp/factory-bench-L1-05-r01/L1-05',
        },
      }),
    });

    expect(result.current.processStreamEvents).toHaveLength(0);

    emitRuntimeMessage({
      type: 'line',
      channel: 'event.bench:bench-active',
      text: JSON.stringify({
        type: 'factory_bench.project.started',
        actor: 'factory-bench',
        summary: 'Active L1-10 event remains visible',
        meta: {
          project_id: 'L1-10',
          workspace_path: activeWorkspace,
        },
      }),
    });

    expect(result.current.processStreamEvents).toHaveLength(1);
    expect(result.current.processStreamEvents[0]?.message).toBe('Active L1-10 event remains visible');
  });

  it('drops dialogue snapshot rows that belong to a different workspace', () => {
    const activeWorkspace = '/tmp/factory-bench-l1-10-r02/L1-10';
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: activeWorkspace, includeInternalBench: true })
    );

    emitRuntimeMessage({
      type: 'snapshot',
      channel: 'dialogue',
      lines: [
        JSON.stringify({
          event_id: 'dialogue-foreign',
          speaker: 'Director',
          text: 'old L1-05 context',
          meta: {
            workspace_path: '/tmp/factory-bench-L1-05-r01/L1-05',
          },
        }),
        JSON.stringify({
          event_id: 'dialogue-active',
          speaker: 'Director',
          text: 'active L1-10 context',
          meta: {
            workspace: activeWorkspace,
          },
        }),
      ],
    });

    expect(result.current.dialogueEvents).toHaveLength(1);
    expect(result.current.dialogueEvents[0]?.eventId).toBe('dialogue-active');
    expect(result.current.dialogueEvents[0]?.content).toBe('active L1-10 context');
  });

  it('drops LLM context snapshot refs that belong to a different workspace', () => {
    const activeWorkspace = '/tmp/factory-bench-l1-10-r02/L1-10';
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: activeWorkspace, includeInternalBench: true })
    );

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        schema_version: 2,
        channel: 'llm',
        domain: 'llm',
        workspace: '/tmp/factory-bench-L1-05-r01/L1-05',
        actor: 'director',
        raw: {
          stream_event: 'llm_completed',
          data: {
            model: 'qwen3.6-27b',
            prompt_tokens: 128,
            completion_tokens: 64,
            metadata: {
              context_snapshot_ref: '99d3de73eedeba4206d0dce2',
            },
          },
        },
      }),
    });

    expect(result.current.llmStreamEvents).toHaveLength(0);

    emitRuntimeMessage({
      type: 'line',
      channel: 'llm',
      text: JSON.stringify({
        schema_version: 2,
        channel: 'llm',
        domain: 'llm',
        project_workspace: activeWorkspace,
        actor: 'director',
        raw: {
          stream_event: 'llm_completed',
          data: {
            model: 'qwen3.6-27b',
            prompt_tokens: 128,
            completion_tokens: 64,
            metadata: {
              context_snapshot_ref: 'f0d7634bde21b6fdd3fdfa03',
            },
          },
        },
      }),
    });

    expect(result.current.llmStreamEvents).toHaveLength(1);
    expect(result.current.llmStreamEvents[0]?.meta?.contextSnapshotRef).toBe('f0d7634bde21b6fdd3fdfa03');
  });

  it('hydrates completed PM and Chief Engineer LLM history from websocket snapshots', () => {
    const activeWorkspace = '/tmp/factory-bench-l1-10-r02/L1-10';
    const { result } = renderHook(() =>
      useRuntime({ autoConnect: false, workspace: activeWorkspace, includeInternalBench: true, tailLines: 240 })
    );

    emitRuntimeMessage({
      type: 'snapshot',
      channel: 'llm',
      lines: [
        JSON.stringify({
          schema_version: 2,
          channel: 'llm',
          domain: 'llm',
          workspace: '/tmp/factory-bench-L1-05-r01/L1-05',
          actor: 'pm',
          raw: {
            stream_event: 'llm_completed',
            data: {
              model: 'kimi-for-coding',
              prompt_tokens: 500,
              completion_tokens: 100,
              context_tokens_after: 600,
              metadata: {
                elapsed_ms: 9000,
                context_snapshot_ref: '999999999999999999999999',
              },
            },
          },
        }),
        JSON.stringify({
          schema_version: 2,
          channel: 'llm',
          domain: 'llm',
          workspace: activeWorkspace,
          actor: 'pm',
          refs: {
            context_snapshot_ref: 'aaaaaaaaaaaaaaaaaaaaaaaa',
          },
          raw: {
            stream_event: 'llm_completed',
            data: {
              model: 'kimi-for-coding',
              prompt_tokens: 2300,
              completion_tokens: 700,
              context_tokens_after: 3000,
              metadata: {
                elapsed_ms: 17000,
              },
            },
          },
        }),
        JSON.stringify({
          schema_version: 2,
          channel: 'llm',
          domain: 'llm',
          project_workspace: activeWorkspace,
          actor: 'chief_engineer',
          refs: {
            context_snapshot_ref: 'bbbbbbbbbbbbbbbbbbbbbbbb',
          },
          raw: {
            stream_event: 'llm_completed',
            data: {
              model: 'kimi-for-coding',
              prompt_tokens: 4100,
              completion_tokens: 900,
              context_tokens_after: 5000,
              metadata: {
                elapsed_ms: 31000,
              },
            },
          },
        }),
      ],
    });

    expect(result.current.llmStreamEvents).toHaveLength(2);
    expect(result.current.llmStreamEvents.map((entry) => entry.source)).toEqual([
      'PM',
      'chief_engineer',
    ]);
    expect(result.current.llmStreamEvents.map((entry) => entry.meta?.contextSnapshotRef)).toEqual([
      'aaaaaaaaaaaaaaaaaaaaaaaa',
      'bbbbbbbbbbbbbbbbbbbbbbbb',
    ]);
    expect(result.current.llmStreamEvents.map((entry) => entry.meta?.contextTokens)).toEqual([
      3000,
      5000,
    ]);
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
        refs: {
          call_id: 'call-context-1',
          context_snapshot_ref: 'a1b2c3d4e5f6a7b8c9d0e1f2',
        },
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
            metadata: {
              elapsed_ms: 71431.06,
              final_request_context_audit: {
                final_request_token_estimate: 4096,
                tool_schema_token_estimate: 1200,
              },
              context_os_audit: {
                state_first_context_os: { projected: true },
              },
            },
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
    expect(entry?.meta?.contextSnapshotRef).toBe('a1b2c3d4e5f6a7b8c9d0e1f2');
    expect(entry?.meta?.callId).toBe('call-context-1');
    expect(entry?.meta?.finalRequestContextAudit).toMatchObject({
      final_request_token_estimate: 4096,
      tool_schema_token_estimate: 1200,
    });
    expect(entry?.meta?.contextOSAudit).toMatchObject({
      state_first_context_os: { projected: true },
    });
    // 真实时延来自 raw.data.metadata.elapsed_ms（四舍五入）。
    expect(entry?.meta?.durationMs).toBe(71431);
    expect(entry?.details).toContain('71431ms');
    expect(entry?.details).toContain('completion=1454');

    const telemetry = buildTelemetryFromStream(result.current.llmStreamEvents, [], []);
    expect(telemetry.projectionCount).toBe(1);
    expect(telemetry.contextTokensLatest).toBe(4096);
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
    expect(entry?.meta?.contextSnapshotRef).toBeUndefined();
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

  it('populates file edit events from schema-tagged runtime v2 messages', () => {
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
      source: 'runtime_v2_jetstream',
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
      provenance: 'runtime_v2_jetstream',
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
      type: 'line',
      channel: 'system',
      text: JSON.stringify({
        name: 'director_task_started',
        actor: 'Director',
        ts: '2026-06-19T12:00:00.000Z',
        data: {
          task_id: 'TASK-2',
          task_title: 'Implement checkout route',
          worker_id: 'worker-1',
        },
      }),
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

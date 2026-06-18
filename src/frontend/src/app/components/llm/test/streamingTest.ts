import { getBackendInfo } from '../../../../api';
import { runtimeSocketManager } from '@/runtime/transport';
import type { TestEvent } from '../test/types';

interface StreamTestOptions {
  role?: string | null;
  providerId: string;
  model: string;
  testRunId?: string;
  suites?: string[];
  testLevel?: string;
  evaluationMode?: string;
  apiKey?: string | null;
  envOverrides?: Record<string, string>;
  promptOverride?: string;
  onEvent?: (event: TestEvent) => void;
  onSuiteStart?: (suite: string) => void;
  onSuiteComplete?: (suite: string, ok: boolean) => void;
  onComplete?: (report: TestReport) => void;
  onError?: (error: string) => void;
  signal?: AbortSignal;
  // Scheme B: direct config fields for connectivity-only tests
  providerType?: string;
  baseUrl?: string;
  apiPath?: string;
  timeout?: number;
}

interface TestReport {
  schema_version: number;
  test_run_id: string;
  timestamp?: string;
  target: {
    role: string;
    provider_id: string;
    model: string;
  };
  suites: Record<string, unknown>;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    estimated?: boolean;
  };
  final: {
    ready: boolean;
    grade: string;
    next_action?: string;
  };
  [key: string]: unknown;
}

interface StreamProtocolEvent {
  type: string;
  data: Record<string, unknown>;
}

interface JetstreamTestStartResponse {
  ok?: boolean;
  test_run_id?: string;
  status?: string;
  channel?: string;
  subject?: string;
  transport?: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function createClientRunId(prefix: string): string {
  const randomId =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2, 12);
  return `${prefix}-${randomId}`;
}

function normalizeRunId(value: string | null | undefined, prefix: string): string {
  const raw = String(value || '').trim() || createClientRunId(prefix);
  const safe = raw.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^[._-]+|[._-]+$/g, '');
  return safe.slice(0, 96) || createClientRunId(prefix);
}

function streamEventFromRuntimeMessage(message: unknown): StreamProtocolEvent | null {
  const msg = asRecord(message);
  const event = msg.type === 'EVENT' ? asRecord(msg.event) : msg;
  const payload = asRecord(event.payload);
  const eventType = typeof payload.type === 'string' ? payload.type : '';
  if (!eventType) return null;
  return {
    type: eventType,
    data: asRecord(payload.data),
  };
}

export async function runStreamingTest(options: StreamTestOptions): Promise<TestReport | null> {
  const {
    role,
    providerId,
    model,
    testRunId,
    suites = ['connectivity', 'response'],
    testLevel = 'quick',
    evaluationMode = 'provider',
    apiKey,
    envOverrides,
    promptOverride,
    onEvent,
    onSuiteStart,
    onSuiteComplete,
    onComplete,
    onError,
    signal,
    providerType,
    baseUrl,
    apiPath,
    timeout,
  } = options;

  const emitEvent = (type: TestEvent['type'], content: string, details?: unknown) => {
    onEvent?.({ type, timestamp: new Date().toISOString(), content, details });
  };

  emitEvent('command', `Starting test for ${providerId}`);
  const runId = normalizeRunId(testRunId, 'llm-test');
  const channel = `llm-test:${runId}`;
  let unsubscribeListener: (() => void) | null = null;
  let settled = false;

  const cleanup = () => {
    unsubscribeListener?.();
    unsubscribeListener = null;
    runtimeSocketManager.unsubscribeChannels([channel]);
  };

  try {
    if (signal?.aborted) {
      emitEvent('error', '测试已取消');
      return null;
    }

    const backendInfo = await getBackendInfo();
    if (!backendInfo.baseUrl) {
      throw new Error('Backend baseUrl missing');
    }

    const resultPromise = new Promise<TestReport | null>((resolve) => {
      const finish = (result: TestReport | null) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(result);
      };

      const handleStreamEvent = (streamEvent: StreamProtocolEvent): TestReport | null | undefined => {
        const { type, data } = streamEvent;

        switch (type) {
          case 'start':
            {
              const activeRunId =
                typeof data.test_run_id === 'string' && data.test_run_id.trim()
                  ? data.test_run_id.trim()
                  : typeof data.run_id === 'string' && data.run_id.trim()
                    ? data.run_id.trim()
                    : '';
              emitEvent('stdout', activeRunId ? `测试开始: ${activeRunId}` : '测试开始', data);
            }
            break;

          case 'suite_start':
            if (typeof data.suite === 'string') {
              onSuiteStart?.(data.suite);
              emitEvent('stdout', `开始测试套件: ${data.suite}`);
            }
            break;

          case 'suite_result':
          case 'suite_complete':
            if (typeof data.suite === 'string' && data.result && typeof data.result === 'object') {
              const result = data.result as { ok?: boolean | string; cases?: Array<{ ok: boolean; reason?: string; id?: string }>; error?: unknown };
              const ok = result.ok === true || result.ok === 'true';
              onSuiteComplete?.(data.suite, ok);
              emitEvent(ok ? 'result' : 'error', `测试套件 ${data.suite}: ${ok ? '通过' : '失败'}`, data);

              if (!ok) {
                if (Array.isArray(result.cases)) {
                  const failures = result.cases.filter((c) => !c.ok);
                  failures.forEach((f) => {
                    emitEvent('stderr', `  [${f.id}] ${f.reason || 'Verification failed'}`);
                  });
                } else if (result.error !== undefined) {
                  emitEvent('stderr', `  Reason: ${String(result.error)}`);
                }
              }
            }
            break;

          case 'suite_error':
            emitEvent('error', `测试套件错误: ${String(data.error || '未知错误')}`, data);
            break;

          case 'complete':
            emitEvent('stdout', '测试完成');
            onComplete?.(data as unknown as TestReport);
            return data as unknown as TestReport;

          case 'error':
            emitEvent('error', String(data.error || '未知错误'));
            onError?.(String(data.error || 'Unknown error'));
            return null;

          case 'ping':
            break;

          default:
            emitEvent('stdout', `[${type}] ${JSON.stringify(data)}`);
        }
        return undefined;
      };

      unsubscribeListener = runtimeSocketManager.registerMessageListener({
        id: `llm-test-${runId}`,
        channel,
        handler: (message) => {
          const streamEvent = streamEventFromRuntimeMessage(message);
          if (!streamEvent) return;
          const result = handleStreamEvent(streamEvent);
          if (result !== undefined) {
            finish(result);
          }
        },
      });
      runtimeSocketManager.subscribeChannels([{ channel, tailLines: 0 }]);
      if (!runtimeSocketManager.getState().connected) {
        runtimeSocketManager.start();
        runtimeSocketManager.reconnect();
      }
      signal?.addEventListener(
        'abort',
        () => {
          emitEvent('error', '测试已取消');
          finish(null);
        },
        { once: true },
      );
    });

    // Build request body
    const requestBody: Record<string, unknown> = {
      role: role || 'connectivity',
      provider_id: providerId,
      model,
      test_run_id: runId,
      suites,
      test_level: testLevel,
      evaluation_mode: evaluationMode,
      api_key: apiKey,
      env_overrides: envOverrides,
      prompt_override: promptOverride,
    };

    // Scheme B: Add direct config fields for connectivity-only tests
    if (baseUrl) {
      requestBody.base_url = baseUrl;
    }
    if (providerType) {
      requestBody.provider_type = providerType;
    }
    if (apiPath) {
      requestBody.api_path = apiPath;
    }
    if (timeout !== undefined) {
      requestBody.timeout = timeout;
    }

    const response = await fetch(`${backendInfo.baseUrl}/v2/llm/test/jetstream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(backendInfo.token ? { Authorization: `Bearer ${backendInfo.token}` } : {}),
      },
      body: JSON.stringify(requestBody),
      signal,
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `HTTP ${response.status}`);
    }

    const startResponse = (await response.json()) as JetstreamTestStartResponse;
    if (startResponse.ok === false) {
      throw new Error('Failed to start LLM test stream');
    }

    emitEvent('stdout', '发送测试请求...');
    return await resultPromise;
  } catch (error) {
    cleanup();
    if (error instanceof Error && error.name === 'AbortError') {
      emitEvent('error', '测试已取消');
      return null;
    }

    const message = error instanceof Error ? error.message : '测试失败';
    emitEvent('error', message);
    onError?.(message);
    return null;
  }
}

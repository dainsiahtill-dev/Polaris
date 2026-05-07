import { getBackendInfo } from '../../../../api';
import type { TestEvent } from '../test/types';

interface StreamTestOptions {
  role?: string | null;
  providerId: string;
  model: string;
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

export async function runStreamingTest(options: StreamTestOptions): Promise<TestReport | null> {
  const {
    role,
    providerId,
    model,
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

  try {
    const backendInfo = await getBackendInfo();
    if (!backendInfo.baseUrl) {
      throw new Error('Backend baseUrl missing');
    }

    // Build request body
    const requestBody: Record<string, unknown> = {
      role: role || 'connectivity',
      provider_id: providerId,
      model,
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

    const response = await fetch(`${backendInfo.baseUrl}/v2/llm/test/stream`, {
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

    if (!response.body) {
      throw new Error('No response body');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    emitEvent('stdout', '发送测试请求...');

    while (true) {
      const { done, value } = await reader.read();

      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let currentEvent: string | null = null;
      let currentData = '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7);
        } else if (line.startsWith('data: ')) {
          currentData = line.slice(6);
        } else if (line === '' && currentEvent) {
          try {
            const data = JSON.parse(currentData);

            switch (currentEvent) {
              case 'start':
                {
                  const runId =
                    typeof data.test_run_id === 'string' && data.test_run_id.trim()
                      ? data.test_run_id.trim()
                      : typeof data.run_id === 'string' && data.run_id.trim()
                        ? data.run_id.trim()
                        : '';
                  emitEvent('stdout', runId ? `测试开始: ${runId}` : '测试开始', data);
                }
                break;

              case 'suite_start':
                if (data.suite) {
                  onSuiteStart?.(data.suite);
                  emitEvent('stdout', `开始测试套件: ${data.suite}`);
                }
                break;

              case 'suite_result':
                if (data.suite && data.result) {
                  const ok = data.result.ok === true || data.result.ok === 'true';
                  onSuiteComplete?.(data.suite, ok);
                  emitEvent(ok ? 'result' : 'error', `测试套件 ${data.suite}: ${ok ? '通过' : '失败'}`, data);
                  
                  if (!ok && data.result) {
                    const result = data.result as { cases?: Array<{ ok: boolean; reason?: string; id?: string }> };
                    if (Array.isArray(result.cases)) {
                      const failures = result.cases.filter(c => !c.ok);
                      failures.forEach(f => {
                        emitEvent('stderr', `  [${f.id}] ${f.reason || 'Verification failed'}`);
                      });
                    } else if (typeof data.result === 'object' && data.result !== null && 'error' in data.result) {
                         emitEvent('stderr', `  Reason: ${String((data.result as { error?: unknown }).error)}`);
                    }
                  }
                }
                break;

              case 'suite_error':
                emitEvent('error', `测试套件错误: ${data.error || '未知错误'}`, data);
                break;

              case 'complete':
                emitEvent('stdout', '测试完成');
                onComplete?.(data as TestReport);
                return data as TestReport;

              case 'error':
                emitEvent('error', data.error || '未知错误');
                onError?.(data.error || 'Unknown error');
                return null;

              case 'ping':
                break;

              default:
                emitEvent('stdout', `[${currentEvent}] ${JSON.stringify(data)}`);
            }
          } catch {
            // Ignore invalid JSON
          }

          currentEvent = null;
          currentData = '';
        }
      }
    }

    return null;
  } catch (error) {
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

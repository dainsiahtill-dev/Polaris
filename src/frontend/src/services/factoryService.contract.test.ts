import { beforeEach, describe, expect, it, vi } from 'vitest';

const getBackendInfoMock = vi.fn();
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();

vi.mock('@/api', () => ({
  getBackendInfo: (...args: unknown[]) => getBackendInfoMock(...args),
}));

vi.mock('./apiClient', () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
  buildQueryString: () => '',
}));

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (event: MessageEvent) => void>();
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(name: string, handler: (event: MessageEvent) => void) {
    this.listeners.set(name, handler);
  }

  emit(name: string, payload: unknown) {
    const handler = this.listeners.get(name);
    if (!handler) {
      return;
    }
    handler({ data: JSON.stringify(payload) } as MessageEvent);
  }
}

vi.stubGlobal('EventSource', MockEventSource as unknown as typeof EventSource);

import { connectFactoryStream } from './factoryService';

describe('factoryService contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    MockEventSource.instances = [];
    getBackendInfoMock.mockResolvedValue({
      baseUrl: 'http://127.0.0.1:49977',
      token: 'token-123',
    });
  });

  it('connects to the canonical Factory SSE endpoint with token query auth', async () => {
    await connectFactoryStream('run-1', {});

    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toBe(
      'http://127.0.0.1:49977/v2/factory/runs/run-1/stream?token=token-123'
    );
  });

  it('parses canonical status, event and done payloads', async () => {
    const onStatus = vi.fn();
    const onEvent = vi.fn();
    const onDone = vi.fn();

    await connectFactoryStream('run-2', { onStatus, onEvent, onDone });
    const stream = MockEventSource.instances[0];

    stream.emit('status', {
      run_id: 'run-2',
      phase: 'planning',
      status: 'running',
      current_stage: 'pm_planning',
      last_successful_stage: null,
      progress: 30,
      roles: {},
      gates: [],
      created_at: '2026-03-07T00:00:00Z',
    });
    stream.emit('event', {
      type: 'stage_started',
      stage: 'pm_planning',
      timestamp: '2026-03-07T00:00:01Z',
    });
    stream.emit('done', {
      run_id: 'run-2',
      phase: 'completed',
      status: 'completed',
      current_stage: 'quality_gate',
      last_successful_stage: 'quality_gate',
      progress: 100,
      roles: {},
      gates: [],
      created_at: '2026-03-07T00:00:00Z',
    });

    expect(onStatus).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'run-2', status: 'running' }));
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'stage_started', stage: 'pm_planning' }));
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'run-2', status: 'completed' }));
  });
});

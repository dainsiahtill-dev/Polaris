import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeSocketManager } from './runtimeSocketManager';

const mockConnectWebSocket = vi.hoisted(() => vi.fn());

vi.mock('@/api', () => ({
  connectWebSocket: mockConnectWebSocket,
}));

vi.mock('@/app/utils/devLogger', () => ({
  devLogger: {
    error: vi.fn(),
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
  },
}));

type MockSocket = {
  readyState: number;
  send: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
};

function createMockSocket(): MockSocket {
  const socket: MockSocket = {
    readyState: WebSocket.CONNECTING,
    send: vi.fn(),
    close: vi.fn(),
    onopen: null,
    onmessage: null,
    onclose: null,
    onerror: null,
  };
  socket.close.mockImplementation(() => {
    socket.readyState = WebSocket.CLOSED;
  });
  return socket;
}

function parseSentMessages(socket: MockSocket): Record<string, unknown>[] {
  return socket.send.mock.calls.map(([payload]) => JSON.parse(String(payload)));
}

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

let manager: RuntimeSocketManager;
let socket: MockSocket;

describe('runtimeSocketManager unsubscribe behavior', () => {
  beforeEach(async () => {
    vi.resetModules();
    socket = createMockSocket();
    mockConnectWebSocket.mockReset();
    mockConnectWebSocket.mockResolvedValue(socket as unknown as WebSocket);

    const runtimeModule = await import('./runtimeSocketManager');
    manager = runtimeModule.runtimeSocketManager;

    manager.start();
    await flushMicrotasks();
    socket.readyState = WebSocket.OPEN;
    socket.onopen?.(new Event('open'));
  });

  afterEach(() => {
    manager.close();
    vi.clearAllMocks();
  });

  it('does not send UNSUBSCRIBE when only part of ref-count is released', () => {
    manager.subscribeChannels([{ channel: 'llm' }, { channel: 'llm' }]);
    socket.send.mockClear();

    manager.unsubscribeChannels(['llm']);

    expect(socket.send).not.toHaveBeenCalled();
  });

  it('sends runtime.v2 UNSUBSCRIBE when ref-count reaches zero', () => {
    manager.subscribeChannels([{ channel: 'llm' }, { channel: 'llm' }, { channel: 'process' }]);
    socket.send.mockClear();

    manager.unsubscribeChannels(['llm']);
    manager.unsubscribeChannels(['llm', 'process']);

    const sentMessages = parseSentMessages(socket);
    expect(sentMessages).toEqual([
      {
        type: 'UNSUBSCRIBE',
        protocol: 'runtime.v2',
        channels: ['llm', 'process'],
      },
    ]);
  });

  it('does not send UNSUBSCRIBE when connection is closed', () => {
    manager.subscribeChannels([{ channel: 'llm' }]);
    socket.send.mockClear();

    manager.close();
    manager.unsubscribeChannels(['llm']);

    expect(socket.send).not.toHaveBeenCalled();
  });

  it('updates internal subscribed roles when sending runtime.v2 SUBSCRIBE command', () => {
    manager.subscribeChannels([{ channel: 'llm' }], ['pm']);

    manager.send({
      type: 'SUBSCRIBE',
      protocol: 'runtime.v2',
      roles: ['director'],
      channels: ['llm'],
      tail: 100,
      cursor: 0,
    });

    expect((manager as unknown as { subscribedRoles: string[] }).subscribedRoles).toEqual(['director']);
  });

  it('clears internal subscribed roles when runtime.v2 SUBSCRIBE explicitly carries roles=[]', () => {
    manager.subscribeChannels([{ channel: 'llm' }], ['pm']);

    manager.send({
      type: 'SUBSCRIBE',
      protocol: 'runtime.v2',
      roles: [],
      channels: ['llm'],
      tail: 100,
      cursor: 0,
    });

    expect((manager as unknown as { subscribedRoles: string[] }).subscribedRoles).toEqual([]);
  });

  it('keeps explicit roles=[] semantics on resubscribe', () => {
    manager.subscribeChannels([{ channel: 'llm' }], ['director']);
    manager.send({
      type: 'SUBSCRIBE',
      protocol: 'runtime.v2',
      roles: [],
      channels: ['llm'],
      tail: 100,
      cursor: 0,
    });
    socket.send.mockClear();

    (manager as unknown as { sendSubscribe: () => void }).sendSubscribe();

    const sentMessages = parseSentMessages(socket);
    expect(sentMessages).toEqual([
      {
        type: 'SUBSCRIBE',
        protocol: 'runtime.v2',
        channels: ['llm'],
        tail: 0,
        cursor: 0,
        roles: [],
      },
    ]);
  });
});

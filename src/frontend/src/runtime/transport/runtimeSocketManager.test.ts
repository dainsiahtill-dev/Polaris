import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeSocketManager } from "./runtimeSocketManager";

const mockConnectWebSocket = vi.hoisted(() => vi.fn());

vi.mock("@/api", () => ({
  connectWebSocket: mockConnectWebSocket,
}));

vi.mock("@/app/utils/devLogger", () => ({
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

describe("runtimeSocketManager unsubscribe behavior", () => {
  beforeEach(async () => {
    vi.resetModules();
    socket = createMockSocket();
    mockConnectWebSocket.mockReset();
    mockConnectWebSocket.mockResolvedValue(socket as unknown as WebSocket);

    const runtimeModule = await import("./runtimeSocketManager");
    manager = runtimeModule.runtimeSocketManager;

    manager.start();
    await flushMicrotasks();
    socket.readyState = WebSocket.OPEN;
    socket.onopen?.(new Event("open"));
  });

  afterEach(() => {
    manager.close();
    vi.clearAllMocks();
  });

  it("does not send UNSUBSCRIBE when only part of ref-count is released", () => {
    manager.subscribeChannels([{ channel: "llm" }, { channel: "llm" }]);
    socket.send.mockClear();

    manager.unsubscribeChannels(["llm"]);

    expect(socket.send).not.toHaveBeenCalled();
  });

  it("keeps shared runtime.v2 service subscriptions monotonic when ref-count reaches zero", () => {
    manager.subscribeChannels([
      { channel: "llm" },
      { channel: "llm" },
      { channel: "process" },
    ]);
    socket.send.mockClear();

    manager.unsubscribeChannels(["llm"]);
    manager.unsubscribeChannels(["llm", "process"]);

    const sentMessages = parseSentMessages(socket);
    expect(sentMessages).toEqual([]);
  });

  it("does not send UNSUBSCRIBE when connection is closed", () => {
    manager.subscribeChannels([{ channel: "llm" }]);
    socket.send.mockClear();

    manager.close();
    manager.unsubscribeChannels(["llm"]);

    expect(socket.send).not.toHaveBeenCalled();
  });

  it("updates internal subscribed roles when sending runtime.v2 SUBSCRIBE command", () => {
    manager.subscribeChannels([{ channel: "llm" }], ["pm"]);

    manager.send({
      type: "SUBSCRIBE",
      protocol: "runtime.v2",
      roles: ["director", "chief_engineer", "resident_agi", "unknown"],
      channels: ["llm"],
      tail: 100,
      cursor: 0,
    });

    expect(
      (manager as unknown as { subscribedRoles: string[] }).subscribedRoles,
    ).toEqual(["director", "chief_engineer", "resident_agi"]);
  });

  it("clears internal subscribed roles when runtime.v2 SUBSCRIBE explicitly carries roles=[]", () => {
    manager.subscribeChannels([{ channel: "llm" }], ["pm"]);

    manager.send({
      type: "SUBSCRIBE",
      protocol: "runtime.v2",
      roles: [],
      channels: ["llm"],
      tail: 100,
      cursor: 0,
    });

    expect(
      (manager as unknown as { subscribedRoles: string[] }).subscribedRoles,
    ).toEqual([]);
  });

  it("keeps explicit roles=[] semantics on resubscribe", () => {
    manager.subscribeChannels([{ channel: "llm" }], ["director"]);
    manager.send({
      type: "SUBSCRIBE",
      protocol: "runtime.v2",
      roles: [],
      channels: ["llm"],
      tail: 100,
      cursor: 0,
    });
    socket.send.mockClear();

    (manager as unknown as { sendSubscribe: () => void }).sendSubscribe();

    const sentMessages = parseSentMessages(socket);
    expect(sentMessages).toEqual([
      {
        type: "SUBSCRIBE",
        protocol: "runtime.v2",
        channels: ["llm"],
        tail: 0,
        cursor: 0,
        roles: [],
      },
    ]);
  });
});

describe("runtimeSocketManager fast-open behavior", () => {
  beforeEach(async () => {
    vi.resetModules();
    socket = createMockSocket();
    socket.readyState = WebSocket.OPEN;
    mockConnectWebSocket.mockReset();
    mockConnectWebSocket.mockResolvedValue(socket as unknown as WebSocket);

    const runtimeModule = await import("./runtimeSocketManager");
    manager = runtimeModule.runtimeSocketManager;
  });

  afterEach(() => {
    manager.close();
    vi.clearAllMocks();
  });

  it("subscribes when the WebSocket is already open before handlers are attached", async () => {
    manager.subscribeChannels([{ channel: "runtime_events" }], ["pm"]);

    manager.start();
    await flushMicrotasks();

    expect(manager.getState().connected).toBe(true);
    const sentMessages = parseSentMessages(socket);
    expect(sentMessages).toContainEqual({
      type: "SUBSCRIBE",
      protocol: "runtime.v2",
      channels: ["runtime_events"],
      tail: 0,
      cursor: 0,
      roles: ["pm"],
    });
  });
});

describe("runtimeSocketManager connection coalescing", () => {
  beforeEach(async () => {
    vi.resetModules();
    socket = createMockSocket();
    mockConnectWebSocket.mockReset();

    const runtimeModule = await import("./runtimeSocketManager");
    manager = runtimeModule.runtimeSocketManager;
  });

  afterEach(() => {
    manager.close();
    vi.clearAllMocks();
  });

  it("does not create a second WebSocket while the first connection is still pending", async () => {
    let resolveSocket: ((value: WebSocket) => void) | null = null;
    mockConnectWebSocket.mockReturnValue(
      new Promise<WebSocket>((resolve) => {
        resolveSocket = resolve;
      }),
    );

    manager.start();
    manager.start();

    expect(mockConnectWebSocket).toHaveBeenCalledTimes(1);

    resolveSocket?.(socket as unknown as WebSocket);
    await flushMicrotasks();

    socket.readyState = WebSocket.OPEN;
    socket.onopen?.(new Event("open"));
    expect(manager.getState().connected).toBe(true);
  });
});

describe("runtimeSocketManager policy rejection", () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    vi.resetModules();
    socket = createMockSocket();
    mockConnectWebSocket.mockReset();
    mockConnectWebSocket.mockResolvedValue(socket as unknown as WebSocket);

    const runtimeModule = await import("./runtimeSocketManager");
    manager = runtimeModule.runtimeSocketManager;

    manager.start();
    await flushMicrotasks();
    socket.readyState = WebSocket.OPEN;
    socket.onopen?.(new Event("open"));
  });

  afterEach(() => {
    manager.close();
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("does not reconnect after the backend rejects the instance binding", () => {
    socket.onclose?.({ code: 1008 } as CloseEvent);
    vi.runAllTimers();

    expect(mockConnectWebSocket).toHaveBeenCalledTimes(1);
    expect(manager.getState()).toMatchObject({
      connected: false,
      reconnecting: false,
      attemptCount: 0,
      error: "Runtime connection rejected by instance policy (1008)",
    });
  });
});

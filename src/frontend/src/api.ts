export type BackendInfo = {
  port: number | null;
  token: string | null;
  baseUrl: string | null;
  pid: number | null;
};

type DevBackendInfo = {
  baseUrl?: string;
  token?: string;
};

type WindowWithDevBackend = Window & {
  __DEV_BACKEND__?: DevBackendInfo;
};

// ═══════════════════════════════════════════════════════════════════════════════
// Default Configuration Constants
// ═══════════════════════════════════════════════════════════════════════════════
const DEFAULT_BACKEND_PORT = 49977;
const DEFAULT_BACKEND_HOST = "127.0.0.1";

let cachedInfo: BackendInfo | null = null;

function getDefaultBackendUrl(): string {
  // Allow port override via environment variable in development
  const port = import.meta.env.VITE_BACKEND_PORT || DEFAULT_BACKEND_PORT;
  const host = import.meta.env.VITE_BACKEND_HOST || DEFAULT_BACKEND_HOST;
  return `http://${host}:${port}`;
}

export async function getBackendInfo(): Promise<BackendInfo> {
  if (cachedInfo) {
    return cachedInfo;
  }
  if (!window.polaris?.getBackendInfo) {
    const devBackend = (window as WindowWithDevBackend).__DEV_BACKEND__;
    const fallbackBase =
      devBackend?.baseUrl ||
      localStorage.getItem("polaris.baseUrl") ||
      getDefaultBackendUrl();
    const fallbackToken =
      devBackend?.token ||
      localStorage.getItem("polaris.token") ||
      null;
    const info: BackendInfo = {
      port: null,
      token: fallbackToken,
      baseUrl: fallbackBase,
      pid: null,
    };
    cachedInfo = info;
    return info;
  }
  const info = await window.polaris.getBackendInfo();
  cachedInfo = info;
  return info;
}

function clearBackendInfoCache() {
  cachedInfo = null;
}

export async function pickWorkspace(defaultPath?: string): Promise<string | null> {
  if (!window.polaris?.pickWorkspace) {
    throw new Error("Electron preload not available.");
  }
  return window.polaris.pickWorkspace({ defaultPath });
}

export async function openPath(targetPath: string): Promise<{ ok: boolean; error?: string | null }> {
  if (!window.polaris?.openPath) {
    throw new Error("Electron preload not available.");
  }
  return window.polaris.openPath(targetPath);
}

const isViteDevMode = typeof window !== 'undefined' && (window as unknown as { __DEV_BACKEND__?: unknown }).__DEV_BACKEND__ !== undefined;

const DEFAULT_TIMEOUT_MS = 30000;

export async function apiFetch(
  path: string,
  init: RequestInit & { timeout?: number } = {}
): Promise<Response> {
  const { timeout = DEFAULT_TIMEOUT_MS, ...fetchOptions } = init;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  const doFetch = async (info: BackendInfo): Promise<Response> => {
    const headers = new Headers(fetchOptions.headers || {});
    if (info.token) {
      headers.set("Authorization", `Bearer ${info.token}`);
    }
    const url = (isViteDevMode && !info.baseUrl) ? path : `${info.baseUrl}${path}`;
    return fetch(url, { ...fetchOptions, headers, signal: controller.signal });
  };

  try {
    let info = await getBackendInfo();
    try {
      const res = await doFetch(info);
      if (res.status === 401) {
        clearBackendInfoCache();
        info = await getBackendInfo();
        return await doFetch(info);
      }
      return res;
    } catch (err) {
      clearBackendInfoCache();
      info = await getBackendInfo();
      return await doFetch(info);
    }
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function apiFetchFresh(path: string, init: RequestInit = {}) {
  clearBackendInfoCache();
  return apiFetch(path, init);
}

export async function connectWebSocket(forceRefresh = false): Promise<WebSocket> {
  if (forceRefresh) {
    clearBackendInfoCache();
  }
  let info = await getBackendInfo();
  if (!info.baseUrl) {
    clearBackendInfoCache();
    info = await getBackendInfo();
  }
  if (!info.baseUrl) {
    throw new Error("Backend baseUrl missing.");
  }
  const wsUrl =
    info.baseUrl.replace(/^http/, "ws") +
    `/v2/ws/runtime?token=${encodeURIComponent(info.token || "")}`;
  return new WebSocket(wsUrl);
}

export interface ReliableWebSocketOptions {
  /** Channels to auto-subscribe on (re)connect */
  channels?: string[];
  /** Tail lines to request on subscribe */
  tailLines?: number;
  /** Max reconnect attempts (default: Infinity) */
  maxRetries?: number;
  /** Base delay in ms for exponential backoff (default: 1000) */
  baseDelay?: number;
  /** Max delay cap in ms (default: 30000) */
  maxDelay?: number;
  /** Callback when connection opens */
  onOpen?: () => void;
  /** Callback when a message arrives */
  onMessage?: (event: MessageEvent) => void;
  /** Callback when connection closes (before reconnect attempt) */
  onClose?: () => void;
  /** Callback when reconnecting (with attempt number) */
  onReconnecting?: (attempt: number) => void;
  /** Callback when all retries exhausted */
  onFailed?: () => void;
}

export interface ReliableWebSocket {
  /** Send data through the WebSocket */
  send: (data: string) => void;
  /** Close the connection permanently (no reconnect) */
  close: () => void;
  /** Get current WebSocket readyState */
  readonly readyState: number;
}

export function connectReliableWebSocket(
  options: ReliableWebSocketOptions = {},
): ReliableWebSocket {
  const {
    channels = [],
    tailLines = 0,
    maxRetries = Infinity,
    baseDelay = 1000,
    maxDelay = 30_000,
    onOpen,
    onMessage,
    onClose,
    onReconnecting,
    onFailed,
  } = options;

  let ws: WebSocket | null = null;
  let attempt = 0;
  let closed = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function subscribe(socket: WebSocket) {
    if (channels.length === 0) return;
    socket.send(
      JSON.stringify({ type: "subscribe", channels, tail_lines: tailLines }),
    );
  }

  function scheduleReconnect() {
    if (closed) return;
    if (attempt >= maxRetries) {
      onFailed?.();
      return;
    }
    attempt++;
    onReconnecting?.(attempt);

    const jitter = Math.random() * 500;
    const delay = Math.min(baseDelay * 2 ** (attempt - 1), maxDelay) + jitter;

    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect(true);
    }, delay);
  }

  function connect(isReconnect: boolean) {
    connectWebSocket(isReconnect).then(
      (socket) => {
        ws = socket;

        socket.addEventListener("open", () => {
          attempt = 0;
          subscribe(socket);
          onOpen?.();
        });

        socket.addEventListener("message", (event) => {
          onMessage?.(event);
        });

        socket.addEventListener("close", (event) => {
          onClose?.();
          if (closed) return;
          if (event.code === 1000 || event.code === 1001) return;
          scheduleReconnect();
        });

        socket.addEventListener("error", () => {
          socket.close();
        });
      },
      () => {
        scheduleReconnect();
      },
    );
  }

  connect(false);

  return {
    send(data: string) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    },
    close() {
      closed = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      ws?.close();
    },
    get readyState() {
      return ws?.readyState ?? WebSocket.CLOSED;
    },
  };
}

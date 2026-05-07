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
  const { timeout = DEFAULT_TIMEOUT_MS, signal: externalSignal, ...fetchOptions } = init;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);
  const abortFromExternalSignal = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) {
    abortFromExternalSignal();
  } else {
    externalSignal?.addEventListener('abort', abortFromExternalSignal, { once: true });
  }

  const doFetch = async (info: BackendInfo): Promise<Response> => {
    const headers = new Headers(fetchOptions.headers || {});
    if (info.token) {
      headers.set("Authorization", `Bearer ${info.token}`);
    }
    const url = (isViteDevMode && !info.baseUrl) ? path : `${info.baseUrl}${path}`;
    return fetch(url, {
      ...fetchOptions,
      cache: fetchOptions.cache ?? 'no-store',
      headers,
      signal: controller.signal,
    });
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
    externalSignal?.removeEventListener('abort', abortFromExternalSignal);
    clearTimeout(timeoutId);
  }
}

export async function apiFetchFresh(path: string, init: RequestInit = {}) {
  clearBackendInfoCache();
  return apiFetch(path, init);
}

export async function connectWebSocket(_forceRefresh = false): Promise<WebSocket> {
  // Always clear cache to fetch the freshest backend info (token may have
  // changed after a backend restart).  The previous forceRefresh-gated path
  // caused a reconnect loop: stale cached token → 403 → reconnect → same
  // stale token → 403 …
  clearBackendInfoCache();
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

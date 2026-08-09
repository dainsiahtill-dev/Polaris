// ═══════════════════════════════════════════════════════════════════════════════
// Default Configuration Constants
// ═══════════════════════════════════════════════════════════════════════════════
const DEFAULT_BACKEND_PORT = 49977;
const DEFAULT_BACKEND_HOST = "127.0.0.1";
const DEFAULT_BACKEND_TOKEN = "polaris-local-dev";
const BACKEND_PROBE_TIMEOUT_MS = 2000;
const STORED_BASE_URL_KEY = "polaris.baseUrl";
const STORED_TOKEN_KEY = "polaris.token";
const INSTANCE_STORAGE_PREFIX = "polaris.instances";
let cachedInfo = null;
function getDefaultBackendUrl() {
    // Allow port override via environment variable in development
    const port = import.meta.env.VITE_BACKEND_PORT || DEFAULT_BACKEND_PORT;
    const host = import.meta.env.VITE_BACKEND_HOST || DEFAULT_BACKEND_HOST;
    return `http://${host}:${port}`;
}
function getEnvBackendUrl() {
    const url = import.meta.env.VITE_BACKEND_URL || import.meta.env.VITE_POLARIS_BACKEND_URL;
    return typeof url === "string" && url.trim() ? url.trim().replace(/\/+$/, "") : null;
}
function getEnvBackendToken() {
    const token = import.meta.env.VITE_BACKEND_TOKEN || import.meta.env.VITE_POLARIS_BACKEND_TOKEN;
    return typeof token === "string" && token.trim() ? token.trim() : null;
}
function getQueryParam(names) {
    if (typeof window === "undefined")
        return null;
    const params = new URLSearchParams(window.location.search);
    for (const name of names) {
        const value = params.get(name);
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    return null;
}
function normalizeBackendUrl(value) {
    return value && value.trim() ? value.trim().replace(/\/+$/, "") : null;
}
function getUrlBackendUrl() {
    return normalizeBackendUrl(getQueryParam(["backend", "backendUrl", "polarisBackend"]));
}
function getUrlBackendToken() {
    return getQueryParam(["token", "backendToken", "polarisToken"]);
}
function getEnvWorkspace() {
    const workspace = import.meta.env.VITE_POLARIS_WORKSPACE || import.meta.env.VITE_WORKSPACE;
    return typeof workspace === "string" && workspace.trim() ? workspace.trim() : null;
}
function getUrlWorkspace() {
    return getQueryParam(["workspace", "polarisWorkspace"]);
}
function getInstanceId() {
    const raw = getQueryParam(["instance", "instanceId", "polarisInstance"]) ||
        (typeof import.meta.env.VITE_POLARIS_INSTANCE_ID === "string"
            ? import.meta.env.VITE_POLARIS_INSTANCE_ID
            : "");
    return String(raw || "").trim().replace(/[^a-zA-Z0-9_.:-]/g, "-").slice(0, 120);
}
function hasIsolatedInstanceBinding() {
    return Boolean(getInstanceId() || getUrlBackendUrl() || getEnvBackendUrl() || getUrlWorkspace() || getEnvWorkspace());
}
function storageKey(key) {
    const instanceId = getInstanceId();
    return instanceId ? `${INSTANCE_STORAGE_PREFIX}.${instanceId}.${key}` : key;
}
const isViteWebDevMode = import.meta.env.DEV &&
    typeof window !== "undefined" &&
    !window.polaris?.getBackendInfo;
function shouldUseCachedInfo(options) {
    return !options.bypassCache && !options.ignoreStoredBase && !options.ignoreStoredToken;
}
function sameBackendInfo(left, right) {
    return left.baseUrl === right.baseUrl && left.token === right.token;
}
function rememberRecoveredBackend(info) {
    cachedInfo = info;
    if (!isViteWebDevMode || !info.baseUrl) {
        return;
    }
    const baseUrlKey = storageKey(STORED_BASE_URL_KEY);
    const tokenKey = storageKey(STORED_TOKEN_KEY);
    if (info.baseUrl === getDefaultBackendUrl()) {
        localStorage.removeItem(baseUrlKey);
    }
    else {
        localStorage.setItem(baseUrlKey, info.baseUrl);
    }
    if (info.token === DEFAULT_BACKEND_TOKEN) {
        localStorage.removeItem(tokenKey);
    }
    else if (info.token) {
        localStorage.setItem(tokenKey, info.token);
    }
}
async function getDefaultLocalBackendInfo() {
    clearBackendInfoCache();
    return getBackendInfo({
        ignoreStoredBase: true,
        ignoreStoredToken: true,
        bypassCache: true,
    });
}
async function isBackendReachable(info) {
    if (!info.baseUrl) {
        return false;
    }
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), BACKEND_PROBE_TIMEOUT_MS);
    try {
        const headers = new Headers();
        if (info.token) {
            headers.set("Authorization", `Bearer ${info.token}`);
        }
        // Use the public /health endpoint (no auth required) to check reachability.
        // /v2/live requires auth and would fail if the token is not yet known.
        const res = await fetch(`${info.baseUrl}/health`, {
            cache: "no-store",
            headers,
            signal: controller.signal,
        });
        return res.ok;
    }
    catch {
        return false;
    }
    finally {
        clearTimeout(timeoutId);
    }
}
async function resolveReachableBackendInfo(info) {
    if (!isViteWebDevMode || !info.baseUrl) {
        return info;
    }
    if (hasIsolatedInstanceBinding()) {
        return info;
    }
    if (await isBackendReachable(info)) {
        return info;
    }
    const fallback = await getDefaultLocalBackendInfo();
    if (sameBackendInfo(info, fallback)) {
        return info;
    }
    if (await isBackendReachable(fallback)) {
        rememberRecoveredBackend(fallback);
        return fallback;
    }
    return info;
}
export async function getBackendInfo(options = {}) {
    if (cachedInfo && shouldUseCachedInfo(options)) {
        return cachedInfo;
    }
    if (!window.polaris?.getBackendInfo) {
        const devBackend = window.__DEV_BACKEND__;
        const storedBase = options.ignoreStoredBase ? null : localStorage.getItem(storageKey(STORED_BASE_URL_KEY));
        const explicitBase = getUrlBackendUrl() || devBackend?.baseUrl || getEnvBackendUrl() || storedBase;
        const fallbackBase = explicitBase || getDefaultBackendUrl();
        const fallbackToken = getUrlBackendToken() ||
            devBackend?.token ||
            getEnvBackendToken() ||
            (options.ignoreStoredToken ? null : localStorage.getItem(storageKey(STORED_TOKEN_KEY))) ||
            (isViteWebDevMode ? DEFAULT_BACKEND_TOKEN : null);
        // Warn if the default dev-only token is being used in Vite web mode.
        if (isViteWebDevMode && fallbackToken === DEFAULT_BACKEND_TOKEN) {
            console.warn(`[Polaris] Using default development token "${DEFAULT_BACKEND_TOKEN}" for local backend. ` +
                'This is intended for local development only and MUST NOT be used in production.');
        }
        const info = {
            port: null,
            token: fallbackToken,
            baseUrl: fallbackBase,
            pid: null,
        };
        if (shouldUseCachedInfo(options)) {
            cachedInfo = info;
        }
        return info;
    }
    const info = await window.polaris.getBackendInfo();
    if (shouldUseCachedInfo(options)) {
        cachedInfo = info;
    }
    return info;
}
function clearBackendInfoCache() {
    cachedInfo = null;
}
export async function pickWorkspace(defaultPath) {
    if (!window.polaris?.pickWorkspace) {
        throw new Error("Electron preload not available.");
    }
    return window.polaris.pickWorkspace({ defaultPath });
}
export async function openPath(targetPath) {
    if (!window.polaris?.openPath) {
        throw new Error("Electron preload not available.");
    }
    return window.polaris.openPath(targetPath);
}
const DEFAULT_TIMEOUT_MS = 30000;
function getSameOriginWebSocketBaseUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}`;
}
export async function apiFetch(path, init = {}) {
    const { timeout = DEFAULT_TIMEOUT_MS, signal: externalSignal, ...fetchOptions } = init;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);
    const abortFromExternalSignal = () => controller.abort(externalSignal?.reason);
    if (externalSignal?.aborted) {
        abortFromExternalSignal();
    }
    else {
        externalSignal?.addEventListener('abort', abortFromExternalSignal, { once: true });
    }
    const doFetch = async (info) => {
        const headers = new Headers(fetchOptions.headers || {});
        if (info.token) {
            headers.set("Authorization", `Bearer ${info.token}`);
        }
        const url = info.baseUrl ? `${info.baseUrl}${path}` : path;
        return fetch(url, {
            ...fetchOptions,
            cache: fetchOptions.cache ?? 'no-store',
            headers,
            signal: controller.signal,
        });
    };
    const discoverToken = async (baseUrl) => {
        try {
            const res = await fetch(`${baseUrl}/v2/auth/token`, {
                cache: "no-store",
                signal: controller.signal,
            });
            if (res.ok) {
                const data = (await res.json());
                return data?.token || null;
            }
        }
        catch {
            // ignore
        }
        return null;
    };
    const doFallbackFetch = async (previousInfo) => {
        if (hasIsolatedInstanceBinding()) {
            return null;
        }
        const fallbackInfo = await getDefaultLocalBackendInfo();
        if (sameBackendInfo(previousInfo, fallbackInfo)) {
            return null;
        }
        const fallbackResponse = await doFetch(fallbackInfo);
        if (fallbackResponse.status !== 401) {
            rememberRecoveredBackend(fallbackInfo);
        }
        return fallbackResponse;
    };
    try {
        let info = await getBackendInfo();
        try {
            let res = await doFetch(info);
            if (res.status === 401) {
                // Try to discover the token from the backend's public endpoint.
                const baseUrl = info.baseUrl || getDefaultBackendUrl();
                const discoveredToken = await discoverToken(baseUrl);
                if (discoveredToken && discoveredToken !== info.token) {
                    const newInfo = {
                        ...info,
                        token: discoveredToken,
                    };
                    rememberRecoveredBackend(newInfo);
                    info = newInfo;
                    res = await doFetch(info);
                    if (res.status !== 401) {
                        return res;
                    }
                }
                clearBackendInfoCache();
                const fallbackResponse = await doFallbackFetch(info);
                if (fallbackResponse) {
                    return fallbackResponse;
                }
                info = await getBackendInfo({ bypassCache: true });
                return await doFetch(info);
            }
            return res;
        }
        catch (err) {
            clearBackendInfoCache();
            const fallbackResponse = await doFallbackFetch(info);
            if (fallbackResponse) {
                return fallbackResponse;
            }
            throw err;
        }
    }
    finally {
        externalSignal?.removeEventListener('abort', abortFromExternalSignal);
        clearTimeout(timeoutId);
    }
}
export async function apiFetchFresh(path, init = {}) {
    clearBackendInfoCache();
    return apiFetch(path, init);
}
export async function connectWebSocket(_forceRefresh = false) {
    // Always clear cache to fetch the freshest backend info (token may have
    // changed after a backend restart).  The previous forceRefresh-gated path
    // caused a reconnect loop: stale cached token → 403 → reconnect → same
    // stale token → 403 …
    clearBackendInfoCache();
    let info = await getBackendInfo();
    if (!info.baseUrl) {
        clearBackendInfoCache();
        info = await getBackendInfo({ bypassCache: true });
    }
    info = await resolveReachableBackendInfo(info);
    // If no token is available, try to discover it from the backend.
    if (!info.token) {
        const baseUrl = info.baseUrl || getDefaultBackendUrl();
        try {
            const res = await fetch(`${baseUrl}/v2/auth/token`, { cache: "no-store" });
            if (res.ok) {
                const data = (await res.json());
                if (data?.token) {
                    info = { ...info, token: data.token };
                    rememberRecoveredBackend(info);
                }
            }
        }
        catch {
            // ignore discovery failure
        }
    }
    const wsBaseUrl = info.baseUrl
        ? info.baseUrl.replace(/^http/, "ws")
        : getSameOriginWebSocketBaseUrl();
    const wsUrl = new URL(`${wsBaseUrl}/v2/ws/runtime`);
    wsUrl.searchParams.set("protocol", "runtime.v2");
    wsUrl.searchParams.set("token", info.token || "");
    const workspace = getUrlWorkspace() || getEnvWorkspace();
    if (workspace) {
        wsUrl.searchParams.set("workspace", workspace);
    }
    return new WebSocket(wsUrl.toString());
}

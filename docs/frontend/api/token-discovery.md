# Frontend API: Token Discovery and Default Dev Fallback

This document describes how the Polaris frontend (`src/frontend/src/api.ts`) discovers and falls back to authentication tokens when communicating with the backend.

## Token Resolution Order

When the frontend needs to make an API request or establish a WebSocket connection, it resolves the backend information (base URL and token) in the following order:

1.  **Electron Preload:** In the Electron environment, `window.polaris.getBackendInfo()` provides the backend details.
2.  **Dev Backend Injection:** `window.__DEV_BACKEND__` (if present) can inject a `baseUrl` and `token`.
3.  **Environment Variables:** The frontend checks for:
    *   `VITE_BACKEND_URL`: Overrides the base URL.
    *   `VITE_BACKEND_TOKEN`: Overrides the token.
    *   `VITE_BACKEND_PORT`: Overrides the port (default: 49977).
    *   `VITE_BACKEND_HOST`: Overrides the host (default: 127.0.0.1).
4.  **Local Storage:** Previously persisted values in `localStorage` are checked:
    *   `polaris.baseUrl`: The backend base URL.
    *   `polaris.token`: The authentication token.
5.  **Default Fallback (Vite Web Dev Mode Only):** If none of the above provide a token, and the frontend is running in Vite web dev mode (`import.meta.env.DEV` is true and `window.polaris.getBackendInfo` is not available), the frontend falls back to the default development token: `polaris-local-dev`.
6.  **Default Backend URL:** If no base URL is found, the frontend defaults to `http://127.0.0.1:49977`.

## Token Discovery Endpoint

If the initial API request fails with a 401 Unauthorized error, the frontend attempts to discover the token from the backend's public endpoint:

`GET /v2/auth/token`

If this endpoint returns a token, the frontend retries the original request with the new token.

## Default Dev Fallback Behavior

In **Vite web dev mode**, if no token is available after checking all sources, the frontend automatically uses the hardcoded default token:

```
polaris-local-dev
```

This is intended for local development only. **Do not use this token in production environments.**

A warning is logged to the console when this fallback occurs:

```
[Polaris] Using default development token "polaris-local-dev" for local backend. This is intended for local development only and MUST NOT be used in production.
```

## WebSocket Connection

When establishing a WebSocket connection (`connectWebSocket`), the frontend follows the same token resolution order. If no token is found, it attempts discovery via `/v2/auth/token` before connecting.

## Important Notes

*   **Production Security:** Ensure that a secure token is configured for production deployments. The `polaris-local-dev` token is not secure.
*   **Vite Web Dev Mode:** The default token fallback only applies when running in Vite web dev mode.
*   **Token Discovery:** The `/v2/auth/token` endpoint is a public endpoint and does not require authentication to access.

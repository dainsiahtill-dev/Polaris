/**
 * Unified API client with runtime validation
 *
 * This provides a centralized way to make API calls with:
 * - Consistent error handling
 * - Runtime type validation using Zod
 * - Automatic base URL resolution
 * - Token-based authentication
 * - Request timeout support
 */
import { z } from 'zod';
import { getBackendInfo } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
// API Error class
export class ApiError extends Error {
    constructor(status, responseText, message = `API Error ${status}: ${responseText}`) {
        super(message);
        this.status = status;
        this.responseText = responseText;
        this.message = message;
        this.name = 'ApiError';
    }
}
// ═══════════════════════════════════════════════════════════════════════════════
// Default Configuration
// ═══════════════════════════════════════════════════════════════════════════════
const DEFAULT_BACKEND_PORT = 49977;
const DEFAULT_BACKEND_HOST = '127.0.0.1';
const DEFAULT_TIMEOUT = 30000;
/**
 * Get the default backend base URL.
 * Allows environment variable override in development mode.
 */
function getDefaultBaseUrl() {
    if (import.meta.env.VITE_BACKEND_URL) {
        return import.meta.env.VITE_BACKEND_URL;
    }
    const port = import.meta.env.VITE_BACKEND_PORT || DEFAULT_BACKEND_PORT;
    const host = import.meta.env.VITE_BACKEND_HOST || DEFAULT_BACKEND_HOST;
    return `http://${host}:${port}`;
}
// API Client class
export class ApiClient {
    constructor(options) {
        this.token = null;
        this.timeout = DEFAULT_TIMEOUT;
        // Use provided baseUrl or default fallback
        this._baseUrl = options?.baseUrl || getDefaultBaseUrl();
        this.timeout = options?.timeout || DEFAULT_TIMEOUT;
        this.defaultHeaders = {
            'Content-Type': 'application/json',
        };
    }
    get baseUrl() {
        return this._baseUrl;
    }
    setToken(token) {
        this.token = token;
    }
    setTimeout(timeout) {
        this.timeout = timeout;
    }
    /**
     * Initialize client with backend info (async)
     */
    async initialize() {
        try {
            const info = await getBackendInfo();
            if (info.baseUrl) {
                this._baseUrl = info.baseUrl;
            }
            if (info.token) {
                this.token = info.token;
            }
        }
        catch {
            // Use existing values
        }
    }
    /**
     * Make a typed API request
     */
    async request(schema, config) {
        // Try to get fresh backend info for each request
        try {
            const info = await getBackendInfo();
            if (info.baseUrl) {
                this._baseUrl = info.baseUrl;
            }
            if (info.token) {
                this.token = info.token;
            }
        }
        catch {
            // Use existing values
        }
        const url = this._buildUrl(config.path, config.params);
        const headers = { ...this.defaultHeaders, ...config.headers };
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), config.timeout || this.timeout);
        try {
            const response = await fetch(url, {
                method: config.method || 'GET',
                headers,
                body: config.body ? JSON.stringify(config.body) : undefined,
                signal: controller.signal,
            });
            if (!response.ok) {
                const text = await response.text();
                throw new ApiError(response.status, text);
            }
            const data = await response.json();
            // Validate response with Zod schema
            try {
                return schema.parse(data);
            }
            catch (error) {
                devLogger.error('API response validation failed:', error);
                devLogger.error('Received data:', data);
                throw error;
            }
        }
        finally {
            clearTimeout(timeoutId);
        }
    }
    /**
     * GET request
     */
    async get(schema, path, params) {
        return this.request(schema, { path, method: 'GET', params });
    }
    /**
     * POST request
     */
    async post(schema, path, body) {
        return this.request(schema, { path, method: 'POST', body });
    }
    /**
     * PUT request
     */
    async put(schema, path, body) {
        return this.request(schema, { path, method: 'PUT', body });
    }
    /**
     * PATCH request
     */
    async patch(schema, path, body) {
        return this.request(schema, { path, method: 'PATCH', body });
    }
    /**
     * DELETE request
     */
    async delete(schema, path) {
        return this.request(schema, { path, method: 'DELETE' });
    }
    _buildUrl(path, params) {
        let url = `${this._baseUrl}${path.startsWith('/') ? path : `/${path}`}`;
        if (params && Object.keys(params).length > 0) {
            const searchParams = new URLSearchParams(params);
            url += `?${searchParams.toString()}`;
        }
        return url;
    }
}
// Global API client instance
let globalClient = null;
export function getApiClient() {
    if (!globalClient) {
        globalClient = new ApiClient();
    }
    return globalClient;
}
export function setApiClient(client) {
    globalClient = client;
}
// Re-export commonly used Zod types
export { z };

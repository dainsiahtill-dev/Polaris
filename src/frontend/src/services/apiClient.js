/**
 * Unified API Client
 *
 * 统一API客户端，提供类型安全的HTTP请求封装
 * 消除重复代码和any类型使用
 */
import { apiFetch } from '@/api';
// ============================================================================
// Error Handling
// ============================================================================
export class ApiError extends Error {
    constructor(status, responseText, message) {
        super(message || `API Error ${status}: ${responseText}`);
        this.status = status;
        this.responseText = responseText;
        this.name = 'ApiError';
    }
}
function extractStringDetail(value) {
    if (typeof value === 'string' && value.trim()) {
        return value;
    }
    if (!value || typeof value !== 'object') {
        return null;
    }
    const payload = value;
    return (extractStringDetail(payload.message) ||
        extractStringDetail(payload.detail) ||
        extractStringDetail(payload.error) ||
        extractStringDetail(payload.code));
}
function stringList(value) {
    if (!Array.isArray(value)) {
        return [];
    }
    return value.map(item => String(item || '').trim()).filter(Boolean);
}
function stringMap(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        return {};
    }
    return Object.fromEntries(Object.entries(value)
        .map(([key, item]) => [key.trim(), String(item || '').trim()])
        .filter(([key, item]) => Boolean(key) && Boolean(item)));
}
function extractStructuredDetails(value) {
    if (!value || typeof value !== 'object') {
        return null;
    }
    const payload = value;
    if (payload.details && typeof payload.details === 'object') {
        return payload.details;
    }
    return (extractStructuredDetails(payload.error) ||
        extractStructuredDetails(payload.detail) ||
        null);
}
function appendRoleReadinessDetails(message, payload) {
    const details = extractStructuredDetails(payload);
    if (!details) {
        return message;
    }
    const missingRoles = stringList(details.missing_roles);
    if (missingRoles.length > 0) {
        const roleIssues = stringMap(details.role_issues);
        const issueText = missingRoles
            .map(role => {
            const issue = roleIssues[role];
            return issue ? `${role} (${issue})` : role;
        })
            .join(', ');
        return `${message} · blocked: ${issueText}`;
    }
    const requiredRoles = stringList(details.required_roles);
    if (requiredRoles.length > 0) {
        return `${message} · required: ${requiredRoles.join(', ')}`;
    }
    return message;
}
export async function extractErrorDetail(response, fallback) {
    try {
        const payload = (await response.json());
        const message = (extractStringDetail(payload.detail) ||
            extractStringDetail(payload.error) ||
            extractStringDetail(payload.message) ||
            fallback);
        return appendRoleReadinessDetails(message, payload);
    }
    catch {
        return fallback;
    }
}
export function formatErrorMessage(error, fallback) {
    if (error instanceof ApiError) {
        return error.message;
    }
    if (error instanceof Error) {
        return error.message;
    }
    return fallback;
}
// ============================================================================
// HTTP Methods
// ============================================================================
export async function apiGet(path, errorMessage) {
    try {
        const response = await apiFetch(path);
        if (!response.ok) {
            const detail = await extractErrorDetail(response, errorMessage);
            return { ok: false, error: detail };
        }
        const data = (await response.json());
        return { ok: true, data };
    }
    catch (error) {
        return { ok: false, error: formatErrorMessage(error, errorMessage) };
    }
}
export async function apiPost(path, body, errorMessage) {
    try {
        const response = await apiFetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            const detail = await extractErrorDetail(response, errorMessage);
            return { ok: false, error: detail };
        }
        const data = (await response.json());
        return { ok: true, data };
    }
    catch (error) {
        return { ok: false, error: formatErrorMessage(error, errorMessage) };
    }
}
export async function apiPostEmpty(path, errorMessage) {
    try {
        const response = await apiFetch(path, {
            method: 'POST',
        });
        if (!response.ok) {
            const detail = await extractErrorDetail(response, errorMessage);
            return { ok: false, error: detail };
        }
        if (response.status === 204) {
            return { ok: true, data: undefined };
        }
        const data = (await response.json());
        return { ok: true, data };
    }
    catch (error) {
        return { ok: false, error: formatErrorMessage(error, errorMessage) };
    }
}
export async function apiPut(path, body, errorMessage) {
    try {
        const response = await apiFetch(path, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            const detail = await extractErrorDetail(response, errorMessage);
            return { ok: false, error: detail };
        }
        const data = (await response.json());
        return { ok: true, data };
    }
    catch (error) {
        return { ok: false, error: formatErrorMessage(error, errorMessage) };
    }
}
export async function apiDelete(path, errorMessage) {
    try {
        const response = await apiFetch(path, {
            method: 'DELETE',
        });
        if (!response.ok) {
            const detail = await extractErrorDetail(response, errorMessage);
            return { ok: false, error: detail };
        }
        if (response.status === 204) {
            return { ok: true, data: undefined };
        }
        const data = (await response.json());
        return { ok: true, data };
    }
    catch (error) {
        return { ok: false, error: formatErrorMessage(error, errorMessage) };
    }
}
// ============================================================================
// Query Parameter Builder
// ============================================================================
export function buildQueryString(params) {
    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) {
            searchParams.set(key, String(value));
        }
    }
    const query = searchParams.toString();
    return query ? `?${query}` : '';
}
// ============================================================================
// Response Handlers
// ============================================================================
export async function handleEmptyResponse(response, successMessage) {
    if (!response.ok) {
        const detail = await extractErrorDetail(response, 'Request failed');
        return { ok: false, error: detail };
    }
    return { ok: true };
}
export async function handleJsonResponse(response, errorMessage) {
    if (!response.ok) {
        const detail = await extractErrorDetail(response, errorMessage);
        return { ok: false, error: detail };
    }
    try {
        const data = (await response.json());
        return { ok: true, data };
    }
    catch {
        return { ok: false, error: 'Failed to parse response' };
    }
}

/**
 * Unified API Client
 *
 * 统一API客户端，提供类型安全的HTTP请求封装
 * 消除重复代码和any类型使用
 */

import { apiFetch } from '@/api';
import type { ApiResult, ApiErrorDetail } from './api.types';

// ============================================================================
// Error Handling
// ============================================================================

export class ApiError extends Error {
  constructor(
    public status: number,
    public responseText: string,
    message?: string
  ) {
    super(message || `API Error ${status}: ${responseText}`);
    this.name = 'ApiError';
  }
}

export async function extractErrorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as ApiErrorDetail;
    return payload.detail || payload.error || payload.message || fallback;
  } catch {
    return fallback;
  }
}

export function formatErrorMessage(error: unknown, fallback: string): string {
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

export async function apiGet<T>(
  path: string,
  errorMessage: string
): Promise<ApiResult<T>> {
  try {
    const response = await apiFetch(path);

    if (!response.ok) {
      const detail = await extractErrorDetail(response, errorMessage);
      return { ok: false, error: detail };
    }

    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch (error) {
    return { ok: false, error: formatErrorMessage(error, errorMessage) };
  }
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  errorMessage: string
): Promise<ApiResult<T>> {
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

    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch (error) {
    return { ok: false, error: formatErrorMessage(error, errorMessage) };
  }
}

export async function apiPostEmpty<T>(
  path: string,
  errorMessage: string
): Promise<ApiResult<T>> {
  try {
    const response = await apiFetch(path, {
      method: 'POST',
    });

    if (!response.ok) {
      const detail = await extractErrorDetail(response, errorMessage);
      return { ok: false, error: detail };
    }

    if (response.status === 204) {
      return { ok: true, data: undefined as T };
    }

    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch (error) {
    return { ok: false, error: formatErrorMessage(error, errorMessage) };
  }
}

export async function apiPut<T>(
  path: string,
  body: unknown,
  errorMessage: string
): Promise<ApiResult<T>> {
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

    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch (error) {
    return { ok: false, error: formatErrorMessage(error, errorMessage) };
  }
}

export async function apiDelete<T>(
  path: string,
  errorMessage: string
): Promise<ApiResult<T>> {
  try {
    const response = await apiFetch(path, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const detail = await extractErrorDetail(response, errorMessage);
      return { ok: false, error: detail };
    }

    if (response.status === 204) {
      return { ok: true, data: undefined as T };
    }

    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch (error) {
    return { ok: false, error: formatErrorMessage(error, errorMessage) };
  }
}

// ============================================================================
// Query Parameter Builder
// ============================================================================

export function buildQueryString(params: Record<string, string | number | boolean | undefined>): string {
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

export async function handleEmptyResponse(
  response: Response,
  successMessage?: string
): Promise<ApiResult<void>> {
  if (!response.ok) {
    const detail = await extractErrorDetail(response, 'Request failed');
    return { ok: false, error: detail };
  }
  return { ok: true };
}

export async function handleJsonResponse<T>(
  response: Response,
  errorMessage: string
): Promise<ApiResult<T>> {
  if (!response.ok) {
    const detail = await extractErrorDetail(response, errorMessage);
    return { ok: false, error: detail };
  }

  try {
    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch {
    return { ok: false, error: 'Failed to parse response' };
  }
}

/**
 * useV2ApiError - V2 API Error Handling Hooks
 *
 * Provides centralized error handling for v2 API services:
 * - Global API error state management
 * - Retry logic with exponential backoff
 * - Rate limit detection and handling
 * - Offline detection and sync recovery
 */

import { useCallback, useEffect, useRef, useState } from 'react';

// ============================================================================
// Types
// ============================================================================

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  status: number;
}

export interface RateLimitInfo {
  isRateLimited: boolean;
  retryAfter: number;
  limit?: number;
  remaining?: number;
}

export interface OfflineState {
  isOffline: boolean;
  wasOffline: boolean;
}

export interface RetryState {
  retryCount: number;
  isRetrying: boolean;
}

// ============================================================================
// useApiError - Global API error state management
// ============================================================================

export interface UseApiErrorResult {
  error: ApiError | null;
  setError: (error: ApiError) => void;
  clearError: () => void;
  hasError: boolean;
}

/**
 * Hook for managing global API error state.
 *
 * @example
 * ```tsx
 * const { error, setError, clearError, hasError } = useApiError();
 * if (hasError) return <ErrorDisplay error={error} onDismiss={clearError} />;
 * ```
 */
export function useApiError(): UseApiErrorResult {
  const [error, setErrorState] = useState<ApiError | null>(null);

  const setError = useCallback((err: ApiError) => {
    setErrorState(err);
  }, []);

  const clearError = useCallback(() => {
    setErrorState(null);
  }, []);

  const hasError = error !== null;

  return {
    error,
    setError,
    clearError,
    hasError,
  };
}

// ============================================================================
// useRetry - Retry logic for failed requests
// ============================================================================

export interface UseRetryResult {
  retry: <T>(fn: () => Promise<T>, maxRetries?: number) => Promise<T>;
  retryCount: number;
  isRetrying: boolean;
  resetRetry: () => void;
}

const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_BASE_DELAY_MS = 1000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function calculateBackoffDelay(attempt: number, baseDelay: number): number {
  const jitter = Math.random() * 200;
  return baseDelay * 2 ** attempt + jitter;
}

/**
 * Hook for retrying failed async operations with exponential backoff.
 *
 * @example
 * ```tsx
 * const { retry, retryCount, isRetrying } = useRetry();
 * const data = await retry(() => fetchData(), 3);
 * ```
 */
export function useRetry(): UseRetryResult {
  const [retryCount, setRetryCount] = useState(0);
  const [isRetrying, setIsRetrying] = useState(false);
  const abortRef = useRef(false);

  const retry = useCallback(
    async <T,>(fn: () => Promise<T>, maxRetries: number = DEFAULT_MAX_RETRIES): Promise<T> => {
      abortRef.current = false;
      setIsRetrying(true);
      setRetryCount(0);

      try {
        for (let attempt = 0; attempt <= maxRetries; attempt++) {
          if (abortRef.current) {
            throw new Error('Retry aborted');
          }

          try {
            const result = await fn();
            setRetryCount(attempt);
            return result;
          } catch (error) {
            const isLastAttempt = attempt >= maxRetries;

            if (isLastAttempt || abortRef.current) {
              throw error;
            }

            const delay = calculateBackoffDelay(attempt, DEFAULT_BASE_DELAY_MS);
            setRetryCount(attempt + 1);
            await sleep(delay);
          }
        }

        // Should never reach here, but TypeScript needs a throw
        throw new Error('Retry exhausted');
      } finally {
        setIsRetrying(false);
      }
    },
    []
  );

  const resetRetry = useCallback(() => {
    abortRef.current = true;
    setRetryCount(0);
    setIsRetrying(false);
  }, []);

  useEffect(() => {
    return () => {
      abortRef.current = true;
    };
  }, []);

  return {
    retry,
    retryCount,
    isRetrying,
    resetRetry,
  };
}

// ============================================================================
// useRateLimit - Rate limit handling
// ============================================================================

export interface UseRateLimitResult {
  isRateLimited: boolean;
  retryAfter: number;
  handleRateLimit: (response: Response) => void;
  clearRateLimit: () => void;
}

const RATE_LIMIT_STATUS = 429;
const HEADER_RETRY_AFTER = 'retry-after';

function parseRetryAfter(value: string | null): number {
  if (!value) return 60;
  const seconds = parseInt(value, 10);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : 60;
}

/**
 * Hook for detecting and handling API rate limits.
 *
 * @example
 * ```tsx
 * const { isRateLimited, retryAfter, handleRateLimit } = useRateLimit();
 * const res = await fetch('/api/data');
 * handleRateLimit(res);
 * if (isRateLimited) return <RateLimitBanner retryAfter={retryAfter} />;
 * ```
 */
export function useRateLimit(): UseRateLimitResult {
  const [isRateLimited, setIsRateLimited] = useState(false);
  const [retryAfter, setRetryAfter] = useState(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearRateLimit = useCallback(() => {
    setIsRateLimited(false);
    setRetryAfter(0);
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const handleRateLimit = useCallback(
    (response: Response) => {
      if (response.status !== RATE_LIMIT_STATUS) {
        if (isRateLimited) {
          clearRateLimit();
        }
        return;
      }

      const retryAfterSeconds = parseRetryAfter(response.headers.get(HEADER_RETRY_AFTER));
      setRetryAfter(retryAfterSeconds);
      setIsRateLimited(true);

      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }

      timerRef.current = setTimeout(() => {
        setIsRateLimited(false);
        setRetryAfter(0);
        timerRef.current = null;
      }, retryAfterSeconds * 1000);
    },
    [isRateLimited, clearRateLimit]
  );

  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  return {
    isRateLimited,
    retryAfter,
    handleRateLimit,
    clearRateLimit,
  };
}

// ============================================================================
// useOffline - Offline detection
// ============================================================================

export interface UseOfflineResult {
  isOffline: boolean;
  wasOffline: boolean;
  syncWhenOnline: (fn: () => void | Promise<void>) => void;
}

/**
 * Hook for detecting browser online/offline state.
 *
 * @example
 * ```tsx
 * const { isOffline, wasOffline, syncWhenOnline } = useOffline();
 * if (isOffline) return <OfflineBanner />;
 * ```
 */
export function useOffline(): UseOfflineResult {
  const [isOffline, setIsOffline] = useState(!navigator.onLine);
  const [wasOffline, setWasOffline] = useState(false);
  const pendingSyncRef = useRef<(() => void | Promise<void>) | null>(null);

  useEffect(() => {
    const handleOnline = () => {
      setIsOffline(false);
      setWasOffline(true);

      if (pendingSyncRef.current) {
        try {
          void pendingSyncRef.current();
        } catch {
          // Silently ignore sync errors
        }
        pendingSyncRef.current = null;
      }
    };

    const handleOffline = () => {
      setIsOffline(true);
    };

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  const syncWhenOnline = useCallback((fn: () => void | Promise<void>) => {
    if (!navigator.onLine) {
      pendingSyncRef.current = fn;
      return;
    }
    try {
      void fn();
    } catch {
      // Silently ignore sync errors
    }
  }, []);

  return {
    isOffline,
    wasOffline,
    syncWhenOnline,
  };
}

// ============================================================================
// Combined hook for convenience
// ============================================================================

export interface UseV2ApiErrorResult {
  apiError: UseApiErrorResult;
  retry: UseRetryResult;
  rateLimit: UseRateLimitResult;
  offline: UseOfflineResult;
}

/**
 * Combined hook that provides all v2 API error handling capabilities.
 *
 * @example
 * ```tsx
 * const { apiError, retry, rateLimit, offline } = useV2ApiError();
 *
 * const fetchData = async () => {
 *   if (offline.isOffline) {
 *     offline.syncWhenOnline(() => fetchData());
 *     return;
 *   }
 *   if (rateLimit.isRateLimited) {
 *     toast.info(`Rate limited. Retry after ${rateLimit.retryAfter}s`);
 *     return;
 *   }
 *   try {
 *     const data = await retry.retry(() => api.getData(), 3);
 *   } catch (err) {
 *     apiError.setError({ code: 'FETCH_ERROR', message: 'Failed', status: 500 });
 *   }
 * };
 * ```
 */
export function useV2ApiError(): UseV2ApiErrorResult {
  const apiError = useApiError();
  const retry = useRetry();
  const rateLimit = useRateLimit();
  const offline = useOffline();

  return {
    apiError,
    retry,
    rateLimit,
    offline,
  };
}

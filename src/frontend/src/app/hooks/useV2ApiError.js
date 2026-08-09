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
/**
 * Hook for managing global API error state.
 *
 * @example
 * ```tsx
 * const { error, setError, clearError, hasError } = useApiError();
 * if (hasError) return <ErrorDisplay error={error} onDismiss={clearError} />;
 * ```
 */
export function useApiError() {
    const [error, setErrorState] = useState(null);
    const setError = useCallback((err) => {
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
const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_BASE_DELAY_MS = 1000;
function sleep(ms) {
    return new Promise((resolve) => {
        setTimeout(resolve, ms);
    });
}
function calculateBackoffDelay(attempt, baseDelay) {
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
export function useRetry() {
    const [retryCount, setRetryCount] = useState(0);
    const [isRetrying, setIsRetrying] = useState(false);
    const abortRef = useRef(false);
    const retry = useCallback(async (fn, maxRetries = DEFAULT_MAX_RETRIES) => {
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
                }
                catch (error) {
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
        }
        finally {
            setIsRetrying(false);
        }
    }, []);
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
const RATE_LIMIT_STATUS = 429;
const HEADER_RETRY_AFTER = 'retry-after';
function parseRetryAfter(value) {
    if (!value)
        return 60;
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
export function useRateLimit() {
    const [isRateLimited, setIsRateLimited] = useState(false);
    const [retryAfter, setRetryAfter] = useState(0);
    const timerRef = useRef(null);
    const clearRateLimit = useCallback(() => {
        setIsRateLimited(false);
        setRetryAfter(0);
        if (timerRef.current) {
            clearTimeout(timerRef.current);
            timerRef.current = null;
        }
    }, []);
    const handleRateLimit = useCallback((response) => {
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
    }, [isRateLimited, clearRateLimit]);
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
/**
 * Hook for detecting browser online/offline state.
 *
 * @example
 * ```tsx
 * const { isOffline, wasOffline, syncWhenOnline } = useOffline();
 * if (isOffline) return <OfflineBanner />;
 * ```
 */
export function useOffline() {
    const [isOffline, setIsOffline] = useState(!navigator.onLine);
    const [wasOffline, setWasOffline] = useState(false);
    const pendingSyncRef = useRef(null);
    useEffect(() => {
        const handleOnline = () => {
            setIsOffline(false);
            setWasOffline(true);
            if (pendingSyncRef.current) {
                try {
                    void pendingSyncRef.current();
                }
                catch {
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
    const syncWhenOnline = useCallback((fn) => {
        if (!navigator.onLine) {
            pendingSyncRef.current = fn;
            return;
        }
        try {
            void fn();
        }
        catch {
            // Silently ignore sync errors
        }
    }, []);
    return {
        isOffline,
        wasOffline,
        syncWhenOnline,
    };
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
export function useV2ApiError() {
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

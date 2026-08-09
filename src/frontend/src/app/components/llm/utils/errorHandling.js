/**
 * Unified Error Handling for LLM Module
 * 统一错误处理机制
 */
import { useState, useCallback } from 'react';
import { devLogger } from '@/app/utils/devLogger';
// ============================================================================
// Error Creation
// ============================================================================
export function createError(message, category = 'unknown', options = {}) {
    return {
        message,
        category,
        code: options.code,
        originalError: options.originalError,
        timestamp: new Date().toISOString(),
        context: options.context,
        skipUiNotification: options.skipUiNotification,
        recoverable: options.recoverable ?? false,
    };
}
// ============================================================================
// Error Normalization
// ============================================================================
export function normalizeError(error, context) {
    // 已经是 AppError
    if (isAppError(error)) {
        return error;
    }
    // Error 实例
    if (error instanceof Error) {
        const category = categorizeError(error);
        return createError(error.message, category, {
            originalError: error,
            context,
            recoverable: isRecoverable(category),
        });
    }
    // 字符串错误
    if (typeof error === 'string') {
        return createError(error, 'unknown', { context, recoverable: false });
    }
    // 对象错误
    if (typeof error === 'object' && error !== null) {
        const err = error;
        const message = typeof err.message === 'string' ? err.message : 'Unknown error';
        return createError(message, 'unknown', {
            originalError: error,
            context,
            recoverable: false
        });
    }
    // 其他
    return createError('Unknown error occurred', 'unknown', {
        originalError: error,
        context,
        recoverable: false
    });
}
// ============================================================================
// Type Guard
// ============================================================================
export function isAppError(error) {
    return (typeof error === 'object' &&
        error !== null &&
        'message' in error &&
        'category' in error &&
        'timestamp' in error &&
        'recoverable' in error);
}
// ============================================================================
// Error Categorization
// ============================================================================
function categorizeError(error) {
    const message = error.message.toLowerCase();
    // 网络错误
    if (message.includes('network') ||
        message.includes('fetch') ||
        message.includes('connection') ||
        message.includes('econnrefused') ||
        message.includes('timeout') ||
        (error.name === 'TypeError' && message.includes('fetch'))) {
        return 'network';
    }
    // 认证错误
    if (message.includes('auth') ||
        message.includes('unauthorized') ||
        message.includes('forbidden') ||
        message.includes('401') ||
        message.includes('403') ||
        message.includes('api key')) {
        return 'authentication';
    }
    // 超时错误
    if (message.includes('timeout') ||
        message.includes('aborted') ||
        error.name === 'AbortError') {
        return 'timeout';
    }
    // 验证错误
    if (message.includes('validation') ||
        message.includes('invalid') ||
        message.includes('required') ||
        message.includes('missing')) {
        return 'validation';
    }
    // 取消错误
    if (message.includes('cancelled') ||
        message.includes('canceled') ||
        message.includes('abort')) {
        return 'cancelled';
    }
    return 'unknown';
}
function isRecoverable(category) {
    switch (category) {
        case 'network':
        case 'timeout':
            return true;
        case 'authentication':
        case 'validation':
        case 'runtime':
        case 'cancelled':
        case 'unknown':
        default:
            return false;
    }
}
// ============================================================================
// User-Friendly Messages
// ============================================================================
const ERROR_MESSAGES = {
    network: '网络连接失败，请检查网络设置后重试',
    authentication: '认证失败，请检查 API 密钥配置',
    validation: '配置验证失败，请检查输入参数',
    runtime: '运行时错误，请联系技术支持',
    timeout: '请求超时，请稍后重试',
    cancelled: '操作已取消',
    unknown: '发生未知错误，请稍后重试',
};
export function getUserFriendlyMessage(error) {
    if (isAppError(error)) {
        return ERROR_MESSAGES[error.category] || error.message;
    }
    return normalizeError(error).message;
}
class InMemoryErrorLogger {
    constructor(maxSize = 100) {
        this.errors = [];
        this.maxSize = maxSize;
    }
    log(error) {
        this.errors.push(error);
        if (this.errors.length > this.maxSize) {
            this.errors = this.errors.slice(-this.maxSize);
        }
        // 开发环境输出到控制台
        if (process.env.NODE_ENV === 'development') {
            devLogger.error('[AppError]', error);
        }
    }
    getRecentErrors(limit = 10) {
        return this.errors.slice(-limit);
    }
    clear() {
        this.errors = [];
    }
}
export const errorLogger = new InMemoryErrorLogger();
export function withErrorHandling(fn, options = {}) {
    return async (...args) => {
        try {
            return await fn(...args);
        }
        catch (error) {
            const appError = normalizeError(error, {
                ...options.context,
                args: args.map(arg => typeof arg === 'object' ? '[Object]' : String(arg)),
            });
            errorLogger.log(appError);
            if (options.onError) {
                options.onError(appError);
            }
            if (options.rethrow) {
                throw appError;
            }
            return undefined;
        }
    };
}
export function useErrorHandling(options = {}) {
    const [error, setErrorState] = useState(null);
    const setError = useCallback((err) => {
        const appError = isAppError(err) ? err : normalizeError(err);
        setErrorState(appError);
        errorLogger.log(appError);
        if (options.onError && !appError.skipUiNotification) {
            options.onError(appError);
        }
    }, [options.onError]);
    const clearError = useCallback(() => {
        setErrorState(null);
    }, []);
    const handleError = useCallback((err) => {
        setError(err);
    }, [setError]);
    const withErrorHandler = useCallback((fn) => {
        return async (...args) => {
            try {
                return await fn(...args);
            }
            catch (err) {
                handleError(err);
                return undefined;
            }
        };
    }, [handleError]);
    return {
        error,
        setError,
        clearError,
        handleError,
        withErrorHandler,
    };
}
// ============================================================================
// Common Error Scenarios
// ============================================================================
export const CommonErrors = {
    networkError: (originalError) => createError('Network connection failed', 'network', {
        originalError,
        recoverable: true
    }),
    authError: (originalError) => createError('Authentication failed', 'authentication', {
        originalError,
        recoverable: false
    }),
    timeoutError: (originalError) => createError('Request timed out', 'timeout', {
        originalError,
        recoverable: true
    }),
    validationError: (message, context) => createError(message, 'validation', { context, recoverable: false }),
    cancelledError: () => createError('Operation cancelled', 'cancelled', {
        skipUiNotification: true,
        recoverable: true
    }),
    providerNotFound: (providerId) => createError(`Provider "${providerId}" not found`, 'validation', {
        context: { providerId },
        recoverable: false
    }),
    modelNotConfigured: (roleId) => createError(`Model not configured for role "${roleId}"`, 'validation', {
        context: { roleId },
        recoverable: false,
    }),
};
export function getErrorFallbackMessage(error) {
    const appError = normalizeError(error);
    return getUserFriendlyMessage(appError);
}

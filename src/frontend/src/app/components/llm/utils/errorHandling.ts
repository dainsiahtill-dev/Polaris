/**
 * Unified Error Handling for LLM Module
 * 统一错误处理机制
 */

import { useState, useCallback } from 'react';
import { devLogger } from '@/app/utils/devLogger';

// ============================================================================
// Error Types
// ============================================================================

export type ErrorCategory = 
  | 'network'
  | 'authentication' 
  | 'validation'
  | 'runtime'
  | 'timeout'
  | 'cancelled'
  | 'unknown';

export interface AppError {
  message: string;
  category: ErrorCategory;
  code?: string;
  originalError?: unknown;
  timestamp: string;
  context?: Record<string, unknown>;
  /** 是否应在 UI 上显示 */
  skipUiNotification?: boolean;
  /** 是否可恢复 */
  recoverable: boolean;
}

// ============================================================================
// Error Creation
// ============================================================================

export function createError(
  message: string,
  category: ErrorCategory = 'unknown',
  options: {
    code?: string;
    originalError?: unknown;
    context?: Record<string, unknown>;
    skipUiNotification?: boolean;
    recoverable?: boolean;
  } = {}
): AppError {
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

export function normalizeError(error: unknown, context?: Record<string, unknown>): AppError {
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
    const err = error as Record<string, unknown>;
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

export function isAppError(error: unknown): error is AppError {
  return (
    typeof error === 'object' &&
    error !== null &&
    'message' in error &&
    'category' in error &&
    'timestamp' in error &&
    'recoverable' in error
  );
}

// ============================================================================
// Error Categorization
// ============================================================================

function categorizeError(error: Error): ErrorCategory {
  const message = error.message.toLowerCase();
  
  // 网络错误
  if (
    message.includes('network') ||
    message.includes('fetch') ||
    message.includes('connection') ||
    message.includes('econnrefused') ||
    message.includes('timeout') ||
    (error.name === 'TypeError' && message.includes('fetch'))
  ) {
    return 'network';
  }

  // 认证错误
  if (
    message.includes('auth') ||
    message.includes('unauthorized') ||
    message.includes('forbidden') ||
    message.includes('401') ||
    message.includes('403') ||
    message.includes('api key')
  ) {
    return 'authentication';
  }

  // 超时错误
  if (
    message.includes('timeout') ||
    message.includes('aborted') ||
    error.name === 'AbortError'
  ) {
    return 'timeout';
  }

  // 验证错误
  if (
    message.includes('validation') ||
    message.includes('invalid') ||
    message.includes('required') ||
    message.includes('missing')
  ) {
    return 'validation';
  }

  // 取消错误
  if (
    message.includes('cancelled') ||
    message.includes('canceled') ||
    message.includes('abort')
  ) {
    return 'cancelled';
  }

  return 'unknown';
}

function isRecoverable(category: ErrorCategory): boolean {
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

const ERROR_MESSAGES: Record<ErrorCategory, string> = {
  network: '网络连接失败，请检查网络设置后重试',
  authentication: '认证失败，请检查 API 密钥配置',
  validation: '配置验证失败，请检查输入参数',
  runtime: '运行时错误，请联系技术支持',
  timeout: '请求超时，请稍后重试',
  cancelled: '操作已取消',
  unknown: '发生未知错误，请稍后重试',
};

export function getUserFriendlyMessage(error: AppError | unknown): string {
  if (isAppError(error)) {
    return ERROR_MESSAGES[error.category] || error.message;
  }
  return normalizeError(error).message;
}

// ============================================================================
// Error Logging
// ============================================================================

export interface ErrorLogger {
  log(error: AppError): void;
  getRecentErrors(limit?: number): AppError[];
  clear(): void;
}

class InMemoryErrorLogger implements ErrorLogger {
  private errors: AppError[] = [];
  private readonly maxSize: number;

  constructor(maxSize = 100) {
    this.maxSize = maxSize;
  }

  log(error: AppError): void {
    this.errors.push(error);
    if (this.errors.length > this.maxSize) {
      this.errors = this.errors.slice(-this.maxSize);
    }
    
    // 开发环境输出到控制台
    if (process.env.NODE_ENV === 'development') {
      devLogger.error('[AppError]', error);
    }
  }

  getRecentErrors(limit = 10): AppError[] {
    return this.errors.slice(-limit);
  }

  clear(): void {
    this.errors = [];
  }
}

export const errorLogger: ErrorLogger = new InMemoryErrorLogger();

// ============================================================================
// Async Error Handler Wrapper
// ============================================================================

export type AsyncFunction<T extends unknown[], R> = (...args: T) => Promise<R>;

export function withErrorHandling<T extends unknown[], R>(
  fn: AsyncFunction<T, R>,
  options: {
    context?: Record<string, unknown>;
    onError?: (error: AppError) => void;
    rethrow?: boolean;
  } = {}
): AsyncFunction<T, R | undefined> {
  return async (...args: T): Promise<R | undefined> => {
    try {
      return await fn(...args);
    } catch (error) {
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

// ============================================================================
// React Hook for Error Handling
// ============================================================================

export interface UseErrorHandlingResult {
  error: AppError | null;
  setError: (error: AppError | unknown) => void;
  clearError: () => void;
  handleError: (error: unknown) => void;
  withErrorHandler: <T extends unknown[], R>(
    fn: AsyncFunction<T, R>
  ) => AsyncFunction<T, R | undefined>;
}

export function useErrorHandling(
  options: {
    onError?: (error: AppError) => void;
  } = {}
): UseErrorHandlingResult {
  const [error, setErrorState] = useState<AppError | null>(null);

  const setError = useCallback((err: AppError | unknown) => {
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

  const handleError = useCallback((err: unknown) => {
    setError(err);
  }, [setError]);

  const withErrorHandler = useCallback(<T extends unknown[], R>(
    fn: AsyncFunction<T, R>
  ): AsyncFunction<T, R | undefined> => {
    return async (...args: T) => {
      try {
        return await fn(...args);
      } catch (err) {
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
  networkError: (originalError?: unknown) => 
    createError('Network connection failed', 'network', { 
      originalError, 
      recoverable: true 
    }),
  
  authError: (originalError?: unknown) => 
    createError('Authentication failed', 'authentication', { 
      originalError, 
      recoverable: false 
    }),
  
  timeoutError: (originalError?: unknown) => 
    createError('Request timed out', 'timeout', { 
      originalError, 
      recoverable: true 
    }),
  
  validationError: (message: string, context?: Record<string, unknown>) => 
    createError(message, 'validation', { context, recoverable: false }),
  
  cancelledError: () => 
    createError('Operation cancelled', 'cancelled', { 
      skipUiNotification: true,
      recoverable: true 
    }),
  
  providerNotFound: (providerId: string) => 
    createError(`Provider "${providerId}" not found`, 'validation', { 
      context: { providerId },
      recoverable: false 
    }),
  
  modelNotConfigured: (roleId: string) => 
    createError(`Model not configured for role "${roleId}"`, 'validation', {
      context: { roleId },
      recoverable: false,
    }),
} as const;

// ============================================================================
// Error Boundary Helper
// ============================================================================

export interface ErrorBoundaryFallbackProps {
  error: AppError;
  resetError: () => void;
}

export function getErrorFallbackMessage(error: unknown): string {
  const appError = normalizeError(error);
  return getUserFriendlyMessage(appError);
}

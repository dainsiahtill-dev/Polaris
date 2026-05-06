/**
 * ApiErrorBoundary - API error boundary component
 *
 * Features:
 * - Catches API errors
 * - Displays friendly error message
 * - Provides retry button
 * - Uses useApiError hook
 */

import React, { useCallback } from 'react';
import { useApiError } from '@/app/hooks/useV2ApiError';

export interface ApiErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  onRetry?: () => void;
}

export const ApiErrorBoundary: React.FC<ApiErrorBoundaryProps> = ({
  children,
  fallback,
  onRetry,
}) => {
  const { error, clearError, hasError } = useApiError();

  const handleRetry = useCallback(() => {
    clearError();
    onRetry?.();
  }, [clearError, onRetry]);

  if (!hasError) {
    return <>{children}</>;
  }

  if (fallback) {
    return <>{fallback}</>;
  }

  return (
    <div
      className="api-error-boundary"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
        border: '1px solid #ffcdd2',
        borderRadius: '8px',
        background: '#ffebee',
        minHeight: '120px',
      }}
      role="alert"
      aria-live="assertive"
    >
      <h3
        style={{
          margin: '0 0 8px 0',
          fontSize: '16px',
          fontWeight: 600,
          color: '#c62828',
        }}
      >
        Something went wrong
      </h3>
      {error && (
        <p
          style={{
            margin: '0 0 16px 0',
            fontSize: '14px',
            color: '#d32f2f',
            textAlign: 'center',
          }}
        >
          {error.message}
          {error.code && (
            <span
              style={{
                display: 'block',
                marginTop: '4px',
                fontSize: '12px',
                color: '#ef5350',
              }}
            >
              Code: {error.code}
            </span>
          )}
        </p>
      )}
      <button
        onClick={handleRetry}
        style={{
          padding: '8px 16px',
          fontSize: '14px',
          fontWeight: 500,
          color: '#fff',
          background: '#d32f2f',
          border: 'none',
          borderRadius: '4px',
          cursor: 'pointer',
        }}
        aria-label="Retry"
      >
        Retry
      </button>
    </div>
  );
};

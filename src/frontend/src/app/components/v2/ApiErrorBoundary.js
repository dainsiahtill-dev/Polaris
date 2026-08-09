import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * ApiErrorBoundary - API error boundary component
 *
 * Features:
 * - Catches API errors
 * - Displays friendly error message
 * - Provides retry button
 * - Uses useApiError hook
 */
import { useCallback } from 'react';
import { useApiError } from '@/app/hooks/useV2ApiError';
export const ApiErrorBoundary = ({ children, fallback, onRetry, }) => {
    const { error, clearError, hasError } = useApiError();
    const handleRetry = useCallback(() => {
        clearError();
        onRetry?.();
    }, [clearError, onRetry]);
    if (!hasError) {
        return _jsx(_Fragment, { children: children });
    }
    if (fallback) {
        return _jsx(_Fragment, { children: fallback });
    }
    return (_jsxs("div", { className: "api-error-boundary", style: {
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '24px',
            border: '1px solid #ffcdd2',
            borderRadius: '8px',
            background: '#ffebee',
            minHeight: '120px',
        }, role: "alert", "aria-live": "assertive", children: [_jsx("h3", { style: {
                    margin: '0 0 8px 0',
                    fontSize: '16px',
                    fontWeight: 600,
                    color: '#c62828',
                }, children: "Something went wrong" }), error && (_jsxs("p", { style: {
                    margin: '0 0 16px 0',
                    fontSize: '14px',
                    color: '#d32f2f',
                    textAlign: 'center',
                }, children: [error.message, error.code && (_jsxs("span", { style: {
                            display: 'block',
                            marginTop: '4px',
                            fontSize: '12px',
                            color: '#ef5350',
                        }, children: ["Code: ", error.code] }))] })), _jsx("button", { onClick: handleRetry, style: {
                    padding: '8px 16px',
                    fontSize: '14px',
                    fontWeight: 500,
                    color: '#fff',
                    background: '#d32f2f',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                }, "aria-label": "Retry", children: "Retry" })] }));
};

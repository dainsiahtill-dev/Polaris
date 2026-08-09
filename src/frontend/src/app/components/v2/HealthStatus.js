import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * HealthStatus - Health status indicator
 *
 * Features:
 * - Green/yellow/red indicator based on /v2/health
 * - Initial/manual health check only
 * - Show detailed status on click
 */
import { useState, useCallback, useEffect } from 'react';
import { useHealth } from '@/app/hooks/useV2Api';
import { useV2ApiError } from '@/app/hooks/useV2ApiError';
function getHealthColor(status) {
    const token = (status || '').toLowerCase();
    if (token === 'healthy' || token === 'ok')
        return 'green';
    if (token === 'degraded')
        return 'yellow';
    if (token === 'unhealthy' || token === 'error')
        return 'red';
    return 'gray';
}
function healthColorClasses(color) {
    switch (color) {
        case 'green':
            return 'bg-green-500';
        case 'yellow':
            return 'bg-yellow-500';
        case 'red':
            return 'bg-red-500';
        case 'gray':
        default:
            return 'bg-gray-400';
    }
}
function healthLabel(color) {
    switch (color) {
        case 'green':
            return 'Healthy';
        case 'yellow':
            return 'Degraded';
        case 'red':
            return 'Unhealthy';
        case 'gray':
        default:
            return 'Unknown';
    }
}
export function HealthStatus() {
    const { health, loading, error, check } = useHealth();
    const { apiError } = useV2ApiError();
    const [showDetails, setShowDetails] = useState(false);
    const color = getHealthColor(health?.status);
    const label = health?.status ? health.status : healthLabel(color);
    const handleToggleDetails = useCallback(() => {
        setShowDetails((prev) => !prev);
    }, []);
    const handleRefresh = useCallback(() => {
        void check();
    }, [check]);
    useEffect(() => {
        if (error) {
            apiError.setError({ code: 'HEALTH_CHECK_ERROR', message: error, status: 500 });
        }
    }, [error, apiError]);
    return (_jsxs("div", { className: "inline-flex flex-col gap-2", children: [_jsxs("button", { onClick: handleToggleDetails, className: "flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors", "aria-label": "Toggle health details", children: [_jsx("span", { className: `h-3 w-3 rounded-full ${healthColorClasses(color)} ${loading ? 'animate-pulse' : ''}`, "aria-hidden": "true" }), _jsx("span", { className: "text-sm font-medium text-gray-700 dark:text-gray-300", children: label }), loading && (_jsx("span", { className: "text-xs text-gray-400", children: "checking..." }))] }), showDetails && (_jsxs("div", { className: "border rounded-lg p-3 bg-white dark:bg-gray-900 shadow-sm min-w-[240px]", children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsx("h3", { className: "text-sm font-semibold text-gray-900 dark:text-gray-100", children: "Health Details" }), _jsx("button", { onClick: handleRefresh, disabled: loading, className: "text-xs text-blue-600 hover:text-blue-700 disabled:opacity-50", children: "Refresh" })] }), health && (_jsxs("dl", { className: "space-y-1 text-sm", children: [_jsxs("div", { className: "flex justify-between", children: [_jsx("dt", { className: "text-gray-500 dark:text-gray-400", children: "Status" }), _jsx("dd", { className: "font-medium text-gray-900 dark:text-gray-100", children: health.status || 'N/A' })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("dt", { className: "text-gray-500 dark:text-gray-400", children: "Version" }), _jsx("dd", { className: "font-medium text-gray-900 dark:text-gray-100", children: health.version || 'N/A' })] }), _jsxs("div", { className: "flex justify-between", children: [_jsx("dt", { className: "text-gray-500 dark:text-gray-400", children: "Timestamp" }), _jsx("dd", { className: "font-medium text-gray-900 dark:text-gray-100", children: health.timestamp ? new Date(health.timestamp).toLocaleString() : 'N/A' })] })] })), (error || apiError.hasError) && (_jsx("div", { className: "mt-2 text-xs text-red-600 dark:text-red-400", children: error || apiError.error?.message }))] }))] }));
}

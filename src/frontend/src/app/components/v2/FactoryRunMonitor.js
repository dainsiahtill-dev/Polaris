import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * FactoryRunMonitor - Factory run monitoring
 *
 * Features:
 * - List of recent runs
 * - Status badges (pending, running, completed, failed)
 * - Cancel button
 * - View artifacts link
 */
import { useState, useCallback } from 'react';
import { useFactoryRuns } from '@/app/hooks/useV2Api';
import { useV2ApiError } from '@/app/hooks/useV2ApiError';
function normalizeStatus(type, stage) {
    const token = (type || stage || '').toLowerCase();
    if (token.includes('pend'))
        return 'pending';
    if (token.includes('run') || token.includes('start') || token.includes('progress'))
        return 'running';
    if (token.includes('complete') || token.includes('success') || token.includes('done'))
        return 'completed';
    if (token.includes('fail') || token.includes('error') || token.includes('abort'))
        return 'failed';
    return 'unknown';
}
function statusBadgeClasses(status) {
    switch (status) {
        case 'pending':
            return 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300';
        case 'running':
            return 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300';
        case 'completed':
            return 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300';
        case 'failed':
            return 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300';
        case 'unknown':
        default:
            return 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300';
    }
}
export function FactoryRunMonitor({ runId, onCancel, onViewArtifacts, }) {
    const { events, auditBundle, loading, error, fetchEvents, fetchAuditBundle } = useFactoryRuns();
    const { apiError } = useV2ApiError();
    const [expanded, setExpanded] = useState(false);
    const handleLoad = useCallback(() => {
        void fetchEvents(runId, { limit: 50 });
    }, [fetchEvents, runId]);
    const handleToggleExpand = useCallback(() => {
        setExpanded((prev) => {
            const next = !prev;
            if (next && !events) {
                void fetchEvents(runId, { limit: 50 });
            }
            return next;
        });
    }, [events, fetchEvents, runId]);
    const handleViewAudit = useCallback(() => {
        void fetchAuditBundle(runId);
    }, [fetchAuditBundle, runId]);
    const handleCancel = useCallback(() => {
        onCancel?.(runId);
    }, [onCancel, runId]);
    const handleViewArtifacts = useCallback(() => {
        onViewArtifacts?.(runId);
    }, [onViewArtifacts, runId]);
    const latestEvent = events?.events?.[events.events.length - 1];
    const status = normalizeStatus(latestEvent?.type, latestEvent?.stage);
    return (_jsxs("div", { className: "border rounded-lg bg-white dark:bg-gray-900", children: [_jsxs("div", { className: "flex items-center justify-between px-4 py-3 border-b", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("h3", { className: "text-sm font-semibold text-gray-900 dark:text-gray-100", children: "Factory Run" }), _jsx("span", { className: `inline-flex px-2 py-0.5 text-xs font-medium rounded-full ${statusBadgeClasses(status)}`, children: status })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("button", { onClick: handleLoad, disabled: loading, className: "text-xs text-blue-600 hover:text-blue-700 disabled:opacity-50", children: "Refresh" }), _jsx("button", { onClick: handleToggleExpand, className: "text-xs text-gray-600 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200", children: expanded ? 'Collapse' : 'Expand' })] })] }), _jsxs("div", { className: "px-4 py-3", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "text-sm text-gray-700 dark:text-gray-300", children: [_jsx("span", { className: "font-medium", children: "Run ID:" }), ' ', _jsx("code", { className: "text-xs bg-gray-100 dark:bg-gray-800 px-1 py-0.5 rounded", children: runId })] }), _jsxs("div", { className: "flex gap-2", children: [status === 'running' && (_jsx("button", { onClick: handleCancel, className: "px-3 py-1 text-xs font-medium text-red-700 bg-red-50 dark:bg-red-900/20 dark:text-red-300 rounded hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors", children: "Cancel" })), _jsx("button", { onClick: handleViewArtifacts, className: "px-3 py-1 text-xs font-medium text-blue-700 bg-blue-50 dark:bg-blue-900/20 dark:text-blue-300 rounded hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors", children: "View Artifacts" }), _jsx("button", { onClick: handleViewAudit, className: "px-3 py-1 text-xs font-medium text-gray-700 bg-gray-50 dark:bg-gray-800 dark:text-gray-300 rounded hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors", children: "Audit" })] })] }), events && events.total !== undefined && (_jsxs("p", { className: "text-xs text-gray-500 dark:text-gray-400 mt-2", children: [events.total, " event(s)"] }))] }), expanded && events && events.events && events.events.length > 0 && (_jsx("div", { className: "border-t px-4 py-3 max-h-64 overflow-y-auto", children: _jsx("ul", { className: "space-y-2", children: events.events.map((event, index) => {
                        const eventStatus = normalizeStatus(event.type, event.stage);
                        return (_jsxs("li", { className: "text-sm border rounded p-2 bg-gray-50 dark:bg-gray-800/50", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "font-medium text-gray-800 dark:text-gray-200", children: event.type || 'Event' }), _jsx("span", { className: `inline-flex px-1.5 py-0.5 text-xs rounded ${statusBadgeClasses(eventStatus)}`, children: eventStatus })] }), event.stage && (_jsxs("p", { className: "text-xs text-gray-500 dark:text-gray-400 mt-0.5", children: ["Stage: ", event.stage] })), event.message && (_jsx("p", { className: "text-xs text-gray-600 dark:text-gray-400 mt-0.5", children: event.message })), event.timestamp && (_jsx("p", { className: "text-xs text-gray-400 dark:text-gray-500 mt-0.5", children: new Date(event.timestamp).toLocaleString() }))] }, event.event_id || `${runId}-${index}`));
                    }) }) })), expanded && events && events.events && events.events.length === 0 && (_jsx("div", { className: "border-t px-4 py-6 text-center text-sm text-gray-400 dark:text-gray-600", children: "No events found for this run." })), auditBundle && (_jsxs("div", { className: "border-t px-4 py-3", children: [_jsx("h4", { className: "text-xs font-semibold text-gray-700 dark:text-gray-300 mb-1", children: "Audit Bundle" }), _jsx("pre", { className: "text-xs bg-gray-100 dark:bg-gray-800 rounded p-2 overflow-x-auto text-gray-700 dark:text-gray-300", children: JSON.stringify(auditBundle.bundle, null, 2) })] })), (error || apiError.hasError) && (_jsx("div", { className: "border-t px-4 py-3 text-xs text-red-600 dark:text-red-400", children: error || apiError.error?.message }))] }));
}

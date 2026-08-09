import { jsx as _jsx } from "react/jsx-runtime";
import { RoleRunEvidenceStrip } from '@/app/components/common/RoleRunEvidenceStrip';
function runProgress(value) {
    const numeric = typeof value === 'number' ? value : Number(value);
    if (!Number.isFinite(numeric))
        return 'progress=0%';
    return `progress=${Math.max(0, Math.min(100, Math.round(numeric)))}%`;
}
export function RoleFactoryRunEvidenceStrip({ tone, testId, runEvidence, cancelEvidence, realtimePushActive, cancelDisabled, onRefresh, onCancel, }) {
    const runId = String(runEvidence.runId || '').trim();
    if (!runId)
        return null;
    const phase = String(runEvidence.data?.phase || 'phase unknown').trim();
    const message = runEvidence.data?.failure?.detail || runEvidence.data?.summary_md || null;
    return (_jsx(RoleRunEvidenceStrip, { tone: tone, testId: testId, endpoint: `/v2/factory/runs/${runId}`, loading: runEvidence.loading, error: runEvidence.error, status: runEvidence.data?.status, details: [`phase=${phase}`, runProgress(runEvidence.data?.progress)], message: message, refreshTestId: `${testId}-refresh`, refreshDisabled: !runId || runEvidence.loading, refreshLoading: runEvidence.loading, realtimePushActive: realtimePushActive, onRefresh: onRefresh, cancelTestId: `${testId}-cancel`, cancelDisabled: cancelDisabled, cancelLoading: cancelEvidence.loading, onCancel: onCancel, cancelResultTestId: `${testId}-cancel-result`, cancelResultEndpoint: `/v2/factory/runs/${runId}/control`, cancelResultVisible: cancelEvidence.runId === runId
            && (cancelEvidence.loading || Boolean(cancelEvidence.message) || Boolean(cancelEvidence.error)), cancelResultLoading: cancelEvidence.loading, cancelResultMessage: cancelEvidence.message, cancelResultError: cancelEvidence.error }));
}

import { useCallback, useEffect, useState } from 'react';
const EMPTY_LLM_RUNTIME_STATE = {
    state: 'UNKNOWN',
    blockedRoles: [],
    requiredRoles: [],
    lastUpdated: null,
    roleDetails: {},
    skippedBindings: [],
};
const READINESS_ISSUE_LABELS = {
    director_codex_invalid_sandbox: 'Director 的 Codex CLI 沙箱配置无效',
    director_codex_read_only_sandbox: 'Director 的 Codex CLI 当前是只读沙箱',
    director_minimax_tool_contract_unverified: 'Director 绑定的 MiniMax 尚未通过工具调用合同验证',
    director_tool_choice_disabled: 'Director 绑定的 Provider 不支持强制工具调用',
    model_mismatch: '最近测试记录的模型不是当前绑定模型',
    provider_mismatch: '最近测试记录的 Provider 不是当前绑定 Provider',
    readiness_failed: '最近一次深度测试失败',
    readiness_stale: '历史测试状态待刷新',
    role_readiness_missing: '该角色还没有通过必需的深度测试',
    runtime_unsupported: '当前 Provider 类型不支持该角色运行时',
    timestamp_invalid: '测试记录时间无效',
    timestamp_missing: '测试记录缺少时间',
    tested_model_missing: '测试记录缺少模型身份',
    unassigned_provider: '该角色未绑定 Provider',
};
function normalizeRoleList(value) {
    if (!Array.isArray(value))
        return [];
    return value
        .map((role) => String(role || '').trim().toLowerCase())
        .filter(Boolean);
}
function readText(value) {
    return typeof value === 'string' ? value.trim() : '';
}
function readPositiveIntOrNull(value) {
    const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number.parseInt(value, 10) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}
function readBooleanOrNull(value) {
    return typeof value === 'boolean' ? value : null;
}
function normalizeRoleBindings(value) {
    if (!Array.isArray(value))
        return [];
    return value
        .map((item) => {
        const binding = item && typeof item === 'object' ? item : {};
        const providerId = readText(binding.provider_id);
        const model = readText(binding.model);
        if (!providerId && !model)
            return null;
        return {
            bindingId: readText(binding.binding_id) || readText(binding.bindingId) || null,
            providerId,
            providerName: readText(binding.provider_name),
            providerType: readText(binding.provider_type),
            model,
            profile: readText(binding.profile),
            maxContextTokens: readPositiveIntOrNull(binding.max_context_tokens),
            maxOutputTokens: readPositiveIntOrNull(binding.max_output_tokens),
            skipped: readBooleanOrNull(binding.skipped) ?? undefined,
            skipReason: readText(binding.skip_reason) || readText(binding.skipReason) || undefined,
        };
    })
        .filter((item) => item !== null);
}
function normalizeRoleDetails(value) {
    const roles = value && typeof value === 'object' ? value : {};
    return Object.fromEntries(Object.entries(roles)
        .map(([rawRoleId, rawRole]) => {
        const roleId = readText(rawRoleId).toLowerCase();
        const role = rawRole && typeof rawRole === 'object' ? rawRole : {};
        const bindings = normalizeRoleBindings(role.bindings);
        return [roleId, {
                providerId: readText(role.provider_id),
                providerName: readText(role.provider_name),
                providerType: readText(role.provider_type),
                model: readText(role.model),
                maxContextTokens: readPositiveIntOrNull(role.max_context_tokens),
                maxOutputTokens: readPositiveIntOrNull(role.max_output_tokens),
                bindings,
                ready: readBooleanOrNull(role.ready),
                runtimeSupported: readBooleanOrNull(role.runtime_supported),
                runtimeIssue: readText(role.runtime_issue),
                readinessIssue: readText(role.readiness_issue),
                readinessSource: readText(role.readiness_source),
                testedProviderId: readText(role.tested_provider_id),
                testedModel: readText(role.tested_model),
                testedTimestamp: readText(role.tested_timestamp) || null,
                timestamp: readText(role.timestamp) || null,
            }];
    })
        .filter(([roleId]) => Boolean(roleId)));
}
function normalizeSkippedBindings(value) {
    if (!Array.isArray(value))
        return [];
    return value
        .map((item) => {
        const record = item && typeof item === 'object' ? item : {};
        const providerId = readText(record.provider_id) || readText(record.providerId);
        const model = readText(record.model);
        const skipReason = readText(record.skip_reason) || readText(record.skipReason) || readText(record.reason);
        if (!providerId && !model && !skipReason)
            return null;
        return {
            roleId: readText(record.role_id) || readText(record.roleId) || null,
            bindingId: readText(record.binding_id) || readText(record.bindingId) || null,
            providerId: providerId || null,
            providerName: readText(record.provider_name) || readText(record.providerName) || null,
            model: model || null,
            skipReason: skipReason || 'binding_skipped',
            skippedAt: readText(record.skipped_at) || readText(record.skippedAt) || null,
        };
    })
        .filter((item) => item !== null);
}
function roleDetailFor(roleDetails, role) {
    return roleDetails[role] || (role === 'architect' ? roleDetails.docs : undefined);
}
function isDeprecatedReadinessStaleBlock(detail) {
    return (detail?.readinessIssue === 'readiness_stale'
        && detail.runtimeSupported !== false
        && !detail.runtimeIssue);
}
function toEpoch(value) {
    const parsed = Date.parse(String(value || '').trim());
    return Number.isFinite(parsed) ? parsed : 0;
}
function isStaleLlmRuntimePayload(current, next) {
    const currentEpoch = toEpoch(current.lastUpdated);
    const nextEpoch = toEpoch(next.lastUpdated);
    if (currentEpoch <= 0 || nextEpoch <= 0 || nextEpoch >= currentEpoch) {
        return false;
    }
    if (current.state === 'BLOCKED' && next.state === 'READY') {
        return false;
    }
    return true;
}
export function normalizeLlmRuntimeGatePayload(payload) {
    const record = payload && typeof payload === 'object' ? payload : {};
    const stateToken = String(record.state || '').trim().toUpperCase();
    const roleDetails = normalizeRoleDetails(record.roles);
    const skippedBindings = normalizeSkippedBindings(record.skipped_bindings ?? record.skippedBindings);
    const rawBlockedRoles = normalizeRoleList(record.blocked_roles);
    const blockedRoles = rawBlockedRoles
        .filter((role) => !isDeprecatedReadinessStaleBlock(roleDetailFor(roleDetails, role)));
    const requiredRoles = normalizeRoleList(record.required_ready_roles);
    const staleOnlyBlock = rawBlockedRoles.length > 0 && blockedRoles.length === 0;
    const state = stateToken === 'READY'
        ? 'READY'
        : stateToken === 'BLOCKED'
            ? staleOnlyBlock
                ? 'READY'
                : 'BLOCKED'
            : stateToken === 'DEGRADED'
                ? 'DEGRADED'
                : 'UNKNOWN';
    return {
        state,
        blockedRoles,
        requiredRoles,
        lastUpdated: typeof record.last_updated === 'string' ? record.last_updated : null,
        roleDetails,
        skippedBindings,
    };
}
export function isRoleLlmBlocked(state, role) {
    const token = String(role || '').trim().toLowerCase();
    if (!token)
        return false;
    return (state.state === 'BLOCKED'
        && state.requiredRoles.includes(token)
        && state.blockedRoles.includes(token));
}
export function getRoleLlmBlockedReason(state, role, roleDisplayName) {
    if (!isRoleLlmBlocked(state, role)) {
        return '';
    }
    const displayName = String(roleDisplayName || role || '当前角色').trim() || '当前角色';
    const roleId = String(role || '').trim().toLowerCase();
    const detail = roleDetailFor(state.roleDetails || {}, roleId);
    if (!detail) {
        return `LLM 就绪检查未通过：${displayName} 角色当前绑定的 provider/model 没有通过真实测试，请先在 LLM 设置中重新测试并保存。`;
    }
    const currentProvider = detail.providerId || '未绑定 Provider';
    const currentModel = detail.model || '未绑定模型';
    const issue = detail.readinessIssue || detail.runtimeIssue || 'role_readiness_missing';
    const issueLabel = READINESS_ISSUE_LABELS[issue] || issue;
    const testedProvider = detail.testedProviderId || '';
    const testedModel = detail.testedModel || '';
    const testedAt = detail.testedTimestamp || detail.timestamp || '';
    const testedSummary = testedProvider || testedModel
        ? `最近测试记录是 ${testedProvider || '未知 Provider'} / ${testedModel || '未知模型'}${testedAt ? `（${testedAt}）` : ''}`
        : '最近没有可用的测试记录';
    return `LLM 就绪检查未通过：${displayName} 当前绑定 ${currentProvider} / ${currentModel}；原因：${issueLabel}；${testedSummary}。请在 LLM 设置中重新测试并保存当前绑定。`;
}
export function useLlmRuntimeGate({ workspace, live, llmStatus, }) {
    const [llmRuntimeState, setLlmRuntimeState] = useState(EMPTY_LLM_RUNTIME_STATE);
    const clearLlmRuntimeState = useCallback(() => {
        setLlmRuntimeState(EMPTY_LLM_RUNTIME_STATE);
    }, []);
    const applyLlmStatusPayload = useCallback((payload) => {
        const nextState = normalizeLlmRuntimeGatePayload(payload);
        setLlmRuntimeState((current) => (isStaleLlmRuntimePayload(current, nextState) ? current : nextState));
    }, []);
    const handleLlmStatusChange = useCallback((status) => {
        if (!live) {
            clearLlmRuntimeState();
            return;
        }
        if (status) {
            applyLlmStatusPayload(status);
            return;
        }
        clearLlmRuntimeState();
    }, [applyLlmStatusPayload, clearLlmRuntimeState, live]);
    const getLlmRoleBlockedReason = useCallback((role, roleDisplayName) => getRoleLlmBlockedReason(llmRuntimeState, role, roleDisplayName), [llmRuntimeState]);
    useEffect(() => {
        if (!workspace || !live) {
            clearLlmRuntimeState();
            return;
        }
        if (llmStatus) {
            applyLlmStatusPayload(llmStatus);
        }
    }, [applyLlmStatusPayload, clearLlmRuntimeState, live, llmStatus, workspace]);
    return {
        llmRuntimeState,
        getLlmRoleBlockedReason,
        handleLlmStatusChange,
    };
}

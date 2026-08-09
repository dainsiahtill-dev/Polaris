const ROLE_LABELS = {
    architect: 'Architect',
    chief_engineer: 'Chief Engineer',
    director: 'Director',
    docs: 'Architect',
    pm: 'PM',
    qa: 'QA',
};
const ISSUE_LABELS = {
    model_mismatch: '最近通过测试的模型不是当前绑定模型',
    provider_mismatch: '最近通过测试的 Provider 不是当前绑定 Provider',
    readiness_failed: '最近一次深度测试失败，请重新测试或切换 Provider/模型',
    readiness_stale: '历史测试状态待刷新',
    director_codex_read_only_sandbox: 'Director 的 Codex CLI 当前是只读沙箱，无法落盘代码或文档',
    director_codex_invalid_sandbox: 'Director 的 Codex CLI 沙箱配置无效，无法确认可写能力',
    director_minimax_tool_contract_unverified: 'Director 绑定的 MiniMax 尚未通过工具调用合同验证',
    director_tool_choice_disabled: 'Director 绑定的 Provider 不支持强制工具调用',
    role_readiness_missing: '该角色还没有通过必需的深度测试',
    runtime_unsupported: '当前 Provider 类型不支持该角色运行时',
    timestamp_invalid: '测试记录时间无效，请重新测试当前 Provider/模型',
    timestamp_missing: '测试记录缺少时间，请重新测试当前 Provider/模型',
    tested_model_missing: '测试记录缺少模型身份，无法确认通过对象',
    unassigned_provider: '该角色未绑定 Provider',
};
function normalizeRoleId(value) {
    return value.trim().toLowerCase();
}
function readText(value) {
    return typeof value === 'string' ? value.trim() : '';
}
function roleLabel(roleId) {
    return ROLE_LABELS[roleId] || roleId;
}
function providerName(providerId, providers) {
    if (!providerId)
        return '未绑定 Provider';
    const provider = providers[providerId];
    return readText(provider?.name) || providerId;
}
function modelName(role, provider) {
    return readText(role?.model) || readText(provider?.model) || readText(provider?.default_model) || '未绑定模型';
}
function issueLabel(issue) {
    return ISSUE_LABELS[issue] || issue || '未获得具体失败原因';
}
function isDeprecatedReadinessStaleOnly(detail) {
    return detail.issue === 'readiness_stale' && detail.runtimeSupported;
}
export function buildBlockedRoleDiagnostics({ blockedRoles = [], unsupportedRoles = [], roles = {}, providers = {}, }) {
    const orderedRoles = [...blockedRoles, ...unsupportedRoles];
    const seen = new Set();
    const unsupportedRoleSet = new Set(unsupportedRoles.map(normalizeRoleId));
    return orderedRoles
        .map(normalizeRoleId)
        .filter((roleId) => {
        if (!roleId || seen.has(roleId))
            return false;
        seen.add(roleId);
        return true;
    })
        .map((roleId) => {
        const role = roles[roleId] || (roleId === 'architect' ? roles.docs : undefined);
        const providerId = readText(role?.provider_id);
        const provider = providerId ? providers[providerId] : undefined;
        const testedProviderId = readText(role?.tested_provider_id);
        const runtimeSupported = role?.runtime_supported !== false && !unsupportedRoleSet.has(roleId);
        const runtimeIssue = readText(role?.runtime_issue);
        const readinessIssue = readText(role?.readiness_issue)
            || (!runtimeSupported ? runtimeIssue || 'runtime_unsupported' : '')
            || (!providerId ? 'unassigned_provider' : '')
            || 'role_readiness_missing';
        return {
            roleId,
            roleLabel: roleLabel(roleId),
            providerId,
            providerName: providerName(providerId, providers),
            configuredModel: modelName(role, provider),
            testedProviderId,
            testedProviderName: providerName(testedProviderId, providers),
            testedModel: readText(role?.tested_model),
            testedTimestamp: readText(role?.tested_timestamp || role?.timestamp),
            issue: readinessIssue,
            issueLabel: issueLabel(readinessIssue),
            ready: Boolean(role?.ready),
            runtimeSupported,
        };
    })
        .filter((detail) => !isDeprecatedReadinessStaleOnly(detail));
}
export function formatBlockedRoleTitle(detail) {
    const tested = detail.testedProviderId || detail.testedModel
        ? `最近测试: ${detail.testedProviderName}/${detail.testedModel || '未知模型'}`
        : '最近测试: 无记录';
    const testedAt = detail.testedTimestamp ? `测试时间: ${detail.testedTimestamp}` : '';
    return [
        `${detail.roleLabel}: ${detail.providerName}/${detail.configuredModel}`,
        detail.issueLabel,
        tested,
        testedAt,
    ].filter(Boolean).join(' | ');
}

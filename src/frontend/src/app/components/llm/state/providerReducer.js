/**
 * Provider State Management - Reducer Pattern
 * 统一处理所有 Provider 相关状态，替代分散的 useState
 */
import { devLogger } from '@/app/utils/devLogger';
// ============================================================================
// Initial State
// ============================================================================
export const initialProviderState = {
    selectedRole: 'pm',
    selectedProviderId: null,
    selectedMethod: 'sdk',
    activeTab: 'config',
    configView: 'list',
    deepView: 'hall',
    interviewMode: 'interactive',
    expandedProviders: new Set(),
    // 新的统一编辑状态
    editingProviderId: null,
    editFormState: {},
    pendingChanges: new Set(),
    savingProvider: null,
    testPanel: {
        selectedProviderId: null,
        status: 'idle',
        cancelled: false,
    },
    providerTestStatus: {},
    connectivityResults: new Map(),
    connectivityRunning: false,
    connectivityRunningKey: null,
    interviewPanel: {
        open: false,
        status: 'idle',
        report: null,
        error: null,
    },
    interviewRunning: false,
    interviewCancelled: false,
    unifiedConfig: null,
    globalError: null,
    providerErrors: {},
};
// ============================================================================
// Reducer
// ============================================================================
export function providerReducer(state, action) {
    switch (action.type) {
        // 选择相关
        case 'SELECT_ROLE': {
            return {
                ...state,
                selectedRole: action.payload,
                // 清理相关状态
                interviewPanel: {
                    ...state.interviewPanel,
                    error: null,
                },
            };
        }
        case 'SELECT_PROVIDER': {
            return {
                ...state,
                selectedProviderId: action.payload,
            };
        }
        case 'SELECT_METHOD': {
            return {
                ...state,
                selectedMethod: action.payload,
            };
        }
        // 视图切换
        case 'SWITCH_TAB': {
            const newTab = action.payload;
            const updates = { activeTab: newTab };
            // 切换标签时清理状态
            if (newTab !== 'deepTest') {
                updates.interviewPanel = initialProviderState.interviewPanel;
            }
            return { ...state, ...updates };
        }
        case 'SET_CONFIG_VIEW': {
            return {
                ...state,
                configView: action.payload,
            };
        }
        case 'SET_DEEP_VIEW': {
            return {
                ...state,
                deepView: action.payload,
            };
        }
        case 'SET_INTERVIEW_MODE': {
            return {
                ...state,
                interviewMode: action.payload,
                // 切换模式时重置视图
                deepView: action.payload === 'auto' ? 'hall' : state.deepView,
            };
        }
        case 'TOGGLE_EXPAND_PROVIDER': {
            const newExpanded = new Set(state.expandedProviders);
            if (newExpanded.has(action.payload)) {
                newExpanded.delete(action.payload);
            }
            else {
                newExpanded.add(action.payload);
            }
            return {
                ...state,
                expandedProviders: newExpanded,
            };
        }
        case 'EXPAND_ALL_PROVIDERS': {
            // 注意：这里需要在组件层注入 providerIds
            return state;
        }
        case 'COLLAPSE_ALL_PROVIDERS': {
            return {
                ...state,
                expandedProviders: new Set(),
            };
        }
        // === 新的统一编辑状态 Reducer Cases ===
        case 'START_EDIT': {
            const { providerId, initialConfig } = action.payload;
            return {
                ...state,
                editingProviderId: providerId,
                // 深拷贝初始配置到 editFormState
                editFormState: {
                    ...state.editFormState,
                    [providerId]: JSON.parse(JSON.stringify(initialConfig)),
                },
                // 清除之前的未保存标记
                pendingChanges: (() => {
                    const newPending = new Set(state.pendingChanges);
                    newPending.delete(providerId);
                    return newPending;
                })(),
                // 清除之前的错误
                providerErrors: {
                    ...state.providerErrors,
                    [providerId]: undefined,
                },
            };
        }
        case 'UPDATE_EDIT_FORM': {
            const { providerId, updates } = action.payload;
            const currentForm = state.editFormState[providerId];
            if (!currentForm)
                return state;
            const updatedForm = { ...currentForm, ...updates };
            // 检查是否有实际变化
            const hasChanges = JSON.stringify(currentForm) !== JSON.stringify(updatedForm);
            return {
                ...state,
                editFormState: {
                    ...state.editFormState,
                    [providerId]: updatedForm,
                },
                pendingChanges: (() => {
                    const newPending = new Set(state.pendingChanges);
                    if (hasChanges) {
                        newPending.add(providerId);
                    }
                    else {
                        newPending.delete(providerId);
                    }
                    return newPending;
                })(),
            };
        }
        case 'SAVE_EDIT_START': {
            return {
                ...state,
                savingProvider: action.payload,
            };
        }
        case 'SAVE_EDIT_SUCCESS': {
            const providerId = action.payload;
            return {
                ...state,
                savingProvider: null,
                editingProviderId: null,
                pendingChanges: (() => {
                    const newPending = new Set(state.pendingChanges);
                    newPending.delete(providerId);
                    return newPending;
                })(),
                // 清除保存成功的 provider 的 editFormState
                editFormState: (() => {
                    const newFormState = { ...state.editFormState };
                    delete newFormState[providerId];
                    return newFormState;
                })(),
            };
        }
        case 'SAVE_EDIT_FAILURE': {
            const { providerId, error } = action.payload;
            return {
                ...state,
                savingProvider: null,
                providerErrors: {
                    ...state.providerErrors,
                    [providerId]: error,
                },
            };
        }
        case 'CANCEL_EDIT': {
            const providerId = action.payload;
            return {
                ...state,
                editingProviderId: null,
                pendingChanges: (() => {
                    const newPending = new Set(state.pendingChanges);
                    newPending.delete(providerId);
                    return newPending;
                })(),
                // 清除 editFormState
                editFormState: (() => {
                    const newFormState = { ...state.editFormState };
                    delete newFormState[providerId];
                    return newFormState;
                })(),
                // 清除错误
                providerErrors: {
                    ...state.providerErrors,
                    [providerId]: undefined,
                },
            };
        }
        case 'SET_PROVIDER_ERROR': {
            const { providerId, error } = action.payload;
            return {
                ...state,
                providerErrors: {
                    ...state.providerErrors,
                    [providerId]: error ?? undefined,
                },
            };
        }
        case 'CLEAR_PROVIDER_ERROR': {
            const providerId = action.payload;
            const newErrors = { ...state.providerErrors };
            delete newErrors[providerId];
            return {
                ...state,
                providerErrors: newErrors,
            };
        }
        // 测试相关
        case 'OPEN_TEST_PANEL': {
            const { providerId, runConfig } = action.payload;
            return {
                ...state,
                testPanel: {
                    selectedProviderId: providerId,
                    status: 'idle',
                    cancelled: false,
                    runConfig,
                },
            };
        }
        case 'CLOSE_TEST_PANEL': {
            return {
                ...state,
                testPanel: initialProviderState.testPanel,
            };
        }
        case 'START_TEST': {
            const { providerId, runConfig } = action.payload;
            return {
                ...state,
                testPanel: {
                    ...state.testPanel,
                    status: 'running',
                    cancelled: false,
                    ...(runConfig && { runConfig }),
                },
                providerTestStatus: {
                    ...state.providerTestStatus,
                    [providerId]: 'running',
                },
            };
        }
        case 'COMPLETE_TEST': {
            const { providerId, success } = action.payload;
            devLogger.debug('[providerReducer] COMPLETE_TEST:', { providerId, success });
            devLogger.debug('[providerReducer] Updating providerTestStatus:', {
                ...state.providerTestStatus,
                [providerId]: success ? 'success' : 'failed'
            });
            return {
                ...state,
                testPanel: {
                    ...state.testPanel,
                    status: success ? 'success' : 'failed',
                },
                providerTestStatus: {
                    ...state.providerTestStatus,
                    [providerId]: success ? 'success' : 'failed',
                },
            };
        }
        case 'CANCEL_TEST': {
            const providerId = state.testPanel.selectedProviderId;
            return {
                ...state,
                testPanel: {
                    ...state.testPanel,
                    status: 'failed',
                    cancelled: true,
                },
                providerTestStatus: {
                    ...state.providerTestStatus,
                    ...(providerId && { [providerId]: 'unknown' }),
                },
            };
        }
        case 'SET_PROVIDER_TEST_STATUS': {
            const { providerId, status } = action.payload;
            return {
                ...state,
                providerTestStatus: {
                    ...state.providerTestStatus,
                    [providerId]: status,
                },
            };
        }
        // 连通性测试
        case 'START_CONNECTIVITY_TEST': {
            return {
                ...state,
                connectivityRunning: true,
                connectivityRunningKey: action.payload,
            };
        }
        case 'COMPLETE_CONNECTIVITY_TEST': {
            const { key, result } = action.payload;
            const newResults = new Map(state.connectivityResults);
            newResults.set(key, result);
            return {
                ...state,
                connectivityResults: newResults,
                connectivityRunning: false,
                connectivityRunningKey: null,
            };
        }
        case 'CLEAR_CONNECTIVITY_RESULT': {
            const newResults = new Map(state.connectivityResults);
            newResults.delete(action.payload);
            return {
                ...state,
                connectivityResults: newResults,
            };
        }
        // 面试相关
        case 'OPEN_INTERVIEW_PANEL': {
            return {
                ...state,
                interviewPanel: {
                    ...state.interviewPanel,
                    open: true,
                    status: 'idle',
                    error: null,
                },
            };
        }
        case 'CLOSE_INTERVIEW_PANEL': {
            return {
                ...state,
                interviewPanel: initialProviderState.interviewPanel,
            };
        }
        case 'START_INTERVIEW': {
            return {
                ...state,
                interviewRunning: true,
                interviewCancelled: false,
                interviewPanel: {
                    ...state.interviewPanel,
                    status: 'running',
                    error: null,
                    report: null,
                },
            };
        }
        case 'COMPLETE_INTERVIEW': {
            return {
                ...state,
                interviewRunning: false,
                interviewPanel: {
                    ...state.interviewPanel,
                    status: 'success',
                    report: action.payload,
                },
            };
        }
        case 'FAIL_INTERVIEW': {
            return {
                ...state,
                interviewRunning: false,
                interviewPanel: {
                    ...state.interviewPanel,
                    status: 'failed',
                    error: action.payload,
                },
            };
        }
        case 'CANCEL_INTERVIEW': {
            return {
                ...state,
                interviewRunning: false,
                interviewCancelled: true,
                interviewPanel: {
                    ...state.interviewPanel,
                    status: 'failed',
                    error: '面试已取消',
                },
            };
        }
        // 错误处理
        case 'SET_ERROR': {
            return {
                ...state,
                globalError: action.payload,
            };
        }
        case 'CLEAR_ERROR': {
            return {
                ...state,
                globalError: null,
            };
        }
        // 批量更新
        case 'HYDRATE_STATE': {
            return {
                ...state,
                ...action.payload,
            };
        }
        case 'UPDATE_UNIFIED_CONFIG': {
            return {
                ...state,
                unifiedConfig: action.payload
            };
        }
        default: {
            return state;
        }
    }
}
// ============================================================================
// Action Creators
// ============================================================================
export const ProviderActions = {
    selectRole: (role) => ({ type: 'SELECT_ROLE', payload: role }),
    selectProvider: (id) => ({ type: 'SELECT_PROVIDER', payload: id }),
    selectMethod: (method) => ({ type: 'SELECT_METHOD', payload: method }),
    switchTab: (tab) => ({ type: 'SWITCH_TAB', payload: tab }),
    setConfigView: (view) => ({ type: 'SET_CONFIG_VIEW', payload: view }),
    setDeepView: (view) => ({ type: 'SET_DEEP_VIEW', payload: view }),
    setInterviewMode: (mode) => ({ type: 'SET_INTERVIEW_MODE', payload: mode }),
    // Provider card expansion actions
    toggleExpandProvider: (id) => ({ type: 'TOGGLE_EXPAND_PROVIDER', payload: id }),
    collapseAllProviders: () => ({ type: 'COLLAPSE_ALL_PROVIDERS' }),
    // === 新的统一编辑状态 Action Creators ===
    startEdit: (providerId, initialConfig) => ({
        type: 'START_EDIT',
        payload: { providerId, initialConfig },
    }),
    updateEditForm: (providerId, updates) => ({
        type: 'UPDATE_EDIT_FORM',
        payload: { providerId, updates },
    }),
    saveEditStart: (providerId) => ({
        type: 'SAVE_EDIT_START',
        payload: providerId,
    }),
    saveEditSuccess: (providerId) => ({
        type: 'SAVE_EDIT_SUCCESS',
        payload: providerId,
    }),
    saveEditFailure: (providerId, error) => ({
        type: 'SAVE_EDIT_FAILURE',
        payload: { providerId, error },
    }),
    cancelEdit: (providerId) => ({
        type: 'CANCEL_EDIT',
        payload: providerId,
    }),
    setProviderError: (providerId, error) => ({
        type: 'SET_PROVIDER_ERROR',
        payload: { providerId, error },
    }),
    clearProviderError: (providerId) => ({
        type: 'CLEAR_PROVIDER_ERROR',
        payload: providerId,
    }),
    openTestPanel: (id, runConfig) => ({
        type: 'OPEN_TEST_PANEL',
        payload: { providerId: id, runConfig }
    }),
    closeTestPanel: () => ({ type: 'CLOSE_TEST_PANEL' }),
    startTest: (id, runConfig) => ({
        type: 'START_TEST',
        payload: { providerId: id, runConfig }
    }),
    completeTest: (id, success) => ({
        type: 'COMPLETE_TEST',
        payload: { providerId: id, success }
    }),
    cancelTest: () => ({ type: 'CANCEL_TEST' }),
    setProviderTestStatus: (providerId, status) => ({
        type: 'SET_PROVIDER_TEST_STATUS',
        payload: { providerId, status },
    }),
    startConnectivityTest: (key) => ({
        type: 'START_CONNECTIVITY_TEST',
        payload: key
    }),
    completeConnectivityTest: (key, result) => ({
        type: 'COMPLETE_CONNECTIVITY_TEST',
        payload: { key, result }
    }),
    openInterviewPanel: () => ({ type: 'OPEN_INTERVIEW_PANEL' }),
    closeInterviewPanel: () => ({ type: 'CLOSE_INTERVIEW_PANEL' }),
    startInterview: () => ({ type: 'START_INTERVIEW' }),
    completeInterview: (report) => ({
        type: 'COMPLETE_INTERVIEW',
        payload: report
    }),
    failInterview: (error) => ({ type: 'FAIL_INTERVIEW', payload: error }),
    cancelInterview: () => ({ type: 'CANCEL_INTERVIEW' }),
    setError: (error) => ({ type: 'SET_ERROR', payload: error }),
    clearError: () => ({ type: 'CLEAR_ERROR' }),
    updateUnifiedConfig: (config) => ({
        type: 'UPDATE_UNIFIED_CONFIG',
        payload: config
    }),
};

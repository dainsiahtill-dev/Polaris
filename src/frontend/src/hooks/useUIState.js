import { useReducer, useCallback } from 'react';
const initialUIState = {
    showCognition: true,
    showTerminal: false,
    isBrainOpen: false,
    isMonitorOpen: true,
    isSettingsOpen: false,
    settingsInitialTab: 'general',
    isDocsInitOpen: false,
    isInterventionOpen: false,
    isHistoryDrawerOpen: false,
    isLogsOpen: false,
    logsSourceId: null,
    logsBanner: null,
    isAgentsDialogOpen: false,
    isRuntimeDialogOpen: false,
    isPlanDialogOpen: false,
    isLanceDbDialogOpen: false,
    isTerminalMaximized: false,
};
function uiReducer(state, action) {
    switch (action.type) {
        case 'SET_SHOW_COGNITION':
            return { ...state, showCognition: action.payload };
        case 'TOGGLE_TERMINAL':
            return { ...state, showTerminal: !state.showTerminal };
        case 'SET_SHOW_TERMINAL':
            return { ...state, showTerminal: action.payload };
        case 'TOGGLE_BRAIN':
            return { ...state, isBrainOpen: !state.isBrainOpen };
        case 'TOGGLE_MONITOR':
            return { ...state, isMonitorOpen: !state.isMonitorOpen };
        case 'OPEN_SETTINGS':
            return {
                ...state,
                isSettingsOpen: true,
                settingsInitialTab: action.payload || 'general',
            };
        case 'CLOSE_SETTINGS':
            return { ...state, isSettingsOpen: false };
        case 'OPEN_DOCS_INIT':
            return { ...state, isDocsInitOpen: true };
        case 'CLOSE_DOCS_INIT':
            return { ...state, isDocsInitOpen: false };
        case 'OPEN_INTERVENTION':
            return { ...state, isInterventionOpen: true };
        case 'CLOSE_INTERVENTION':
            return { ...state, isInterventionOpen: false };
        case 'OPEN_HISTORY_DRAWER':
            return { ...state, isHistoryDrawerOpen: true };
        case 'CLOSE_HISTORY_DRAWER':
            return { ...state, isHistoryDrawerOpen: false };
        case 'OPEN_LOGS':
            {
                let bannerText = null;
                const banner = action.payload.banner;
                if (typeof banner === 'string') {
                    const text = banner.trim();
                    bannerText = text || null;
                }
                else if (banner != null) {
                    try {
                        bannerText = JSON.stringify(banner, null, 2);
                    }
                    catch {
                        bannerText = String(banner);
                    }
                }
                return {
                    ...state,
                    isLogsOpen: true,
                    logsSourceId: action.payload.sourceId,
                    logsBanner: bannerText,
                };
            }
        case 'CLOSE_LOGS':
            return { ...state, isLogsOpen: false, logsBanner: null };
        case 'DISMISS_LOGS_BANNER':
            return { ...state, logsBanner: null };
        case 'OPEN_AGENTS_DIALOG':
            return { ...state, isAgentsDialogOpen: true };
        case 'CLOSE_AGENTS_DIALOG':
            return { ...state, isAgentsDialogOpen: false };
        case 'OPEN_RUNTIME_DIALOG':
            return { ...state, isRuntimeDialogOpen: true };
        case 'CLOSE_RUNTIME_DIALOG':
            return { ...state, isRuntimeDialogOpen: false };
        case 'OPEN_PLAN_DIALOG':
            return { ...state, isPlanDialogOpen: true };
        case 'CLOSE_PLAN_DIALOG':
            return { ...state, isPlanDialogOpen: false };
        case 'OPEN_LANCEDB_DIALOG':
            return { ...state, isLanceDbDialogOpen: true };
        case 'CLOSE_LANCEDB_DIALOG':
            return { ...state, isLanceDbDialogOpen: false };
        case 'TOGGLE_TERMINAL_MAXIMIZE':
            return { ...state, isTerminalMaximized: !state.isTerminalMaximized };
        case 'SET_TERMINAL_MAXIMIZE':
            return { ...state, isTerminalMaximized: action.payload };
        case 'RESET':
            return initialUIState;
        default:
            return state;
    }
}
export function useUIState(initial) {
    const [state, dispatch] = useReducer(uiReducer, {
        ...initialUIState,
        ...initial,
    });
    const actions = {
        setShowCognition: useCallback((v) => dispatch({ type: 'SET_SHOW_COGNITION', payload: v }), []),
        toggleTerminal: useCallback(() => dispatch({ type: 'TOGGLE_TERMINAL' }), []),
        setShowTerminal: useCallback((v) => dispatch({ type: 'SET_SHOW_TERMINAL', payload: v }), []),
        toggleBrain: useCallback(() => dispatch({ type: 'TOGGLE_BRAIN' }), []),
        toggleMonitor: useCallback(() => dispatch({ type: 'TOGGLE_MONITOR' }), []),
        openSettings: useCallback((tab) => dispatch({ type: 'OPEN_SETTINGS', payload: tab }), []),
        closeSettings: useCallback(() => dispatch({ type: 'CLOSE_SETTINGS' }), []),
        openDocsInit: useCallback(() => dispatch({ type: 'OPEN_DOCS_INIT' }), []),
        closeDocsInit: useCallback(() => dispatch({ type: 'CLOSE_DOCS_INIT' }), []),
        openIntervention: useCallback(() => dispatch({ type: 'OPEN_INTERVENTION' }), []),
        closeIntervention: useCallback(() => dispatch({ type: 'CLOSE_INTERVENTION' }), []),
        openHistoryDrawer: useCallback(() => dispatch({ type: 'OPEN_HISTORY_DRAWER' }), []),
        closeHistoryDrawer: useCallback(() => dispatch({ type: 'CLOSE_HISTORY_DRAWER' }), []),
        openLogs: useCallback((sourceId, banner) => dispatch({ type: 'OPEN_LOGS', payload: { sourceId, banner } }), []),
        dismissLogsBanner: useCallback(() => dispatch({ type: 'DISMISS_LOGS_BANNER' }), []),
        closeLogs: useCallback(() => dispatch({ type: 'CLOSE_LOGS' }), []),
        openAgentsDialog: useCallback(() => dispatch({ type: 'OPEN_AGENTS_DIALOG' }), []),
        closeAgentsDialog: useCallback(() => dispatch({ type: 'CLOSE_AGENTS_DIALOG' }), []),
        openRuntimeDialog: useCallback(() => dispatch({ type: 'OPEN_RUNTIME_DIALOG' }), []),
        closeRuntimeDialog: useCallback(() => dispatch({ type: 'CLOSE_RUNTIME_DIALOG' }), []),
        openPlanDialog: useCallback(() => dispatch({ type: 'OPEN_PLAN_DIALOG' }), []),
        closePlanDialog: useCallback(() => dispatch({ type: 'CLOSE_PLAN_DIALOG' }), []),
        openLanceDbDialog: useCallback(() => dispatch({ type: 'OPEN_LANCEDB_DIALOG' }), []),
        closeLanceDbDialog: useCallback(() => dispatch({ type: 'CLOSE_LANCEDB_DIALOG' }), []),
        toggleTerminalMaximize: useCallback(() => dispatch({ type: 'TOGGLE_TERMINAL_MAXIMIZE' }), []),
        setTerminalMaximize: useCallback((v) => dispatch({ type: 'SET_TERMINAL_MAXIMIZE', payload: v }), []),
        reset: useCallback(() => dispatch({ type: 'RESET' }), []),
    };
    return { state, dispatch, actions };
}

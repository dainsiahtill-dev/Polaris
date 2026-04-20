import { useReducer, useCallback } from 'react';

export interface UIState {
  showCognition: boolean;
  showTerminal: boolean;
  isBrainOpen: boolean;
  isMonitorOpen: boolean;
  isSettingsOpen: boolean;
  settingsInitialTab: 'general' | 'llm' | 'arsenal' | 'services';
  isDocsInitOpen: boolean;
  isInterventionOpen: boolean;
  isHistoryDrawerOpen: boolean;
  isLogsOpen: boolean;
  logsSourceId: string | null;
  logsBanner: string | null;
  isAgentsDialogOpen: boolean;
  isRuntimeDialogOpen: boolean;
  isPlanDialogOpen: boolean;
  isLanceDbDialogOpen: boolean;
  isTerminalMaximized: boolean;
}

const initialUIState: UIState = {
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

type UIAction =
  | { type: 'TOGGLE_TERMINAL' }
  | { type: 'SET_SHOW_TERMINAL'; payload: boolean }
  | { type: 'TOGGLE_BRAIN' }
  | { type: 'TOGGLE_MONITOR' }
  | { type: 'OPEN_SETTINGS'; payload?: UIState['settingsInitialTab'] }
  | { type: 'CLOSE_SETTINGS' }
  | { type: 'OPEN_DOCS_INIT' }
  | { type: 'CLOSE_DOCS_INIT' }
  | { type: 'OPEN_INTERVENTION' }
  | { type: 'CLOSE_INTERVENTION' }
  | { type: 'OPEN_HISTORY_DRAWER' }
  | { type: 'CLOSE_HISTORY_DRAWER' }
  | { type: 'OPEN_LOGS'; payload: { sourceId: string; banner?: unknown } }
  | { type: 'CLOSE_LOGS' }
  | { type: 'OPEN_AGENTS_DIALOG' }
  | { type: 'CLOSE_AGENTS_DIALOG' }
  | { type: 'OPEN_RUNTIME_DIALOG' }
  | { type: 'CLOSE_RUNTIME_DIALOG' }
  | { type: 'OPEN_PLAN_DIALOG' }
  | { type: 'CLOSE_PLAN_DIALOG' }
  | { type: 'OPEN_LANCEDB_DIALOG' }
  | { type: 'CLOSE_LANCEDB_DIALOG' }
  | { type: 'TOGGLE_TERMINAL_MAXIMIZE' }
  | { type: 'SET_TERMINAL_MAXIMIZE'; payload: boolean }
  | { type: 'RESET' };

function uiReducer(state: UIState, action: UIAction): UIState {
  switch (action.type) {
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
        let bannerText: string | null = null;
        const banner = action.payload.banner;
        if (typeof banner === 'string') {
          const text = banner.trim();
          bannerText = text || null;
        } else if (banner != null) {
          try {
            bannerText = JSON.stringify(banner, null, 2);
          } catch {
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

export function useUIState(initial?: Partial<UIState>) {
  const [state, dispatch] = useReducer(uiReducer, {
    ...initialUIState,
    ...initial,
  });

  const actions = {
    toggleTerminal: useCallback(() => dispatch({ type: 'TOGGLE_TERMINAL' }), []),
    setShowTerminal: useCallback((v: boolean) => dispatch({ type: 'SET_SHOW_TERMINAL', payload: v }), []),
    toggleBrain: useCallback(() => dispatch({ type: 'TOGGLE_BRAIN' }), []),
    toggleMonitor: useCallback(() => dispatch({ type: 'TOGGLE_MONITOR' }), []),
    openSettings: useCallback((tab?: UIState['settingsInitialTab']) => dispatch({ type: 'OPEN_SETTINGS', payload: tab }), []),
    closeSettings: useCallback(() => dispatch({ type: 'CLOSE_SETTINGS' }), []),
    openDocsInit: useCallback(() => dispatch({ type: 'OPEN_DOCS_INIT' }), []),
    closeDocsInit: useCallback(() => dispatch({ type: 'CLOSE_DOCS_INIT' }), []),
    openIntervention: useCallback(() => dispatch({ type: 'OPEN_INTERVENTION' }), []),
    closeIntervention: useCallback(() => dispatch({ type: 'CLOSE_INTERVENTION' }), []),
    openHistoryDrawer: useCallback(() => dispatch({ type: 'OPEN_HISTORY_DRAWER' }), []),
    closeHistoryDrawer: useCallback(() => dispatch({ type: 'CLOSE_HISTORY_DRAWER' }), []),
    openLogs: useCallback((sourceId: string, banner?: unknown) => dispatch({ type: 'OPEN_LOGS', payload: { sourceId, banner } }), []),
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
    setTerminalMaximize: useCallback((v: boolean) => dispatch({ type: 'SET_TERMINAL_MAXIMIZE', payload: v }), []),
    reset: useCallback(() => dispatch({ type: 'RESET' }), []),
  };

  return { state, dispatch, actions };
}

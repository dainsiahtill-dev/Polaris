/**
 * Provider State Management - Reducer Pattern
 * 统一处理所有 Provider 相关状态，替代分散的 useState
 */

import type { RoleIdStrict, ProviderConfigStrict, ConnectivityResultStrict, InterviewSuiteReportStrict } from '../types/strict';
export type { ConnectivityResultStrict } from '../types/strict';
import type { ProviderConfig, UnifiedLlmConfig } from '../types';
import { devLogger } from '@/app/utils/devLogger';

// ============================================================================
// State Types
// ============================================================================

/** 连接性测试状态 */
export type ConnectivityStatus = 'unknown' | 'running' | 'success' | 'failed';

/** 测试状态 */
export type TestStatus = 'idle' | 'running' | 'success' | 'failed';

/** 配置视图 */
export type ConfigView = 'list' | 'visual';

/** 深度测试视图 */
export type DeepView = 'hall' | 'session';

/** 面试模式 */
export type InterviewMode = 'interactive' | 'auto';

/** 活跃标签 */
export type ActiveTab = 'config' | 'deepTest';

/** 连接方式 */
export type ConnectionMethodId = 'sdk' | 'api' | 'cli';

/** 面试面板状态 */
export interface InterviewPanelState {
  open: boolean;
  status: TestStatus;
  report: InterviewSuiteReportStrict | null;
  error: string | null;
}

/** 测试面板状态 */
export interface TestPanelState {
  selectedProviderId: string | null;
  status: TestStatus;
  cancelled: boolean;
  /** 测试运行配置（用于深度面试等场景） */
  runConfig?: {
    suites?: string[];
    role?: string;
    model?: string;
  };
}

/** Provider 状态 */
export interface ProviderState {
  // 选择状态
  selectedRole: RoleIdStrict;
  selectedProviderId: string | null;
  selectedMethod: ConnectionMethodId;
  
  // 视图状态
  activeTab: ActiveTab;
  configView: ConfigView;
  deepView: DeepView;
  interviewMode: InterviewMode;
  
  // Provider 编辑状态 (Legacy - 将逐步迁移到新的 editFormState)
  editingProvider: string | null;
  expandedProviders: Set<string>;
  
  // === 新的统一编辑状态 ===
  // 当前正在编辑的 provider ID
  editingProviderId: string | null;
  // 编辑表单状态 - 存储每个 provider 的编辑中数据 (work-in-progress)
  editFormState: Record<string, ProviderConfig>;
  // 标记哪些 provider 有未保存的更改
  pendingChanges: Set<string>;
  // 保存中状态
  savingProvider: string | null;
  
  // 测试状态
  testPanel: TestPanelState;
  providerTestStatus: Record<string, ConnectivityStatus>;
  connectivityResults: Map<string, ConnectivityResultStrict>;
  connectivityRunning: boolean;
  connectivityRunningKey: string | null;
  
  // 面试状态
  interviewPanel: InterviewPanelState;
  interviewRunning: boolean;
  interviewCancelled: boolean;

  // Unified Configuration
  unifiedConfig: UnifiedLlmConfig | null;
  
  // 错误状态
  globalError: string | null;
  // 每个 provider 的错误信息
  providerErrors: Record<string, string | undefined>;
}

// ============================================================================
// Action Types
// ============================================================================

export type ProviderAction =
  // 选择相关
  | { type: 'SELECT_ROLE'; payload: RoleIdStrict }
  | { type: 'SELECT_PROVIDER'; payload: string | null }
  | { type: 'SELECT_METHOD'; payload: ConnectionMethodId }
  
  // 视图切换
  | { type: 'SWITCH_TAB'; payload: ActiveTab }
  | { type: 'SET_CONFIG_VIEW'; payload: ConfigView }
  | { type: 'SET_DEEP_VIEW'; payload: DeepView }
  | { type: 'SET_INTERVIEW_MODE'; payload: InterviewMode }
  
  // Provider 编辑 (Legacy)
  | { type: 'START_EDIT_PROVIDER'; payload: string }
  | { type: 'STOP_EDIT_PROVIDER' }
  | { type: 'TOGGLE_EXPAND_PROVIDER'; payload: string }
  | { type: 'EXPAND_ALL_PROVIDERS' }
  | { type: 'COLLAPSE_ALL_PROVIDERS' }
  
  // === 新的统一编辑状态 Actions ===
  // 开始编辑 - 初始化表单状态
  | { type: 'START_EDIT'; payload: { providerId: string; initialConfig: ProviderConfig } }
  // 更新编辑表单字段
  | { type: 'UPDATE_EDIT_FORM'; payload: { providerId: string; updates: Partial<ProviderConfig> } }
  // 保存编辑 - 开始保存流程
  | { type: 'SAVE_EDIT_START'; payload: string }
  // 保存成功
  | { type: 'SAVE_EDIT_SUCCESS'; payload: string }
  // 保存失败
  | { type: 'SAVE_EDIT_FAILURE'; payload: { providerId: string; error: string } }
  // 取消编辑 - 丢弃更改
  | { type: 'CANCEL_EDIT'; payload: string }
  // 设置 provider 错误
  | { type: 'SET_PROVIDER_ERROR'; payload: { providerId: string; error: string | null | undefined } }
  // 清除 provider 错误
  | { type: 'CLEAR_PROVIDER_ERROR'; payload: string }
  
  // 测试相关
  | { type: 'OPEN_TEST_PANEL'; payload: { providerId: string; runConfig?: { suites?: string[]; role?: string; model?: string } } }
  | { type: 'CLOSE_TEST_PANEL' }
  | { type: 'START_TEST'; payload: { providerId: string; runConfig?: { suites?: string[]; role?: string; model?: string } } }
  | { type: 'COMPLETE_TEST'; payload: { providerId: string; success: boolean } }
  | { type: 'CANCEL_TEST' }
  | { type: 'SET_PROVIDER_TEST_STATUS'; payload: { providerId: string; status: ConnectivityStatus } }
  
  // 连通性测试
  | { type: 'START_CONNECTIVITY_TEST'; payload: string }
  | { type: 'COMPLETE_CONNECTIVITY_TEST'; payload: { key: string; result: ConnectivityResultStrict } }
  | { type: 'CLEAR_CONNECTIVITY_RESULT'; payload: string }
  
  // 面试相关
  | { type: 'OPEN_INTERVIEW_PANEL' }
  | { type: 'CLOSE_INTERVIEW_PANEL' }
  | { type: 'START_INTERVIEW' }
  | { type: 'COMPLETE_INTERVIEW'; payload: InterviewSuiteReportStrict }
  | { type: 'FAIL_INTERVIEW'; payload: string }
  | { type: 'CANCEL_INTERVIEW' }
  
  // 错误处理
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'CLEAR_ERROR' }
  
  // 批量更新（用于初始化或外部更新）
  | { type: 'HYDRATE_STATE'; payload: Partial<ProviderState> }

  // Unified Config Update
  | { type: 'UPDATE_UNIFIED_CONFIG'; payload: UnifiedLlmConfig };

// ============================================================================
// Initial State
// ============================================================================

export const initialProviderState: ProviderState = {
  selectedRole: 'pm',
  selectedProviderId: null,
  selectedMethod: 'sdk',
  
  activeTab: 'config',
  configView: 'list',
  deepView: 'hall',
  interviewMode: 'interactive',
  
  // Legacy edit state
  editingProvider: null,
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

export function providerReducer(state: ProviderState, action: ProviderAction): ProviderState {
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
      const updates: Partial<ProviderState> = { activeTab: newTab };
      
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
    
    // Provider 编辑
    case 'START_EDIT_PROVIDER': {
      return {
        ...state,
        editingProvider: action.payload,
      };
    }
    
    case 'STOP_EDIT_PROVIDER': {
      return {
        ...state,
        editingProvider: null,
      };
    }
    
    case 'TOGGLE_EXPAND_PROVIDER': {
      const newExpanded = new Set(state.expandedProviders);
      if (newExpanded.has(action.payload)) {
        newExpanded.delete(action.payload);
      } else {
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
          [providerId]: undefined as unknown as string,
        },
      };
    }
    
    case 'UPDATE_EDIT_FORM': {
      const { providerId, updates } = action.payload;
      const currentForm = state.editFormState[providerId];
      if (!currentForm) return state;
      
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
          } else {
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
          [providerId]: undefined as unknown as string,
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
  selectRole: (role: RoleIdStrict): ProviderAction => ({ type: 'SELECT_ROLE', payload: role }),
  selectProvider: (id: string | null): ProviderAction => ({ type: 'SELECT_PROVIDER', payload: id }),
  selectMethod: (method: ConnectionMethodId): ProviderAction => ({ type: 'SELECT_METHOD', payload: method }),
  
  switchTab: (tab: ActiveTab): ProviderAction => ({ type: 'SWITCH_TAB', payload: tab }),
  setConfigView: (view: ConfigView): ProviderAction => ({ type: 'SET_CONFIG_VIEW', payload: view }),
  setDeepView: (view: DeepView): ProviderAction => ({ type: 'SET_DEEP_VIEW', payload: view }),
  setInterviewMode: (mode: InterviewMode): ProviderAction => ({ type: 'SET_INTERVIEW_MODE', payload: mode }),
  
  // Legacy edit actions
  startEditProvider: (id: string): ProviderAction => ({ type: 'START_EDIT_PROVIDER', payload: id }),
  stopEditProvider: (): ProviderAction => ({ type: 'STOP_EDIT_PROVIDER' }),
  toggleExpandProvider: (id: string): ProviderAction => ({ type: 'TOGGLE_EXPAND_PROVIDER', payload: id }),
  collapseAllProviders: (): ProviderAction => ({ type: 'COLLAPSE_ALL_PROVIDERS' }),
  
  // === 新的统一编辑状态 Action Creators ===
  startEdit: (providerId: string, initialConfig: ProviderConfig): ProviderAction => ({
    type: 'START_EDIT',
    payload: { providerId, initialConfig },
  }),
  updateEditForm: (providerId: string, updates: Partial<ProviderConfig>): ProviderAction => ({
    type: 'UPDATE_EDIT_FORM',
    payload: { providerId, updates },
  }),
  saveEditStart: (providerId: string): ProviderAction => ({
    type: 'SAVE_EDIT_START',
    payload: providerId,
  }),
  saveEditSuccess: (providerId: string): ProviderAction => ({
    type: 'SAVE_EDIT_SUCCESS',
    payload: providerId,
  }),
  saveEditFailure: (providerId: string, error: string): ProviderAction => ({
    type: 'SAVE_EDIT_FAILURE',
    payload: { providerId, error },
  }),
  cancelEdit: (providerId: string): ProviderAction => ({
    type: 'CANCEL_EDIT',
    payload: providerId,
  }),
  setProviderError: (providerId: string, error: string | null | undefined): ProviderAction => ({
    type: 'SET_PROVIDER_ERROR',
    payload: { providerId, error },
  }),
  clearProviderError: (providerId: string): ProviderAction => ({
    type: 'CLEAR_PROVIDER_ERROR',
    payload: providerId,
  }),
  
  openTestPanel: (id: string, runConfig?: { suites?: string[]; role?: string; model?: string }): ProviderAction => ({ 
    type: 'OPEN_TEST_PANEL', 
    payload: { providerId: id, runConfig } 
  }),
  closeTestPanel: (): ProviderAction => ({ type: 'CLOSE_TEST_PANEL' }),
  startTest: (id: string, runConfig?: { suites?: string[]; role?: string; model?: string }): ProviderAction => ({ 
    type: 'START_TEST', 
    payload: { providerId: id, runConfig } 
  }),
  completeTest: (id: string, success: boolean): ProviderAction => ({ 
    type: 'COMPLETE_TEST', 
    payload: { providerId: id, success } 
  }),
  cancelTest: (): ProviderAction => ({ type: 'CANCEL_TEST' }),
  
  setProviderTestStatus: (providerId: string, status: ConnectivityStatus): ProviderAction => ({
    type: 'SET_PROVIDER_TEST_STATUS',
    payload: { providerId, status },
  }),
  
  startConnectivityTest: (key: string): ProviderAction => ({ 
    type: 'START_CONNECTIVITY_TEST', 
    payload: key 
  }),
  completeConnectivityTest: (key: string, result: ConnectivityResultStrict): ProviderAction => ({ 
    type: 'COMPLETE_CONNECTIVITY_TEST', 
    payload: { key, result } 
  }),
  
  openInterviewPanel: (): ProviderAction => ({ type: 'OPEN_INTERVIEW_PANEL' }),
  closeInterviewPanel: (): ProviderAction => ({ type: 'CLOSE_INTERVIEW_PANEL' }),
  startInterview: (): ProviderAction => ({ type: 'START_INTERVIEW' }),
  completeInterview: (report: InterviewSuiteReportStrict): ProviderAction => ({ 
    type: 'COMPLETE_INTERVIEW', 
    payload: report 
  }),
  failInterview: (error: string): ProviderAction => ({ type: 'FAIL_INTERVIEW', payload: error }),
  cancelInterview: (): ProviderAction => ({ type: 'CANCEL_INTERVIEW' }),
  
  setError: (error: string | null): ProviderAction => ({ type: 'SET_ERROR', payload: error }),
  clearError: (): ProviderAction => ({ type: 'CLEAR_ERROR' }),
  
  updateUnifiedConfig: (config: UnifiedLlmConfig): ProviderAction => ({
    type: 'UPDATE_UNIFIED_CONFIG',
    payload: config
  }),
} as const;

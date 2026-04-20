/**
 * Store 导出入口
 * 集中导出所有Zustand状态管理store
 */

export {
  useSettingsStore,
  useGeneralSettings,
} from './settingsStore';

export type {
  SettingsModalState,
  SettingsModalActions,
  SettingsTab,
} from './settingsStore';

export { DEFAULT_JSON_LOG_PATH } from './settingsStore';

// UI Store
export {
  useUIStore,
  useThemeSettings,
  useLayoutState,
  useModalState,
  useToasts as useUIToasts,
} from './uiStore';

export type {
  UIState,
  UIActions,
  RightPanelTab,
} from './uiStore';

// LLM Store
export {
  useLLMStore,
  useLlmSettings,
  useLlmConfig,
  useLlmStatus,
  useProviders,
  useProviderModels,
  useProviderKeyStatus,
} from './llmStore';

export type {
  LLMState,
  LLMActions,
} from './llmStore';

// Test Store
export {
  useTestStore,
  useTestState,
  useReportDrawer,
} from './testStore';

export type {
  TestState,
  TestActions,
} from './testStore';

// Interview Store
export {
  useInterviewStore,
  useTuiDrawerState,
} from './interviewStore';

export type {
  InterviewState,
  InterviewActions,
} from './interviewStore';

// Runtime Store
export {
  useRuntimeStore,
  usePmRuntimeStatus,
  useDirectorRuntimeStatus,
  useActiveWorkers,
  useWorkerTasks,
  useTaskQueue,
} from './runtimeStore';

export type {
  RoleRuntimeState,
  RoleRuntimeActions,
  RoleRuntimeStatus,
} from './runtimeStore';

// Notification Store
export {
  useNotificationStore,
  useToasts,
  useNotificationSettings,
  useNotificationPreferences,
  useDndSettings,
  useDesktopNotificationPermission,
} from './notificationStore';

export type {
  NotificationState,
  NotificationActions,
} from './notificationStore';

// Provider Store
export {
  useProviderStore,
  useSelectedRole,
  useSelectedProvider,
  useActiveTab,
  useTestPanelState,
  useInterviewPanelState,
  useConnectivityStatus,
  useIsProviderExpanded,
  useEditingProviderId,
  useEditFormState,
  useHasPendingChanges,
  useIsSavingProvider,
  useProviderError,
  useGlobalPendingChangesCount,
} from './providerStore';

export type {
  ProviderStoreState,
  ProviderStoreActions,
  ConnectionMethodId,
} from './providerStore';

/**
 * Settings Store - Zustand状态管理
 * 集中管理SettingsModal的UI状态和通用设置
 */
import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { apiFetch } from '@/api';

// ============ 常量 ============
const DEFAULT_PROFILE = 'zhenguan_governance';
export const DEFAULT_JSON_LOG_PATH = 'runtime/events/pm.events.jsonl';
const SETTINGS_MODAL_SIZE_KEY = 'polaris:ui:settings_modal:size';

// ============ 类型定义 ============

export type SettingsTab = 'general' | 'llm' | 'arsenal' | 'services';

export interface SettingsModalState {
  // UI状态
  activeTab: SettingsTab;
  saving: boolean;
  error: string | null;
  settingsModalResizing: boolean;
  settingsModalSize: { width: number; height: number };

  // 通用设置状态
  promptProfile: string;
  refreshInterval: number;
  autoRefresh: boolean;
  pmInterval: number;
  pmTimeout: number;
  pmRunsDirector: boolean;
  pmDirectorShowOutput: boolean;
  pmDirectorTimeout: number;
  pmDirectorIterations: number;
  pmDirectorMatchMode: string;
  pmShowOutput: boolean;
  pmMaxFailures: number;
  pmMaxBlocked: number;
  pmMaxSame: number;
  directorIterations: number;
  directorExecutionMode: 'serial' | 'parallel';
  directorMaxParallelTasks: number;
  directorReadyTimeoutSeconds: number;
  directorClaimTimeoutSeconds: number;
  directorPhaseTimeoutSeconds: number;
  directorCompleteTimeoutSeconds: number;
  directorTaskTimeoutSeconds: number;
  directorForever: boolean;
  directorShowOutput: boolean;
  slmEnabled: boolean;
  qaEnabled: boolean;
  ramdiskRoot: string;
  jsonLogPath: string;
  showMemory: boolean;
  debugTracing: boolean;
  ioFsyncMode: 'strict' | 'relaxed';
  memoryRefsMode: 'strict' | 'soft' | 'off';
}

export interface SettingsModalActions {
  // UI操作
  setActiveTab: (tab: SettingsTab) => void;
  setSettingsModalSize: (size: { width: number; height: number }) => void;
  setSettingsModalResizing: (resizing: boolean) => void;
  setError: (error: string | null) => void;

  // 通用设置操作
  setPromptProfile: (value: string) => void;
  setRefreshInterval: (value: number) => void;
  setAutoRefresh: (value: boolean) => void;
  setPmInterval: (value: number) => void;
  setPmTimeout: (value: number) => void;
  setPmRunsDirector: (value: boolean) => void;
  setPmDirectorShowOutput: (value: boolean) => void;
  setPmDirectorTimeout: (value: number) => void;
  setPmDirectorIterations: (value: number) => void;
  setPmDirectorMatchMode: (value: string) => void;
  setPmShowOutput: (value: boolean) => void;
  setPmMaxFailures: (value: number) => void;
  setPmMaxBlocked: (value: number) => void;
  setPmMaxSame: (value: number) => void;
  setDirectorIterations: (value: number) => void;
  setDirectorExecutionMode: (value: 'serial' | 'parallel') => void;
  setDirectorMaxParallelTasks: (value: number) => void;
  setDirectorReadyTimeoutSeconds: (value: number) => void;
  setDirectorClaimTimeoutSeconds: (value: number) => void;
  setDirectorPhaseTimeoutSeconds: (value: number) => void;
  setDirectorCompleteTimeoutSeconds: (value: number) => void;
  setDirectorTaskTimeoutSeconds: (value: number) => void;
  setDirectorForever: (value: boolean) => void;
  setDirectorShowOutput: (value: boolean) => void;
  setSlmEnabled: (value: boolean) => void;
  setQaEnabled: (value: boolean) => void;
  setRamdiskRoot: (value: string) => void;
  setJsonLogPath: (value: string) => void;
  setShowMemory: (value: boolean) => void;
  setDebugTracing: (value: boolean) => void;
  setIoFsyncMode: (value: 'strict' | 'relaxed') => void;
  setMemoryRefsMode: (value: 'strict' | 'soft' | 'off') => void;

  // 批量设置通用配置
  loadGeneralSettings: (settings: Record<string, unknown> | null) => void;
}

// ============ 辅助函数 ============
const clampNumber = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

const clampSettingsModalSize = (size: { width: number; height: number }) => {
  if (typeof window === 'undefined') return size;

  const margin = 48;
  const maxWidth = Math.max(320, window.innerWidth - margin);
  const maxHeight = Math.max(240, window.innerHeight - margin);

  const minWidth = Math.min(860, maxWidth);
  const minHeight = Math.min(560, maxHeight);

  return {
    width: clampNumber(Math.round(size.width), minWidth, maxWidth),
    height: clampNumber(Math.round(size.height), minHeight, maxHeight),
  };
};

const getDefaultModalSize = () => {
  if (typeof window === 'undefined') return { width: 1200, height: 800 };

  const defaults = clampSettingsModalSize({
    width: window.innerWidth * 0.92,
    height: window.innerHeight * 0.86,
  });

  try {
    const raw = localStorage.getItem(SETTINGS_MODAL_SIZE_KEY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw) as { width?: unknown; height?: unknown } | null;
    const width = Number(parsed?.width);
    const height = Number(parsed?.height);
    if (!Number.isFinite(width) || !Number.isFinite(height)) return defaults;
    return clampSettingsModalSize({ width, height });
  } catch {
    return defaults;
  }
};

const normalizeJsonLogPath = (value: string | null | undefined): string => {
  const raw = String(value ?? '').trim();
  if (!raw) return DEFAULT_JSON_LOG_PATH;
  return raw.replace(/\\/g, '/');
};

// ============ Store创建 ============
export const useSettingsStore = create<SettingsModalState & SettingsModalActions>()(
  immer((set) => ({
    // ============ 初始状态 ============
    activeTab: 'general',
    saving: false,
    error: null,
    settingsModalResizing: false,
    settingsModalSize: getDefaultModalSize(),

    // 通用设置初始值
    promptProfile: DEFAULT_PROFILE,
    refreshInterval: 3,
    autoRefresh: true,
    pmInterval: 20,
    pmTimeout: 0,
    pmRunsDirector: true,
    pmDirectorShowOutput: true,
    pmDirectorTimeout: 600,
    pmDirectorIterations: 1,
    pmDirectorMatchMode: 'latest',
    pmShowOutput: true,
    pmMaxFailures: 5,
    pmMaxBlocked: 5,
    pmMaxSame: 3,
    directorIterations: 1,
    directorExecutionMode: 'parallel',
    directorMaxParallelTasks: 3,
    directorReadyTimeoutSeconds: 30,
    directorClaimTimeoutSeconds: 30,
    directorPhaseTimeoutSeconds: 900,
    directorCompleteTimeoutSeconds: 30,
    directorTaskTimeoutSeconds: 3600,
    directorForever: false,
    directorShowOutput: true,
    slmEnabled: false,
    qaEnabled: true,
    ramdiskRoot: '',
    jsonLogPath: DEFAULT_JSON_LOG_PATH,
    showMemory: false,
    debugTracing: false,
    ioFsyncMode: 'strict',
    memoryRefsMode: 'soft',

    // ============ UI操作 ============
    setActiveTab: (tab) => set((state) => { state.activeTab = tab; }),
    setSettingsModalSize: (size) => {
      const clamped = clampSettingsModalSize(size);
      set((state) => { state.settingsModalSize = clamped; });
      try {
        localStorage.setItem(SETTINGS_MODAL_SIZE_KEY, JSON.stringify(clamped));
      } catch {
        // ignore
      }
    },
    setSettingsModalResizing: (resizing) => set((state) => { state.settingsModalResizing = resizing; }),
    setError: (error) => set((state) => { state.error = error; }),

    // ============ 通用设置操作 ============
    setPromptProfile: (value) => set((state) => { state.promptProfile = value; }),
    setRefreshInterval: (value) => set((state) => { state.refreshInterval = Math.max(1, value); }),
    setAutoRefresh: (value) => set((state) => { state.autoRefresh = value; }),
    setPmInterval: (value) => set((state) => { state.pmInterval = Math.max(1, value); }),
    setPmTimeout: (value) => set((state) => { state.pmTimeout = Math.max(0, value); }),
    setPmRunsDirector: (value) => set((state) => { state.pmRunsDirector = value; }),
    setPmDirectorShowOutput: (value) => set((state) => { state.pmDirectorShowOutput = value; }),
    setPmDirectorTimeout: (value) => set((state) => { state.pmDirectorTimeout = Math.max(1, value); }),
    setPmDirectorIterations: (value) => set((state) => { state.pmDirectorIterations = Math.max(1, value); }),
    setPmDirectorMatchMode: (value) => set((state) => { state.pmDirectorMatchMode = value; }),
    setPmShowOutput: (value) => set((state) => { state.pmShowOutput = value; }),
    setPmMaxFailures: (value) => set((state) => { state.pmMaxFailures = Math.max(1, value); }),
    setPmMaxBlocked: (value) => set((state) => { state.pmMaxBlocked = Math.max(1, value); }),
    setPmMaxSame: (value) => set((state) => { state.pmMaxSame = Math.max(1, value); }),
    setDirectorIterations: (value) => set((state) => { state.directorIterations = Math.max(1, value); }),
    setDirectorExecutionMode: (value) => set((state) => { state.directorExecutionMode = value; }),
    setDirectorMaxParallelTasks: (value) => set((state) => { state.directorMaxParallelTasks = Math.max(1, value); }),
    setDirectorReadyTimeoutSeconds: (value) => set((state) => { state.directorReadyTimeoutSeconds = Math.max(1, value); }),
    setDirectorClaimTimeoutSeconds: (value) => set((state) => { state.directorClaimTimeoutSeconds = Math.max(1, value); }),
    setDirectorPhaseTimeoutSeconds: (value) => set((state) => { state.directorPhaseTimeoutSeconds = Math.max(1, value); }),
    setDirectorCompleteTimeoutSeconds: (value) => set((state) => { state.directorCompleteTimeoutSeconds = Math.max(1, value); }),
    setDirectorTaskTimeoutSeconds: (value) => set((state) => { state.directorTaskTimeoutSeconds = Math.max(1, value); }),
    setDirectorForever: (value) => set((state) => { state.directorForever = value; }),
    setDirectorShowOutput: (value) => set((state) => { state.directorShowOutput = value; }),
    setSlmEnabled: (value) => set((state) => { state.slmEnabled = value; }),
    setQaEnabled: (value) => set((state) => { state.qaEnabled = value; }),
    setRamdiskRoot: (value) => set((state) => { state.ramdiskRoot = value; }),
    setJsonLogPath: (value) => set((state) => { state.jsonLogPath = normalizeJsonLogPath(value); }),
    setShowMemory: (value) => set((state) => { state.showMemory = value; }),
    setDebugTracing: (value) => set((state) => { state.debugTracing = value; }),
    setIoFsyncMode: (value) => set((state) => { state.ioFsyncMode = value; }),
    setMemoryRefsMode: (value) => set((state) => { state.memoryRefsMode = value; }),

    // ============ 批量加载通用配置 ============
    loadGeneralSettings: (settings) => {
      if (!settings) return;
      set((state) => {
        state.promptProfile = String(settings.prompt_profile || DEFAULT_PROFILE);
        state.refreshInterval = Math.max(1, Number(settings.refresh_interval ?? 3));
        state.autoRefresh = Boolean(settings.auto_refresh ?? true);
        state.pmInterval = Math.max(1, Number(settings.interval ?? 20));
        state.pmTimeout = Math.max(0, Number(settings.timeout ?? 0));
        state.pmShowOutput = Boolean(settings.pm_show_output ?? true);
        state.pmRunsDirector = Boolean(settings.pm_runs_director ?? true);
        state.pmDirectorShowOutput = Boolean(settings.pm_director_show_output ?? true);
        state.pmDirectorTimeout = Math.max(1, Number(settings.pm_director_timeout ?? 600));
        state.pmDirectorIterations = Math.max(1, Number(settings.pm_director_iterations ?? 1));
        state.pmDirectorMatchMode = String(settings.pm_director_match_mode ?? 'latest');
        state.pmMaxFailures = Math.max(1, Number(settings.pm_max_failures ?? 5));
        state.pmMaxBlocked = Math.max(1, Number(settings.pm_max_blocked ?? 5));
        state.pmMaxSame = Math.max(1, Number(settings.pm_max_same ?? 3));
        state.directorIterations = Math.max(1, Number(settings.director_iterations ?? 1));
        state.directorExecutionMode = settings.director_execution_mode === 'serial' ? 'serial' : 'parallel';
        state.directorMaxParallelTasks = Math.max(1, Number(settings.director_max_parallel_tasks ?? 3));
        state.directorReadyTimeoutSeconds = Math.max(1, Number(settings.director_ready_timeout_seconds ?? 30));
        state.directorClaimTimeoutSeconds = Math.max(1, Number(settings.director_claim_timeout_seconds ?? 30));
        state.directorPhaseTimeoutSeconds = Math.max(1, Number(settings.director_phase_timeout_seconds ?? 900));
        state.directorCompleteTimeoutSeconds = Math.max(1, Number(settings.director_complete_timeout_seconds ?? 30));
        state.directorTaskTimeoutSeconds = Math.max(1, Number(settings.director_task_timeout_seconds ?? 3600));
        state.directorForever = Boolean(settings.director_forever ?? false);
        state.directorShowOutput = Boolean(settings.director_show_output ?? true);
        state.slmEnabled = Boolean(settings.slm_enabled ?? false);
        state.qaEnabled = Boolean(settings.qa_enabled ?? true);
        state.ramdiskRoot = String(settings.ramdisk_root ?? '');
        state.jsonLogPath = normalizeJsonLogPath(String(settings.json_log_path ?? ''));
        state.showMemory = Boolean(settings.show_memory ?? false);
        state.debugTracing = Boolean(settings.debug_tracing ?? false);
        state.ioFsyncMode = settings.io_fsync_mode === 'relaxed' ? 'relaxed' : 'strict';
        state.memoryRefsMode =
          settings.memory_refs_mode === 'strict'
            ? 'strict'
            : settings.memory_refs_mode === 'off'
              ? 'off'
              : 'soft';
      });
    },
  }))
);

// ============ 选择器Hooks ============
export const useGeneralSettings = () => useSettingsStore((state) => ({
  promptProfile: state.promptProfile,
  refreshInterval: state.refreshInterval,
  autoRefresh: state.autoRefresh,
  pmInterval: state.pmInterval,
  pmTimeout: state.pmTimeout,
  pmRunsDirector: state.pmRunsDirector,
  pmDirectorShowOutput: state.pmDirectorShowOutput,
  pmDirectorTimeout: state.pmDirectorTimeout,
  pmDirectorIterations: state.pmDirectorIterations,
  pmDirectorMatchMode: state.pmDirectorMatchMode,
  pmShowOutput: state.pmShowOutput,
  pmMaxFailures: state.pmMaxFailures,
  pmMaxBlocked: state.pmMaxBlocked,
  pmMaxSame: state.pmMaxSame,
  directorIterations: state.directorIterations,
  directorExecutionMode: state.directorExecutionMode,
  directorMaxParallelTasks: state.directorMaxParallelTasks,
  directorReadyTimeoutSeconds: state.directorReadyTimeoutSeconds,
  directorClaimTimeoutSeconds: state.directorClaimTimeoutSeconds,
  directorPhaseTimeoutSeconds: state.directorPhaseTimeoutSeconds,
  directorCompleteTimeoutSeconds: state.directorCompleteTimeoutSeconds,
  directorTaskTimeoutSeconds: state.directorTaskTimeoutSeconds,
  directorForever: state.directorForever,
  directorShowOutput: state.directorShowOutput,
  slmEnabled: state.slmEnabled,
  qaEnabled: state.qaEnabled,
  ramdiskRoot: state.ramdiskRoot,
  jsonLogPath: state.jsonLogPath,
  showMemory: state.showMemory,
  debugTracing: state.debugTracing,
  ioFsyncMode: state.ioFsyncMode,
  memoryRefsMode: state.memoryRefsMode,
}));

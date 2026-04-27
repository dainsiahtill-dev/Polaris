import { X, Save, Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/app/components/ui/tabs';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';

import type {
  SimpleProvider,
  ProviderConfig,
  LLMConfig,
  LLMStatus,
  RoleConfig,
  ProviderKind
} from '@/app/components/llm/types';
import { isCLIProviderType } from '@/app/components/llm/types';
import type { TestEvent, TestResult, TestSuiteSummary, TestUsageSummary } from '@/app/components/llm/test/types';
import type { InteractiveInterviewReport } from '@/app/components/llm/interview/InteractiveInterviewHall';
import { runStreamingTest } from '@/app/components/llm/test/streamingTest';
import { resolveProviderAwareRoleModel } from '@/app/components/llm/utils/providerModelResolver';

const SETTINGS_MODAL_SIZE_KEY = 'polaris:ui:settings_modal:size';
const DEFAULT_JSON_LOG_PATH = 'runtime/events/pm.events.jsonl';
const clampNumber = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

const PtyDrawer = lazy(() =>
  import('@/app/components/PtyDrawer').then((module) => ({ default: module.PtyDrawer }))
);
const LLMSettingsTab = lazy(() =>
  import('@/app/components/llm/LLMSettingsTab').then((module) => ({ default: module.LLMSettingsTab }))
);
const ArsenalPanel = lazy(() =>
  import('./arsenal/ArsenalPanel').then((module) => ({ default: module.ArsenalPanel }))
);
const SystemServicesTab = lazy(() =>
  import('./SystemServicesTab').then((module) => ({ default: module.SystemServicesTab }))
);

// 安全的JSON序列化函数，处理循环引用
const safeJsonStringify = (obj: unknown, space?: number): string => {
  const seen = new WeakSet();
  return JSON.stringify(obj, (key, val) => {
    if (val != null && typeof val === 'object') {
      if (seen.has(val)) {
        return '[Circular Reference]';
      }
      seen.add(val);
    }
    // 过滤掉React Fiber节点和其他不可序列化的对象
    if (val && typeof val === 'object') {
      if (val.constructor?.name === 'HTMLButtonElement' ||
        val.constructor?.name === 'FiberNode' ||
        val.constructor?.name === 'Object' && val.$$typeof) {
        return '[React Element]';
      }
    }
    return val;
  }, space);
};

function DeferredSectionFallback({ label, overlay = false }: { label: string; overlay?: boolean }) {
  const content = (
    <div className="flex items-center justify-center gap-2 rounded-lg border border-white/10 bg-[rgba(35,25,14,0.55)] px-4 py-5 text-xs text-text-muted">
      <Loader2 className="size-4 animate-spin text-cyan-300" />
      <span>正在载入{label}...</span>
    </div>
  );

  if (overlay) {
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-[rgba(35,25,14,0.7)] backdrop-blur-sm px-6">
        {content}
      </div>
    );
  }

  return content;
}

interface SettingsModalProps {
  isOpen: boolean;
  initialTab?: 'general' | 'llm' | 'arsenal' | 'services';
  onClose: () => void;
  onLlmStatusChange?: (status: LLMStatus | null) => void;
  settings: {
    prompt_profile?: string;
    interval?: number;
    timeout?: number;
    refresh_interval?: number;
    auto_refresh?: boolean;
    show_memory?: boolean;
    io_fsync_mode?: string;
    memory_refs_mode?: string;
    ramdisk_root?: string;
    json_log_path?: string;
    pm_show_output?: boolean;
    pm_runs_director?: boolean;
    pm_director_show_output?: boolean;
    pm_director_timeout?: number;
    pm_director_iterations?: number;
    pm_director_match_mode?: string;
    pm_max_failures?: number;
    pm_max_blocked?: number;
    pm_max_same?: number;
    pm_blocked_strategy?: 'skip' | 'manual' | 'degrade_retry' | 'auto';
    pm_blocked_degrade_max_retries?: number;
    director_iterations?: number;
    director_execution_mode?: 'serial' | 'parallel' | string;
    director_max_parallel_tasks?: number;
    director_ready_timeout_seconds?: number;
    director_claim_timeout_seconds?: number;
    director_phase_timeout_seconds?: number;
    director_complete_timeout_seconds?: number;
    director_task_timeout_seconds?: number;
    director_forever?: boolean;
    director_show_output?: boolean;
    slm_enabled?: boolean;
    qa_enabled?: boolean;
    debug_tracing?: boolean;
  } | null;
  onSave: (payload: {
    prompt_profile?: string;
    interval?: number;
    timeout?: number;
    refresh_interval?: number;
    auto_refresh?: boolean;
    show_memory?: boolean;
    io_fsync_mode?: string;
    memory_refs_mode?: string;
    ramdisk_root?: string;
    json_log_path?: string;
    pm_show_output?: boolean;
    pm_runs_director?: boolean;
    pm_director_show_output?: boolean;
    pm_director_timeout?: number;
    pm_director_iterations?: number;
    pm_director_match_mode?: string;
    pm_max_failures?: number;
    pm_max_blocked?: number;
    pm_max_same?: number;
    pm_blocked_strategy?: 'skip' | 'manual' | 'degrade_retry' | 'auto';
    pm_blocked_degrade_max_retries?: number;
    director_iterations?: number;
    director_execution_mode?: 'serial' | 'parallel';
    director_max_parallel_tasks?: number;
    director_ready_timeout_seconds?: number;
    director_claim_timeout_seconds?: number;
    director_phase_timeout_seconds?: number;
    director_complete_timeout_seconds?: number;
    director_task_timeout_seconds?: number;
    director_forever?: boolean;
    director_show_output?: boolean;
    slm_enabled?: boolean;
    qa_enabled?: boolean;
    debug_tracing?: boolean;
  }) => Promise<void>;
}

interface ProviderValidationResult {
  valid: boolean;
  errors?: string[];
  warnings?: string[];
  normalized_config?: Record<string, unknown> | null;
}

const ROLE_META: Record<string, { label: string; color: string; badge: string }> = {
  pm: { label: 'PM', color: 'text-cyan-300', badge: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30' },
  director: { label: 'Director', color: 'text-purple-300', badge: 'bg-purple-500/20 text-purple-200 border-purple-500/30' },
  qa: { label: 'QA', color: 'text-blue-200', badge: 'bg-blue-500/20 text-blue-200 border-blue-500/30' },
  architect: { label: 'Architect', color: 'text-emerald-300', badge: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30' },
};

const normalizeJsonLogPath = (value: string | null | undefined): string => {
  const raw = String(value ?? '').trim();
  if (!raw) return DEFAULT_JSON_LOG_PATH;
  return raw.replace(/\\/g, '/');
};

export function SettingsModal({ isOpen, initialTab = 'general', onClose, onLlmStatusChange, settings, onSave }: SettingsModalProps) {
  const defaultProfile = 'zhenguan_governance';
  const [promptProfile, setPromptProfile] = useState(defaultProfile);
  const [refreshInterval, setRefreshInterval] = useState(3);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [pmInterval, setPmInterval] = useState(20);
  const [pmTimeout, setPmTimeout] = useState(0);
  const [pmRunsDirector, setPmRunsDirector] = useState(true);
  const [pmDirectorShowOutput, setPmDirectorShowOutput] = useState(true);
  const [pmDirectorTimeout, setPmDirectorTimeout] = useState(600);
  const [pmDirectorIterations, setPmDirectorIterations] = useState(1);
  const [pmDirectorMatchMode, setPmDirectorMatchMode] = useState('latest');
  const [pmShowOutput, setPmShowOutput] = useState(true);
  const [pmMaxFailures, setPmMaxFailures] = useState(5);
  const [pmMaxBlocked, setPmMaxBlocked] = useState(5);
  const [pmMaxSame, setPmMaxSame] = useState(3);
  const [pmBlockedStrategy, setPmBlockedStrategy] = useState<'skip' | 'manual' | 'degrade_retry' | 'auto'>('auto');
  const [pmBlockedDegradeMaxRetries, setPmBlockedDegradeMaxRetries] = useState(1);
  const [directorIterations, setDirectorIterations] = useState(1);
  const [directorExecutionMode, setDirectorExecutionMode] = useState<'serial' | 'parallel'>('parallel');
  const [directorMaxParallelTasks, setDirectorMaxParallelTasks] = useState(3);
  const [directorReadyTimeoutSeconds, setDirectorReadyTimeoutSeconds] = useState(30);
  const [directorClaimTimeoutSeconds, setDirectorClaimTimeoutSeconds] = useState(30);
  const [directorPhaseTimeoutSeconds, setDirectorPhaseTimeoutSeconds] = useState(900);
  const [directorCompleteTimeoutSeconds, setDirectorCompleteTimeoutSeconds] = useState(30);
  const [directorTaskTimeoutSeconds, setDirectorTaskTimeoutSeconds] = useState(3600);
  const [directorForever, setDirectorForever] = useState(false);
  const [directorShowOutput, setDirectorShowOutput] = useState(true);
  const [slmEnabled, setSlmEnabled] = useState(false);
  const [qaEnabled, setQaEnabled] = useState(true);
  const [ramdiskRoot, setRamdiskRoot] = useState('');
  const [jsonLogPath, setJsonLogPath] = useState(DEFAULT_JSON_LOG_PATH);
  const [showMemory, setShowMemory] = useState(false);
  const [debugTracing, setDebugTracing] = useState(false);
  const [ioFsyncMode, setIoFsyncMode] = useState<'strict' | 'relaxed'>('strict');
  const [memoryRefsMode, setMemoryRefsMode] = useState<'strict' | 'soft' | 'off'>('soft');
  const [activeTab, setActiveTab] = useState<'general' | 'llm' | 'arsenal' | 'services'>(initialTab);

  useEffect(() => {
    if (!isOpen) return;
    setActiveTab(initialTab);
  }, [isOpen, initialTab]);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [llmConfig, setLLMConfig] = useState<LLMConfig | null>(null);
  const [llmStatus, setLLMStatus] = useState<LLMStatus | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmError, setLlmError] = useState<string | null>(null);
  const [llmTesting, setLlmTesting] = useState<Record<string, boolean>>({});
  const [providerModels, setProviderModels] = useState<Record<string, { supported: boolean; models: string[] }>>({});
  const [providerKeyDrafts, setProviderKeyDrafts] = useState<Record<string, string>>({});
  const [providerKeyStatus, setProviderKeyStatus] = useState<Record<string, string>>({});
  const [reportDrawer, setReportDrawer] = useState<{ open: boolean; data: unknown | null }>({ open: false, data: null });
  const [testSuites, setTestSuites] = useState({ connectivity: true, response: true, qualification: false });
  const [testLevel, setTestLevel] = useState<'quick' | 'full'>('quick');
  const [runAllBusy, setRunAllBusy] = useState(false);
  const [tuiDrawer, setTuiDrawer] = useState<{ open: boolean; role: string; providerId: string }>({
    open: false,
    role: '',
    providerId: '',
  });
  const [shouldMountTuiDrawer, setShouldMountTuiDrawer] = useState(false);
  const [tuiModelDraft, setTuiModelDraft] = useState('');
  const [tuiError, setTuiError] = useState<string | null>(null);
  const testAbortRef = useRef<AbortController | null>(null);
  const interviewAbortRef = useRef<AbortController | null>(null);
  const llmConfigRef = useRef<LLMConfig | null>(null);
  const lastSavedConfigRef = useRef<LLMConfig | null>(null);
  const llmSavePendingRef = useRef<LLMConfig | null>(null);
  const llmSaveQueueRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const [deletingProviders, setDeletingProviders] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (tuiDrawer.open) {
      setShouldMountTuiDrawer(true);
    }
  }, [tuiDrawer.open]);

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

  const [settingsModalSize, setSettingsModalSize] = useState<{ width: number; height: number }>(() => {
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
  });

  const [settingsModalResizing, setSettingsModalResizing] = useState(false);
  const resizeStateRef = useRef<null | { startX: number; startY: number; startWidth: number; startHeight: number }>(null);

  const handleResizePointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();

    resizeStateRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      startWidth: settingsModalSize.width,
      startHeight: settingsModalSize.height,
    };

    setSettingsModalResizing(true);

    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Best-effort; pointer capture can fail in some environments.
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    setSettingsModalSize((prev) => clampSettingsModalSize(prev));

    const onResize = () => setSettingsModalSize((prev) => clampSettingsModalSize(prev));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [isOpen]);

  // 当弹窗关闭时，刷新一次 LLM 状态并上报给上层，避免旧阻断残留
  useEffect(() => {
    if (!isOpen) {
      loadLLMStatus().catch((err) => {
        devLogger.error('[Settings] LLM status load failed:', err);
      });
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    if (settingsModalResizing) return;

    try {
      localStorage.setItem(SETTINGS_MODAL_SIZE_KEY, JSON.stringify(settingsModalSize));
    } catch {
      // ignore
    }
  }, [isOpen, settingsModalResizing, settingsModalSize]);

  useEffect(() => {
    if (!isOpen) return;
    if (!settingsModalResizing) return;

    const onPointerMove = (e: PointerEvent) => {
      const state = resizeStateRef.current;
      if (!state) return;

      const nextWidth = state.startWidth + (e.clientX - state.startX);
      const nextHeight = state.startHeight + (e.clientY - state.startY);
      setSettingsModalSize(clampSettingsModalSize({ width: nextWidth, height: nextHeight }));
    };

    const stop = () => {
      resizeStateRef.current = null;
      setSettingsModalResizing(false);
    };

    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);

    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
  }, [isOpen, settingsModalResizing]);

  useEffect(() => {
    if (!settings) return;
    setPromptProfile(settings.prompt_profile || defaultProfile);
    setRefreshInterval(settings.refresh_interval ?? 3);
    setAutoRefresh(settings.auto_refresh ?? true);
    setPmInterval(settings.interval ?? 20);
    setPmTimeout(settings.timeout ?? 0);
    setPmShowOutput(settings.pm_show_output ?? true);
    setPmRunsDirector(settings.pm_runs_director ?? true);
    setPmDirectorShowOutput(settings.pm_director_show_output ?? true);
    setPmDirectorTimeout(settings.pm_director_timeout ?? 600);
    setPmDirectorIterations(settings.pm_director_iterations ?? 1);
    setPmDirectorMatchMode(settings.pm_director_match_mode ?? 'latest');
    setPmMaxFailures(settings.pm_max_failures ?? 5);
    setPmMaxBlocked(settings.pm_max_blocked ?? 5);
    setPmMaxSame(settings.pm_max_same ?? 3);
    setPmBlockedStrategy(settings.pm_blocked_strategy ?? 'auto');
    setPmBlockedDegradeMaxRetries(settings.pm_blocked_degrade_max_retries ?? 1);
    setDirectorIterations(settings.director_iterations ?? 1);
    setDirectorExecutionMode(settings.director_execution_mode === 'serial' ? 'serial' : 'parallel');
    setDirectorMaxParallelTasks(settings.director_max_parallel_tasks ?? 3);
    setDirectorReadyTimeoutSeconds(settings.director_ready_timeout_seconds ?? 30);
    setDirectorClaimTimeoutSeconds(settings.director_claim_timeout_seconds ?? 30);
    setDirectorPhaseTimeoutSeconds(settings.director_phase_timeout_seconds ?? 900);
    setDirectorCompleteTimeoutSeconds(settings.director_complete_timeout_seconds ?? 30);
    setDirectorTaskTimeoutSeconds(settings.director_task_timeout_seconds ?? 3600);
    setDirectorForever(settings.director_forever ?? false);
    setDirectorShowOutput(settings.director_show_output ?? true);
    setSlmEnabled(settings.slm_enabled ?? false);
    setQaEnabled(settings.qa_enabled ?? true);
    setRamdiskRoot(settings.ramdisk_root ?? '');
    setJsonLogPath(normalizeJsonLogPath(settings.json_log_path));
    setShowMemory(settings.show_memory ?? false);
    setDebugTracing(settings.debug_tracing ?? false);
    setIoFsyncMode(settings.io_fsync_mode === 'relaxed' ? 'relaxed' : 'strict');
    setMemoryRefsMode(
      settings.memory_refs_mode === 'strict'
        ? 'strict'
        : settings.memory_refs_mode === 'off'
          ? 'off'
          : 'soft'
    );
  }, [settings]);

  useEffect(() => {
    llmConfigRef.current = llmConfig;
  }, [llmConfig]);

  useEffect(() => {
    if (!isOpen) {
      testAbortRef.current?.abort();
      interviewAbortRef.current?.abort();
    }
    return () => {
      testAbortRef.current?.abort();
      interviewAbortRef.current?.abort();
    };
  }, [isOpen]);

  const loadLLMConfig = async () => {
    setLlmLoading(true);
    setLlmError(null);
    try {
      const res = await apiFetch('/llm/config');
      if (!res.ok) {
        throw new Error('读取 LLM 配置失败');
      }
      const data = (await res.json()) as LLMConfig;
      setLLMConfig(data);
      llmConfigRef.current = data;
      lastSavedConfigRef.current = data;
      await refreshProviderKeyStatus(data.providers || {});
    } catch (err) {
      setLlmError(err instanceof Error ? err.message : '读取 LLM 配置失败');
    } finally {
      setLlmLoading(false);
    }
  };

  const loadLLMStatus = async () => {
    try {
      const res = await apiFetch('/llm/status');
      if (!res.ok) {
        throw new Error('读取 LLM 状态失败');
      }
      const data = (await res.json()) as LLMStatus;
      setLLMStatus(data);
      onLlmStatusChange?.(data);
    } catch (err) {
      setLLMStatus(null);
      onLlmStatusChange?.(null);
    }
  };

  const refreshProviderKeyStatus = async (providers: Record<string, ProviderConfig>) => {
    if (!window.polaris?.secrets?.get) {
      return;
    }
    const status: Record<string, string> = {};
    for (const [providerId, cfg] of Object.entries(providers)) {
      if (cfg.type !== 'openai_compat' && cfg.type !== 'anthropic_compat' && cfg.type !== 'codex_sdk') continue;
      const keyRef = cfg.api_key_ref || `keychain:llm:${providerId}`;
      const keyName = keyRef.startsWith('keychain:') ? keyRef.slice('keychain:'.length) : keyRef;
      try {
        const result = await window.polaris.secrets.get(keyName);
        if (result?.ok && result.value) {
          const value = String(result.value);
          const mask = value.length > 8 ? `${value.slice(0, 3)}****${value.slice(-4)}` : 'stored';
          status[providerId] = mask;
        }
      } catch {
        // ignore
      }
    }
    setProviderKeyStatus(status);
  };

  const resolveApiKey = async (providerId: string, cfg: ProviderConfig) => {
    if (cfg.type !== 'openai_compat' && cfg.type !== 'anthropic_compat' && cfg.type !== 'codex_sdk') return null;
    if (!window.polaris?.secrets?.get) return null;
    const keyRef = cfg.api_key_ref || `keychain:llm:${providerId}`;
    const keyName = keyRef.startsWith('keychain:') ? keyRef.slice('keychain:'.length) : keyRef;
    try {
      const result = await window.polaris.secrets.get(keyName);
      if (result?.ok && result.value) {
        return String(result.value);
      }
    } catch {
      // ignore
    }
    return null;
  };

  const resolveEnvOverrides = async (providerId: string, cfg: ProviderConfig) => {
    if (!isCLIProviderType(String(cfg.type || ''))) return null;
    const env = cfg.env && typeof cfg.env === 'object' ? cfg.env : {};
    const resolved: Record<string, string> = {};
    const missing: string[] = [];
    for (const [key, value] of Object.entries(env)) {
      if (value === undefined || value === null) continue;
      const raw = String(value).trim();
      const match = raw.match(/^\$?\{?keychain:([^}]+)\}?$/i);
      if (match) {
        if (!window.polaris?.secrets?.get) {
          missing.push(key);
          continue;
        }
        try {
          const result = await window.polaris.secrets.get(match[1]);
          if (result?.ok && result.value) {
            resolved[key] = String(result.value);
          } else {
            missing.push(key);
          }
        } catch {
          missing.push(key);
        }
      } else {
        resolved[key] = raw;
      }
    }
    return { env: resolved, missing };
  };

  useEffect(() => {
    if (!isOpen) return;
    loadLLMConfig().catch((err) => {
      devLogger.error('[Settings] LLM config load failed:', err);
    });
    loadLLMStatus().catch((err) => {
      devLogger.error('[Settings] LLM status load failed:', err);
    });
  }, [isOpen]);

  const updateRole = (role: string, updates: Partial<RoleConfig>) => {
    setLLMConfig((prev) => {
      if (!prev) return prev;
      const next = {
        ...prev,
        roles: {
          ...prev.roles,
          [role]: {
            ...prev.roles[role],
            ...updates,
          },
        },
      };
      llmConfigRef.current = next;
      return next;
    });
  };

  const updateProvider = (providerId: string, updates: Partial<ProviderConfig>) => {
    setLLMConfig((prev) => {
      if (!prev) return prev;
      const prevProvider = prev.providers?.[providerId] || {};
      const prevModel =
        typeof prevProvider.model === 'string'
          ? prevProvider.model
          : typeof prevProvider.model_id === 'string'
            ? prevProvider.model_id
            : typeof prevProvider.default_model === 'string'
              ? prevProvider.default_model
              : '';
      const nextProvider = {
        ...prevProvider,
        ...updates,
      };
      const nextModel =
        typeof nextProvider.model === 'string'
          ? nextProvider.model
          : typeof nextProvider.model_id === 'string'
            ? nextProvider.model_id
            : typeof nextProvider.default_model === 'string'
              ? nextProvider.default_model
              : '';

      let nextRoles = prev.roles || {};
      if (nextModel && nextModel !== prevModel) {
        nextRoles = { ...(prev.roles || {}) };
        Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
          if (!roleCfg || typeof roleCfg !== 'object') return;
          if (roleCfg.provider_id !== providerId) return;
          const roleModel = typeof roleCfg.model === 'string' ? roleCfg.model : '';
          if (!roleModel || roleModel === prevModel) {
            nextRoles[roleId] = { ...roleCfg, model: nextModel };
          }
        });
      }
      const next = {
        ...prev,
        providers: {
          ...prev.providers,
          [providerId]: nextProvider,
        },
        roles: nextRoles,
      };
      llmConfigRef.current = next;
      return next;
    });
  };

  const updateLLMConfigDraft = (nextConfig: LLMConfig) => {
    setLLMConfig(nextConfig);
    llmConfigRef.current = nextConfig;
  };

  const parseListInput = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return [];
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return parsed.map((item) => String(item));
    } catch {
      // fallback to line split
    }
    return trimmed
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  };

  const queueLlmSave = async (nextConfig: LLMConfig) => {
    llmSavePendingRef.current = nextConfig;
    const run = async (): Promise<boolean> => {
      if (!llmSavePendingRef.current) return true;
      setLlmSaving(true);
      setLlmError(null);
      let success = true;
      while (llmSavePendingRef.current) {
        const configToSave = llmSavePendingRef.current;
        llmSavePendingRef.current = null;
        try {
          const res = await apiFetch('/llm/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(configToSave),
          });
          if (!res.ok) {
            throw new Error('保存 LLM 配置失败');
          }
          const data = (await res.json()) as LLMConfig;
          setLLMConfig(data);
          llmConfigRef.current = data;
          lastSavedConfigRef.current = data;
          await refreshProviderKeyStatus(data.providers || {});
          await loadLLMStatus();
        } catch (err) {
          setLlmError(err instanceof Error ? err.message : '保存 LLM 配置失败');
          success = false;
          llmSavePendingRef.current = null;
          break;
        }
      }
      setLlmSaving(false);
      return success;
    };
    const runPromise = llmSaveQueueRef.current.then(run, run);
    llmSaveQueueRef.current = runPromise;
    return runPromise;
  };

  const applyLLMConfigMutation = async (mutator: (current: LLMConfig) => LLMConfig) => {
    const current = llmConfigRef.current;
    if (!current) return null;
    const nextConfig = mutator(current);
    setLLMConfig(nextConfig);
    llmConfigRef.current = nextConfig;
    return nextConfig;
  };

  const saveProviderKey = async (providerId: string) => {
    const key = providerKeyDrafts[providerId];
    if (!key || !window.polaris?.secrets?.set) return;
    const ref = `keychain:llm:${providerId}`;
    const keyName = ref.slice('keychain:'.length);
    const result = await window.polaris.secrets.set(keyName, key);
    if (result?.ok) {
      await applyLLMConfigMutation((current) => ({
        ...current,
        providers: {
          ...(current.providers || {}),
          [providerId]: {
            ...(current.providers?.[providerId] || {}),
            api_key_ref: ref,
          },
        },
      }));
      setProviderKeyDrafts((prev) => ({ ...prev, [providerId]: '' }));
      setProviderKeyStatus((prev) => ({ ...prev, [providerId]: `${key.slice(0, 3)}****${key.slice(-4)}` }));
    }
  };

  const saveLLMConfig = async (config?: LLMConfig): Promise<boolean> => {
    const target = config || llmConfigRef.current;
    if (!target) return true;
    return queueLlmSave(target);
  };

  const mapSimpleProviderToConfig = (
    provider: SimpleProvider,
    existing?: ProviderConfig
  ): ProviderConfig => {
    const base: ProviderConfig = { ...(existing || {}) };
    base.type = (provider.kind || 'cli') as ProviderKind;
    base.name = provider.name;
    base.model = provider.modelId;
    if (provider.conn.kind === 'http') {
      base.base_url = provider.conn.baseUrl;
      delete base.command;
      delete base.args;
      delete base.env;
    } else {
      base.command = provider.conn.command;
      base.args = provider.conn.args || [];
      base.env = provider.conn.env || {};
      base.cli_mode = provider.cliMode || 'headless';
      delete base.base_url;
    }
    if (provider.outputPath !== undefined) {
      base.output_path = provider.outputPath;
    }
    return base;
  };

  const ensureProviderSaved = async (provider: SimpleProvider) => {
    const current = llmConfigRef.current;
    if (!current) return null;
    const mapped = mapSimpleProviderToConfig(provider, current.providers?.[provider.id]);
    const nextConfig: LLMConfig = {
      ...current,
      providers: {
        ...(current.providers || {}),
        [provider.id]: mapped
      }
    };
    setLLMConfig(nextConfig);
    llmConfigRef.current = nextConfig;
    return nextConfig;
  };

  const addProviderAndPersist = async (providerId: string, provider: ProviderConfig) => {
    await applyLLMConfigMutation((current) => ({
      ...current,
      providers: {
        ...(current.providers || {}),
        [providerId]: provider,
      },
    }));
  };

  const deleteProviderAndPersist = async (providerId: string) => {
    setDeletingProviders((prev) => ({ ...prev, [providerId]: true }));
    try {
      await applyLLMConfigMutation((current) => {
        const nextProviders = { ...(current.providers || {}) };
        delete nextProviders[providerId];
        const nextRoles = { ...(current.roles || {}) };
        Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
          if (roleCfg?.provider_id === providerId) {
            nextRoles[roleId] = { ...roleCfg, provider_id: '', model: '' };
          }
        });
        return { ...current, providers: nextProviders, roles: nextRoles };
      });
    } finally {
      setDeletingProviders((prev) => {
        const next = { ...prev };
        delete next[providerId];
        return next;
      });
    }
  };

  const buildTestUsage = (usage: unknown): TestUsageSummary | undefined => {
    if (!usage || typeof usage !== 'object') return undefined;
    const data = usage as Record<string, unknown>;
    const prompt = Number(data.prompt_tokens ?? data.promptTokens);
    const completion = Number(data.completion_tokens ?? data.completionTokens);
    const total = Number(data.total_tokens ?? data.totalTokens);
    if ([prompt, completion, total].some((value) => Number.isNaN(value))) return undefined;
    return {
      promptTokens: prompt,
      completionTokens: completion,
      totalTokens: total,
      estimated: Boolean(data.estimated)
    };
  };

  const buildSuiteSummary = (suites: unknown): TestSuiteSummary[] => {
    if (!suites || typeof suites !== 'object') return [];
    const entries = Object.entries(suites as Record<string, unknown>);
    return entries.map(([name, value]) => {
      const payload = value as Record<string, unknown>;
      const ok = Boolean(payload?.ok);
      const note = typeof payload?.status === 'string' ? payload.status : undefined;
      return { name, ok, note };
    });
  };

  const buildLatency = (suites: unknown): number | undefined => {
    if (!suites || typeof suites !== 'object') return undefined;
    const entries = Object.values(suites as Record<string, unknown>);
    const samples: number[] = [];
    for (const entry of entries) {
      if (!entry || typeof entry !== 'object') continue;
      const payload = entry as Record<string, unknown>;
      const details = payload.details as Record<string, unknown> | undefined;
      if (details && typeof details.latency_ms === 'number') {
        samples.push(details.latency_ms);
      }
      const cases = payload.cases;
      if (Array.isArray(cases)) {
        cases.forEach((item) => {
          if (item && typeof item === 'object' && typeof (item as Record<string, unknown>).latency_ms === 'number') {
            samples.push(Number((item as Record<string, unknown>).latency_ms));
          }
        });
      }
    }
    if (samples.length === 0) return undefined;
    const total = samples.reduce((sum, value) => sum + value, 0);
    return total / samples.length;
  };

  const buildThinkingMeta = (suites: unknown): TestResult['thinking'] => {
    if (!suites || typeof suites !== 'object') return undefined;
    const suite = (suites as Record<string, unknown>).thinking as Record<string, unknown> | undefined;
    if (!suite) return undefined;
    const details = suite.details as Record<string, unknown> | undefined;
    const thinking = (details?.thinking as Record<string, unknown>) || (suite.thinking as Record<string, unknown>);
    if (!thinking) return undefined;
    return {
      supportsThinking: typeof thinking.supports_thinking === 'boolean' ? thinking.supports_thinking : undefined,
      confidence: typeof thinking.confidence === 'number' ? thinking.confidence : undefined,
      format: typeof thinking.format === 'string' ? thinking.format : undefined
    };
  };

  const buildTestResult = (report: Record<string, unknown>): TestResult => {
    // API returns ready/grade either in report.final or directly in report
    // Handle both cases
    let final = report?.final as Record<string, unknown> | undefined;

    // If report has ready directly, use report as final
    if (!final && typeof report?.ready === 'boolean') {
      final = report;
    }

    const runId = typeof report?.test_run_id === 'string' ? report.test_run_id : undefined;
    const ready = typeof final?.ready === 'boolean' ? final.ready : undefined;
    const grade = typeof final?.grade === 'string' ? final.grade : undefined;

    return {
      report,
      runId,
      ready,
      grade,
      usage: buildTestUsage(report.usage),
      suites: buildSuiteSummary(report.suites),
      thinking: buildThinkingMeta(report.suites),
      latencyMs: buildLatency(report.suites)
    };
  };

  const isMissingCodexCliError = (message: string): boolean => {
    const lowered = message.toLowerCase();
    if (lowered.includes('codex cli command not found') || lowered.includes('codex command not found')) {
      return true;
    }
    if (lowered.includes('codex') && (lowered.includes('not found') || lowered.includes('not recognized'))) {
      return true;
    }
    if (lowered.includes('command not found') || lowered.includes('enoent')) {
      return true;
    }
    return false;
  };

  const collectSuiteErrors = (report: Record<string, unknown>): string[] => {
    const suites = report.suites as Record<string, unknown> | undefined;
    if (!suites || typeof suites !== 'object') return [];
    const errors: string[] = [];

    const responseDetails = (suites.response as Record<string, unknown> | undefined)?.details as Record<string, unknown> | undefined;
    if (responseDetails && typeof responseDetails.error === 'string') {
      errors.push(responseDetails.error);
    }

    const connectivityDetails = (suites.connectivity as Record<string, unknown> | undefined)?.details as Record<string, unknown> | undefined;
    const healthError = (connectivityDetails?.health as Record<string, unknown> | undefined)?.error;
    if (typeof healthError === 'string') {
      errors.push(healthError);
    }
    const modelError = (connectivityDetails?.model_available as Record<string, unknown> | undefined)?.error;
    if (typeof modelError === 'string') {
      errors.push(modelError);
    }

    return errors;
  };

  const emitPromptEvents = (report: Record<string, unknown>, emitEvent: (type: TestEvent['type'], content: string) => void) => {
    const suites = report.suites as Record<string, unknown> | undefined;
    if (!suites || typeof suites !== 'object') return;

    const responseSuite = suites.response as Record<string, unknown> | undefined;
    const responsePrompt = responseSuite?.details && typeof (responseSuite.details as Record<string, unknown>).prompt === 'string'
      ? String((responseSuite.details as Record<string, unknown>).prompt)
      : '';
    if (responsePrompt) {
      emitEvent('stdout', `Response prompt: ${responsePrompt}`);
    }

    const thinkingSuite = suites.thinking as Record<string, unknown> | undefined;
    const thinkingPrompt = thinkingSuite?.details && typeof (thinkingSuite.details as Record<string, unknown>).prompt === 'string'
      ? String((thinkingSuite.details as Record<string, unknown>).prompt)
      : '';
    if (thinkingPrompt) {
      emitEvent('stdout', `Thinking prompt: ${thinkingPrompt}`);
    }

    const qualificationSuite = suites.qualification as Record<string, unknown> | undefined;
    const qualificationCases = qualificationSuite?.cases as Array<Record<string, unknown>> | undefined;
    if (Array.isArray(qualificationCases)) {
      qualificationCases.forEach((caseItem) => {
        const prompt = typeof caseItem.prompt === 'string' ? caseItem.prompt : '';
        const caseId = typeof caseItem.id === 'string' ? caseItem.id : 'case';
        if (prompt) {
          emitEvent('stdout', `Qualification ${caseId} prompt: ${prompt}`);
        }
      });
    }

    const interviewSuite = suites.interview as Record<string, unknown> | undefined;
    const interviewCases = interviewSuite?.cases as Array<Record<string, unknown>> | undefined;
    if (Array.isArray(interviewCases)) {
      interviewCases.forEach((caseItem) => {
        const question = typeof caseItem.question === 'string' ? caseItem.question : '';
        const caseId = typeof caseItem.id === 'string' ? caseItem.id : 'question';
        if (question) {
          emitEvent('stdout', `Interview ${caseId} question: ${question}`);
        }
      });
    }
  };

  const validateProviderConfig = async (
    providerType: string,
    config: ProviderConfig
  ): Promise<ProviderValidationResult | null> => {
    if (!providerType) return null;
    try {
      const res = await apiFetch(`/llm/providers/${providerType}/validate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
      });
      if (!res.ok) return null;
      return (await res.json()) as ProviderValidationResult;
    } catch {
      return null;
    }
  };

  const runProviderTest = async (
    provider: SimpleProvider,
    onEvent?: (event: TestEvent) => void
  ): Promise<TestResult | null> => {
    if (!llmConfig) return null;
    let activeConfig = llmConfig;
    const providerId = provider.id;
    let providerCfg = activeConfig.providers?.[providerId];

    // Find a role that uses this provider, or use connectivity mode
    let testRole = null;
    let testModel = provider.modelId || 'test-model';
    let foundMatchingRole = false;

    devLogger.debug('[runProviderTest] Looking for matching role for provider:', providerId);
    devLogger.debug('[runProviderTest] Available roles:', Object.keys(activeConfig.roles || {}));

    // Look for existing role that uses this provider
    for (const [roleId, roleCfg] of Object.entries(activeConfig.roles || {})) {
      devLogger.debug('[runProviderTest] Checking role:', roleId, 'provider_id:', roleCfg?.provider_id);
      if (roleCfg.provider_id === providerId && roleCfg.model) {
        testRole = roleId;
        testModel = roleCfg.model;
        foundMatchingRole = true;
        devLogger.debug('[runProviderTest] Found matching role:', roleId);
        break;
      }
    }

    // If no role found, use 'connectivity' mode for standalone provider testing
    // This ensures backend uses connectivity-only evaluation (not role-based)
    if (!testRole) {
      testRole = 'connectivity';
      devLogger.debug('[runProviderTest] ***** NO MATCHING ROLE FOUND, USING CONNECTIVITY MODE *****');
    } else {
      devLogger.debug('[runProviderTest] Using existing role:', testRole);
    }

    const providerName = provider.name || providerId;
    const emitEvent = (type: TestEvent['type'], content: string, details?: unknown) => {
      if (!onEvent) return;
      onEvent({
        type,
        timestamp: new Date().toISOString(),
        content,
        details
      });
    };
    const controller = new AbortController();
    testAbortRef.current = controller;
    let codexGuidanceEmitted = false;
    const emitCodexGuidanceOnce = (errors?: string[]) => {
      if (codexGuidanceEmitted) return;
      codexGuidanceEmitted = true;
      emitEvent('stdout', '安装 Codex CLI：npm install -g @openai/codex');
      emitEvent('stdout', '验证安装：codex --version');
      emitEvent('stdout', '临时使用：npx @openai/codex --version');
      if (errors && errors.some((item) => item.toLowerCase().includes('eperm'))) {
        emitEvent('stdout', '如果出现 EPERM，请关闭占用的 codex 进程或用管理员终端清理后重装。');
      }
    };

    try {
      emitEvent('command', `Starting test for ${providerName}`);

      emitEvent('stdout', '使用当前未保存草稿配置进行测试（不会自动持久化）...');
      activeConfig = (await ensureProviderSaved(provider)) || activeConfig;
      providerCfg = activeConfig.providers?.[providerId];

      if (!providerCfg) {
        emitEvent('error', '提供商配置未保存，无法执行测试');
        return null;
      }

      if (!testRole) {
        emitEvent('error', '未找到可用角色，请先配置角色后再测试');
        return null;
      }

      const apiKey = await resolveApiKey(providerId, providerCfg);
      const envResult = await resolveEnvOverrides(providerId, providerCfg);
      if (envResult?.missing && envResult.missing.length > 0) {
        emitEvent('stderr', `环境变量缺少密钥: ${envResult.missing.join(', ')}`);
      }

      emitEvent('stdout', '验证配置');
      const warnings: string[] = [];
      const providerType = String(providerCfg.type || '').toLowerCase();
      if ((providerType === 'openai_compat' || providerType === 'anthropic_compat' || providerType === 'codex_sdk') && !providerCfg.base_url) {
        warnings.push('缺少 Base URL');
      }
      if ((providerType === 'codex_cli' || providerType === 'gemini_cli' || providerType === 'cli') && !providerCfg.command) {
        warnings.push('缺少 CLI command');
      }
      if (warnings.length > 0) {
        emitEvent('stderr', `配置可能不完整: ${warnings.join(' / ')}`);
      } else {
        emitEvent('stdout', '配置验证通过');
      }

      const isCodex = provider.kind === 'codex_cli';
      const shouldValidateCli =
        providerType === 'codex_cli' || providerType === 'gemini_cli' || providerType === 'cli';
      const validationResult = shouldValidateCli ? await validateProviderConfig(providerType, providerCfg) : null;
      const validationErrors = Array.isArray(validationResult?.errors) ? validationResult?.errors : [];
      const validationWarnings = Array.isArray(validationResult?.warnings) ? validationResult?.warnings : [];
      if (validationWarnings.length > 0) {
        emitEvent('stderr', `配置警告: ${validationWarnings.join(' / ')}`);
      }
      if (validationErrors.length > 0) {
        emitEvent('stderr', `配置验证失败: ${validationErrors.join(' / ')}`);
      }
      if (isCodex && validationErrors.some((item) => isMissingCodexCliError(item))) {
        emitEvent('error', '未检测到 Codex CLI，测试已取消');
        emitCodexGuidanceOnce(validationErrors);
        const error = new Error('未检测到 Codex CLI，请先安装 @openai/codex') as Error & { skipUiEvent?: boolean };
        error.skipUiEvent = true;
        throw error;
      }

      const promptOverride = undefined;
      const promptPreview = isCodex ? 'Reply with the single word OK.' : undefined;
      const evaluationMode = 'provider';
      if (promptPreview) {
        emitEvent('stdout', `Prompt: ${promptPreview}`);
      }
      const suites = isCodex
        ? ['response']
        : llmConfig.policies?.test_required_suites || ['connectivity', 'response'];
      const payload = {
        role: testRole,
        provider_id: providerId,
        model: testModel,
        suites,
        test_level: 'full',
        evaluation_mode: evaluationMode,
        prompt_override: promptOverride,
        api_key: apiKey ? '***' : null
      };
      devLogger.debug('[runProviderTest] Test parameters:', { testRole, testModel, suites, providerId });
      emitEvent('command', `POST /llm/test ${JSON.stringify(payload)}`);

      const willInvoke = suites.some((suite) => suite !== 'connectivity');
      if (willInvoke && (provider.conn.kind === 'codex_cli' || provider.conn.kind === 'gemini_cli')) {
        const command = String(provider.conn.command || '');
        const args = Array.isArray(provider.conn.args) ? provider.conn.args : [];
        const promptDisplay = promptPreview ? JSON.stringify(promptPreview) : '{prompt}';
        const renderedArgs = args
          .map((arg) => arg.replace('{model}', testModel).replace('{prompt}', promptDisplay))
          .join(' ');
        if (command) {
          emitEvent('command', `Command: ${command} ${renderedArgs}`.trim());
        }
      }

      emitEvent('stdout', '发送测试请求...');
      const res = await apiFetch('/llm/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role: testRole,
          provider_id: providerId,
          model: testModel,
          suites,
          test_level: 'full',
          evaluation_mode: evaluationMode,
          prompt_override: promptOverride,
          api_key: apiKey,
          env_overrides: envResult?.env && Object.keys(envResult.env).length > 0 ? envResult.env : undefined
        }),
        signal: controller.signal
      });
      emitEvent('stdout', '响应已返回，解析结果...');

      if (!res.ok) {
        const detail = await res.text().catch(() => res.statusText);
        throw new Error(`Provider test failed: ${detail || res.statusText}`);
      }

      const report = (await res.json()) as Record<string, unknown>;
      emitPromptEvents(report, (type, content) => emitEvent(type, content));
      const suiteErrors = collectSuiteErrors(report);
      suiteErrors.forEach((error) => emitEvent('stderr', error));
      if (isCodex && suiteErrors.some((item) => isMissingCodexCliError(item))) {
        emitCodexGuidanceOnce(suiteErrors);
      }
      const result = buildTestResult(report);
      emitEvent('response', safeJsonStringify(report, 2));
      emitEvent(
        result.ready ? 'result' : 'error',
        result.ready ? 'Test completed successfully' : 'Test failed'
      );

      await loadLLMStatus();
      return result;
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        emitEvent('error', 'Test cancelled');
        return null;
      }
      const message = err instanceof Error ? err.message : 'Provider test failed';
      const skipUiEvent =
        typeof err === 'object' &&
        err !== null &&
        'skipUiEvent' in err &&
        Boolean((err as { skipUiEvent?: boolean }).skipUiEvent);
      if (!skipUiEvent) {
        emitEvent('error', message);
      }
      setLlmError(message);
      throw err;
    } finally {
      if (testAbortRef.current === controller) {
        testAbortRef.current = null;
      }
    }
  };

  const runProviderTestStreaming = async (
    provider: SimpleProvider,
    onEvent?: (event: TestEvent) => void
  ): Promise<TestResult | null> => {
    if (!llmConfig) return null;

    const providerId = provider.id;
    const providerCfg = llmConfig.providers?.[providerId];

    if (!providerCfg) {
      onEvent?.({
        type: 'error',
        timestamp: new Date().toISOString(),
        content: `未找到提供商 "${providerId}" 的配置`
      });
      return null;
    }

    // 🔧 对于连通性测试，不需要角色配置，直接使用 provider 的 model
    const testModel = provider.modelId || providerCfg.model || providerCfg.default_model || 'default';

    // Provider 卡片的一键测试只做连通性，不做资格/能力评估。
    const roleForTest = 'connectivity';

    const apiKey = await resolveApiKey(providerId, providerCfg);
    const envResult = await resolveEnvOverrides(providerId, providerCfg);
    const suites = ['connectivity'];
    const controller = new AbortController();
    testAbortRef.current = controller;

    try {
      const report = await runStreamingTest({
        role: roleForTest,
        providerId,
        model: testModel,
        suites,
        testLevel: 'quick',
        evaluationMode: 'provider',
        apiKey,
        envOverrides: envResult?.env,
        onEvent,
        onSuiteComplete: (suite, ok) => {
          if (!ok) {
            const errorMsg = `Suite ${suite} failed`;
            setLlmError(errorMsg);
          }
        },
        onComplete: (result) => {
          loadLLMStatus();
        },
        onError: (error) => {
          setLlmError(error);
        },
        signal: controller.signal
      });

      if (!report) {
        return null;
      }

      const result: TestResult = {
        ready: (report.final as { ready?: boolean })?.ready ?? false,
        grade: (report.final as { grade?: string })?.grade || 'FAIL',
        report,
        suites: Object.entries(report.suites || {}).map(([name, suiteResult]) => ({
          name,
          ok: (suiteResult as { ok?: boolean })?.ok ?? false
        }))
      };

      return result;
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        const message = err.message;
        onEvent?.({ type: 'error', timestamp: new Date().toISOString(), content: message });
        setLlmError(message);
      }
      return null;
    } finally {
      if (testAbortRef.current === controller) {
        testAbortRef.current = null;
      }
    }
  };

  const cancelProviderTest = () => {
    testAbortRef.current?.abort();
  };

  const cancelInterview = () => {
    interviewAbortRef.current?.abort();
  };

  const runLlmTest = async (
    role: string,
    level: 'quick' | 'full' = 'quick',
    suites?: string[],
    showReport: boolean = true,
    overrides?: { providerId?: string; model?: string },
  ) => {
    if (!llmConfig) return null;
    const roleCfg = llmConfig.roles?.[role];
    const providerIdRaw = overrides?.providerId || roleCfg?.provider_id;
    const providerId = typeof providerIdRaw === 'string' ? providerIdRaw.trim() : '';
    if (!providerId) return null;
    const providerCfg = llmConfig.providers?.[providerId];
    const model = resolveProviderAwareRoleModel(roleCfg, providerId, providerCfg, overrides?.model);
    if (!model) return null;
    const apiKey = providerCfg ? await resolveApiKey(providerId, providerCfg) : null;
    const envResult = providerCfg ? await resolveEnvOverrides(providerId, providerCfg) : null;
    setLlmTesting((prev) => ({ ...prev, [role]: true }));
    try {
      const res = await apiFetch('/llm/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role,
          provider_id: providerId,
          model,
          suites: suites || llmConfig.policies?.test_required_suites,
          test_level: level,
          api_key: apiKey,
          env_overrides: envResult?.env && Object.keys(envResult.env).length > 0 ? envResult.env : undefined
        }),
      });
      if (!res.ok) {
        throw new Error('LLM test failed');
      }
      const report = await res.json();
      if (showReport) {
        setReportDrawer({ open: true, data: report });
      }
      await loadLLMStatus();
      return report as Record<string, unknown>;
    } catch (err) {
      setLlmError(err instanceof Error ? err.message : 'LLM test failed');
      return null;
    } finally {
      setLlmTesting((prev) => ({ ...prev, [role]: false }));
    }
  };

  const runLlmTestStreaming = async (
    role: string,
    level: 'quick' | 'full' = 'quick',
    suites?: string[],
    showReport: boolean = true,
    overrides?: { providerId?: string; model?: string },
  ): Promise<Record<string, unknown> | null> => {
    if (!llmConfig) return null;
    const roleCfg = llmConfig.roles?.[role];
    const providerIdRaw = overrides?.providerId || roleCfg?.provider_id;
    const providerId = typeof providerIdRaw === 'string' ? providerIdRaw.trim() : '';
    if (!providerId) return null;
    const providerCfg = llmConfig.providers?.[providerId];
    const model = resolveProviderAwareRoleModel(roleCfg, providerId, providerCfg, overrides?.model);
    if (!model) return null;
    const apiKey = providerCfg ? await resolveApiKey(providerId, providerCfg) : null;
    const envResult = providerCfg ? await resolveEnvOverrides(providerId, providerCfg) : null;

    setLlmTesting((prev) => ({ ...prev, [role]: true }));

    const controller = new AbortController();
    testAbortRef.current = controller;

    try {
      const report = await runStreamingTest({
        role,
        providerId,
        model,
        suites: suites || llmConfig.policies?.test_required_suites,
        testLevel: level,
        evaluationMode: 'provider',
        apiKey,
        envOverrides: envResult?.env,
        signal: controller.signal
      });

      if (report && showReport) {
        setReportDrawer({ open: true, data: report });
      }

      await loadLLMStatus();
      return report || null;
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        setLlmError(err.message);
      }
      return null;
    } finally {
      setLlmTesting((prev) => ({ ...prev, [role]: false }));
      if (testAbortRef.current === controller) {
        testAbortRef.current = null;
      }
    }
  };

  const runInterview = async (
    role: string,
    providerIdOverride?: string,
    modelOverride?: string,
    onEvent?: (event: TestEvent) => void
  ): Promise<Record<string, unknown> | null> => {
    if (!llmConfig) return null;
    const roleCfg = llmConfig.roles?.[role];
    const providerId = providerIdOverride || roleCfg?.provider_id;
    const roleModel = roleCfg?.model;
    const emitEvent = (type: TestEvent['type'], content: string, details?: unknown) => {
      if (!onEvent) return;
      onEvent({
        type,
        timestamp: new Date().toISOString(),
        content,
        details
      });
    };
    emitEvent('command', `Starting interview for ${role}`);
    if (!providerId) {
      emitEvent('error', '缺少角色提供商或模型，无法开始面试');
      return null;
    }
    const providerCfg = llmConfig.providers?.[providerId];
    devLogger.debug('[runInterview] providerCfg:', {
      providerId,
      providerCfg: providerCfg ? {
        model: providerCfg.model,
        model_id: providerCfg.model_id,
        default_model: providerCfg.default_model,
        type: providerCfg.type,
        name: providerCfg.name,
      } : null,
    });
    if (!providerCfg) {
      emitEvent('error', '提供商未配置，无法开始面试');
      return null;
    }
    const isRoleDefaultProvider = !providerIdOverride || providerIdOverride === roleCfg?.provider_id;
    const providerModel =
      typeof providerCfg?.model === 'string'
        ? providerCfg.model
        : typeof providerCfg?.model_id === 'string'
          ? providerCfg.model_id
          : typeof providerCfg?.default_model === 'string'
            ? providerCfg.default_model
            : '';
    devLogger.debug('[runInterview] providerModel:', providerModel, 'modelOverride:', modelOverride, 'roleModel:', roleModel);
    const shouldSyncRoleModel = isRoleDefaultProvider && Boolean(providerModel) && roleModel !== providerModel && !modelOverride;
    let model = modelOverride || providerModel || roleModel;
    devLogger.debug('[runInterview] final model:', model);
    if (providerModel && roleModel && providerModel !== roleModel && !modelOverride) {
      emitEvent(
        'stderr',
        `角色模型(${roleModel}) 与提供商模型(${providerModel})不一致，面试将使用提供商模型`
      );
      model = providerModel;
    }
    if (shouldSyncRoleModel && providerModel) {
      emitEvent('stdout', `自动同步角色模型为 ${providerModel}`);
      await applyLLMConfigMutation((current) => {
        const currentRole = current.roles?.[role];
        if (!currentRole || currentRole.provider_id !== providerId) {
          return current;
        }
        return {
          ...current,
          roles: {
            ...current.roles,
            [role]: {
              ...currentRole,
              model: providerModel
            }
          }
        };
      });
    }
    if (!model) {
      emitEvent('error', '缺少角色模型，无法开始面试');
      return null;
    }
    const apiKey = providerCfg ? await resolveApiKey(providerId, providerCfg) : null;
    const envResult = providerCfg ? await resolveEnvOverrides(providerId, providerCfg) : null;
    const suites = ['thinking', 'interview'];
    const payload = {
      role,
      provider_id: providerId,
      model,
      suites,
      test_level: 'full',
      api_key: apiKey ? '***' : null
    };
    emitEvent('command', `POST /llm/test ${JSON.stringify(payload)}`);
    emitEvent('stdout', '发送面试请求...');
    if (interviewAbortRef.current) {
      interviewAbortRef.current.abort();
    }
    const controller = new AbortController();
    interviewAbortRef.current = controller;
    try {
      const res = await apiFetch('/llm/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role,
          provider_id: providerId,
          model,
          suites,
          test_level: 'full',
          api_key: apiKey,
          env_overrides: envResult?.env && Object.keys(envResult.env).length > 0 ? envResult.env : undefined
        }),
        signal: controller.signal
      });
      emitEvent('stdout', '响应已返回，解析结果...');
      if (!res.ok) {
        const detail = await res.text().catch(() => res.statusText);
        emitEvent('error', `面试请求失败: ${detail || res.statusText}`);
        return null;
      }
      const report = (await res.json()) as Record<string, unknown>;
      emitEvent('response', safeJsonStringify(report, 2));
      const final = report.final as Record<string, unknown> | undefined;
      const ready = typeof final?.ready === 'boolean' ? final.ready : undefined;
      emitEvent(ready ? 'result' : 'error', ready ? '面试完成' : '面试未通过');
      await loadLLMStatus();
      return report;
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        emitEvent('error', '面试已取消');
        return null;
      }
      const message = err instanceof Error ? err.message : '面试请求失败';
      emitEvent('error', message);
      setLlmError(message);
      return null;
    } finally {
      if (interviewAbortRef.current === controller) {
        interviewAbortRef.current = null;
      }
    }
  };

  const askInteractiveInterview = async (payload: {
    roleId: string;
    providerId: string;
    model: string;
    question: string;
    expectedCriteria?: string[];
    expectsThinking?: boolean;
    sessionId?: string | null;
    context?: Array<{ question: string; answer: string }>;
  }): Promise<{
    sessionId: string;
    answer: string;
    output?: string;
    thinking?: string;
    latencyMs?: number;
    ok?: boolean;
    error?: string | null;
    debug?: {
      prompt?: string;
      cli_args?: string[] | null;
      cli_send_prompt?: boolean | null;
      stdin_prompt?: string | null;
      cli_command?: string | null;
    };
  } | null> => {
    if (!llmConfig) return null;
    const providerCfg = llmConfig.providers?.[payload.providerId];
    if (!providerCfg) {
      throw new Error('提供商未配置');
    }
    const apiKey = await resolveApiKey(payload.providerId, providerCfg);
    const envResult = await resolveEnvOverrides(payload.providerId, providerCfg);
    const res = await apiFetch('/llm/interview/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        role: payload.roleId,
        provider_id: payload.providerId,
        model: payload.model,
        question: payload.question,
        context: payload.context,
        expects_thinking: payload.expectsThinking,
        criteria: payload.expectedCriteria,
        session_id: payload.sessionId,
        api_key: apiKey,
        env_overrides: envResult?.env && Object.keys(envResult.env).length > 0 ? envResult.env : undefined
      })
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(detail || '发送问题失败');
    }
    const data = (await res.json()) as Record<string, unknown>;
    return {
      sessionId: String(data.session_id || data.sessionId || payload.sessionId || ''),
      answer: String(data.answer || data.output || ''),
      output: typeof data.output === 'string' ? data.output : undefined,
      thinking: typeof data.thinking === 'string' ? data.thinking : undefined,
      latencyMs: typeof data.latency_ms === 'number' ? data.latency_ms : undefined,
      ok: typeof data.ok === 'boolean' ? data.ok : undefined,
      error: typeof data.error === 'string' ? data.error : null,
      debug:
        typeof data.debug === 'object'
          ? (data.debug as {
            prompt?: string;
            cli_args?: string[] | null;
            cli_send_prompt?: boolean | null;
            stdin_prompt?: string | null;
            cli_command?: string | null;
          })
          : undefined
    };
  };

  const saveInteractiveInterview = async (payload: {
    roleId: string;
    providerId: string;
    model: string | null;
    report: InteractiveInterviewReport;
  }): Promise<{ saved: boolean; report_path?: string } | null> => {
    if (!payload.model) {
      throw new Error('Model is required to save interview report');
    }
    const res = await apiFetch('/llm/interview/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        role: payload.roleId,
        provider_id: payload.providerId,
        model: payload.model,
        report: payload.report
      })
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(detail || '保存面试报告失败');
    }
    const data = (await res.json()) as { saved: boolean; report_path?: string };
    await loadLLMStatus();
    return data;
  };

  const runConnectivityTest = async (
    role: string,
    providerId: string,
    model: string
  ): Promise<Record<string, unknown> | null> => {
    if (!llmConfig) return null;
    const providerCfg = llmConfig.providers?.[providerId];
    const apiKey = providerCfg ? await resolveApiKey(providerId, providerCfg) : null;
    const envResult = providerCfg ? await resolveEnvOverrides(providerId, providerCfg) : null;
    const suites = ['connectivity'];

    const controller = new AbortController();
    testAbortRef.current = controller;

    try {
      const report = await runStreamingTest({
        role: 'connectivity',
        providerId,
        model,
        suites,
        testLevel: 'quick',
        evaluationMode: 'provider',
        apiKey,
        envOverrides: envResult?.env,
        signal: controller.signal
      });

      return report || null;
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        setLlmError(err.message);
      }
      return null;
    } finally {
      if (testAbortRef.current === controller) {
        testAbortRef.current = null;
      }
    }
  };

  const resolveProviderEnvOverrides = async (providerId: string) => {
    const cfg = llmConfigRef.current?.providers?.[providerId];
    if (!cfg) return null;
    const envResult = await resolveEnvOverrides(providerId, cfg);
    if (!envResult?.env || Object.keys(envResult.env).length === 0) {
      return null;
    }
    return envResult.env;
  };

  const runAllTests = async () => {
    if (!llmConfig) return;
    setRunAllBusy(true);
    const suites = getSelectedSuites();
    for (const role of Object.keys(llmConfig.roles || {})) {
      await runLlmTest(role, testLevel, suites, false);
    }
    setRunAllBusy(false);
  };

  const getSelectedSuites = () => {
    if (!llmConfig) {
      return ['connectivity', 'response'];
    }
    const suites = Object.entries(testSuites)
      .filter(([, enabled]) => enabled)
      .map(([name]) => name);
    if (suites.length === 0) {
      return llmConfig.policies?.test_required_suites || ['connectivity', 'response'];
    }
    return suites;
  };

  const loadProviderModels = async (providerId: string) => {
    if (!llmConfig) return;
    if (!providerId) return;
    const providerCfg = llmConfig.providers?.[providerId];
    if (!providerCfg) return;
    const apiKey = await resolveApiKey(providerId, providerCfg);
    const res = await apiFetch(`/llm/providers/${providerId}/models`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey }),
    });
    if (!res.ok) {
      return;
    }
    const payload = (await res.json()) as { supported?: boolean; models?: Array<string | { id?: string }> };
    const rawModels = Array.isArray(payload.models) ? payload.models : [];
    const models = rawModels
      .map((model) => (typeof model === 'string' ? model : model?.id))
      .filter((modelId): modelId is string => typeof modelId === 'string' && modelId.length > 0);
    setProviderModels((prev) => ({ ...prev, [providerId]: { supported: !!payload.supported, models } }));
  };

  const openReport = async (runId: string) => {
    try {
      const res = await apiFetch(`/llm/test/${runId}`);
      if (!res.ok) return;
      const data = await res.json();
      setReportDrawer({ open: true, data });
    } catch {
      // ignore
    }
  };

  const openTuiBrowser = (role: string) => {
    if (!llmConfig) return;
    const roleCfg = llmConfig.roles?.[role];
    const providerId = roleCfg?.provider_id || '';
    if (!providerId) return;
    setTuiModelDraft(roleCfg?.model || '');
    setTuiError(null);
    setTuiDrawer({ open: true, role, providerId });
  };

  const handleTuiSave = async (runTest: boolean) => {
    const trimmed = tuiModelDraft.trim();
    if (!trimmed) {
      setTuiError('Model id is required.');
      return;
    }
    setTuiError(null);
    if (tuiDrawer.role) {
      updateRole(tuiDrawer.role, { model: trimmed });
    }
    if (runTest && tuiDrawer.role && tuiDrawer.providerId) {
      await runLlmTest(tuiDrawer.role, 'quick', undefined, true, {
        providerId: tuiDrawer.providerId,
        model: trimmed,
      });
    }
  };

  const handleTuiModelChange = (value: string) => {
    setTuiModelDraft(value);
    if (tuiError && value.trim()) {
      setTuiError(null);
    }
  };

  const renderSuiteStatus = (label: string, ok?: boolean) => {
    let icon = <AlertTriangle className="size-3 text-amber-300" />;
    if (ok === true) {
      icon = <CheckCircle2 className="size-3 text-emerald-300" />;
    } else if (ok === undefined) {
      icon = <div className="size-2 rounded-full bg-gray-500/60" />;
    }
    return (
      <div className="flex items-center gap-1 text-[10px] text-text-dim">
        {icon}
        <span>{label}</span>
      </div>
    );
  };

  if (!isOpen) return null;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const llmSaved = await saveLLMConfig();
      if (!llmSaved) {
        throw new Error('LLM 配置保存失败');
      }
      await onSave({
        prompt_profile: promptProfile,
        refresh_interval: refreshInterval,
        auto_refresh: autoRefresh,
        interval: pmInterval,
        timeout: pmTimeout,
        pm_show_output: pmShowOutput,
        pm_runs_director: pmRunsDirector,
        pm_director_show_output: pmDirectorShowOutput,
        pm_director_timeout: pmDirectorTimeout,
        pm_director_iterations: pmDirectorIterations,
        pm_director_match_mode: pmDirectorMatchMode,
        pm_max_failures: pmMaxFailures,
        pm_max_blocked: pmMaxBlocked,
        pm_max_same: pmMaxSame,
        pm_blocked_strategy: pmBlockedStrategy,
        pm_blocked_degrade_max_retries: pmBlockedDegradeMaxRetries,
        director_iterations: directorIterations,
        director_execution_mode: directorExecutionMode,
        director_max_parallel_tasks:
          directorExecutionMode === 'serial'
            ? 1
            : Math.max(1, directorMaxParallelTasks),
        director_ready_timeout_seconds: directorReadyTimeoutSeconds,
        director_claim_timeout_seconds: directorClaimTimeoutSeconds,
        director_phase_timeout_seconds: directorPhaseTimeoutSeconds,
        director_complete_timeout_seconds: directorCompleteTimeoutSeconds,
        director_task_timeout_seconds: directorTaskTimeoutSeconds,
        director_forever: directorForever,
        director_show_output: directorShowOutput,
        slm_enabled: slmEnabled,
        qa_enabled: qaEnabled,
        debug_tracing: debugTracing,
        ramdisk_root: ramdiskRoot || '',
        json_log_path: normalizeJsonLogPath(jsonLogPath),
        show_memory: showMemory,
        io_fsync_mode: ioFsyncMode,
        memory_refs_mode: memoryRefsMode,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 animate-in fade-in duration-200">
      <div className="relative">
        <div
          data-settings-modal
          className="bg-bg-panel/95 border border-white/10 rounded-xl w-full flex flex-col shadow-2xl shadow-purple-900/20 backdrop-filter backdrop-blur-xl max-w-none max-h-none relative overflow-hidden"
          style={{
            width: settingsModalSize.width,
            height: settingsModalSize.height,
            userSelect: settingsModalResizing ? 'none' : undefined,
          }}
        >
          {/* 头部 */}
          <div className="flex items-center justify-between p-4 border-b border-white/10">
            <h2 className="text-lg font-heading font-bold text-text-main flex items-center gap-2">
              <span className="w-1 h-5 bg-accent rounded-full shadow-[0_0_8px_rgba(124,58,237,0.5)]"></span>
              系统配置
            </h2>
            <button
              onClick={onClose}
              className="text-text-dim hover:text-text-main hover:bg-white/5 rounded-full p-1 transition-colors"
            >
              <X className="size-5" />
            </button>
          </div>

          {/* 内容 */}
          <div className="flex-1 min-h-0 p-4 flex flex-col">
            {error ? (
              <div className="text-xs text-status-error bg-status-error/10 border border-status-error/20 rounded p-2 mb-4">
                {error}
              </div>
            ) : null}

            <Tabs
              value={activeTab}
              onValueChange={(value) => {
                if (value === 'general' || value === 'llm' || value === 'arsenal' || value === 'services') {
                  setActiveTab(value);
                }
              }}
              className="gap-4 flex flex-col flex-1 min-h-0"
            >
              <TabsList className="bg-white/5 border border-white/5 p-1 rounded-lg flex-wrap shrink-0">
                <TabsTrigger value="general" data-testid="settings-tab-general" className="data-[state=active]:bg-accent/20 data-[state=active]:text-accent text-text-muted hover:text-text-main">通用设置</TabsTrigger>
                <TabsTrigger value="llm" data-testid="settings-tab-llm" className="data-[state=active]:bg-accent/20 data-[state=active]:text-accent text-text-muted hover:text-text-main">LLM 设置</TabsTrigger>
                <TabsTrigger value="arsenal" className="text-cyan-400 data-[state=active]:text-cyan-300">军械库</TabsTrigger>
                <TabsTrigger value="services" className="text-cyan-400 data-[state=active]:text-cyan-300">内务司</TabsTrigger>
              </TabsList>

              <TabsContent value="general" className="mt-6 space-y-6 flex-1 min-h-0 overflow-y-auto custom-scrollbar pr-1">
                {/* Prompt 模板 */}
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <h3 className="text-sm font-semibold text-text-main mb-3 flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-accent"></span>
                    诏令模板
                  </h3>
                  <div>
                    <label className="block text-xs text-text-muted mb-1.5 font-medium">文风模板</label>
                    <select
                      value={promptProfile}
                      onChange={(e) => setPromptProfile(e.target.value)}
                      className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                    >
                      <option value="zhenguan_governance">zhenguan_governance (贞观治理架构)</option>
                      <option value="demo_ming_armada">demo_ming_armada (兼容旧配置)</option>
                      <option value="generic">generic (通用)</option>
                    </select>
                    <p className="text-[10px] text-text-dim mt-1.5">
                      定义多角色协作提示词模板（PM、Director、QA 等）。
                    </p>
                  </div>
                </div>

                {/* 刷新设置 */}
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <h3 className="text-sm font-semibold text-text-main mb-3 flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-accent"></span>
                    刷新设置
                  </h3>
                  <div className="space-y-3">
                    <div className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        id="auto-refresh"
                        checked={autoRefresh}
                        onChange={(e) => setAutoRefresh(e.target.checked)}
                        className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent checked:border-accent focus:ring-accent/50 text-accent transition-colors"
                      />
                      <label htmlFor="auto-refresh" className="text-sm text-text-muted cursor-pointer select-none">
                        自动刷新
                      </label>
                    </div>

                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">刷新间隔（秒）</label>
                      <input
                        type="number"
                        min="1"
                        value={refreshInterval}
                        onChange={(e) => setRefreshInterval(Math.max(1, Number(e.target.value) || 1))}
                        disabled={!autoRefresh}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 disabled:opacity-50 transition-all"
                      />
                      <p className="text-[10px] text-text-dim mt-1.5">建议 1-10 秒。</p>
                    </div>
                  </div>
                </div>

                {/* PM 运行设置 */}
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <h3 className="text-sm font-semibold text-text-main mb-3 flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-accent"></span>
                    PM 运行设置
                  </h3>
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">循环间隔（秒）</label>
                        <input
                          type="number"
                          min="1"
                          value={pmInterval}
                          onChange={(e) => setPmInterval(Math.max(1, Number(e.target.value) || 1))}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">单次超时（秒）</label>
                        <input
                          type="number"
                          min="0"
                          value={pmTimeout}
                          onChange={(e) => setPmTimeout(Math.max(0, Number(e.target.value) || 0))}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        />
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-4">
                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          id="pm-show-output"
                          checked={pmShowOutput}
                          onChange={(e) => setPmShowOutput(e.target.checked)}
                          className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50"
                        />
                        <label htmlFor="pm-show-output" className="text-sm text-text-muted cursor-pointer select-none">
                          显示 PM 输出
                        </label>
                      </div>

                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          id="pm-runs-director"
                          checked={pmRunsDirector}
                          onChange={(e) => setPmRunsDirector(e.target.checked)}
                          className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50"
                        />
                        <label htmlFor="pm-runs-director" className="text-sm text-text-muted cursor-pointer select-none">
                          PM 触发 Director
                        </label>
                      </div>

                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          id="pm-director-output"
                          checked={pmDirectorShowOutput}
                          onChange={(e) => setPmDirectorShowOutput(e.target.checked)}
                          disabled={!pmRunsDirector}
                          className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50 disabled:opacity-50"
                        />
                        <label htmlFor="pm-director-output" className="text-sm text-text-muted cursor-pointer select-none">
                          显示 Director 输出
                        </label>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4 pt-2 border-t border-white/5">
                      <div>
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">Director 结果超时</label>
                        <input
                          type="number"
                          min="1"
                          value={pmDirectorTimeout}
                          onChange={(e) => setPmDirectorTimeout(Math.max(1, Number(e.target.value) || 1))}
                          disabled={!pmRunsDirector}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 disabled:opacity-50 transition-all"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">Director 尝试次数</label>
                        <input
                          type="number"
                          min="1"
                          value={pmDirectorIterations}
                          onChange={(e) => setPmDirectorIterations(Math.max(1, Number(e.target.value) || 1))}
                          disabled={!pmRunsDirector}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 disabled:opacity-50 transition-all"
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">Director 结果匹配模式</label>
                      <select
                        value={pmDirectorMatchMode}
                        onChange={(e) => setPmDirectorMatchMode(e.target.value)}
                        disabled={!pmRunsDirector}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 disabled:opacity-50 transition-all"
                      >
                        <option value="latest">最新回执（推荐）</option>
                        <option value="run_id">按 run_id</option>
                        <option value="any">任意可用</option>
                        <option value="strict">严格一致</option>
                      </select>
                    </div>
                  </div>
                </div>

                {/* PM 限制 */}
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <h3 className="text-sm font-semibold text-text-main mb-3 flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-accent"></span>
                    PM 限制
                  </h3>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">最大失败次数</label>
                      <input
                        type="number"
                        min="1"
                        value={pmMaxFailures}
                        onChange={(e) => setPmMaxFailures(Math.max(1, Number(e.target.value) || 1))}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">最大阻塞次数</label>
                      <input
                        type="number"
                        min="1"
                        value={pmMaxBlocked}
                        onChange={(e) => setPmMaxBlocked(Math.max(1, Number(e.target.value) || 1))}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                      />
                    </div>
                    <div className="col-span-2">
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">最大连续重复次数</label>
                      <input
                        type="number"
                        min="1"
                        value={pmMaxSame}
                        onChange={(e) => setPmMaxSame(Math.max(1, Number(e.target.value) || 1))}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                      />
                    </div>
                  </div>
                </div>

                {/* Director阻塞处理策略 */}
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <h3 className="text-sm font-semibold text-text-main mb-3 flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-accent"></span>
                    Director阻塞处理策略
                  </h3>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="col-span-2">
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">处理策略</label>
                      <select
                        value={pmBlockedStrategy}
                        onChange={(e) => setPmBlockedStrategy(e.target.value as 'skip' | 'manual' | 'degrade_retry' | 'auto')}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                      >
                        <option value="auto">自动决策 (推荐)</option>
                        <option value="skip">跳过任务并继续</option>
                        <option value="manual">停止等待人工处理</option>
                        <option value="degrade_retry">降级设置后重试</option>
                      </select>
                      <p className="text-xs text-text-muted/70 mt-1.5">
                        当Director任务被阻塞时的处理方式
                      </p>
                    </div>
                    {(pmBlockedStrategy === 'degrade_retry' || pmBlockedStrategy === 'auto') && (
                      <div className="col-span-2">
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">降级重试次数</label>
                        <input
                          type="number"
                          min="0"
                          max="5"
                          value={pmBlockedDegradeMaxRetries}
                          onChange={(e) => setPmBlockedDegradeMaxRetries(Math.max(0, Math.min(5, Number(e.target.value) || 0)))}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        />
                        <p className="text-xs text-text-muted/70 mt-1.5">
                          使用降级设置重试的最大次数 (0-5)
                        </p>
                      </div>
                    )}
                  </div>
                </div>

                {/* Director 设置 */}
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <h3 className="text-sm font-semibold text-text-main mb-3 flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-accent"></span>
                    Director 设置
                  </h3>
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="director-qa"
                        checked={qaEnabled}
                        onChange={(e) => setQaEnabled(e.target.checked)}
                        className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50"
                      />
                      <label htmlFor="director-qa" className="text-sm text-text-muted cursor-pointer select-none">
                        启用 QA 审核
                      </label>
                    </div>

                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="director-forever"
                        checked={directorForever}
                        onChange={(e) => setDirectorForever(e.target.checked)}
                        className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50"
                      />
                      <label htmlFor="director-forever" className="text-sm text-text-muted cursor-pointer select-none">
                        持续运行（忽略迭代次数）
                      </label>
                    </div>

                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">迭代次数</label>
                      <input
                        type="number"
                        min="1"
                        value={directorIterations}
                        onChange={(e) => setDirectorIterations(Math.max(1, Number(e.target.value) || 1))}
                        disabled={directorForever}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 disabled:opacity-50 transition-all"
                      />
                      <p className="text-[10px] text-text-dim mt-1.5">关闭“持续运行”后生效。</p>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="col-span-2">
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">任务调度模式</label>
                        <select
                          value={directorExecutionMode}
                          onChange={(e) => setDirectorExecutionMode(e.target.value === 'serial' ? 'serial' : 'parallel')}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        >
                          <option value="parallel">并行</option>
                          <option value="serial">串行</option>
                        </select>
                      </div>
                      <div className="col-span-2">
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">最大并发运行数</label>
                        <input
                          type="number"
                          min="1"
                          value={directorMaxParallelTasks}
                          onChange={(e) => setDirectorMaxParallelTasks(Math.max(1, Number(e.target.value) || 1))}
                          disabled={directorExecutionMode === 'serial'}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 disabled:opacity-50 transition-all"
                        />
                        {directorExecutionMode === 'serial' ? (
                          <p className="text-[10px] text-text-dim mt-1.5">串行模式下固定为 1。</p>
                        ) : null}
                      </div>
                      <div>
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">就绪扫描超时(秒)</label>
                        <input
                          type="number"
                          min="1"
                          value={directorReadyTimeoutSeconds}
                          onChange={(e) => setDirectorReadyTimeoutSeconds(Math.max(1, Number(e.target.value) || 1))}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">认领任务超时(秒)</label>
                        <input
                          type="number"
                          min="1"
                          value={directorClaimTimeoutSeconds}
                          onChange={(e) => setDirectorClaimTimeoutSeconds(Math.max(1, Number(e.target.value) || 1))}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">阶段执行超时(秒)</label>
                        <input
                          type="number"
                          min="1"
                          value={directorPhaseTimeoutSeconds}
                          onChange={(e) => setDirectorPhaseTimeoutSeconds(Math.max(1, Number(e.target.value) || 1))}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">任务收尾超时(秒)</label>
                        <input
                          type="number"
                          min="1"
                          value={directorCompleteTimeoutSeconds}
                          onChange={(e) => setDirectorCompleteTimeoutSeconds(Math.max(1, Number(e.target.value) || 1))}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        />
                      </div>
                      <div className="col-span-2">
                        <label className="block text-xs text-text-muted mb-1.5 font-medium">单任务工作流超时(秒)</label>
                        <input
                          type="number"
                          min="1"
                          value={directorTaskTimeoutSeconds}
                          onChange={(e) => setDirectorTaskTimeoutSeconds(Math.max(1, Number(e.target.value) || 1))}
                          className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                        />
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="director-output"
                        checked={directorShowOutput}
                        onChange={(e) => setDirectorShowOutput(e.target.checked)}
                        className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50"
                      />
                      <label htmlFor="director-output" className="text-sm text-text-muted cursor-pointer select-none">
                        显示 Director 输出
                      </label>
                    </div>

                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="director-slm-enabled"
                        checked={slmEnabled}
                        onChange={(e) => setSlmEnabled(e.target.checked)}
                        className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50"
                      />
                      <label htmlFor="director-slm-enabled" className="text-sm text-text-muted cursor-pointer select-none">
                        启用 SLM 前置分流（可选）
                      </label>
                    </div>
                    <p className="text-[10px] text-text-dim">
                      关闭时保持现有流程；开启后优先走本地小模型路由，异常会回退到原流程。
                    </p>
                  </div>
                </div>

                {/* 不变量策略 */}
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <h3 className="text-sm font-semibold text-text-main mb-3 flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-accent"></span>
                    不变量策略
                  </h3>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">原子写入 (fsync)</label>
                      <select
                        value={ioFsyncMode}
                        onChange={(e) => setIoFsyncMode(e.target.value as 'strict' | 'relaxed')}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                      >
                        <option value="strict">严格：fsync + 原子替换</option>
                        <option value="relaxed">宽松：跳过 fsync（仍原子替换）</option>
                      </select>
                      <p className="text-[10px] text-text-dim mt-1.5">
                        严格模式最安全，宽松模式更快但降低断电一致性保障。
                      </p>
                    </div>

                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">记忆证据引用</label>
                      <select
                        value={memoryRefsMode}
                        onChange={(e) => setMemoryRefsMode(e.target.value as 'strict' | 'soft' | 'off')}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all"
                      >
                        <option value="strict">严格：缺 refs 直接丢弃</option>
                        <option value="soft">软性：保留但标记未验证</option>
                        <option value="off">关闭：不检查</option>
                      </select>
                      <p className="text-[10px] text-text-dim mt-1.5">
                        refs 包含 run_id / event / artifact / code_ref 等可回放证据。
                      </p>
                    </div>
                  </div>
                </div>

                {/* 存储与日志 */}
                <div className="bg-white/5 rounded-xl p-4 border border-white/5">
                  <h3 className="text-sm font-semibold text-text-main mb-3 flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-accent"></span>
                    存储与日志
                  </h3>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">RAMDisk 根目录（可选）</label>
                      <input
                        type="text"
                        value={ramdiskRoot}
                        onChange={(e) => setRamdiskRoot(e.target.value)}
                        placeholder="X:\\"
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all font-mono"
                      />
                      <p className="text-[10px] text-text-dim mt-1.5">留空禁用；示例：X:\</p>
                    </div>

                    <div>
                      <label className="block text-xs text-text-muted mb-1.5 font-medium">JSON 日志路径</label>
                      <input
                        type="text"
                        value={jsonLogPath}
                        onChange={(e) => setJsonLogPath(e.target.value)}
                        className="w-full bg-[rgba(35,25,14,0.55)] text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 transition-all font-mono"
                      />
                      <p className="text-[10px] text-text-dim mt-1.5">
                        相对 Workspace 的路径。默认：{DEFAULT_JSON_LOG_PATH}
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="show-memory"
                        checked={showMemory}
                        onChange={(e) => setShowMemory(e.target.checked)}
                        className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50"
                      />
                      <label htmlFor="show-memory" className="text-sm text-text-muted cursor-pointer select-none">
                        显示记忆面板
                      </label>
                    </div>

                    <div className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        id="debug-tracing"
                        checked={debugTracing}
                        onChange={(e) => setDebugTracing(e.target.checked)}
                        className="w-4 h-4 rounded bg-[rgba(35,25,14,0.55)] border-white/10 checked:bg-accent text-accent focus:ring-accent/50"
                      />
                      <label htmlFor="debug-tracing" className="text-sm text-text-muted cursor-pointer select-none">
                        启用后端请求/响应调试日志
                      </label>
                    </div>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="llm" className="mt-6 flex-1 min-h-0 overflow-y-auto custom-scrollbar pr-1">
                <Suspense fallback={<DeferredSectionFallback label="LLM 设置" />}>
                  <LLMSettingsTab
                    llmConfig={llmConfig}
                    llmStatus={llmStatus}
                    llmLoading={llmLoading}
                    llmSaving={llmSaving}
                    llmError={llmError}
                    deletingProviders={deletingProviders}
                    onSaveConfig={saveLLMConfig}
                    onRunInterview={runInterview}
                    onRunConnectivityTest={runConnectivityTest}
                    onAskInteractiveInterview={askInteractiveInterview}
                    onSaveInteractiveInterview={saveInteractiveInterview}
                    resolveProviderEnvOverrides={resolveProviderEnvOverrides}
                    onUpdateConfig={updateLLMConfigDraft}
                    onTestProvider={runProviderTestStreaming}
                    onCancelTestProvider={cancelProviderTest}
                    onCancelInterview={cancelInterview}
                    onAddProvider={async (providerId, provider) => {
                      const payload: ProviderConfig = {
                        type: (provider.type || 'cli') as ProviderKind,
                        name: provider.name,
                        command: provider.command,
                        args: provider.args,
                        env: provider.env,
                        base_url: provider.base_url,
                        api_key_ref: provider.api_key_ref,
                        model: provider.model,
                        default_model: provider.default_model,
                      };
                      await addProviderAndPersist(providerId, payload);
                    }}
                    onUpdateProvider={(providerId, updates) => {
                      updateProvider(providerId, updates as Partial<ProviderConfig>);
                    }}
                    onDeleteProvider={async (providerId) => {
                      await deleteProviderAndPersist(providerId);
                    }}
                  />
                </Suspense>
                {reportDrawer.open ? (
                  <div className="mt-4 rounded-lg border border-white/10 bg-[rgba(35,25,14,0.7)] p-3">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-text-main">LLM 测试回执</span>
                      <button
                        type="button"
                        onClick={() => setReportDrawer({ open: false, data: null })}
                        className="text-[10px] text-text-dim hover:text-text-main"
                      >
                        关闭
                      </button>
                    </div>
                    <pre className="text-[11px] text-text-muted whitespace-pre-wrap font-mono max-h-64 overflow-auto">
                      {safeJsonStringify(reportDrawer.data, 2)}
                    </pre>
                  </div>
                ) : null}
              </TabsContent>

              <TabsContent value="arsenal" className="mt-6 flex-1 min-h-0 overflow-y-auto custom-scrollbar pr-1">
                <Suspense fallback={<DeferredSectionFallback label="军械库" />}>
                  <ArsenalPanel />
                </Suspense>
              </TabsContent>

              <TabsContent value="services" className="mt-6 flex-1 min-h-0 overflow-y-auto custom-scrollbar pr-1">
                <Suspense fallback={<DeferredSectionFallback label="内务司" />}>
                  <SystemServicesTab />
                </Suspense>
              </TabsContent>

            </Tabs>
            {shouldMountTuiDrawer ? (
              <Suspense fallback={<DeferredSectionFallback label="终端抽屉" overlay={true} />}>
                <PtyDrawer
                  open={tuiDrawer.open}
                  onOpenChange={(open) => {
                    setTuiDrawer((prev) => ({ ...prev, open }));
                    if (!open) setTuiError(null);
                  }}
                  roleLabel={ROLE_META[tuiDrawer.role]?.label || tuiDrawer.role || '角色'}
                  providerId={tuiDrawer.providerId || ''}
                  providerConfig={
                    tuiDrawer.providerId && llmConfig?.providers?.[tuiDrawer.providerId]
                      ? { id: tuiDrawer.providerId, ...llmConfig.providers[tuiDrawer.providerId] }
                      : null
                  }
                  modelValue={tuiModelDraft}
                  onModelChange={handleTuiModelChange}
                  onSaveModel={() => handleTuiSave(false)}
                  onSaveAndTest={() => handleTuiSave(true)}
                  error={tuiError}
                />
              </Suspense>
            ) : null}

          </div>

          {/* 底部按钮 */}
          <div className="flex items-center justify-end gap-3 p-4 border-t border-white/10 bg-[rgba(35,25,14,0.55)] backdrop-blur-md">
            <button
              onClick={onClose}
              className="px-4 py-2 text-xs text-text-dim hover:text-text-main hover:bg-white/5 rounded transition-colors"
              disabled={saving}
            >
              取消
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-5 py-2 text-xs font-semibold bg-accent hover:bg-accent-hover text-white rounded shadow-lg shadow-accent/20 transition-all flex items-center gap-2 disabled:opacity-60 disabled:shadow-none"
            >
              <Save className="size-4" />
              {saving ? '保存中...' : '保存配置'}
            </button>
          </div>

          <div
            aria-label="Resize settings panel"
            onPointerDown={handleResizePointerDown}
            className="absolute bottom-0 right-0 z-10 size-5 cursor-se-resize touch-none"
          >
            <div className="absolute bottom-1 right-1 size-3 border-b-2 border-r-2 border-white/30" />
          </div>
        </div>
        <div
          id="llm-test-panel-slot"
          className="absolute left-0 top-full mt-4 w-full max-w-[90vw] lg:top-6 lg:left-full lg:ml-4 lg:mt-0 lg:w-[360px] xl:w-[420px]"
        />
      </div>
    </div>
  );
}


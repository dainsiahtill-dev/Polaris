/**
 * LLMSettingsTab
 * LLM 设置主组件，使用 Context + Reducer 模式
 */

import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Loader2, CheckCircle2, AlertTriangle } from 'lucide-react';

import {
  ProviderContextProvider,
  useProviderContext,
  useSelectedRole,
  useConnectivityStore,
  type RoleId,
} from './state';
import { devLogger } from '@/app/utils/devLogger';
import type { ProviderState } from './state';
import { ProviderListManager } from './providers';

import type {
  ProviderConfig,
  ProviderKind,
  ProviderConnection,
  SimpleProvider,
  LLMStatus,
} from './types';
import { PROVIDER_KINDS, isCLIProviderType } from './types';
import type { TestEvent, TestResult } from './test/types';
import { TestPanel } from './test/TestPanel';
import { useTestEvents } from './test/hooks/useTestEvents';
import { useProviderRegistry } from './ProviderRegistry';
import { LLMVisualEditor } from './visual/LLMVisualEditor';
import { resolveModelName, validateModelName, getModelResolutionLog, type ModelResolutionContext } from './utils';
import type { VisualGraphConfig, VisualGraphStatus } from './visual/types/visual';

import { 
  InterviewHall, 
  type ConnectivityResult as InterviewConnectivityResult,
} from './interview/InterviewHall';
import { InterviewSession } from './interview/InterviewSession';
import { 
  InteractiveInterviewHall, 
  type InteractiveInterviewAnswer,
  type InteractiveInterviewReport,
} from './interview/InteractiveInterviewHall';

// ============================================================================
// Types
// ============================================================================

interface LlmConfig {
  schema_version: number;
  providers: Record<string, ProviderConfig>;
  roles: Record<string, {
    provider_id?: string;
    model?: string;
    profile?: string;
  }>;
  policies?: {
    required_ready_roles?: string[];
    test_required_suites?: string[];
    role_requirements?: Record<string, {
      requires_thinking?: boolean;
      min_confidence?: number;
      error_message?: string;
    }>;
  };
  visual_layout?: Record<string, unknown>;
  visual_node_states?: Record<string, unknown>;
  visual_viewport?: Record<string, unknown>;
}

interface LLMSettingsTabProps {
  llmConfig: LlmConfig | null;
  llmStatus: LLMStatus | null;
  llmLoading: boolean;
  llmSaving: boolean;
  llmError: string | null;
  deletingProviders?: Record<string, boolean>;
  onSaveConfig: (config?: LlmConfig) => void | Promise<boolean>;
  onRunInterview: (
    role: RoleId,
    providerId: string,
    model: string,
    onEvent?: (event: TestEvent) => void
  ) => Promise<Record<string, unknown> | null>;
  onRunConnectivityTest: (
    role: RoleId,
    providerId: string,
    model: string
  ) => Promise<Record<string, unknown> | null>;
  onAskInteractiveInterview: (payload: {
    roleId: RoleId;
    providerId: string;
    model: string;
    question: string;
    expectedCriteria?: string[];
    expectsThinking?: boolean;
    sessionId?: string | null;
    context?: Array<{ question: string; answer: string }>;
  }) => Promise<InteractiveInterviewAnswer | null>;
  onSaveInteractiveInterview: (payload: {
    roleId: RoleId;
    providerId: string;
    model: string | null;
    report: InteractiveInterviewReport;
  }) => Promise<{ saved: boolean; report_path?: string } | null>;
  resolveProviderEnvOverrides?: (providerId: string) => Promise<Record<string, string> | null>;
  onAddProvider?: (providerId: string, provider: ProviderConfig) => void;
  onUpdateProvider?: (providerId: string, updates: Partial<ProviderConfig>) => void;
  onDeleteProvider?: (providerId: string) => void | Promise<void>;
  onUpdateConfig?: (config: LlmConfig) => void;
  onTestProvider?: (provider: SimpleProvider, onEvent?: (event: TestEvent) => void) => Promise<TestResult | null>;
  onCancelTestProvider?: () => void;
  onCancelInterview?: () => void;
}

// ============================================================================
// Helper Functions
// ============================================================================

function buildSimpleProvider(
  providerId: string,
  provider: ProviderConfig,
  roles?: Record<string, { provider_id?: string; model?: string }>
): SimpleProvider {
  const kind = (provider.type || PROVIDER_KINDS.OPENAI_COMPAT) as ProviderKind;
  const isCli = isCLIProviderType(provider.type) || Boolean(provider.command);
  
  const conn: ProviderConnection = isCli
    ? {
        kind: provider.type === PROVIDER_KINDS.GEMINI_CLI ? 'gemini_cli' : 'codex_cli',
        command: provider.command || (provider.type === PROVIDER_KINDS.GEMINI_CLI ? 'gemini' : 'codex'),
        args: provider.args || [],
        env: provider.env || {},
      }
    : {
        kind: 'http',
        baseUrl: provider.base_url || '',
        apiKey: provider.api_key,
      };

  // 解析模型
  let modelId = '';
  if (typeof provider.model === 'string' && provider.model.trim()) {
    modelId = provider.model.trim();
  } else if (typeof provider.default_model === 'string' && provider.default_model.trim()) {
    modelId = provider.default_model.trim();
  }
  
  if (!modelId && roles) {
    for (const roleCfg of Object.values(roles)) {
      if (roleCfg?.provider_id === providerId && roleCfg.model) {
        modelId = roleCfg.model;
        break;
      }
    }
  }

  return {
    id: providerId,
    name: provider.name || providerId,
    kind,
    conn,
    cliMode: provider.cli_mode,
    modelId,
    status: 'untested',
  };
}

function mergeVisualConfigIntoLlmConfig(current: LlmConfig, nextVisual: VisualGraphConfig): LlmConfig {
  const nextProviders = (nextVisual.providers || {}) as Record<string, ProviderConfig>;
  const nextRoles = (nextVisual.roles || {}) as LlmConfig['roles'];
  return {
    ...current,
    providers: nextProviders,
    roles: nextRoles,
    policies: nextVisual.policies || current.policies,
    visual_layout: nextVisual.visual_layout || {},
    visual_node_states: nextVisual.visual_node_states || current.visual_node_states,
    visual_viewport: nextVisual.visual_viewport as Record<string, unknown> | undefined,
  };
}

function resolveModelForSelection(
  roleId: RoleId,
  providerId: string,
  config: {
    roles?: Record<string, { provider_id?: string; model?: string } | null>;
    providers?: Record<string, ProviderConfig | null>;
  } | null,
  providers?: Array<{ id: string; type?: string; model?: string }>
): string {
  const context: ModelResolutionContext = {
    roleId,
    providerId,
    llmConfig: config,
    providers: providers as ModelResolutionContext['providers']
  };

  const result = resolveModelName(context);

  devLogger.debug('[ModelResolver] ' + getModelResolutionLog(context));

  if (result.warning) {
    devLogger.warn('[ModelResolver] 警告:', result.warning);
  }

  const validation = validateModelName(result.model);
  if (!validation.isValid) {
    devLogger.error('[ModelResolver] 模型验证失败:', validation.error);
  }

  return result.model;
}

// ============================================================================
// Navigation Component
// ============================================================================

function TabNavigation({ 
  globalReadiness,
  blockedRoles,
  unsupportedRoles,
}: { 
  globalReadiness: { state: string; color: string },
  blockedRoles: string[],
  unsupportedRoles: string[],
}) {
  const { state, switchTab } = useProviderContext();
  const { activeTab } = state;
  const hasBlock = globalReadiness.state === 'BLOCKED';
  const tips: string[] = [];
  if (blockedRoles.length) tips.push(`未通过测试: ${blockedRoles.join(', ')}`);
  if (unsupportedRoles.length) tips.push(`运行时不支持: ${unsupportedRoles.join(', ')}`);
  const tipText = tips.length ? tips.join(' | ') : '请完成必需的 LLM 测试';

  return (
    <div className="rounded-2xl border border-cyan-500/20 bg-[radial-gradient(circle_at_top,_rgba(14,116,144,0.22),_transparent_60%)] p-4 shadow-[0_0_30px_rgba(34,211,238,0.2)]">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <button
            onClick={() => switchTab('config')}
            className={`px-4 py-2 text-[11px] font-semibold uppercase tracking-wider rounded-lg border transition-all ${
              activeTab === 'config'
                ? 'bg-cyan-500/20 text-cyan-200 border-cyan-400/40 shadow-[0_0_16px_rgba(34,211,238,0.25)]'
                : 'text-text-dim border-white/10 hover:border-cyan-400/40 hover:text-cyan-100'
            }`}
          >
            CONFIG
          </button>
          <button
            type="button"
            onClick={() => switchTab('deepTest')}
            className={`px-4 py-2 text-[11px] font-semibold uppercase tracking-wider rounded-lg border transition-all ${
              activeTab === 'deepTest'
                ? 'bg-emerald-500/20 text-emerald-200 border-emerald-400/40 shadow-[0_0_16px_rgba(16,185,129,0.25)]'
                : 'text-text-dim border-white/10 hover:border-emerald-400/40 hover:text-emerald-100'
            }`}
          >
            DEEP TEST
          </button>
        </div>

        <div className="flex items-center gap-2">
          {globalReadiness.state === 'READY' ? (
            <CheckCircle2 className="size-4 text-emerald-400" />
          ) : (
            <AlertTriangle className="size-4 text-yellow-400" />
          )}
          <span className="text-[10px] uppercase tracking-wider px-2 py-1 rounded border border-white/10 bg-black/40">
            {globalReadiness.state}
          </span>
          {hasBlock ? (
            <span className="text-[10px] text-amber-300 border border-amber-400/30 bg-amber-500/10 rounded px-2 py-1" title={tipText}>
              {tipText}
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Deep Test Panel
// ============================================================================

function DeepTestPanel({
  llmConfig,
  llmStatus,
  onRunConnectivityTest,
  onAskInteractiveInterview,
  onSaveInteractiveInterview,
  resolveProviderEnvOverrides,
  addTestEvent,
  resetTestEvents,
}: {
  llmConfig: LlmConfig | null;
  llmStatus: LLMStatus | null;
  onRunConnectivityTest: (role: RoleId, providerId: string, model: string) => Promise<Record<string, unknown> | null>;
  onAskInteractiveInterview: LLMSettingsTabProps['onAskInteractiveInterview'];
  onSaveInteractiveInterview: LLMSettingsTabProps['onSaveInteractiveInterview'];
  resolveProviderEnvOverrides?: (providerId: string) => Promise<Record<string, string> | null>;
  addTestEvent: (event: TestEvent) => void;
  resetTestEvents: () => void;
}) {
  const { state, setInterviewMode, setDeepView, selectRole, selectProvider, openTestPanel, startTest, completeTest } = useProviderContext();
  const { interviewMode, deepView, interviewPanel, interviewRunning, connectivityRunning } = state;
  const selectedRole = useSelectedRole();
  const { buildProviderSummaries, buildConnectivityMap } = useConnectivityStore();

  const providers = useMemo(() => {
    if (!llmConfig?.providers) return [];
    return buildProviderSummaries(llmConfig.providers);
  }, [llmConfig?.providers, buildProviderSummaries]);

  const connectivityResults = useMemo(() => {
    return buildConnectivityMap();
  }, [buildConnectivityMap]);

  const selectedProviderId = state.selectedProviderId;
  
  // 简化的角色配置
  const roles = useMemo(() => {
    const roleIds: RoleId[] = ['pm', 'director', 'chief_engineer', 'qa', 'architect', 'cfo', 'hr'];
    const roleMeta: Record<RoleId, { label: string; description: string }> = {
      pm: { label: 'PM', description: '统筹任务、节奏与推进。' },
      director: { label: 'Director', description: '负责实现、调度与技术裁断（实际编码）。' },
      chief_engineer: { label: 'Chief Engineer', description: '绘制技术蓝图，定体例与纲目（设计不编码）。' },
      qa: { label: 'QA', description: '主司审核与勘验，确保证据链完备。' },
      architect: { label: 'Architect', description: '草拟项目规格与架构文档，定体例与纲目。' },
      cfo: { label: 'CFO', description: '核算预算，监控Token用量与成本。' },
      hr: { label: 'HR', description: '管理LLM配置与模型任免。' },
    };
    
    return roleIds.map((roleId) => {
      const roleCfg = llmConfig?.roles?.[roleId] || (roleId === 'architect' ? llmConfig?.roles?.docs : undefined);
      const status = llmStatus?.roles?.[roleId] || (roleId === 'architect' ? llmStatus?.roles?.docs : undefined);
      
      return {
        id: roleId,
        label: roleMeta[roleId].label,
        description: roleMeta[roleId].description,
        requiresThinking: roleId === 'pm' || roleId === 'director',
        minConfidence: 0.5,
        candidate: {
          providerId: roleCfg?.provider_id || '',
          providerName: roleCfg?.provider_id 
            ? (llmConfig?.providers?.[roleCfg.provider_id]?.name || roleCfg.provider_id)
            : '未指派',
          model: roleCfg?.model || '',
        },
        readiness: {
          ready: status?.ready,
          grade: status?.grade,
        },
      };
    });
  }, [llmConfig, llmStatus]);

  const selectedMeta = roles.find((r) => r.id === selectedRole);

  const handleRunConnectivity = useCallback(async (payload: { role: RoleId; providerId: string; model: string }) => {
    await onRunConnectivityTest(payload.role, payload.providerId, payload.model);
  }, [onRunConnectivityTest]);

  const handleStartInterview = useCallback(async (payload: { role: RoleId; providerId: string; model: string }) => {
    // 使用 TestPanel 流式测试替代直接调用 onRunInterview
    // 配置测试运行参数：connectivity + thinking + interview suites
    const runConfig = {
      suites: ['connectivity', 'thinking', 'interview'],
      role: payload.role,
      model: payload.model,
    };
    
    devLogger.debug('[DeepTestPanel] Starting interview with runConfig:', runConfig);
    
    // 打开 TestPanel 并开始测试
    resetTestEvents();
    openTestPanel(payload.providerId, runConfig);
    startTest(payload.providerId, runConfig);
  }, [openTestPanel, resetTestEvents, startTest]);

  const handleInteractivePanelStateSync = useCallback((payload: {
    providerId: string;
    roleId: RoleId;
    model: string | null;
    status: 'idle' | 'running' | 'success' | 'failed';
  }) => {
    const runConfig = {
      suites: ['interactive_stream_view'],
      role: payload.roleId,
      ...(payload.model ? { model: payload.model } : {})
    };

    if (payload.status === 'idle') {
      openTestPanel(payload.providerId, runConfig);
      return;
    }

    if (payload.status === 'running') {
      openTestPanel(payload.providerId, runConfig);
      startTest(payload.providerId, runConfig);
      return;
    }

    completeTest(payload.providerId, payload.status === 'success');
  }, [completeTest, openTestPanel, startTest]);

  return (
    <div className="flex flex-col gap-4 w-full flex-1 min-h-0">
      <div className={`rounded-2xl border border-emerald-500/20 bg-[radial-gradient(circle_at_top,_rgba(16,185,129,0.22),_transparent_60%)] shadow-[0_0_30px_rgba(16,185,129,0.18)] ${interviewMode === 'interactive' ? 'p-2.5' : 'p-4'}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-widest text-emerald-200">深测议堂</div>
            {interviewMode !== 'interactive' ? (
              <div className="text-[10px] text-text-dim mt-1">
                深度测试用于验证角色与模型适配度，输出详细能力报告。
              </div>
            ) : null}
          </div>
          <div className="flex items-center gap-1 rounded-lg border border-white/10 bg-black/40 p-1">
            <button
              onClick={() => setInterviewMode('interactive')}
              className={`px-2.5 py-1 text-[10px] font-semibold rounded transition-all ${
                interviewMode === 'interactive'
                  ? 'bg-emerald-500/20 text-emerald-200'
                  : 'text-text-dim hover:text-emerald-100'
              }`}
            >
              交互问答
            </button>
            <button
              onClick={() => {
                setInterviewMode('auto');
                setDeepView('hall');
              }}
              className={`px-2.5 py-1 text-[10px] font-semibold rounded transition-all ${
                interviewMode === 'auto'
                  ? 'bg-cyan-500/20 text-cyan-200'
                  : 'text-text-dim hover:text-cyan-100'
              }`}
            >
              自动巡检
            </button>
          </div>
        </div>
      </div>

      <div className="w-full flex-1 min-h-0">
        {interviewMode === 'interactive' ? (
          <InteractiveInterviewHall
            roles={roles}
            providers={providers}
            selectedRole={selectedRole}
            selectedProvider={selectedProviderId}
            selectedModel={resolveModelForSelection(selectedRole, selectedProviderId ?? '', llmConfig, providers) ?? null}
            onSelectRole={selectRole}
            onSelectProvider={selectProvider}
            onAskQuestion={onAskInteractiveInterview}
            onSaveReport={onSaveInteractiveInterview}
            resolveEnvOverrides={resolveProviderEnvOverrides}
            onTestEvent={addTestEvent}
            onResetTestEvents={resetTestEvents}
            onSyncTestPanelState={handleInteractivePanelStateSync}
            isDeepTestMode={true}
          />
        ) : deepView === 'hall' ? (
          <InterviewHall
            roles={roles}
            selectedRole={selectedRole}
            providers={providers}
            selectedProvider={selectedProviderId}
            onSelectRole={selectRole}
            onSelectProvider={selectProvider}
            onRunConnectivityTest={handleRunConnectivity}
            onRunInterview={handleStartInterview}
            connectivityResults={connectivityResults}
            interviewRunning={interviewRunning}
            connectivityRunning={connectivityRunning}
            onSkipConnectivityTest={() => {}}
          />
        ) : (
          <InterviewSession
            roleLabel={selectedMeta?.label || selectedRole}
            roleId={selectedRole}
            report={interviewPanel.report || null}
            running={interviewRunning}
            error={interviewPanel.error}
            onBack={() => setDeepView('hall')}
          />
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

function LLMSettingsTabInner({
  llmConfig,
  llmStatus,
  llmLoading,
  llmSaving,
  llmError,
  deletingProviders,
  onSaveConfig,
  onRunInterview: _onRunInterview,
  onRunConnectivityTest,
  onAskInteractiveInterview,
  onSaveInteractiveInterview,
  resolveProviderEnvOverrides,
  onAddProvider,
  onUpdateProvider,
  onDeleteProvider,
  onUpdateConfig,
  onTestProvider,
  onCancelTestProvider,
  onCancelInterview: _onCancelInterview,
}: LLMSettingsTabProps) {
  const { state, switchTab, startTest, completeTest, closeTestPanel, setConfigView } = useProviderContext();
  const { activeTab, configView, testPanel } = state;
  
  const { events, addEvent, resetEvents } = useTestEvents();
  const panelHostRef = useRef<HTMLElement | null>(null);
  
  // Use ref to avoid closure staleness in TestPanel callbacks
  const completeTestRef = useRef(completeTest);
  const selectedProviderIdRef = useRef(testPanel.selectedProviderId);
  useEffect(() => {
    completeTestRef.current = completeTest;
    selectedProviderIdRef.current = testPanel.selectedProviderId;
  }, [completeTest, testPanel.selectedProviderId]);

  // 初始化 portal host
  useEffect(() => {
    if (typeof document !== 'undefined') {
      panelHostRef.current = document.getElementById('llm-test-panel-slot');
    }
  }, []);

  // Provider Registry
  const {
    loading: providersLoading,
    error: providersError,
    providers,
    getProviderInfo,
    getProviderDefaultConfig,
    getProviderComponent,
    getCostClass,
  } = useProviderRegistry();

  // Global readiness
  const globalReadiness = useMemo(() => {
    const s = llmStatus?.state || 'UNKNOWN';
    if (s === 'READY') return { state: 'READY', color: 'text-emerald-400' };
    if (s === 'BLOCKED') return { state: 'BLOCKED', color: 'text-amber-400' };
    return { state: 'UNKNOWN', color: 'text-gray-400' };
  }, [llmStatus]);
  const blockedRoles = useMemo(() => llmStatus?.blocked_roles || [], [llmStatus]);
  const unsupportedRoles = useMemo(() => llmStatus?.unsupported_roles || [], [llmStatus]);

  // Visual config
  const visualConfig = useMemo(() => {
    if (!llmConfig) return null;
    return {
      providers: llmConfig.providers || {},
      roles: llmConfig.roles || {},
      visual_layout: (llmConfig as unknown as Record<string, unknown>).visual_layout as Record<string, { x: number; y: number }> || {},
      visual_node_states:
        (llmConfig as unknown as Record<string, unknown>).visual_node_states as VisualGraphConfig['visual_node_states'] || {},
      visual_viewport:
        (llmConfig as unknown as Record<string, unknown>).visual_viewport as VisualGraphConfig['visual_viewport'] || undefined,
      policies: llmConfig.policies,
    } as VisualGraphConfig;
  }, [llmConfig]);

  const { getLatestProviderConnectivity } = useConnectivityStore();
  const visualStatus = useMemo(() => {
    const rolesStatus: Record<string, { ready?: boolean; grade?: string }> = {};
    Object.entries(llmStatus?.roles || {}).forEach(([roleId, role]) => {
      rolesStatus[roleId] = { ready: role.ready, grade: role.grade };
    });

    const providersStatus: Record<
      string,
      { status?: 'unknown' | 'running' | 'success' | 'failed' } | undefined
    > = {};
    Object.keys(llmConfig?.providers || {}).forEach((providerId) => {
      const cachedConnectivityStatus = state.providerTestStatus[providerId] || 'unknown';
      const latestConnectivity = getLatestProviderConnectivity(providerId);
      const persistedConnectivitySuite = llmStatus?.providers?.[providerId]?.suites?.connectivity as
        | { ok?: unknown }
        | undefined;
      const persistedConnectivityOk =
        typeof persistedConnectivitySuite?.ok === 'boolean'
          ? persistedConnectivitySuite.ok
          : undefined;
      const status =
        cachedConnectivityStatus !== 'unknown'
          ? cachedConnectivityStatus
          : latestConnectivity
            ? latestConnectivity.ok
              ? 'success'
              : 'failed'
            : persistedConnectivityOk === true
              ? 'success'
              : persistedConnectivityOk === false
                ? 'failed'
                : 'unknown';
      providersStatus[providerId] = { status };
    });

    if (Object.keys(rolesStatus).length === 0 && Object.keys(providersStatus).length === 0) {
      return null;
    }

    return { roles: rolesStatus, providers: providersStatus } as VisualGraphStatus;
  }, [getLatestProviderConnectivity, llmConfig?.providers, llmStatus, state.providerTestStatus]);

  // Test handlers
  const handleTestProvider = useCallback(async (providerId: string) => {
    devLogger.debug('[LLMSettingsTab] handleTestProvider called for:', providerId);
    if (!onTestProvider || !llmConfig) {
      devLogger.debug('[LLMSettingsTab] Missing onTestProvider or llmConfig, returning early');
      return;
    }
    
    const cfg = llmConfig.providers?.[providerId];
    if (!cfg) {
      devLogger.debug('[LLMSettingsTab] No config found for provider:', providerId);
      return;
    }

    const simpleProvider = buildSimpleProvider(providerId, cfg, llmConfig.roles);
    
    devLogger.debug('[LLMSettingsTab] Starting test for provider:', providerId);
    startTest(providerId);
    resetEvents();
    
    try {
      devLogger.debug('[LLMSettingsTab] Calling onTestProvider...');
      const result = await onTestProvider(simpleProvider, (event) => {
        addEvent(event);
      });
      devLogger.debug('[LLMSettingsTab] onTestProvider returned:', result);
      devLogger.debug('[LLMSettingsTab] Calling completeTest from handleTestProvider, success:', result?.ready ?? false);
      completeTest(providerId, result?.ready ?? false);
    } catch (err) {
      devLogger.debug('[LLMSettingsTab] onTestProvider threw error:', err);
      devLogger.debug('[LLMSettingsTab] Calling completeTest from handleTestProvider catch block, success: false');
      completeTest(providerId, false);
    }
  }, [llmConfig, onTestProvider, startTest, completeTest, addEvent, resetEvents]);

  // Handle visual config change
  const handleVisualConfigChange = useCallback((nextConfig: VisualGraphConfig) => {
    if (!onUpdateConfig || !llmConfig) return;

    onUpdateConfig(mergeVisualConfigIntoLlmConfig(llmConfig, nextConfig));
  }, [llmConfig, onUpdateConfig]);

  const handleVisualSave = useCallback(
    (nextConfig?: VisualGraphConfig) => {
      if (!onSaveConfig) return;
      if (nextConfig && llmConfig) {
        const merged = mergeVisualConfigIntoLlmConfig(llmConfig, nextConfig);
        onUpdateConfig?.(merged);
        void onSaveConfig(merged);
        return;
      }
      void onSaveConfig();
    },
    [llmConfig, onSaveConfig, onUpdateConfig]
  );

  // Loading state
  if (llmLoading || providersLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="flex items-center gap-2 text-text-muted">
          <Loader2 className="size-4 animate-spin" />
          <span className="text-sm">正在载入 LLM 配置...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 h-full min-h-0">
      <TabNavigation
        globalReadiness={globalReadiness}
        blockedRoles={blockedRoles}
        unsupportedRoles={unsupportedRoles}
      />

      {(llmError || providersError) && (
        <div className="text-xs text-status-error bg-status-error/10 border border-status-error/20 rounded p-2">
          {llmError || providersError}
        </div>
      )}

      {llmSaving && (
        <div className="flex items-center gap-2 text-[10px] text-text-dim">
          <Loader2 className="size-3 animate-spin" />
          <span>Saving LLM configuration...</span>
        </div>
      )}

      {activeTab === 'config' && (
        <div className="space-y-4">
          {/* View Switcher */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-text-main mb-1">LLM 提供商配置</h3>
              <p className="text-[10px] text-text-dim">
                列表视图用于日常配置，吏部·铨选司用于角色-模型连线。
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-1 rounded-lg border border-cyan-500/20 bg-black/40 p-1">
                <button
                  onClick={() => setConfigView('list')}
                  data-testid="llm-config-view-list"
                  className={`px-3 py-1.5 text-[10px] font-semibold rounded transition-all ${
                    configView === 'list'
                      ? 'bg-cyan-500/20 text-cyan-200'
                      : 'text-text-dim hover:text-cyan-100'
                  }`}
                >
                  列表视图
                </button>
                <button
                  onClick={() => setConfigView('visual')}
                  data-testid="llm-config-view-visual"
                  aria-label="视觉视图"
                  className={`px-3 py-1.5 text-[10px] font-semibold rounded transition-all ${
                    configView === 'visual'
                      ? 'bg-fuchsia-500/20 text-fuchsia-200'
                      : 'text-text-dim hover:text-fuchsia-100'
                  }`}
                >
                  吏部·铨选司（视觉视图）
                </button>
              </div>
            </div>
          </div>

          {configView === 'visual' ? (
            <LLMVisualEditor
              config={visualConfig}
              status={visualStatus}
              onConfigChange={handleVisualConfigChange}
              onSave={handleVisualSave}
            />
          ) : (
            <ProviderListManager
              providers={providers}
              configuredProviders={llmConfig?.providers || {}}
              llmStatus={llmStatus}
              isSaving={llmSaving}
              deletingProviders={deletingProviders}
              getProviderInfo={(type) => {
                const entry = getProviderInfo(type);
                if (!entry) return undefined;
                const defaults = getProviderDefaultConfig(type);
                if (!defaults) return undefined;
                const component = getProviderComponent(type);
                if (!component) return undefined;
                return { info: entry, defaultConfig: defaults, component };
              }}
              getProviderComponent={(type) => getProviderComponent(type) ?? null}
              getCostClass={getCostClass}
              onAddProvider={onAddProvider || (() => {})}
              onUpdateProvider={onUpdateProvider || (() => {})}
              onDeleteProvider={onDeleteProvider || (() => {})}
              onTestProvider={handleTestProvider}
              onEnterDeepTest={() => switchTab('deepTest')}
            />
          )}
        </div>
      )}

      {activeTab === 'deepTest' && (
        <DeepTestPanel
          llmConfig={llmConfig}
          llmStatus={llmStatus}
          onRunConnectivityTest={onRunConnectivityTest}
          onAskInteractiveInterview={onAskInteractiveInterview}
          onSaveInteractiveInterview={onSaveInteractiveInterview}
          resolveProviderEnvOverrides={resolveProviderEnvOverrides}
          addTestEvent={addEvent}
          resetTestEvents={resetEvents}
        />
      )}

      {/* Test Panel Portal */}
      {panelHostRef.current && testPanel.selectedProviderId && (
        createPortal(
          <TestPanel
            provider={buildSimpleProvider(
              testPanel.selectedProviderId,
              llmConfig?.providers?.[testPanel.selectedProviderId] || {},
              llmConfig?.roles
            )}
            events={events}
            status={testPanel.status}
            runConfig={testPanel.runConfig}
            autoStart={Boolean(testPanel.runConfig?.suites && !testPanel.runConfig.suites.includes('interactive_stream_view'))}
            panelMode={testPanel.runConfig?.suites?.includes('interactive_stream_view') ? 'event-viewer' : 'stream-runner'}
            title={testPanel.runConfig?.suites?.includes('interactive_stream_view') ? '🖥️ 正在测试：交互式面试' : undefined}
            subtitle={testPanel.runConfig?.suites?.includes('interactive_stream_view')
              ? `供应商：${llmConfig?.providers?.[testPanel.selectedProviderId]?.name || testPanel.selectedProviderId} · 模型：${testPanel.runConfig?.model || llmConfig?.providers?.[testPanel.selectedProviderId]?.model || '默认'}`
              : undefined}
            placeholder={testPanel.runConfig?.suites?.includes('interactive_stream_view') ? '$ 尚未发送面试问题...' : undefined}
            onClearEvents={testPanel.runConfig?.suites?.includes('interactive_stream_view') ? resetEvents : undefined}
            onClose={() => {
              closeTestPanel();
              resetEvents();
            }}
            onCancel={() => {
              onCancelTestProvider?.();
              closeTestPanel();
              resetEvents();
            }}
            onTestComplete={({ success }) => {
              // Use ref to ensure we call the latest completeTest with latest providerId
              const providerId = selectedProviderIdRef.current;
              devLogger.debug('[LLMSettingsTab] onTestComplete called:', { providerId, success });
              if (providerId) {
                devLogger.debug('[LLMSettingsTab] Calling completeTest for provider:', providerId, 'success:', success);
                completeTestRef.current(providerId, success);
              } else {
                devLogger.warn('[LLMSettingsTab] onTestComplete called but no providerId available');
              }
            }}
          />,
          panelHostRef.current
        )
      )}
    </div>
  );
}

// ============================================================================
// Exported Component with Provider
// ============================================================================

export function LLMSettingsTab(props: LLMSettingsTabProps) {
  return (
    <ProviderContextProvider>
      <LLMSettingsTabInner {...props} />
    </ProviderContextProvider>
  );
}

export default LLMSettingsTab;

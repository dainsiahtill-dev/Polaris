/**
 * ProviderListManager Component
 * Provider 列表管理组件
 */

import React, { useCallback, useMemo } from 'react';
import { Plus, Settings, PlayCircle } from 'lucide-react';
import type { ProviderConfig, ProviderSettingsProps } from '../types';
import { useConnectivityStore, useProviderContext } from '../state';
import { ProviderCard } from './ProviderCard';
import { ConnectionMethodSelector } from './ConnectionMethodSelector';
import type { ProviderInfo } from '../types';
import type { ProviderRegistryEntry } from '../ProviderRegistry';

type ProviderEntry = ProviderRegistryEntry;

interface ProviderListManagerProps {
  providers: ProviderRegistryEntry[];
  configuredProviders: Record<string, ProviderConfig>;
  llmStatus?: {
    providers?: Record<string, {
      ready?: boolean | null;
      suites?: Record<string, unknown> | null;
    }>;
    interviews?: {
      latest_by_provider?: Record<string, {
        status: 'passed' | 'failed';
        timestamp: string;
        role: string;
        model: string;
      }>;
    };
  } | null;
  isSaving?: boolean;
  deletingProviders?: Record<string, boolean>;
  getProviderInfo: (type: string) => ProviderRegistryEntry | undefined;
  getProviderComponent: (type: string) => React.ComponentType<ProviderSettingsProps> | null;
  getCostClass: (type: string) => string;
  onAddProvider: (id: string, config: ProviderConfig) => void;
  onUpdateProvider: (id: string, updates: Partial<ProviderConfig>) => void;
  onDeleteProvider: (id: string) => void | Promise<void>;
  onTestProvider: (id: string) => void;
  onEnterDeepTest: () => void;
}

const PROVIDER_FAMILY_ORDER = [
  'Codex',
  'OpenAI',
  'Anthropic',
  'Gemini',
  'MiniMax',
  'Ollama',
  'Custom',
  'Other',
];

function resolveProviderFamily(providerType: string, providerName: string): string {
  const type = providerType.toLowerCase();
  const name = providerName.toLowerCase();
  if (type.includes('codex') || name.includes('codex')) return 'Codex';
  if (type.includes('openai') || name.includes('openai')) return 'OpenAI';
  if (type.includes('anthropic') || name.includes('anthropic')) return 'Anthropic';
  if (type.includes('gemini') || name.includes('gemini')) return 'Gemini';
  if (type.includes('minimax') || name.includes('minimax')) return 'MiniMax';
  if (type.includes('ollama') || name.includes('ollama')) return 'Ollama';
  if (type.includes('custom')) return 'Custom';
  return 'Other';
}

function resolveConnectionMethod(providerType?: string): 'sdk' | 'api' | 'cli' {
  const normalized = String(providerType || '').toLowerCase();
  if (normalized.includes('sdk')) return 'sdk';
  if (normalized.includes('cli')) return 'cli';
  return 'api';
}

export function ProviderListManager({
  providers,
  configuredProviders,
  llmStatus,
  isSaving,
  deletingProviders,
  getProviderInfo,
  getProviderComponent,
  getCostClass,
  onAddProvider,
  onUpdateProvider,
  onDeleteProvider,
  onTestProvider,
  onEnterDeepTest,
}: ProviderListManagerProps) {
  const { state, selectMethod, openTestPanel } = useProviderContext();
  const { selectedMethod } = state;
  const providerTestStatusMap = state.providerTestStatus;
  const { getLatestProviderConnectivity } = useConnectivityStore();

  const availableMethods = useMemo(() => {
    const methodSet = new Set<'sdk' | 'api' | 'cli'>();
    providers.forEach((provider) => {
      methodSet.add(resolveConnectionMethod(provider.info.type));
    });
    return Array.from(methodSet);
  }, [providers]);

  const filteredProviderEntries = useMemo(() => {
    return providers.filter((provider) => {
      return resolveConnectionMethod(provider.info.type) === selectedMethod;
    });
  }, [providers, selectedMethod]);

  const providerGroups = useMemo(() => {
    const groups = new Map<string, ProviderEntry[]>();
    filteredProviderEntries.forEach((provider) => {
      const family = resolveProviderFamily(provider.info.type, provider.info.name);
      const existing = groups.get(family) || [];
      groups.set(family, [...existing, provider]);
    });
    const ordered: Array<[string, ProviderEntry[]]> = [];
    PROVIDER_FAMILY_ORDER.forEach((family) => {
      const entries = groups.get(family);
      if (entries && entries.length > 0) {
        ordered.push([family, entries]);
      }
    });
    groups.forEach((entries, family) => {
      if (!PROVIDER_FAMILY_ORDER.includes(family)) {
        ordered.push([family, entries]);
      }
    });
    return ordered;
  }, [filteredProviderEntries]);

  const configuredProviderCount = Object.keys(configuredProviders).length;
  const hasConfiguredProviders = configuredProviderCount > 0;

  const handleAddProvider = useCallback((providerType: string) => {
    const providerEntry = getProviderInfo(providerType);
    if (!providerEntry) return;

    const providerId = `${providerType}-${Date.now()}`;
    const newProvider: ProviderConfig = {
      ...providerEntry.defaultConfig,
      name: providerEntry.defaultConfig.name || `${providerType} 提供商`,
      type: providerType,
    };

    onAddProvider(providerId, newProvider);
  }, [getProviderInfo, onAddProvider]);

  const recommendedProvider = useMemo(() => {
    const primaryType = selectedMethod === 'sdk' 
      ? 'codex_sdk' 
      : selectedMethod === 'cli' 
        ? 'codex_cli' 
        : 'openai_compat';
    const preferred = providers.find((p) => p.info.type === primaryType);
    if (preferred) return preferred;
    return providers.find((p) => resolveConnectionMethod(p.info.type) === selectedMethod) || null;
  }, [providers, selectedMethod]);

  return (
    <div className="space-y-4">
      <ConnectionMethodSelector availableMethods={availableMethods} />

      {/* Available Providers to Add */}
      <div className="rounded-2xl border border-white/10 bg-[rgba(35,25,14,0.45)] p-4">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div>
            <div className="text-xs font-semibold text-text-main">支持的提供商</div>
            <div className="text-[10px] text-text-dim">
              当前显示：{selectedMethod === 'sdk' ? 'SDK 方式' : selectedMethod === 'cli' ? '命令行方式' : 'HTTP API 方式'}
            </div>
          </div>
          <div className="text-[10px] text-text-dim">选择后将自动创建配置并进入编辑模式。</div>
        </div>

        {providerGroups.length === 0 ? (
          <div className="text-xs text-text-dim">暂无可用提供商</div>
        ) : (
          <div className="space-y-4">
            {providerGroups.map(([family, entries]) => (
              <div key={family} className="space-y-2">
                <div className="text-[11px] uppercase tracking-wider text-text-dim">{family}</div>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                  {entries.map((provider) => (
                    <button
                      key={provider.info.type}
                      type="button"
                      onClick={() => handleAddProvider(provider.info.type)}
                      disabled={isSaving}
                      className="text-left rounded-xl border border-white/10 bg-[rgba(35,25,14,0.3)] hover:border-white/30 transition-all disabled:opacity-60 p-3"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <div className="text-xs font-semibold text-text-main">{provider.info.name}</div>
                          <div className="text-[10px] text-text-dim">{provider.info.type}</div>
                        </div>
                        <span className="text-[9px] px-2 py-0.5 rounded border border-white/10 bg-black/40">
                          {provider.info.cost_class}
                        </span>
                      </div>
                      <div className="mt-2 text-[10px] text-text-dim line-clamp-2">
                        {provider.info.description}
                      </div>
                      {provider.info.supported_features?.length ? (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {provider.info.supported_features.slice(0, 3).map((feature: string) => (
                            <span key={feature} className="text-[9px] px-2 py-0.5 rounded bg-white/5 text-text-dim">
                              {feature}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      <div className="mt-2 flex items-center justify-between text-[10px] text-text-dim">
                        <span>点击添加并配置</span>
                        <Plus className="size-3" />
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {recommendedProvider && (
          <div className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3 text-[10px] text-emerald-100 flex flex-wrap items-center justify-between gap-2">
            <div>
              推荐提供商：<span className="font-semibold">{recommendedProvider.info.name}</span>
              <span className="text-emerald-200/70"> · {recommendedProvider.info.description}</span>
            </div>
            <button
              type="button"
              onClick={() => handleAddProvider(recommendedProvider.info.type)}
              className="px-3 py-1.5 rounded bg-emerald-500/80 text-white text-[10px] font-semibold hover:bg-emerald-500 transition-colors"
            >
              一键添加
            </button>
          </div>
        )}
      </div>

      {/* Configured Providers List */}
      {hasConfiguredProviders ? (
        <div className="space-y-3">
          {Object.entries(configuredProviders).map(([providerId, provider]) => {
            const providerEntry = getProviderInfo(provider.type || '');
            const ProviderComponent = getProviderComponent(provider.type || '');
            const cachedConnectivityStatus = providerTestStatusMap[providerId] || 'unknown';
            const latestConnectivity = getLatestProviderConnectivity(providerId);
            const persistedConnectivitySuiteRaw = llmStatus?.providers?.[providerId]?.suites?.connectivity;
            const persistedConnectivitySuite =
              persistedConnectivitySuiteRaw && typeof persistedConnectivitySuiteRaw === 'object'
                ? (persistedConnectivitySuiteRaw as { ok?: unknown })
                : undefined;
            const persistedConnectivityOk =
              typeof persistedConnectivitySuite?.ok === 'boolean'
                ? persistedConnectivitySuite.ok
                : undefined;
            const connectivityStatus =
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

            return (
              <ProviderCard
                key={providerId}
                providerId={providerId}
                provider={provider}
                providerInfo={providerEntry?.info || null}
                ProviderComponent={ProviderComponent}
                connectivityStatus={connectivityStatus}
                costClass={getCostClass(provider.type || '')}
                isDeleting={deletingProviders?.[providerId]}
                isSaving={isSaving}
                llmStatus={llmStatus}
                onUpdate={onUpdateProvider}
                onDelete={onDeleteProvider}
                onTest={(id) => {
                  openTestPanel(id);
                  onTestProvider(id);
                }}
              />
            );
          })}
          <div className="flex flex-col items-center gap-2">
            <span className="text-[10px] text-text-dim">
              配置状态：{configuredProviderCount} 个提供商已准备
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onEnterDeepTest}
                className="px-4 py-2 text-xs font-semibold bg-emerald-500/80 hover:bg-emerald-500 text-white rounded transition-colors flex items-center gap-2"
              >
                进入深度测试
                <PlayCircle className="size-3" />
              </button>
              <button
                type="button"
                onClick={() => {
                  // 直接打开第一个配置提供商的测试面板，跳过连通性测试
                  const firstProviderId = Object.keys(configuredProviders)[0];
                  if (firstProviderId) {
                    openTestPanel(firstProviderId);
                  }
                }}
                className="px-3 py-2 text-[10px] border border-amber-500/30 text-amber-400 rounded hover:border-amber-400/60 hover:bg-amber-500/10 transition-colors"
                title="跳过连通性测试，直接打开测试面板"
              >
                直接打开面板
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="bg-white/5 rounded-xl p-8 border border-white/5 text-center space-y-4">
          <Settings className="size-8 text-text-dim mx-auto mb-3" />
          <h4 className="text-sm font-medium text-text-main mb-2">尚未配置LLM提供商</h4>
          <p className="text-xs text-text-dim mb-4">
            选择一个提供商类型并添加配置，然后进行模型测试
          </p>
          <div className="text-xs text-text-dim">
            <p>支持的提供商类型：</p>
            <div className="flex flex-wrap gap-2 justify-center mt-2">
              {providers.slice(0, 6).map((provider) => (
                <span key={provider.info.type} className="bg-[rgba(35,25,14,0.45)] px-2 py-1 rounded text-[9px]">
                  {provider.info.name}
                </span>
              ))}
            </div>
          </div>
          <div className="flex flex-col items-center gap-2">
            <span className="text-[10px] text-text-dim">配置状态：{configuredProviderCount} 个提供商</span>
            <button
              type="button"
              onClick={onEnterDeepTest}
              className="px-4 py-2 text-xs font-semibold bg-emerald-500/80 hover:bg-emerald-500 text-white rounded transition-colors flex items-center gap-2 opacity-80 hover:opacity-100"
            >
              进入深度测试（无配置）
              <PlayCircle className="size-3" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

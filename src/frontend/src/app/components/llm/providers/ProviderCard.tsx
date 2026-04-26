/**
 * ProviderCard Component
 * 单个 Provider 的展示和编辑卡片
 */

import React, { memo, useCallback, useEffect, useMemo } from 'react';
import { devLogger } from '@/app/utils/devLogger';
import {
  Loader2,
  Settings,
  ChevronDown,
  ChevronUp,
  Zap,
  Key,
  Shield,
  HelpCircle,
  Clock,
  UserCheck,
  UserX,
  PlayCircle,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import type { ProviderConfig, ProviderSettingsProps } from '../types';
import type { ConnectivityStatus } from '../state';
import { useProviderContext, useIsProviderExpanded } from '../state';
import type { SimpleProviderStrict } from '../types/strict';
import { isCLIProviderType, requiresApiKey } from '../types';
import { CyberpunkCard, CyberpunkGlitchText } from '../visual/CyberpunkTestAnimation';

interface ProviderCardProps {
  providerId: string;
  provider: ProviderConfig;
  providerInfo: {
    name: string;
    type: string;
    supported_features: string[];
  } | null;
  ProviderComponent: React.ComponentType<ProviderSettingsProps> | null;
  connectivityStatus: ConnectivityStatus;
  costClass: string;
  isDeleting?: boolean;
  isSaving?: boolean;
  llmStatus?: {
    providers?: Record<string, {
      ready?: boolean | null;
      grade?: string;
      timestamp?: string | null;
      last_run_id?: string | null;
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
  onUpdate: (id: string, updates: Partial<ProviderConfig>) => void;
  onDelete: (id: string) => void;
  onTest: (id: string) => void;
}

function toProviderSlug(value: string): string {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export const ProviderCard = memo(function ProviderCard({
  providerId,
  provider,
  providerInfo,
  ProviderComponent,
  connectivityStatus,
  costClass,
  isDeleting,
  isSaving,
  llmStatus,
  onUpdate,
  onDelete,
  onTest,
}: ProviderCardProps) {
  const { 
    startEditProvider, 
    stopEditProvider, 
    toggleExpandProvider,
    state 
  } = useProviderContext();
  
  const isExpanded = useIsProviderExpanded(providerId);
  const isEditing = state.editingProvider === providerId;

  // Debug: log status changes
  useEffect(() => {
    devLogger.debug('[ProviderCard]', providerId, 'status changed to:', connectivityStatus);
  }, [providerId, connectivityStatus]);

  const statusStyles = useMemo(() => {
    const styleKey = connectivityStatus === 'running' ? 'unknown' : connectivityStatus;
    return {
      unknown: {
        border: 'border-amber-500/30',
        bg: 'bg-amber-500/5',
        glow: 'shadow-[0_0_24px_rgba(251,191,36,0.15)]',
        dot: 'bg-amber-400',
        text: 'text-amber-300',
      },
      success: {
        border: 'border-emerald-500/40',
        bg: 'bg-emerald-500/5',
        glow: 'shadow-[0_0_24px_rgba(16,185,129,0.18)]',
        dot: 'bg-emerald-400',
        text: 'text-emerald-300',
      },
      failed: {
        border: 'border-rose-500/40',
        bg: 'bg-rose-500/5',
        glow: 'shadow-[0_0_24px_rgba(244,63,94,0.18)]',
        dot: 'bg-rose-400',
        text: 'text-rose-300',
      },
    }[styleKey];
  }, [connectivityStatus]);

  const connectivityLabel = useMemo(() => {
    if (connectivityStatus === 'running') return '测试中';
    if (connectivityStatus === 'success') return '连通正常';
    if (connectivityStatus === 'failed') return '连通失败';
    return '连通未知';
  }, [connectivityStatus]);

  const providerInterview = useMemo(() => {
    return llmStatus?.interviews?.latest_by_provider?.[providerId];
  }, [llmStatus, providerId]);
  const providerReadiness = useMemo(() => {
    return llmStatus?.providers?.[providerId];
  }, [llmStatus, providerId]);
  const readinessStatus = useMemo<'passed' | 'failed' | 'unknown'>(() => {
    if (providerReadiness?.ready === true) return 'passed';
    if (providerReadiness?.ready === false) return 'failed';
    return 'unknown';
  }, [providerReadiness]);
  const readinessLabel = useMemo(() => {
    if (readinessStatus === 'passed') return '就绪通过';
    if (readinessStatus === 'failed') return '就绪失败';
    return '就绪未知';
  }, [readinessStatus]);
  const deepTestLabel = useMemo(() => {
    if (!providerInterview) return '深测未测';
    return providerInterview.status === 'passed' ? '深测通过' : '深测失败';
  }, [providerInterview]);

  const providerType = useMemo(() => {
    return isCLIProviderType(provider.type || '') ? '命令行' : '接口';
  }, [provider.type]);

  const authType = useMemo(() => {
    return requiresApiKey(provider.type || '') ? 'API 密钥' : '无';
  }, [provider.type]);

  const getRoleDisplayName = useCallback((roleId?: string) => {
    if (roleId === 'pm') return 'PM';
    if (roleId === 'director') return '工部侍郎';
    if (roleId === 'qa') return 'QA';
    if (roleId === 'architect' || roleId === 'docs') return 'Architect';
    return roleId || '未署名';
  }, []);

  const handleToggleEdit = useCallback(() => {
    if (isEditing) {
      stopEditProvider();
    } else {
      startEditProvider(providerId);
    }
  }, [isEditing, providerId, startEditProvider, stopEditProvider]);

  const handleToggleExpand = useCallback(() => {
    toggleExpandProvider(providerId);
  }, [providerId, toggleExpandProvider]);

  const handleDelete = useCallback(() => {
    onDelete(providerId);
  }, [providerId, onDelete]);

  const handleTest = useCallback(() => {
    onTest(providerId);
  }, [providerId, onTest]);

  const handleUpdate = useCallback((updates: Partial<ProviderConfig>) => {
    onUpdate(providerId, updates);
  }, [providerId, onUpdate]);

  const actionsDisabled = isSaving || !!isDeleting;
  const testDisabled = actionsDisabled;
  const providerLabel = provider.name || providerInfo?.name || providerId;
  const providerSlug = toProviderSlug(providerLabel || providerId) || 'provider';

  return (
    <CyberpunkCard
      status={connectivityStatus}
      className="p-4"
      data-testid={`provider-card-${providerSlug}`}
      data-provider-id={providerId}
      data-provider-type={provider.type || ''}
      data-provider-name={providerLabel}
      data-provider-connectivity-status={connectivityStatus}
    >
      {/* Compact View */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <CyberpunkGlitchText 
            text={provider.name || providerInfo?.name || providerId} 
            status={connectivityStatus}
            className="text-sm font-semibold"
          />
          <div className="flex items-center gap-2 text-[10px] text-text-dim">
            <span className="font-mono">{provider.model || '默认'}</span>
            <span className={`${
              costClass.toLowerCase() === 'local' 
                ? 'text-green-400' 
                : costClass.toLowerCase() === 'fixed' 
                  ? 'text-blue-400' 
                  : 'text-purple-400'
            }`}>
              {costClass}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Connectivity Status Badge */}
          <div className={`flex items-center gap-1.5 px-2 py-1 rounded border ${statusStyles.border} bg-white/5`}>
            <CyberpunkGlitchText text={connectivityLabel} status={connectivityStatus} className="text-[10px]" />
          </div>

          {/* Readiness Status Badge */}
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded border border-white/10 bg-white/5"
            title={`就绪状态（综合套件）${providerReadiness?.grade ? `: ${providerReadiness.grade}` : ''}`}
          >
            {readinessStatus === 'passed' ? (
              <CheckCircle2 className="size-3 text-emerald-400" />
            ) : readinessStatus === 'failed' ? (
              <AlertTriangle className="size-3 text-amber-400" />
            ) : (
              <HelpCircle className="size-3 text-gray-400" />
            )}
            <span className="text-[10px] text-text-main">{readinessLabel}</span>
          </div>

          {/* Deep Test Status Badge */}
          <div className="flex items-center gap-1.5 px-2 py-1 rounded border border-white/10 bg-white/5">
            {providerInterview ? (
              providerInterview.status === 'passed' ? (
                <UserCheck className="size-3 text-emerald-400" />
              ) : (
                <UserX className="size-3 text-rose-400" />
              )
            ) : (
              <HelpCircle className="size-3 text-gray-400" />
            )}
            <span className="text-[10px] text-text-main">
              {deepTestLabel}
            </span>
          </div>

          <button
            onClick={handleTest}
            disabled={testDisabled}
            data-provider-action="test"
            data-testid={`provider-test-button-${providerSlug}`}
            className="p-1.5 rounded border border-cyan-500/30 hover:border-cyan-500/60 text-cyan-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="测试连通性"
          >
            <PlayCircle className="size-3" />
          </button>
          <button
            onClick={handleToggleEdit}
            disabled={actionsDisabled}
            data-provider-action="edit"
            data-testid={`provider-edit-button-${providerSlug}`}
            className={`p-1.5 rounded border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
              isEditing 
                ? 'border-cyan-400/60 bg-cyan-500/20 text-cyan-200' 
                : 'border-white/10 hover:border-accent/40'
            }`}
            title={isEditing ? '完成编辑' : '编辑提供商'}
          >
            <Settings className="size-3" />
          </button>
          <button
            onClick={handleToggleExpand}
            data-provider-action="expand"
            data-testid={`provider-expand-button-${providerSlug}`}
            className="p-1.5 rounded border border-white/10 hover:border-accent/40 transition-colors"
            title={isExpanded ? '收起详情' : '展开详情'}
          >
            {isExpanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
          </button>
          <button
            onClick={handleDelete}
            disabled={actionsDisabled}
            data-provider-action="delete"
            data-testid={`provider-delete-button-${providerSlug}`}
            className="p-1.5 rounded border border-red-500/30 hover:border-red-500/40 text-red-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="删除提供商"
          >
            {isDeleting ? <Loader2 className="size-3 animate-spin" /> : '×'}
          </button>
        </div>
      </div>

      {/* Expanded View */}
      {isExpanded && !isEditing && (
        <div className="mt-4 pt-4 border-t border-white/10 space-y-4">
          {/* Three-column info cards */}
          <div className="grid grid-cols-3 gap-3">
            <div className="flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5">
              <Zap className="size-3.5 text-amber-400" />
              <div className="flex-1 min-w-0">
                <div className="text-[9px] text-text-dim uppercase tracking-wide">类型</div>
                <div className="text-xs text-text-main truncate">{providerType}</div>
              </div>
            </div>

            <div className="flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5">
              <Key className="size-3.5 text-cyan-400" />
              <div className="flex-1 min-w-0">
                <div className="text-[9px] text-text-dim uppercase tracking-wide">认证</div>
                <div className="text-xs text-text-main truncate">{authType}</div>
              </div>
            </div>

            <div className="flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5">
              <Shield className="size-3.5 text-green-400" />
              <div className="flex-1 min-w-0">
                <div className="text-[9px] text-text-dim uppercase tracking-wide">特性</div>
                <div className="text-xs text-text-main truncate">
                  {providerInfo?.supported_features.slice(0, 2).join(', ') || '-'}
                  {providerInfo && providerInfo.supported_features.length > 2 && '...'}
                </div>
              </div>
            </div>
          </div>

          {/* Interview Details */}
          {providerInterview && (
            <div className="space-y-2">
              <h5 className="text-xs font-semibold text-text-main flex items-center gap-2">
                <UserCheck className="size-3.5 text-accent" />
                深度测试记录
              </h5>
              <div className="flex items-center gap-2">
                <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${
                  providerInterview.status === 'passed'
                    ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                    : 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                }`}>
                  {providerInterview.status === 'passed' ? '通过' : '失败'}
                </span>
                <span className="flex items-center gap-1 text-[10px] text-text-dim">
                  <Clock className="size-3" />
                  {new Date(providerInterview.timestamp).toLocaleString()}
                </span>
              </div>
              <div className="text-[10px] text-text-muted">
                角色: <span className="text-text-main">{getRoleDisplayName(providerInterview.role)}</span>
                {' · '}
                模型: <span className="text-text-main font-mono">{providerInterview.model}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Edit View */}
      {isEditing && ProviderComponent && (
        <div className="mt-4 pt-4 border-t border-white/10">
          <ProviderComponent
            providerId={providerId}
            provider={{
              ...provider,
              type: provider.type || 'openai_compat',
              name: provider.name == null ? '' : String(provider.name),
            }}
            onUpdate={handleUpdate}
            onValidate={() => ({ valid: true, errors: [], warnings: [] })}
          />
        </div>
      )}
    </CyberpunkCard>
  );
});

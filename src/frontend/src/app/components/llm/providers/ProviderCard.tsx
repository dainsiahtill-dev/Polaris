/**
 * ProviderCard Component
 * 单个 Provider 的展示和编辑卡片
 */

import React, { memo, useCallback, useEffect, useMemo } from "react";
import { devLogger } from "@/app/utils/devLogger";
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
} from "lucide-react";
import type { ProviderConfig, ProviderSettingsProps } from "../types";
import type { ConnectivityStatus } from "../state";
import { useProviderContext, useIsProviderExpanded } from "../state";
import type { SimpleProviderStrict } from "../types/strict";
import { isCLIProviderType, requiresApiKey } from "../types";
import {
  CyberpunkCard,
  CyberpunkGlitchText,
} from "../visual/CyberpunkTestAnimation";
import { getRoleDisplayLabel } from "@/app/constants/roleLabels";

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
    providers?: Record<
      string,
      {
        ready?: boolean | null;
        grade?: string;
        timestamp?: string | null;
        last_run_id?: string | null;
      }
    >;
    interviews?: {
      latest_by_provider?: Record<
        string,
        {
          status: "passed" | "failed";
          timestamp: string;
          role: string;
          model: string;
        }
      >;
    };
  } | null;
  onUpdate: (id: string, updates: Partial<ProviderConfig>) => void;
  onDelete: (id: string) => void;
  onTest: (id: string) => void;
}

function toProviderSlug(value: string): string {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
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
  const { startEditProvider, stopEditProvider, toggleExpandProvider, state } =
    useProviderContext();

  const isExpanded = useIsProviderExpanded(providerId);
  const isEditing = state.editingProvider === providerId;

  // Debug: log status changes
  useEffect(() => {
    devLogger.debug(
      "[ProviderCard]",
      providerId,
      "status changed to:",
      connectivityStatus,
    );
  }, [providerId, connectivityStatus]);

  const statusStyles = useMemo(() => {
    const styleKey =
      connectivityStatus === "running" ? "unknown" : connectivityStatus;
    return {
      unknown: {
        border: "border-status-warning/35",
        bg: "bg-status-warning/10",
        dot: "bg-status-warning",
        text: "text-status-warning",
      },
      success: {
        border: "border-status-success/40",
        bg: "bg-status-success/10",
        dot: "bg-status-success",
        text: "text-status-success",
      },
      failed: {
        border: "border-status-error/40",
        bg: "bg-status-error/10",
        dot: "bg-status-error",
        text: "text-status-error",
      },
    }[styleKey];
  }, [connectivityStatus]);

  const connectivityLabel = useMemo(() => {
    if (connectivityStatus === "running") return "测试中";
    if (connectivityStatus === "success") return "连通正常";
    if (connectivityStatus === "failed") return "连通失败";
    return "连通未知";
  }, [connectivityStatus]);

  const providerInterview = useMemo(() => {
    return llmStatus?.interviews?.latest_by_provider?.[providerId];
  }, [llmStatus, providerId]);
  const providerReadiness = useMemo(() => {
    return llmStatus?.providers?.[providerId];
  }, [llmStatus, providerId]);
  const readinessStatus = useMemo<"passed" | "failed" | "unknown">(() => {
    if (providerReadiness?.ready === true) return "passed";
    if (providerReadiness?.ready === false) return "failed";
    return "unknown";
  }, [providerReadiness]);
  const readinessLabel = useMemo(() => {
    if (readinessStatus === "passed") return "就绪通过";
    if (readinessStatus === "failed") return "就绪失败";
    return "就绪未知";
  }, [readinessStatus]);
  const deepTestLabel = useMemo(() => {
    if (!providerInterview) return "深测未测";
    return providerInterview.status === "passed" ? "深测通过" : "深测失败";
  }, [providerInterview]);

  const providerType = useMemo(() => {
    return isCLIProviderType(provider.type || "") ? "命令行" : "接口";
  }, [provider.type]);

  const authType = useMemo(() => {
    return requiresApiKey(provider.type || "") ? "API 密钥" : "无";
  }, [provider.type]);

  const getRoleDisplayName = useCallback((roleId?: string) => {
    return roleId ? getRoleDisplayLabel(roleId) : "未署名";
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

  const handleUpdate = useCallback(
    (updates: Partial<ProviderConfig>) => {
      onUpdate(providerId, updates);
    },
    [providerId, onUpdate],
  );

  const actionsDisabled = isSaving || !!isDeleting;
  const testDisabled = actionsDisabled;
  const providerLabel = provider.name || providerInfo?.name || providerId;
  const providerSlug =
    toProviderSlug(providerLabel || providerId) || "provider";

  return (
    <CyberpunkCard
      status={connectivityStatus}
      className="p-4"
      data-testid={`provider-card-${providerSlug}`}
      data-provider-id={providerId}
      data-provider-type={provider.type || ""}
      data-provider-name={providerLabel}
      data-provider-connectivity-status={connectivityStatus}
    >
      {/* Compact View */}
      <div
        data-testid={`provider-card-header-${providerSlug}`}
        className="flex min-w-0 flex-col gap-3 lg:flex-row lg:items-start lg:justify-between"
      >
        <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
          <CyberpunkGlitchText
            text={provider.name || providerInfo?.name || providerId}
            status={connectivityStatus}
            className="min-w-0 truncate text-sm font-semibold"
          />
          <div className="flex min-w-0 flex-wrap items-center gap-2 text-[10px] text-text-dim">
            <span className="min-w-0 max-w-full truncate font-mono sm:max-w-72">
              {provider.model || "默认"}
            </span>
            <span
              className={`${
                costClass.toLowerCase() === "local"
                  ? "text-status-success"
                  : costClass.toLowerCase() === "fixed"
                    ? "text-accent-text"
                    : "text-status-warning"
              }`}
            >
              {costClass}
            </span>
          </div>
        </div>

        <div
          data-testid={`provider-card-actions-${providerSlug}`}
          className="flex shrink-0 flex-wrap items-center gap-2 lg:justify-end"
        >
          {/* Connectivity Status Badge */}
          <div
            className={`flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 ${statusStyles.border} ${statusStyles.bg}`}
          >
            <CyberpunkGlitchText
              text={connectivityLabel}
              status={connectivityStatus}
              className="text-[10px]"
            />
          </div>

          {/* Readiness Status Badge */}
          <div
            className="soft-chip flex shrink-0 items-center gap-1.5 px-2 py-1"
            title={`就绪状态（综合套件）${providerReadiness?.grade ? `: ${providerReadiness.grade}` : ""}`}
          >
            {readinessStatus === "passed" ? (
              <CheckCircle2 className="size-3 text-status-success" />
            ) : readinessStatus === "failed" ? (
              <AlertTriangle className="size-3 text-status-warning" />
            ) : (
              <HelpCircle className="size-3 text-text-muted" />
            )}
            <span className="text-[10px] text-text-main">{readinessLabel}</span>
          </div>

          {/* Deep Test Status Badge */}
          <div className="soft-chip flex shrink-0 items-center gap-1.5 px-2 py-1">
            {providerInterview ? (
              providerInterview.status === "passed" ? (
                <UserCheck className="size-3 text-status-success" />
              ) : (
                <UserX className="size-3 text-status-error" />
              )
            ) : (
              <HelpCircle className="size-3 text-text-muted" />
            )}
            <span className="text-[10px] text-text-main">{deepTestLabel}</span>
          </div>

          <button
            onClick={handleTest}
            disabled={testDisabled}
            data-provider-action="test"
            data-testid={`provider-test-button-${providerSlug}`}
            className="rounded border border-accent/35 p-1.5 text-accent-text transition-colors hover:border-accent/60 hover:bg-accent/10 disabled:cursor-not-allowed disabled:opacity-50"
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
                ? "border-accent/55 bg-accent/15 text-accent-text"
                : "border-border text-text-muted hover:border-accent/40 hover:text-text-main"
            }`}
            title={isEditing ? "完成编辑" : "编辑提供商"}
          >
            <Settings className="size-3" />
          </button>
          <button
            onClick={handleToggleExpand}
            data-provider-action="expand"
            data-testid={`provider-expand-button-${providerSlug}`}
            className="rounded border border-border p-1.5 text-text-muted transition-colors hover:border-accent/40 hover:text-text-main"
            title={isExpanded ? "收起详情" : "展开详情"}
          >
            {isExpanded ? (
              <ChevronUp className="size-3" />
            ) : (
              <ChevronDown className="size-3" />
            )}
          </button>
          <button
            onClick={handleDelete}
            disabled={actionsDisabled}
            data-provider-action="delete"
            data-testid={`provider-delete-button-${providerSlug}`}
            className="rounded border border-status-error/35 p-1.5 text-status-error transition-colors hover:border-status-error/55 hover:bg-status-error/10 disabled:cursor-not-allowed disabled:opacity-50"
            title="删除提供商"
          >
            {isDeleting ? <Loader2 className="size-3 animate-spin" /> : "×"}
          </button>
        </div>
      </div>

      {/* Expanded View */}
      {isExpanded && !isEditing && (
        <div className="mt-4 space-y-4 border-t border-border pt-4">
          {/* Three-column info cards */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="soft-chip flex items-center gap-2 rounded px-3 py-2">
              <Zap className="size-3.5 text-status-warning" />
              <div className="flex-1 min-w-0">
                <div className="text-[9px] text-text-dim uppercase tracking-wide">
                  类型
                </div>
                <div className="text-xs text-text-main truncate">
                  {providerType}
                </div>
              </div>
            </div>

            <div className="soft-chip flex items-center gap-2 rounded px-3 py-2">
              <Key className="size-3.5 text-accent-text" />
              <div className="flex-1 min-w-0">
                <div className="text-[9px] text-text-dim uppercase tracking-wide">
                  认证
                </div>
                <div className="text-xs text-text-main truncate">
                  {authType}
                </div>
              </div>
            </div>

            <div className="soft-chip flex items-center gap-2 rounded px-3 py-2">
              <Shield className="size-3.5 text-status-success" />
              <div className="flex-1 min-w-0">
                <div className="text-[9px] text-text-dim uppercase tracking-wide">
                  特性
                </div>
                <div className="text-xs text-text-main truncate">
                  {providerInfo?.supported_features.slice(0, 2).join(", ") ||
                    "-"}
                  {providerInfo &&
                    providerInfo.supported_features.length > 2 &&
                    "..."}
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
                <span
                  className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${
                    providerInterview.status === "passed"
                      ? "bg-status-success/15 text-status-success border-status-success/35"
                      : "bg-status-error/15 text-status-error border-status-error/35"
                  }`}
                >
                  {providerInterview.status === "passed" ? "通过" : "失败"}
                </span>
                <span className="flex items-center gap-1 text-[10px] text-text-dim">
                  <Clock className="size-3" />
                  {new Date(providerInterview.timestamp).toLocaleString()}
                </span>
              </div>
              <div className="break-words text-[10px] text-text-muted">
                角色:{" "}
                <span className="text-text-main">
                  {getRoleDisplayName(providerInterview.role)}
                </span>
                {" · "}
                模型:{" "}
                <span className="font-mono text-text-main">
                  {providerInterview.model}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Edit View */}
      {isEditing && ProviderComponent && (
        <div className="mt-4 border-t border-border pt-4">
          <ProviderComponent
            providerId={providerId}
            provider={{
              ...provider,
              type: provider.type || "openai_compat",
              name: provider.name == null ? "" : String(provider.name),
            }}
            onUpdate={handleUpdate}
            onValidate={() => ({ valid: true, errors: [], warnings: [] })}
          />
        </div>
      )}
    </CyberpunkCard>
  );
});

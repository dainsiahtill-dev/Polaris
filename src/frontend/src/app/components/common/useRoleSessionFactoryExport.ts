import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { devLogger } from '@/app/utils/devLogger';
import { getFactoryRun, stopFactoryRun, type FactoryRunStatus } from '@/services/factoryService';
import { exportRoleSessionToWorkflow } from '@/services/roleSessionService';

export interface RoleSessionFactoryRunEvidence {
  runId: string | null;
  loading: boolean;
  data: FactoryRunStatus | null;
  error: string | null;
}

export interface RoleSessionFactoryRunCancelEvidence {
  runId: string | null;
  loading: boolean;
  message: string | null;
  error: string | null;
}

interface LoadFactoryRunEvidenceOptions {
  preserveData?: boolean;
  preserveCancel?: boolean;
}

interface UseRoleSessionFactoryExportOptions {
  sessionId: string | null;
  logPrefix: string;
}

const TERMINAL_FACTORY_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'canceled', 'blocked', 'timeout']);

function runLifecycleToken(run?: FactoryRunStatus | null): string {
  const status = String(run?.status || '').trim().toLowerCase();
  if (TERMINAL_FACTORY_RUN_STATUSES.has(status)) return status;
  const phase = String(run?.phase || '').trim().toLowerCase();
  if (TERMINAL_FACTORY_RUN_STATUSES.has(phase)) return phase;
  return status || phase;
}

function isTerminalFactoryRun(run?: FactoryRunStatus | null): boolean {
  return TERMINAL_FACTORY_RUN_STATUSES.has(runLifecycleToken(run));
}

export function useRoleSessionFactoryExport({
  sessionId,
  logPrefix,
}: UseRoleSessionFactoryExportOptions) {
  const [isExportingFactory, setIsExportingFactory] = useState(false);
  const [factoryRunEvidence, setFactoryRunEvidence] = useState<RoleSessionFactoryRunEvidence>({
    runId: null,
    loading: false,
    data: null,
    error: null,
  });
  const [factoryRunCancelEvidence, setFactoryRunCancelEvidence] = useState<RoleSessionFactoryRunCancelEvidence>({
    runId: null,
    loading: false,
    message: null,
    error: null,
  });

  const loadFactoryRunEvidence = useCallback(async (runId: string, options: LoadFactoryRunEvidenceOptions = {}) => {
    setFactoryRunEvidence((current) => ({
      runId,
      loading: true,
      data: options.preserveData && current.runId === runId ? current.data : null,
      error: null,
    }));
    if (!options.preserveCancel) {
      setFactoryRunCancelEvidence({
        runId,
        loading: false,
        message: null,
        error: null,
      });
    }

    try {
      const result = await getFactoryRun(runId);
      if (result.ok && result.data) {
        setFactoryRunEvidence({
          runId,
          loading: false,
          data: result.data,
          error: null,
        });
        return;
      }
      setFactoryRunEvidence({
        runId,
        loading: false,
        data: null,
        error: result.error || 'Factory run detail unavailable',
      });
    } catch (err) {
      setFactoryRunEvidence({
        runId,
        loading: false,
        data: null,
        error: err instanceof Error ? err.message : 'Factory run detail unavailable',
      });
    }
  }, []);

  const handleRefreshFactoryRun = useCallback(() => {
    const runId = String(factoryRunEvidence.runId || '').trim();
    if (!runId) return;
    void loadFactoryRunEvidence(runId, {
      preserveData: true,
      preserveCancel: true,
    });
  }, [factoryRunEvidence.runId, loadFactoryRunEvidence]);

  const handleExportToFactory = useCallback(async () => {
    if (!sessionId || isExportingFactory) return;

    setIsExportingFactory(true);
    try {
      const result = await exportRoleSessionToWorkflow(sessionId, {
        target: 'factory',
        export_kind: 'session_bundle',
        include_audit_log: true,
      });

      if (result.ok && result.data?.run_id) {
        const runId = result.data.run_id;
        devLogger.debug(`[${logPrefix}] Exported to Factory workflow:`, result.data);
        toast.success('已导出到 Factory 流水线', {
          description: `Run ID: ${runId}\nArtifacts: ${result.data.artifact_count || 0}`,
        });
        await loadFactoryRunEvidence(runId);
      } else {
        const error = result.ok ? result.data?.error || '后端未返回 Run ID' : result.error;
        devLogger.error(`[${logPrefix}] Factory export failed:`, error);
        toast.error('导出 Factory 失败', {
          description: error || '未知错误',
        });
      }
    } catch (err) {
      devLogger.error(`[${logPrefix}] Failed to export Factory workflow:`, err);
      toast.error('导出 Factory 失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    } finally {
      setIsExportingFactory(false);
    }
  }, [isExportingFactory, loadFactoryRunEvidence, logPrefix, sessionId]);

  const handleCancelFactoryRun = useCallback(async () => {
    const runId = String(factoryRunEvidence.runId || '').trim();
    if (!runId) return;

    setFactoryRunCancelEvidence({
      runId,
      loading: true,
      message: null,
      error: null,
    });

    try {
      const result = await stopFactoryRun(runId);
      if (result.ok && result.data) {
        const status = String(result.data.status || 'unknown').trim() || 'unknown';
        setFactoryRunEvidence({
          runId,
          loading: false,
          data: result.data,
          error: null,
        });
        setFactoryRunCancelEvidence({
          runId,
          loading: false,
          message: `取消运行已提交: ${status}`,
          error: null,
        });
        toast.success('Factory 流水线取消已提交', {
          description: `Run ID: ${runId}`,
        });
      } else {
        const error = result.ok ? '后端未返回取消结果' : result.error;
        setFactoryRunCancelEvidence({
          runId,
          loading: false,
          message: null,
          error: error || 'Factory run cancel failed',
        });
        toast.error('Factory 流水线取消失败', {
          description: error || '未知错误',
        });
      }
    } catch (err) {
      const error = err instanceof Error ? err.message : '未知错误';
      setFactoryRunCancelEvidence({
        runId,
        loading: false,
        message: null,
        error,
      });
      toast.error('Factory 流水线取消失败', {
        description: error,
      });
    }
  }, [factoryRunEvidence.runId]);

  const cancelFactoryRunDisabled =
    !factoryRunEvidence.runId ||
    factoryRunEvidence.loading ||
    factoryRunCancelEvidence.loading ||
    isTerminalFactoryRun(factoryRunEvidence.data);
  const factoryRunRealtimePushActive = Boolean(factoryRunEvidence.runId)
    && !factoryRunEvidence.loading
    && !factoryRunCancelEvidence.loading
    && !factoryRunEvidence.error
    && !isTerminalFactoryRun(factoryRunEvidence.data);

  return {
    isExportingFactory,
    factoryRunEvidence,
    factoryRunCancelEvidence,
    factoryRunRealtimePushActive,
    cancelFactoryRunDisabled,
    handleExportToFactory,
    handleRefreshFactoryRun,
    handleCancelFactoryRun,
  };
}

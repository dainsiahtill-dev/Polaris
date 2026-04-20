import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { residentService } from '@/services/api';
import type {
  GoalExecutionView,
  ResidentDecisionPayload,
  ResidentExperimentPayload,
  ResidentGoalPayload,
  ResidentGoalRunPayload,
  ResidentGoalStagePayload,
  ResidentImprovementPayload,
  ResidentStatusDetailsPayload,
  ResidentStatusPayload,
  ResidentSkillPayload,
} from '@/app/types/appContracts';

interface UseResidentOptions {
  workspace?: string | null;
  liveResident?: ResidentStatusPayload | null;
  autoRefreshMs?: number;
}

interface ResidentIdentityPatch {
  name?: string;
  mission?: string;
  owner?: string;
  operating_mode?: string;
  values?: string[];
  memory_lineage?: string[];
  capability_profile?: Record<string, number>;
}

interface ResidentGoalDraft {
  goal_type?: string;
  title: string;
  motivation?: string;
  source?: string;
  scope?: string[];
  evidence_refs?: string[];
  budget?: Record<string, unknown>;
  expected_value?: number;
  risk_score?: number;
}

function emptyDetails(workspace: string, liveResident?: ResidentStatusPayload | null): ResidentStatusDetailsPayload | null {
  if (!liveResident) {
    return null;
  }
  return {
    workspace: workspace || liveResident.workspace,
    identity: liveResident.identity,
    runtime: liveResident.runtime,
    agenda: liveResident.agenda,
    counts: liveResident.counts,
    decisions: [],
    goals: [],
    insights: [],
    skills: [],
    experiments: [],
    improvements: [],
    capability_graph: { generated_at: '', capabilities: [], gaps: [] },
  };
}

export function useResident(options: UseResidentOptions = {}) {
  const workspace = String(options.workspace || '').trim();
  const autoRefreshMs = options.autoRefreshMs ?? 15000;
  const [status, setStatus] = useState<ResidentStatusDetailsPayload | null>(
    emptyDetails(workspace, options.liveResident),
  );
  const [loading, setLoading] = useState(false);
  const [actionKey, setActionKey] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  // Phase 1.2: Goal Execution Projection (synced from WebSocket status)
  const [goalExecutions, setGoalExecutions] = useState<Map<string, GoalExecutionView>>(new Map());

  const refresh = useCallback(async () => {
    if (!workspace) {
      setStatus(emptyDetails('', options.liveResident));
      setError(null);
      return null;
    }
    setLoading(true);
    const result = await residentService.getStatus(workspace, true);
    setLoading(false);
    if (!result.ok || !result.data) {
      const message = result.error || '加载 AGI 状态失败';
      setError(message);
      return null;
    }
    setStatus(result.data);
    setError(null);
    return result.data;
  }, [options.liveResident, workspace]);

  const runAction = useCallback(
    async <T,>(
      key: string,
      action: () => Promise<{ ok: boolean; data?: T; error?: string }>,
      successMessage: string,
    ): Promise<T | null> => {
      if (!workspace) {
        toast.error('请先选择 Workspace');
        return null;
      }
      setActionKey(key);
      const result = await action();
      setActionKey('');
      if (!result.ok) {
        const message = result.error || 'AGI 操作失败';
        setError(message);
        toast.error(message);
        return null;
      }
      setError(null);
      toast.success(successMessage);
      await refresh();
      return result.data ?? null;
    },
    [refresh, workspace],
  );

  useEffect(() => {
    if (!workspace) {
      setStatus(emptyDetails('', options.liveResident));
      setError(null);
      return;
    }
    void refresh();
  }, [options.liveResident, refresh, workspace]);

  // Phase 1.2: Sync goal executions from status (WebSocket)
  useEffect(() => {
    if (status?.goal_executions) {
      const newMap = new Map<string, GoalExecutionView>();
      status.goal_executions.forEach((exec) => {
        if (exec.goal_id) {
          newMap.set(exec.goal_id, exec);
        }
      });
      setGoalExecutions(newMap);
    }
  }, [status?.goal_executions]);

  useEffect(() => {
    if (!workspace || autoRefreshMs <= 0) {
      return;
    }
    const timer = window.setInterval(() => {
      void refresh();
    }, autoRefreshMs);
    return () => window.clearInterval(timer);
  }, [autoRefreshMs, refresh, workspace]);

  const summary = useMemo(
    () => status ?? emptyDetails(workspace, options.liveResident),
    [options.liveResident, status, workspace],
  );

  return {
    workspace,
    status: summary,
    goals: summary?.goals ?? [],
    decisions: summary?.decisions ?? [],
    loading,
    actionKey,
    error,
    residentRuntime: summary?.runtime ?? null,
    residentIdentity: summary?.identity ?? null,
    residentAgenda: summary?.agenda ?? null,
    residentCounts: summary?.counts ?? null,
    residentInsights: summary?.insights ?? [],
    residentSkills: summary?.skills ?? [],
    residentExperiments: summary?.experiments ?? [],
    residentImprovements: summary?.improvements ?? [],
    residentCapabilityGraph: summary?.capability_graph ?? null,
    refresh,
    isActing: (key: string) => actionKey === key,
    start: (mode: string) =>
      runAction('start', () => residentService.start(workspace, mode), 'AGI 已启动'),
    stop: () =>
      runAction('stop', () => residentService.stop(workspace), 'AGI 已停止'),
    tick: () =>
      runAction('tick', () => residentService.tick(workspace, true), 'AGI 已完成一次刷新'),
    saveIdentity: (payload: ResidentIdentityPatch) =>
      runAction('save-identity', () => residentService.updateIdentity(workspace, payload), 'AGI 身份已更新'),
    createGoal: (payload: ResidentGoalDraft) =>
      runAction('create-goal', () => residentService.createGoal(workspace, payload), 'AGI 目标已创建'),
    approveGoal: (goalId: string, note = 'approved in AGI workspace') =>
      runAction('approve-goal', () => residentService.approveGoal(goalId, workspace, note), 'AGI 目标已批准'),
    rejectGoal: (goalId: string, note = 'rejected in AGI workspace') =>
      runAction('reject-goal', () => residentService.rejectGoal(goalId, workspace, note), 'AGI 目标已拒绝'),
    stageGoal: (goalId: string, promoteToPmRuntime = false) =>
      runAction<ResidentGoalStagePayload>(
        'stage-goal',
        () => residentService.stageGoal(goalId, workspace, promoteToPmRuntime),
        promoteToPmRuntime ? 'AGI 目标已写入 PM 运行态' : 'AGI 目标已暂存',
      ),
    runGoal: (goalId: string, runDirector = false, directorIterations = 1) =>
      runAction<ResidentGoalRunPayload>(
        'run-goal',
        () =>
          residentService.runGoal(goalId, workspace, {
            runDirector,
            directorIterations,
          }),
        'AGI 目标已送交 PM',
      ),
    extractSkills: () =>
      runAction<ResidentSkillPayload[]>(
        'extract-skills',
        () => residentService.extractSkills(workspace),
        'AGI 技能工坊已刷新',
      ),
    runExperiments: () =>
      runAction<ResidentExperimentPayload[]>(
        'run-experiments',
        () => residentService.runExperiments(workspace),
        'AGI 反事实实验已刷新',
      ),
    runImprovements: () =>
      runAction<ResidentImprovementPayload[]>(
        'run-improvements',
        () => residentService.runImprovements(workspace),
        'AGI 自改提案已刷新',
      ),

    // Phase 1.2: Goal Execution Projection (synced from WebSocket status)
    goalExecutions,
    getGoalExecution: (goalId: string) => goalExecutions.get(goalId),
  };
}

export type {
  ResidentDecisionPayload,
  ResidentExperimentPayload,
  ResidentGoalPayload,
  ResidentImprovementPayload,
  ResidentSkillPayload,
  ResidentStatusDetailsPayload,
};

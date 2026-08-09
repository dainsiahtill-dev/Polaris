import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { residentService } from "@/services/api";
function emptyDetails(workspace, liveResident) {
    if (!liveResident) {
        return null;
    }
    return {
        workspace: workspace || liveResident.workspace,
        identity: liveResident.identity,
        runtime: liveResident.runtime,
        agenda: liveResident.agenda,
        counts: liveResident.counts,
        decisions: liveResident.decisions ?? [],
        goals: liveResident.goals ?? [],
        insights: liveResident.insights ?? [],
        skills: liveResident.skills ?? [],
        experiments: liveResident.experiments ?? [],
        improvements: liveResident.improvements ?? [],
        capability_graph: liveResident.capability_graph ?? {
            generated_at: "",
            capabilities: [],
            gaps: [],
        },
        agi_capability_surface: liveResident.agi_capability_surface,
        agi_participation_policy: liveResident.agi_participation_policy,
        goal_executions: liveResident.goal_executions ?? [],
    };
}
export function useResident(options = {}) {
    const workspace = String(options.workspace || "").trim();
    const [status, setStatus] = useState(emptyDetails(workspace, options.liveResident));
    const [loading, setLoading] = useState(false);
    const [actionKey, setActionKey] = useState("");
    const [error, setError] = useState(null);
    const [agiAuditPack, setAgiAuditPack] = useState(null);
    const [agiEvidenceInterfaces, setAgiEvidenceInterfaces] = useState(null);
    const [agiHandoffs, setAgiHandoffs] = useState(null);
    const [agiRepairAdvisoryOverlay, setAgiRepairAdvisoryOverlay] = useState(null);
    const [agiActionCatalog, setAgiActionCatalog] = useState(null);
    const [lastAgiDecisionResult, setLastAgiDecisionResult] = useState(null);
    const [httpDetailsLoaded, setHttpDetailsLoaded] = useState(false);
    // Phase 1.2: Goal Execution Projection (synced from WebSocket status)
    const [goalExecutions, setGoalExecutions] = useState(new Map());
    const refresh = useCallback(async () => {
        if (!workspace) {
            setStatus(emptyDetails("", options.liveResident));
            setAgiAuditPack(null);
            setAgiEvidenceInterfaces(null);
            setAgiHandoffs(null);
            setAgiRepairAdvisoryOverlay(null);
            setAgiActionCatalog(null);
            setLastAgiDecisionResult(null);
            setHttpDetailsLoaded(false);
            setError(null);
            return null;
        }
        setLoading(true);
        const result = await residentService.getStatus(workspace, true);
        setLoading(false);
        if (!result.ok || !result.data) {
            const message = result.error || "加载 AGI 状态失败";
            setError(message);
            setHttpDetailsLoaded(false);
            return null;
        }
        setStatus(result.data);
        setHttpDetailsLoaded(true);
        const auditPackResult = await residentService.getAgiAuditPack(workspace, 12);
        setAgiAuditPack(auditPackResult.ok && auditPackResult.data ? auditPackResult.data : null);
        const evidenceInterfacesResult = await residentService.getAgiEvidenceInterfaces(workspace, {
            decisionType: "quality_gate_response",
            maxRuns: 20,
        });
        setAgiEvidenceInterfaces(evidenceInterfacesResult.ok && evidenceInterfacesResult.data
            ? evidenceInterfacesResult.data
            : null);
        const handoffsResult = await residentService.getAgiHandoffs(workspace, {
            limit: 50,
        });
        setAgiHandoffs(handoffsResult.ok && handoffsResult.data ? handoffsResult.data : null);
        const repairOverlayResult = await residentService.getAgiRepairAdvisoryOverlay(workspace, {
            limit: 50,
        });
        setAgiRepairAdvisoryOverlay(repairOverlayResult.ok && repairOverlayResult.data
            ? repairOverlayResult.data
            : null);
        const actionCatalogResult = await residentService.getAgiActionCatalog();
        setAgiActionCatalog(actionCatalogResult.ok && actionCatalogResult.data
            ? actionCatalogResult.data
            : null);
        setError(null);
        return result.data;
    }, [options.liveResident, workspace]);
    const runAction = useCallback(async (key, action, successMessage) => {
        if (!workspace) {
            toast.error("请先选择 Workspace");
            return null;
        }
        setActionKey(key);
        const result = await action();
        setActionKey("");
        if (!result.ok) {
            const message = result.error || "AGI 操作失败";
            setError(message);
            toast.error(message);
            return null;
        }
        setError(null);
        toast.success(successMessage);
        await refresh();
        return result.data ?? null;
    }, [refresh, workspace]);
    const refreshAgiEvidenceInterfaces = useCallback(async (decisionType = "quality_gate_response") => {
        if (!workspace) {
            toast.error("请先选择 Workspace");
            return null;
        }
        setActionKey("agi-evidence-interfaces");
        const result = await residentService.getAgiEvidenceInterfaces(workspace, {
            decisionType,
            maxRuns: 20,
        });
        setActionKey("");
        if (!result.ok || !result.data) {
            const message = result.error || "加载 AGI 证据接口失败";
            setError(message);
            toast.error(message);
            return null;
        }
        setAgiEvidenceInterfaces(result.data);
        setError(null);
        toast.success("AGI 证据接口已刷新");
        return result.data;
    }, [workspace]);
    useEffect(() => {
        if (!workspace) {
            setStatus(emptyDetails("", options.liveResident));
            setAgiAuditPack(null);
            setAgiEvidenceInterfaces(null);
            setAgiHandoffs(null);
            setAgiRepairAdvisoryOverlay(null);
            setAgiActionCatalog(null);
            setLastAgiDecisionResult(null);
            setHttpDetailsLoaded(false);
            setError(null);
            return;
        }
        void refresh();
    }, [options.liveResident, refresh, workspace]);
    // Phase 1.2: Sync goal executions from status (WebSocket)
    useEffect(() => {
        if (status?.goal_executions) {
            const newMap = new Map();
            status.goal_executions.forEach((exec) => {
                if (exec.goal_id) {
                    newMap.set(exec.goal_id, exec);
                }
            });
            setGoalExecutions(newMap);
        }
    }, [status?.goal_executions]);
    const summary = useMemo(() => status ?? emptyDetails(workspace, options.liveResident), [options.liveResident, status, workspace]);
    const residentRuntimeEvidence = useMemo(() => ({
        schema_version: "resident.runtime_projection_evidence.v1",
        realtime_channel: "runtime.v2.status.resident",
        snapshot_channel: "runtime.v2.status.snapshot",
        projection_field: "snapshot.resident",
        live_snapshot_available: Boolean(options.liveResident),
        http_details_loaded: httpDetailsLoaded,
        source: options.liveResident
            ? httpDetailsLoaded
                ? "runtime.v2_snapshot+http_details"
                : "runtime.v2_snapshot"
            : httpDetailsLoaded
                ? "http_details"
                : "unavailable",
    }), [httpDetailsLoaded, options.liveResident]);
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
        residentAgiCapabilitySurface: summary?.agi_capability_surface ?? null,
        residentAgiAuditPack: agiAuditPack,
        residentAgiEvidenceInterfaces: agiEvidenceInterfaces,
        residentAgiHandoffs: agiHandoffs,
        residentAgiRepairAdvisoryOverlay: agiRepairAdvisoryOverlay,
        residentAgiActionCatalog: agiActionCatalog,
        lastAgiDecisionResult,
        residentRuntimeEvidence,
        refresh,
        refreshAgiEvidenceInterfaces,
        refreshAgiActionCatalog: async () => {
            const result = await residentService.getAgiActionCatalog();
            if (!result.ok || !result.data) {
                const message = result.error || "加载 AGI 战术动作目录失败";
                setError(message);
                toast.error(message);
                return null;
            }
            setAgiActionCatalog(result.data);
            setError(null);
            return result.data;
        },
        isActing: (key) => actionKey === key,
        start: (mode) => runAction("start", () => residentService.start(workspace, mode), "AGI 已启动"),
        stop: () => runAction("stop", () => residentService.stop(workspace), "AGI 已停止"),
        tick: () => runAction("tick", () => residentService.tick(workspace, true), "AGI 已完成一次刷新"),
        runAgiDecision: async (payload) => {
            const result = await runAction("agi-decide", () => residentService.decide(workspace, payload), "AGI 决策已记录");
            setLastAgiDecisionResult(result);
            return result;
        },
        chatAgi: async (payload) => {
            if (!workspace) {
                toast.error("请先选择 Workspace");
                return null;
            }
            setActionKey("agi-chat");
            const result = await residentService.chat(workspace, payload);
            setActionKey("");
            if (!result.ok || !result.data) {
                const message = result.error || "AGI 战术控制台请求失败";
                setError(message);
                toast.error(message);
                return null;
            }
            setError(null);
            if (result.data.action_catalog) {
                setAgiActionCatalog(result.data.action_catalog);
            }
            return result.data;
        },
        executeAgiAction: async (payload) => {
            if (!workspace) {
                toast.error("请先选择 Workspace");
                return null;
            }
            setActionKey("agi-action");
            const result = await residentService.executeAgiAction(workspace, payload);
            setActionKey("");
            if (!result.ok || !result.data) {
                const message = result.error || "AGI 受控动作执行失败";
                setError(message);
                toast.error(message);
                return null;
            }
            setError(null);
            return result.data;
        },
        saveIdentity: (payload) => {
            const { resident_agi_participation: agiParticipation, ...identityPatch } = payload;
            const hasIdentityPatch = Object.values(identityPatch).some((value) => {
                if (Array.isArray(value)) {
                    return value.length > 0;
                }
                if (value && typeof value === "object") {
                    return Object.keys(value).length > 0;
                }
                return value !== undefined && value !== null && String(value).trim();
            });
            return runAction("save-identity", async () => {
                let identity = summary?.identity ?? null;
                if (hasIdentityPatch) {
                    const identityResult = await residentService.updateIdentity(workspace, identityPatch);
                    if (!identityResult.ok || !identityResult.data) {
                        return {
                            ok: false,
                            error: identityResult.error || "AGI 身份更新失败",
                        };
                    }
                    identity = identityResult.data;
                }
                if (agiParticipation) {
                    const participationResult = await residentService.updateAgiParticipation(workspace, agiParticipation);
                    if (!participationResult.ok || !participationResult.data) {
                        return {
                            ok: false,
                            error: participationResult.error || "AGI 参与策略更新失败",
                        };
                    }
                    return {
                        ok: true,
                        data: {
                            ...(identity || {}),
                            resident_agi_participation: participationResult.data,
                        },
                    };
                }
                return { ok: true, data: identity ?? undefined };
            }, "AGI 身份已更新");
        },
        createGoal: (payload) => runAction("create-goal", () => residentService.createGoal(workspace, payload), "AGI 目标已创建"),
        recordDecision: (payload) => runAction("record-decision", () => residentService.recordDecision(workspace, payload), "AGI 决策已记录"),
        approveGoal: (goalId, note = "approved in AGI workspace") => runAction("approve-goal", () => residentService.approveGoal(goalId, workspace, note), "AGI 目标已批准"),
        rejectGoal: (goalId, note = "rejected in AGI workspace") => runAction("reject-goal", () => residentService.rejectGoal(goalId, workspace, note), "AGI 目标已拒绝"),
        materializeGoal: (goalId) => runAction("materialize-goal", () => residentService.materializeGoal(goalId, workspace), "AGI 目标已固化"),
        stageGoal: (goalId, promoteToPmRuntime = false) => runAction("stage-goal", () => residentService.stageGoal(goalId, workspace, promoteToPmRuntime), promoteToPmRuntime ? "AGI 目标已写入 PM 运行态" : "AGI 目标已暂存"),
        runGoal: (goalId, runDirector = true, directorIterations = 1) => runAction("run-goal", () => residentService.runGoal(goalId, workspace, {
            runDirector,
            directorIterations,
        }), "AGI 目标已送交 PM"),
        extractSkills: () => runAction("extract-skills", () => residentService.extractSkills(workspace), "AGI 技能工坊已刷新"),
        runExperiments: () => runAction("run-experiments", () => residentService.runExperiments(workspace), "AGI 反事实实验已刷新"),
        runImprovements: () => runAction("run-improvements", () => residentService.runImprovements(workspace), "AGI 自改提案已刷新"),
        // Phase 1.2: Goal Execution Projection (synced from WebSocket status)
        goalExecutions,
        getGoalExecution: (goalId) => goalExecutions.get(goalId),
    };
}

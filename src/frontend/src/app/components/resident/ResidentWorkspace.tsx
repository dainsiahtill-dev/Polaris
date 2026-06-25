import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  ArrowLeft,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  FileText,
  Play,
  Plus,
  RefreshCw,
  Settings,
  Square,
  Target,
  X,
  FileSearch,
  Sparkles,
  FlaskConical,
  Wrench,
  Ban,
  Package,
  Pencil,
} from 'lucide-react';

import { EvidenceViewer } from './EvidenceViewer';
import { ExecutionProgressBar } from './ExecutionProgressBar';

import { useResident } from '@/hooks/useResident';
import type {
  ResidentAgiAuditPackPayload,
  ResidentAgiAuthorityMatrixPayload,
  ResidentAgiCapabilityPayload,
  ResidentAgiDecisionBoundaryPayload,
  ResidentDecisionPayload,
  ResidentGoalPayload,
  ResidentStatusDetailsPayload,
} from '@/app/types/appContracts';
import { Button } from '@/app/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/app/components/ui/card';
import { Input } from '@/app/components/ui/input';
import { Textarea } from '@/app/components/ui/textarea';
import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';

const TAB_OPTIONS = ['overview', 'goals', 'decisions', 'evolution'] as const;
type AgiTab = (typeof TAB_OPTIONS)[number];

interface CapabilityGovernanceStats {
  readOnly: number;
  governedMutation: number;
  highRisk: number;
  categories: string[];
  contractRefs: string[];
  chainRequired: boolean;
}

interface ResidentWorkspaceProps {
  workspace: string;
  onBackToMain: () => void;
  residentSnapshot?: ResidentStatusDetailsPayload | null;
  initialTab?: AgiTab;
}

function formatTime(value?: string | null): string {
  if (!value) return '暂无';
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  const date = new Date(parsed);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes}分钟前`;
  if (hours < 24) return `${hours}小时前`;
  if (days < 7) return `${days}天前`;
  return date.toLocaleDateString();
}

function GoalStatusBadge({ status }: { status: string }) {
  const token = status.toLowerCase();
  if (token === 'approved' || token === 'materialized') {
    return <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20">已批准</Badge>;
  }
  if (token === 'rejected') {
    return <Badge className="bg-red-500/10 text-red-400 border-red-500/20">已拒绝</Badge>;
  }
  return <Badge className="bg-amber-500/10 text-amber-400 border-amber-500/20">待审批</Badge>;
}

export function ResidentWorkspace({
  workspace,
  onBackToMain,
  residentSnapshot = null,
  initialTab = 'overview',
}: ResidentWorkspaceProps) {
  const resident = useResident({ workspace, liveResident: residentSnapshot });
  const [activeTab, setActiveTab] = useState<AgiTab>(initialTab);
  const [showNewGoal, setShowNewGoal] = useState(initialTab === 'goals');
  const [expandedGoal, setExpandedGoal] = useState<string | null>(null);

  // New goal form state
  const [newGoalTitle, setNewGoalTitle] = useState('');
  const [newGoalDesc, setNewGoalDesc] = useState('');
  const [agiDecisionObjective, setAgiDecisionObjective] = useState(
    '审计当前运行证据，判断是否允许进入下一步。',
  );

  // Identity edit state
  const [editingIdentity, setEditingIdentity] = useState(false);
  const [identityName, setIdentityName] = useState('');
  const [identityMission, setIdentityMission] = useState('');

  const isActive = Boolean(resident.residentRuntime?.active);
  const mode = resident.residentRuntime?.mode || 'observe';
  const runtimeEvidence = resident.residentRuntimeEvidence;

  // Current focus - simplified
  const currentFocus = resident.residentAgenda?.current_focus?.[0] || null;
  const pendingGoals = resident.goals.filter(g => g.status === 'pending');
  const approvedGoals = resident.goals.filter(g => g.status === 'approved' || g.status === 'materialized');
  const latestInsight = resident.residentInsights?.[0] || null;
  const capabilities = resident.residentCapabilityGraph?.capabilities || [];
  const agiCapabilitySurface = resident.residentAgiCapabilitySurface;
  const agiAuditPack = resident.residentAgiAuditPack;
  const agiAuthorityMatrix = agiAuditPack?.authority_matrix || agiCapabilitySurface?.authority_matrix;
  const agiCapabilities = agiCapabilitySurface?.items || [];
  const agiDecisionBoundaries = agiCapabilitySurface?.decision_boundaries || [];
  const decisionStats = useMemo(() => buildDecisionStats(resident.decisions), [resident.decisions]);
  const capabilityGovernance = useMemo(
    () => buildCapabilityGovernanceStats(agiCapabilities),
    [agiCapabilities],
  );

  const handleCreateGoal = async () => {
    if (!newGoalTitle.trim()) return;
    const created = await resident.createGoal({
      title: newGoalTitle.trim(),
      goal_type: 'maintenance',
      motivation: newGoalDesc.trim(),
      source: 'manual',
      scope: [],
      evidence_refs: [],
    });
    if (created) {
      setNewGoalTitle('');
      setNewGoalDesc('');
      setShowNewGoal(false);
    }
  };

  const handleRunAgiDecision = async () => {
    const objective = agiDecisionObjective.trim();
    if (!objective) return;
    const latestDecision = resident.decisions[0] || null;
    await resident.runAgiDecision({
      decision_type: 'platform_supervision',
      objective,
      evidence: {
        workspace,
        runtime_active: isActive,
        mode,
        goal_count: resident.goals.length,
        decision_count: resident.decisions.length,
        latest_decision_id: latestDecision?.decision_id || '',
        latest_verdict: latestDecision?.verdict || '',
        resident_agi_audit_pack_loaded: Boolean(agiAuditPack),
        resident_agi_audit_pack_schema: agiAuditPack?.schema_version || '',
        resident_agi_available: Boolean(agiAuditPack?.role_registry?.resident_agi_available),
        resident_agi_hard_rule_gate_status: agiAuditPack?.hard_rule_gate?.status || '',
        resident_agi_evidence_gate_status: agiAuditPack?.evidence_gate?.status || '',
        resident_agi_evidence_gate_recommended_verdict: agiAuditPack?.evidence_gate?.recommended_verdict || '',
        resident_agi_authority_matrix_schema: agiAuthorityMatrix?.schema_version || '',
        resident_agi_chain_required: Boolean(agiAuthorityMatrix?.chain_required),
      },
      constraints: [
        'preserve_pm_chief_engineer_director_qa_chain',
        'request_evidence_or_block_when_context_is_insufficient',
      ],
      candidate_actions: ['continue', 'block', 'request_evidence', 'escalate'],
      context_refs: latestDecision?.context_refs || [],
      evidence_refs: latestDecision?.evidence_refs || [],
      confidence: latestDecision ? 0.7 : 0.5,
      include_audit_pack: true,
      audit_pack_decision_limit: 12,
    });
  };

  return (
    <div data-testid="resident-workspace" className="flex h-full flex-col bg-slate-950 text-slate-100">
      {/* Simplified Header */}
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={onBackToMain} className="text-slate-400 hover:text-white">
            <ArrowLeft className="size-4" />
          </Button>
          <div className="flex items-center gap-2">
            <Bot className="size-5 text-cyan-400" />
            <span className="font-medium">AGI 工作区</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Badge variant="outline" className={cn(
            isActive ? 'border-emerald-500/30 text-emerald-400' : 'border-slate-600 text-slate-400'
          )}>
            {isActive ? '运行中' : '已停止'}
          </Badge>
          {isActive ? (
            <Button size="sm" variant="destructive" onClick={() => void resident.stop()}>
              <Square className="mr-1 size-3" />
              停止
            </Button>
          ) : (
            <Button size="sm" onClick={() => void resident.start(mode)} className="bg-cyan-500 text-black hover:bg-cyan-400">
              <Play className="mr-1 size-3" />
              启动
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            data-testid="resident-tick"
            title="立即运行一轮反思 (Tick)：元认知 / 技能 / 反事实 / 自改 / 目标生成"
            onClick={() => void resident.tick()}
            disabled={resident.isActing('tick')}
            className="border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/10"
          >
            <Brain className={cn('mr-1 size-3', resident.isActing('tick') && 'animate-pulse')} />
            反思一轮
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void resident.refresh()} disabled={resident.loading}>
            <RefreshCw className={cn("size-4", resident.loading && "animate-spin")} />
          </Button>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 overflow-auto p-4">
        {/* Current Status Card - Always visible */}
        <Card className="mb-4 border-slate-800 bg-slate-900/50">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
              <Clock className="size-4 text-cyan-400" />
              当前状态
            </CardTitle>
          </CardHeader>
          <CardContent>
            {currentFocus ? (
              <div className="space-y-2">
                <div className="text-lg font-medium text-white">{currentFocus}</div>
                <div className="flex items-center gap-4 text-sm text-slate-400">
                  <span>模式: {mode}</span>
                  <span>上次更新: {formatTime(resident.residentRuntime?.last_tick_at)}</span>
                </div>
                <div
                  className="mt-2 inline-flex max-w-full flex-wrap items-center gap-1 rounded border border-cyan-500/15 bg-slate-950/70 px-2 py-1 font-mono text-[10px] text-cyan-200/80"
                  data-testid="resident-runtime-evidence"
                >
                  <span>{runtimeEvidence?.schema_version || 'resident.runtime_projection_evidence.v1'}</span>
                  <span>· {runtimeEvidence?.realtime_channel || 'runtime.v2.status.snapshot'}</span>
                  <span>· {runtimeEvidence?.projection_field || 'snapshot.resident'}</span>
                  <span>· {runtimeEvidence?.source || 'unavailable'}</span>
                </div>
              </div>
            ) : (
              <div className="text-slate-500">AGI 尚未设置当前焦点</div>
            )}
          </CardContent>
        </Card>

        {/* Stats Row */}
        <div className="mb-4 grid grid-cols-3 gap-3">
          <Card className="border-slate-800 bg-slate-900/50 p-3">
            <div className="text-2xl font-semibold text-white">{resident.goals.length}</div>
            <div className="text-xs text-slate-400">目标总数</div>
          </Card>
          <Card className="border-slate-800 bg-slate-900/50 p-3">
            <div className="text-2xl font-semibold text-emerald-400">{approvedGoals.length}</div>
            <div className="text-xs text-slate-400">已批准</div>
          </Card>
          <Card className="border-slate-800 bg-slate-900/50 p-3">
            <div className="text-2xl font-semibold text-amber-400">{pendingGoals.length}</div>
            <div className="text-xs text-slate-400">待审批</div>
          </Card>
        </div>

        {/* Tabs */}
        <div className="mb-4 flex gap-1 border-b border-slate-800">
          {[
            { key: 'overview', label: '概览' },
            { key: 'goals', label: '目标' },
            { key: 'decisions', label: '决策' },
            { key: 'evolution', label: '进化' },
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key as AgiTab)}
              data-testid={`resident-tab-${tab.key}`}
              className={cn(
                'px-4 py-2 text-sm font-medium transition-colors',
                activeTab === tab.key
                  ? 'border-b-2 border-cyan-400 text-cyan-400'
                  : 'text-slate-400 hover:text-slate-200'
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'overview' && (
          <div className="space-y-3">
            <div className="grid gap-3 lg:grid-cols-2">
              <Card className="border-slate-800 bg-slate-900/50">
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
                    <Bot className="size-4 text-cyan-400" />
                    AGI 身份
                  </CardTitle>
                  {!editingIdentity && (
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid="resident-edit-identity"
                      onClick={() => {
                        setIdentityName(resident.residentIdentity?.name || '');
                        setIdentityMission(resident.residentIdentity?.mission || '');
                        setEditingIdentity(true);
                      }}
                    >
                      <Pencil className="size-3" />
                    </Button>
                  )}
                </CardHeader>
                <CardContent>
                  {editingIdentity ? (
                    <div className="space-y-2">
                      <Input
                        aria-label="AGI 名称"
                        value={identityName}
                        onChange={(e) => setIdentityName(e.target.value)}
                        placeholder="名称"
                        className="bg-slate-950"
                      />
                      <Textarea
                        aria-label="AGI 任务宣言"
                        value={identityMission}
                        onChange={(e) => setIdentityMission(e.target.value)}
                        placeholder="任务宣言"
                        className="bg-slate-950"
                      />
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          data-testid="resident-save-identity"
                          disabled={resident.isActing('save-identity')}
                          onClick={async () => {
                            await resident.saveIdentity({
                              name: identityName.trim(),
                              mission: identityMission.trim(),
                            });
                            setEditingIdentity(false);
                          }}
                          className="bg-cyan-500 text-black hover:bg-cyan-400"
                        >
                          保存
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditingIdentity(false)}>
                          取消
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="text-base font-medium text-white">
                        {resident.residentIdentity?.name || 'Resident AGI Supervisor'}
                      </div>
                      <div className="mt-1 text-sm text-slate-400">
                        {resident.residentIdentity?.mission || '尚未设定任务宣言'}
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>

              <Card className="border-slate-800 bg-slate-900/50">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
                    <FileSearch className="size-4 text-cyan-400" />
                    最新元认知
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {latestInsight ? (
                    <div className="space-y-1">
                      <div className="text-sm font-medium text-white">{latestInsight.summary}</div>
                      <div className="text-xs text-slate-500">
                        {latestInsight.strategy_tag || latestInsight.insight_type || '未分类'} · 置信度{' '}
                        {Math.round((latestInsight.confidence ?? 0) * 100)}%
                      </div>
                    </div>
                  ) : (
                    <div className="text-sm text-slate-500">暂无元认知记录</div>
                  )}
                </CardContent>
              </Card>
            </div>

            {capabilities.length > 0 && (
              <Card className="border-slate-800 bg-slate-900/50">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm text-slate-300">能力图谱</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {capabilities.slice(0, 4).map((capability) => (
                      <div key={capability.capability_id} className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
                        <div className="text-sm font-medium text-slate-200">{capability.name}</div>
                        <div className="mt-1 text-xs text-slate-500">
                          成功率 {Math.round((capability.success_rate ?? 0) * 100)}% · 证据 {capability.evidence_count ?? 0}
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}

            <Card className="border-slate-800 bg-slate-900/50">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
                  <Brain className="size-4 text-cyan-400" />
                  AGI Role 能力面
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid gap-2 sm:grid-cols-3">
                  <CapabilityMetric label="Role" value={agiCapabilitySurface?.role_id || 'resident_agi'} />
                  <CapabilityMetric label="Runtime" value={agiCapabilitySurface?.runtime_foundation || 'RoleRuntime / ContextOS / TurnEngine'} />
                  <CapabilityMetric label="Capabilities" value={String(agiCapabilitySurface?.count ?? agiCapabilities.length)} />
                </div>
                <div
                  className="mt-3 rounded-lg border border-cyan-500/15 bg-slate-950/70 px-3 py-2 text-xs text-slate-300"
                  data-testid="resident-agi-role-foundation"
                >
                  <span className="font-mono text-cyan-200">resident_agi</span>{' '}
                  运行在同一 RoleRuntime / ContextOS / TurnEngine 底座上；平台级证据访问更宽，但执行必须服从硬规则、能力目录、
                  canonical contract 和 PM → Chief Engineer → Director。
                </div>
                <CapabilityGovernanceMatrix
                  stats={capabilityGovernance}
                  authorityMatrix={agiAuthorityMatrix}
                  runtimeFoundation={agiCapabilitySurface?.runtime_foundation || 'roles.runtime + ContextOS + TurnEngine'}
                />
                <DecisionBoundaryMatrix
                  schema={agiCapabilitySurface?.decision_boundary_schema}
                  boundaries={agiDecisionBoundaries}
                />
                <AgiAuditPackPanel pack={agiAuditPack} />
                <div className="mt-3 grid gap-2 lg:grid-cols-2">
                  {agiCapabilities.slice(0, 6).map((capability) => (
                    <div
                      key={capability.capability_id || capability.name}
                      className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-slate-200">{capability.name || '未命名能力'}</span>
                        <span className="shrink-0 text-[10px] uppercase text-slate-500">{capability.access || 'read_only'}</span>
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
                        {[capability.category, capability.contract_ref].filter(Boolean).join(' · ')}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {capability.risk_level && (
                          <span className={cn(
                            'rounded border px-1.5 py-0.5 text-[10px]',
                            capability.risk_level === 'high'
                              ? 'border-amber-500/20 bg-amber-500/10 text-amber-300'
                              : 'border-slate-700 bg-slate-900 text-slate-400',
                          )}>
                            risk {capability.risk_level}
                          </span>
                        )}
                        {(capability.guardrails || []).slice(0, 1).map((guardrail) => (
                          <span key={guardrail} className="truncate rounded border border-cyan-700/40 px-1.5 py-0.5 text-[10px] text-cyan-200/80" title={guardrail}>
                            {guardrail}
                          </span>
                        ))}
                        {(capability.evidence_refs || []).slice(0, 1).map((evidenceRef) => (
                          <span key={evidenceRef} className="truncate rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400" title={evidenceRef}>
                            {evidenceRef}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                  {agiCapabilities.length === 0 && (
                    <div className="rounded-lg border border-dashed border-slate-700 p-4 text-sm text-slate-500">
                      暂无能力面投影
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Recent Goals */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-slate-300">最近目标</h3>
                <button
                  onClick={() => setActiveTab('goals')}
                  className="text-xs text-cyan-400 hover:text-cyan-300"
                >
                  查看全部 →
                </button>
              </div>
              {resident.goals.slice(0, 3).map((goal) => (
                <GoalItem
                  key={goal.goal_id}
                  goal={goal}
                  execution={goal.goal_id ? resident.getGoalExecution?.(goal.goal_id) : undefined}
                  expanded={expandedGoal === goal.goal_id}
                  onToggle={() => setExpandedGoal(expandedGoal === goal.goal_id ? null : goal.goal_id || null)}
                  onApprove={() => void resident.approveGoal(String(goal.goal_id))}
                  onReject={() => void resident.rejectGoal(String(goal.goal_id))}
                  onMaterialize={() => void resident.materializeGoal(String(goal.goal_id))}
                  onStage={() => void resident.stageGoal(String(goal.goal_id), false)}
                  onPromoteToPm={() => void resident.stageGoal(String(goal.goal_id), true)}
                  onRun={() => void resident.runGoal(String(goal.goal_id), false, 1)}
                  disabled={Boolean(resident.actionKey)}
                />
              ))}
              {resident.goals.length === 0 && (
                <div className="rounded-lg border border-dashed border-slate-700 p-4 text-center text-sm text-slate-500">
                  暂无目标，点击"目标"标签创建
                </div>
              )}
            </div>

            {/* Recent Decisions */}
            <div className="space-y-2 pt-2">
              <h3 className="text-sm font-medium text-slate-300">最近决策</h3>
              {resident.decisions.slice(0, 2).map((decision) => (
                <DecisionItem
                  key={decision.decision_id || decision.timestamp}
                  decision={decision}
                  workspace={workspace}
                />
              ))}
              {resident.decisions.length === 0 && (
                <div className="rounded-lg border border-dashed border-slate-700 p-4 text-center text-sm text-slate-500">
                  暂无决策记录
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'goals' && (
          <div className="space-y-3">
            {/* New Goal Button */}
            {!showNewGoal ? (
              <Button
                variant="outline"
                className="w-full border-dashed border-slate-700 text-slate-400 hover:text-white"
                onClick={() => setShowNewGoal(true)}
              >
                <Plus className="mr-1 size-4" />
                新建目标
              </Button>
            ) : (
              <Card className="border-slate-800 bg-slate-900/50">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center justify-between text-sm">
                    <span>目标生成台</span>
                    <Button size="sm" variant="ghost" onClick={() => setShowNewGoal(false)}>
                      <X className="size-4" />
                    </Button>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <Input
                    aria-label="目标标题"
                    placeholder="目标标题"
                    value={newGoalTitle}
                    onChange={(e) => setNewGoalTitle(e.target.value)}
                    className="border-slate-700 bg-slate-950"
                  />
                  <Textarea
                    aria-label="目标描述"
                    placeholder="目标描述（可选）"
                    value={newGoalDesc}
                    onChange={(e) => setNewGoalDesc(e.target.value)}
                    className="border-slate-700 bg-slate-950"
                    rows={2}
                  />
                  <div className="flex gap-2">
                    <Button
                      onClick={handleCreateGoal}
                      disabled={!newGoalTitle.trim() || resident.isActing('create-goal')}
                      className="bg-cyan-500 text-black hover:bg-cyan-400"
                    >
                      创建 AGI 目标
                    </Button>
                    <Button variant="ghost" onClick={() => setShowNewGoal(false)}>
                      取消
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Goals List */}
            <div className="space-y-2">
              {resident.goals.map((goal) => (
                <GoalItem
                  key={goal.goal_id}
                  goal={goal}
                  execution={goal.goal_id ? resident.getGoalExecution?.(goal.goal_id) : undefined}
                  expanded={expandedGoal === goal.goal_id}
                  onToggle={() => setExpandedGoal(expandedGoal === goal.goal_id ? null : goal.goal_id || null)}
                  onApprove={() => void resident.approveGoal(String(goal.goal_id))}
                  onReject={() => void resident.rejectGoal(String(goal.goal_id))}
                  onMaterialize={() => void resident.materializeGoal(String(goal.goal_id))}
                  onStage={() => void resident.stageGoal(String(goal.goal_id), false)}
                  onPromoteToPm={() => void resident.stageGoal(String(goal.goal_id), true)}
                  onRun={() => void resident.runGoal(String(goal.goal_id), false, 1)}
                  disabled={Boolean(resident.actionKey)}
                />
              ))}
              {resident.goals.length === 0 && !showNewGoal && (
                <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-500">
                  暂无目标，点击上方按钮创建
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'decisions' && (
          <div className="space-y-3">
            <Card className="border-slate-800 bg-slate-900/50" data-testid="resident-agi-decision-turn">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center justify-between gap-2 text-sm text-slate-300">
                  <span className="flex items-center gap-2">
                    <Brain className="size-4 text-cyan-400" />
                    AGI 决策回合
                  </span>
                  <Badge className="border-cyan-500/20 bg-cyan-500/10 text-cyan-200">resident_agi</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <Textarea
                  aria-label="AGI 决策目标"
                  value={agiDecisionObjective}
                  onChange={(event) => setAgiDecisionObjective(event.target.value)}
                  className="min-h-20 border-slate-700 bg-slate-950"
                />
                <div className="flex items-center justify-end">
                  <Button
                    size="sm"
                    data-testid="resident-run-agi-decision"
                    disabled={!agiDecisionObjective.trim() || resident.isActing('agi-decide')}
                    onClick={() => void handleRunAgiDecision()}
                    className="bg-cyan-500 text-black hover:bg-cyan-400"
                  >
                    <Brain className={cn('mr-1 size-3', resident.isActing('agi-decide') && 'animate-pulse')} />
                    运行决策
                  </Button>
                </div>
              </CardContent>
            </Card>
            <DecisionAuditSummary stats={decisionStats} />
            <div className="space-y-2">
              {resident.decisions.map((decision) => (
                <DecisionItem
                  key={decision.decision_id || decision.timestamp}
                  decision={decision}
                  workspace={workspace}
                />
              ))}
            </div>
            {resident.decisions.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-500">
                暂无决策记录
              </div>
            )}
          </div>
        )}

        {activeTab === 'evolution' && (
          <div className="space-y-4">
            {/* Skill Foundry */}
            <EvolutionSection
              icon={<Sparkles className="size-4 text-cyan-400" />}
              title="技能工坊"
              count={resident.residentSkills.length}
              actionLabel="提炼技能"
              actionTestId="resident-extract-skills"
              onAction={() => void resident.extractSkills()}
              acting={resident.isActing('extract-skills')}
              emptyHint="尚无技能（运行一轮反思后生成）"
            >
              {resident.residentSkills.map((skill, idx) => (
                <div key={skill.skill_id || idx} className="rounded border border-slate-800 bg-slate-950/50 p-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-200">{skill.name || '未命名技能'}</span>
                    <span className="text-xs text-slate-500">
                      v{skill.version ?? 1} · {Math.round((skill.confidence ?? 0) * 100)}%
                    </span>
                  </div>
                  {skill.trigger && <div className="mt-1 text-xs text-slate-400">触发: {skill.trigger}</div>}
                </div>
              ))}
            </EvolutionSection>

            {/* Counterfactual Lab */}
            <EvolutionSection
              icon={<FlaskConical className="size-4 text-cyan-400" />}
              title="反事实实验"
              count={resident.residentExperiments.length}
              actionLabel="运行实验"
              actionTestId="resident-run-experiments"
              onAction={() => void resident.runExperiments()}
              acting={resident.isActing('run-experiments')}
              emptyHint="尚无实验（需有失败决策作为输入）"
            >
              {resident.residentExperiments.map((exp, idx) => (
                <div key={exp.experiment_id || idx} className="rounded border border-slate-800 bg-slate-950/50 p-2">
                  <div className="text-sm text-slate-200">
                    {(exp.baseline_strategy || '基线') + ' → ' + (exp.counterfactual_strategy || '反事实')}
                  </div>
                  {exp.recommendation && <div className="mt-1 text-xs text-slate-400">建议: {exp.recommendation}</div>}
                  {exp.status && <div className="mt-1 text-xs text-slate-500">状态: {exp.status}</div>}
                </div>
              ))}
            </EvolutionSection>

            {/* Self-Improvement Lab */}
            <EvolutionSection
              icon={<Wrench className="size-4 text-cyan-400" />}
              title="自改提案"
              count={resident.residentImprovements.length}
              actionLabel="生成提案"
              actionTestId="resident-run-improvements"
              onAction={() => void resident.runImprovements()}
              acting={resident.isActing('run-improvements')}
              emptyHint="尚无提案（需有高分实验作为输入）"
            >
              {resident.residentImprovements.map((imp, idx) => (
                <div key={imp.improvement_id || idx} className="rounded border border-slate-800 bg-slate-950/50 p-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-slate-200">{imp.title || '未命名提案'}</span>
                    {imp.status && <span className="text-xs text-slate-500">{imp.status}</span>}
                  </div>
                  {(imp.category || imp.target_surface) && (
                    <div className="mt-1 text-xs text-slate-400">
                      {[imp.category, imp.target_surface].filter(Boolean).join(' · ')}
                    </div>
                  )}
                </div>
              ))}
            </EvolutionSection>
          </div>
        )}
      </div>
    </div>
  );
}

// Evolution section: skill / experiment / improvement list with a run action
function EvolutionSection({
  icon,
  title,
  count,
  actionLabel,
  actionTestId,
  onAction,
  acting,
  emptyHint,
  children,
}: {
  icon: ReactNode;
  title: string;
  count: number;
  actionLabel: string;
  actionTestId: string;
  onAction: () => void;
  acting: boolean;
  emptyHint: string;
  children: ReactNode;
}) {
  return (
    <Card className="border-slate-800 bg-slate-900/50">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="flex items-center gap-2 text-sm text-slate-300">
          {icon}
          {title} ({count})
        </CardTitle>
        <Button
          size="sm"
          variant="outline"
          data-testid={actionTestId}
          onClick={onAction}
          disabled={acting}
        >
          {actionLabel}
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        {count === 0 ? <div className="text-xs text-slate-500">{emptyHint}</div> : children}
      </CardContent>
    </Card>
  );
}

function CapabilityMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.18em] text-slate-500">{label}</div>
      <div className="mt-1 truncate text-xs font-medium text-slate-200" title={value}>
        {value}
      </div>
    </div>
  );
}

function buildCapabilityGovernanceStats(capabilities: ResidentAgiCapabilityPayload[]): CapabilityGovernanceStats {
  const categories = new Set<string>();
  const contractRefs = new Set<string>();
  let readOnly = 0;
  let governedMutation = 0;
  let highRisk = 0;
  let chainRequired = false;

  for (const capability of capabilities) {
    const access = String(capability.access || '').toLowerCase();
    const risk = String(capability.risk_level || '').toLowerCase();
    const category = String(capability.category || '').trim();
    const contractRef = String(capability.contract_ref || '').trim();

    if (category) categories.add(category);
    if (contractRef) contractRefs.add(contractRef);
    if (access === 'read_only') readOnly += 1;
    if (access.includes('write') || access.includes('execute')) governedMutation += 1;
    if (risk === 'high') highRisk += 1;
    if (access.includes('pm_ce_director') || contractRef.includes('goal_bridge')) chainRequired = true;
  }

  return {
    readOnly,
    governedMutation,
    highRisk,
    categories: Array.from(categories).sort(),
    contractRefs: Array.from(contractRefs).sort(),
    chainRequired,
  };
}

function CapabilityGovernanceMatrix({
  stats,
  authorityMatrix,
  runtimeFoundation,
}: {
  stats: CapabilityGovernanceStats;
  authorityMatrix?: ResidentAgiAuthorityMatrixPayload;
  runtimeFoundation: string;
}) {
  const chainLabel = authorityMatrix?.chain_required
    ? authorityMatrix.chain || 'PM → Chief Engineer → Director'
    : stats.chainRequired
      ? 'PM → Chief Engineer → Director'
      : '只读/观察优先';
  const counts = authorityMatrix?.counts || {};
  const readOnly = counts.read_only_capabilities ?? stats.readOnly;
  const governedOps = counts.governed_operation_capabilities ?? stats.governedMutation;
  const highRisk = counts.high_risk_capabilities ?? stats.highRisk;
  const contracts = counts.canonical_contracts ?? stats.contractRefs.length;
  const contractRefs = authorityMatrix?.canonical_contracts || stats.contractRefs;
  const policy = authorityMatrix?.decision_policy || {};
  return (
    <div
      className="mt-3 rounded-lg border border-cyan-500/15 bg-cyan-500/[0.04] px-3 py-2"
      data-testid="resident-agi-governance-matrix"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-cyan-100">能力治理矩阵</div>
          <div className="mt-0.5 text-[10px] text-slate-500">
            底座: {authorityMatrix?.runtime_foundation || runtimeFoundation}
          </div>
        </div>
        <Badge className="border-cyan-500/20 bg-cyan-500/10 text-cyan-200">
          {chainLabel}
        </Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric label="Read only" value={String(readOnly)} />
        <CapabilityMetric label="Governed ops" value={String(governedOps)} />
        <CapabilityMetric label="High risk" value={String(highRisk)} />
        <CapabilityMetric label="Contracts" value={String(contracts)} />
      </div>
      {authorityMatrix && (
        <div
          className="mt-2 rounded border border-cyan-500/10 bg-slate-950/50 px-2 py-1 font-mono text-[10px] text-cyan-100/80"
          data-testid="resident-agi-authority-matrix"
        >
          {authorityMatrix.schema_version || 'resident.agi_authority_matrix.v1'} · hard rules{' '}
          {counts.platform_hard_rules ?? 0} · AGI judgement {counts.agi_recommendations ?? 0} · governed execution{' '}
          {counts.governed_execution_boundaries ?? 0}
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-1" data-testid="resident-agi-governance-tags">
        {stats.categories.slice(0, 8).map((category) => (
          <span key={category} className="rounded bg-slate-950/80 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
            {category}
          </span>
        ))}
        {contractRefs.slice(0, 6).map((contractRef) => (
          <span key={contractRef} className="rounded border border-slate-700 bg-slate-950/50 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
            {contractRef}
          </span>
        ))}
        {Object.values(policy).slice(0, 3).map((policyValue) => (
          <span key={policyValue} className="rounded border border-cyan-700/40 bg-cyan-950/20 px-1.5 py-0.5 font-mono text-[10px] text-cyan-200/80">
            {policyValue}
          </span>
        ))}
      </div>
    </div>
  );
}

function boundaryAuthorityLabel(authority?: string): string {
  const normalized = String(authority || '').toLowerCase();
  if (normalized === 'platform_hard_rule') return '平台硬规则';
  if (normalized === 'agi_governed_execution') return 'AGI 受控执行';
  if (normalized === 'agi_recommendation') return 'AGI 智能判断';
  return authority || '未分类';
}

function boundaryAuthorityClass(authority?: string): string {
  const normalized = String(authority || '').toLowerCase();
  if (normalized === 'platform_hard_rule') return 'border-rose-500/20 bg-rose-500/10 text-rose-300';
  if (normalized === 'agi_governed_execution') return 'border-amber-500/20 bg-amber-500/10 text-amber-300';
  if (normalized === 'agi_recommendation') return 'border-cyan-500/20 bg-cyan-500/10 text-cyan-200';
  return 'border-slate-700 bg-slate-900 text-slate-300';
}

function countBoundariesByAuthority(boundaries: ResidentAgiDecisionBoundaryPayload[], authority: string): number {
  return boundaries.filter((boundary) => boundary.authority === authority).length;
}

function DecisionBoundaryMatrix({
  schema,
  boundaries,
}: {
  schema?: string;
  boundaries: ResidentAgiDecisionBoundaryPayload[];
}) {
  if (boundaries.length === 0) return null;
  return (
    <div
      className="mt-3 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2"
      data-testid="resident-agi-decision-boundaries"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-slate-100">AGI 决策边界</div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {schema || 'resident.agi_decision_boundary.v1'}
          </div>
        </div>
        <div className="flex flex-wrap gap-1">
          <Badge className="border-rose-500/20 bg-rose-500/10 text-rose-300">
            硬规则 {countBoundariesByAuthority(boundaries, 'platform_hard_rule')}
          </Badge>
          <Badge className="border-cyan-500/20 bg-cyan-500/10 text-cyan-200">
            智能判断 {countBoundariesByAuthority(boundaries, 'agi_recommendation')}
          </Badge>
          <Badge className="border-amber-500/20 bg-amber-500/10 text-amber-300">
            受控执行 {countBoundariesByAuthority(boundaries, 'agi_governed_execution')}
          </Badge>
        </div>
      </div>
      <div className="mt-3 grid gap-2 lg:grid-cols-2">
        {boundaries.slice(0, 4).map((boundary) => (
          <div key={boundary.boundary_id || boundary.name} className="rounded border border-slate-800 bg-slate-900/50 px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium text-slate-200">{boundary.name || '未命名边界'}</span>
              <span className={cn('shrink-0 rounded border px-1.5 py-0.5 text-[10px]', boundaryAuthorityClass(boundary.authority))}>
                {boundaryAuthorityLabel(boundary.authority)}
              </span>
            </div>
            <div className="mt-1 line-clamp-2 text-[11px] text-slate-500" title={boundary.platform_hard_rule || ''}>
              硬约束: {boundary.platform_hard_rule || '未声明'}
            </div>
            <div className="mt-1 line-clamp-2 text-[11px] text-slate-400" title={boundary.agi_decision_scope || ''}>
              AGI: {boundary.agi_decision_scope || '未声明'}
            </div>
            {(boundary.evidence_required || []).length > 0 && (
              <div className="mt-1 truncate font-mono text-[10px] text-slate-500">
                evidence: {(boundary.evidence_required || []).slice(0, 3).join(', ')}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function AgiAuditPackPanel({ pack }: { pack?: ResidentAgiAuditPackPayload | null }) {
  if (!pack) {
    return (
      <div
        className="mt-3 rounded-lg border border-dashed border-slate-700 bg-slate-950/40 px-3 py-2 text-xs text-slate-500"
        data-testid="resident-agi-audit-pack"
      >
        AGI 审计包尚未加载
      </div>
    );
  }

  const roleRegistry = pack.role_registry;
  const missingRoles = roleRegistry?.missing_required_roles || [];
  const evidenceRefs = pack.evidence_refs || [];
  const recentDecisions = pack.recent_decisions || [];
  const constraints = pack.execution_constraints || [];
  const boundaryIds = pack.boundary_summary?.boundary_ids || [];
  const hardRuleGate = pack.hard_rule_gate;
  const hardRuleStatus = String(hardRuleGate?.status || 'unknown').toLowerCase();
  const hardRuleFailedChecks = hardRuleGate?.failed_check_ids || [];
  const evidenceGate = pack.evidence_gate;
  const evidenceGateStatus = String(evidenceGate?.status || 'unknown').toLowerCase();
  const runLedgerSummary = pack.run_ledger_summary;
  const authorityMatrix = pack.authority_matrix;
  const authorityCounts = authorityMatrix?.counts || {};
  const capabilityIds = (pack.capability_surface?.items || [])
    .map((capability) => capability.capability_id || '')
    .filter(Boolean);

  return (
    <div
      className="mt-3 rounded-lg border border-emerald-500/15 bg-emerald-500/[0.04] px-3 py-2"
      data-testid="resident-agi-audit-pack"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-xs font-medium text-emerald-100">AGI 审计包</div>
          <div className="mt-0.5 font-mono text-[10px] text-slate-500">
            {pack.schema_version || 'resident.agi_audit_pack.v1'}
          </div>
        </div>
        <Badge
          className={cn(
            hardRuleStatus === 'pass'
              ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
              : 'border-rose-500/20 bg-rose-500/10 text-rose-300',
          )}
        >
          Hard gate {hardRuleStatus}
        </Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <CapabilityMetric label="Dialogue roles" value={String(roleRegistry?.dialogue_roles?.length ?? 0)} />
        <CapabilityMetric label="Adapter roles" value={String(roleRegistry?.adapter_roles?.length ?? 0)} />
        <CapabilityMetric label="Evidence gate" value={evidenceGateStatus} />
        <CapabilityMetric label="Hard checks" value={String(hardRuleGate?.checks?.length ?? 0)} />
      </div>
      {authorityMatrix && (
        <div
          className="mt-2 rounded border border-emerald-500/10 bg-slate-950/70 px-2.5 py-2"
          data-testid="resident-agi-audit-authority-matrix"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[10px] uppercase text-slate-500">Authority matrix</div>
            <Badge className="border-emerald-500/20 bg-emerald-500/10 text-emerald-300">
              {authorityMatrix.chain_required ? authorityMatrix.chain || 'PM → Chief Engineer → Director' : 'read-only'}
            </Badge>
          </div>
          <div className="mt-1 font-mono text-[10px] text-slate-400">
            {authorityMatrix.schema_version || 'resident.agi_authority_matrix.v1'} · hard rules{' '}
            {authorityCounts.platform_hard_rules ?? 0} · AGI judgement{' '}
            {authorityCounts.agi_recommendations ?? 0} · governed ops{' '}
            {authorityCounts.governed_operation_capabilities ?? 0}
          </div>
        </div>
      )}
      <div className="mt-2 grid gap-2 lg:grid-cols-2">
        <div className="rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2">
          <div className="text-[10px] uppercase text-slate-500">Execution constraints</div>
          <div className="mt-1 space-y-1">
            {constraints.slice(0, 4).map((constraint) => (
              <div key={constraint} className="text-[11px] text-slate-300">
                {constraint}
              </div>
            ))}
          </div>
        </div>
        <div className="rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2">
          <div className="text-[10px] uppercase text-slate-500">Audit sources</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {(pack.truth_sources || []).slice(0, 6).map((source) => (
              <span key={source} className="rounded bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                {source}
              </span>
            ))}
            {boundaryIds.slice(0, 4).map((boundaryId) => (
              <span key={boundaryId} className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                {boundaryId}
              </span>
            ))}
            {capabilityIds.slice(0, 4).map((capabilityId) => (
              <span key={capabilityId} className="rounded border border-emerald-700/50 px-1.5 py-0.5 font-mono text-[10px] text-emerald-300/80">
                {capabilityId}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-2 rounded border border-slate-800 bg-slate-950/70 px-2.5 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[10px] uppercase text-slate-500">Evidence gate</div>
          <Badge
            className={cn(
              evidenceGateStatus === 'pass'
                ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
                : evidenceGateStatus === 'fail'
                  ? 'border-rose-500/20 bg-rose-500/10 text-rose-300'
                  : 'border-amber-500/20 bg-amber-500/10 text-amber-300',
            )}
          >
            {evidenceGateStatus} → {evidenceGate?.recommended_verdict || 'request_evidence'}
          </Badge>
        </div>
        <div className="mt-1 text-[11px] text-slate-400">{evidenceGate?.reason || '暂无证据门说明'}</div>
        <div className="mt-1 font-mono text-[10px] text-slate-500">
          Run Ledger {runLedgerSummary?.status || 'unknown'} · projected {runLedgerSummary?.projected ?? 0}/
          {runLedgerSummary?.total ?? 0} · failed {runLedgerSummary?.failed ?? 0} · ctx refs{' '}
          {evidenceGate?.context_snapshot_ref_count ?? evidenceRefs.length}
        </div>
      </div>
      {missingRoles.length > 0 && (
        <div className="mt-2 rounded border border-rose-500/20 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">
          missing roles: {missingRoles.join(', ')}
        </div>
      )}
      {hardRuleFailedChecks.length > 0 && (
        <div className="mt-2 rounded border border-rose-500/20 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">
          failed hard rules: {hardRuleFailedChecks.join(', ')}
        </div>
      )}
      <div className="mt-2 text-[10px] text-slate-500">
        recent decisions: {recentDecisions.length} · llm override:{' '}
        {hardRuleGate?.llm_override_allowed ? 'allowed' : 'blocked'}
      </div>
      {pack.decision_endpoint && (
        <div className="mt-2 font-mono text-[10px] text-slate-500">decision endpoint: {pack.decision_endpoint}</div>
      )}
    </div>
  );
}

// Simplified Goal Item
function GoalItem({
  goal,
  execution,
  expanded,
  onToggle,
  onApprove,
  onReject,
  onMaterialize,
  onStage,
  onPromoteToPm,
  onRun,
  disabled,
}: {
  goal: ResidentGoalPayload;
  execution?: import('@/app/types/appContracts').GoalExecutionView;
  expanded: boolean;
  onToggle: () => void;
  onApprove: () => void;
  onReject: () => void;
  onMaterialize: () => void;
  onStage: () => void;
  onPromoteToPm: () => void;
  onRun: () => void;
  disabled: boolean;
}) {
  const status = goal.status || 'pending';
  const isPending = status === 'pending';
  const isApproved = status === 'approved' || status === 'materialized';

  return (
    <Card className={cn('border-slate-800 bg-slate-900/50', expanded && 'border-slate-700')}>
      <div
        className="flex cursor-pointer items-center justify-between p-3"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3">
          {expanded ? <ChevronDown className="size-4 text-slate-400" /> : <ChevronRight className="size-4 text-slate-400" />}
          <div className="flex-1">
            <div className="font-medium text-slate-200">{goal.title || '未命名目标'}</div>
            {/* Phase 1.2: Execution Progress */}
            {execution ? (
              <div className="mt-1">
                <ExecutionProgressBar execution={execution} compact />
              </div>
            ) : (
              <div className="text-xs text-slate-500">{formatTime(goal.updated_at)}</div>
            )}
          </div>
        </div>
        <GoalStatusBadge status={status} />
      </div>

      {expanded && (
        <div className="border-t border-slate-800 px-3 pb-3">
          <div className="pt-3 text-sm text-slate-400">
            {goal.motivation || '暂无描述'}
          </div>
          {/* Phase 1.2: Full Execution Progress */}
          {execution && (
            <div className="mt-3 rounded bg-slate-950 p-3">
              <ExecutionProgressBar execution={execution} />
            </div>
          )}
          <div className="mt-3 flex gap-2">
            {isPending && (
              <>
                <Button size="sm" onClick={onApprove} disabled={disabled} className="bg-emerald-500 text-black hover:bg-emerald-400">
                  <CheckCircle2 className="mr-1 size-3" />
                  批准
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  data-testid="resident-reject-goal"
                  onClick={onReject}
                  disabled={disabled}
                  className="border-rose-500/30 text-rose-300 hover:bg-rose-500/10"
                >
                  <Ban className="mr-1 size-3" />
                  拒绝
                </Button>
              </>
            )}
            {isApproved && (
              <>
                {status === 'approved' && (
                  <Button
                    size="sm"
                    variant="outline"
                    data-testid="resident-materialize-goal"
                    onClick={onMaterialize}
                    disabled={disabled}
                  >
                    <Package className="mr-1 size-3" />
                    固化
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={onStage} disabled={disabled}>
                  暂存
                </Button>
                <Button size="sm" variant="outline" onClick={onPromoteToPm} disabled={disabled}>
                  写入 PM
                </Button>
                <Button size="sm" onClick={onRun} disabled={disabled} className="bg-cyan-500 text-black hover:bg-cyan-400">
                  <Play className="mr-1 size-3" />
                  交给 PM
                </Button>
              </>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function decisionString(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function decisionNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function decisionStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(decisionString).filter(Boolean);
}

function shortDecisionId(value?: string): string {
  const token = String(value || '').trim();
  if (!token) return '';
  if (token.length <= 14) return token;
  return `${token.slice(0, 10)}...${token.slice(-4)}`;
}

function formatConfidence(value?: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '暂无';
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function decisionHasEvidence(decision: ResidentDecisionPayload): boolean {
  return Boolean(
    decision.evidence_bundle_id ||
    (decision.evidence_refs || []).length > 0 ||
    (decision.context_refs || []).length > 0 ||
    (decision.affected_files || []).length > 0 ||
    (decision.affected_symbols || []).length > 0,
  );
}

function decisionHasHandoffImpact(decision: ResidentDecisionPayload): boolean {
  const haystack = [
    decision.stage,
    decision.goal_id,
    decision.task_id,
    ...(decision.strategy_tags || []),
    ...(decision.evidence_refs || []),
    decisionString(decision.actual_outcome?.pm_run_id),
    decisionString(decision.actual_outcome?.pm_contract_path),
    decisionString(decision.actual_outcome?.promoted_to_pm_runtime),
  ]
    .join(' ')
    .toLowerCase();
  return [
    'handoff',
    'goal_staging',
    'pm_bridge',
    'pm_runtime',
    'pm_contract',
    'chief_engineer',
    'director',
  ].some((token) => haystack.includes(token));
}

function buildDecisionStats(decisions: ResidentDecisionPayload[]) {
  const total = decisions.length;
  const evidenceBacked = decisions.filter(decisionHasEvidence).length;
  const handoffImpact = decisions.filter(decisionHasHandoffImpact).length;
  const blockedOrFailed = decisions.filter((decision) => {
    const verdict = String(decision.verdict || '').toLowerCase();
    const actual = decision.actual_outcome || {};
    const blockers = decisionStringList(actual.hard_rule_blockers);
    return verdict === 'failure' || verdict === 'blocked' || blockers.length > 0;
  }).length;
  return {
    total,
    evidenceBacked,
    handoffImpact,
    blockedOrFailed,
  };
}

function DecisionAuditSummary({ stats }: { stats: ReturnType<typeof buildDecisionStats> }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-slate-200">
            <FileSearch className="size-4 text-cyan-400" />
            决策审计面
          </div>
          <div className="mt-1 text-xs text-slate-500">source of truth: decision_trace.jsonl</div>
        </div>
        <Badge className="border-cyan-500/20 bg-cyan-500/10 text-cyan-200">resident.decision_event.v1</Badge>
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-4">
        <DecisionMetric label="Decisions" value={String(stats.total)} tone="neutral" />
        <DecisionMetric label="Evidence" value={String(stats.evidenceBacked)} tone="cyan" />
        <DecisionMetric label="Handoff" value={String(stats.handoffImpact)} tone="emerald" />
        <DecisionMetric label="Blocked" value={String(stats.blockedOrFailed)} tone={stats.blockedOrFailed ? 'amber' : 'neutral'} />
      </div>
    </div>
  );
}

function DecisionMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: 'neutral' | 'cyan' | 'emerald' | 'amber';
}) {
  return (
    <div
      className={cn(
        'rounded border bg-slate-950/70 px-3 py-2',
        tone === 'neutral' && 'border-slate-800',
        tone === 'cyan' && 'border-cyan-500/20',
        tone === 'emerald' && 'border-emerald-500/20',
        tone === 'amber' && 'border-amber-500/20',
      )}
    >
      <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">{label}</div>
      <div
        className={cn(
          'mt-1 text-lg font-semibold',
          tone === 'neutral' && 'text-slate-200',
          tone === 'cyan' && 'text-cyan-300',
          tone === 'emerald' && 'text-emerald-300',
          tone === 'amber' && 'text-amber-300',
        )}
      >
        {value}
      </div>
    </div>
  );
}

// Decision Item with Evidence support
function DecisionItem({
  decision,
  workspace,
}: {
  decision: ResidentDecisionPayload;
  workspace: string;
}) {
  const verdict = decision.verdict || 'unknown';
  const isSuccess = verdict === 'success';
  const isFailure = verdict === 'failure';
  const hasEvidence = Boolean(decision.evidence_bundle_id);
  const [showEvidence, setShowEvidence] = useState(false);
  const actual = decision.actual_outcome || {};
  const decisionSource = decisionString(actual.decision_source) || decision.actor || '';
  const evidenceSchema = decisionString(actual.evidence_schema);
  const profileSchema = decisionString(actual.execution_profile_schema) || decisionString(actual.profile_schema);
  const validatorResult = decisionString(actual.validator_result) || decisionString(actual.validation_status);
  const selectedOption = (decision.options || []).find((option) => option.option_id === decision.selected_option_id);
  const taskCount = decisionNumber(actual.task_count);
  const confidence = formatConfidence(decision.confidence);
  const evidenceRefs = (decision.evidence_refs || []).filter(Boolean);
  const affectedFiles = (decision.affected_files || []).filter(Boolean);
  const affectedSymbols = (decision.affected_symbols || []).filter(Boolean);
  const strategyTags = (decision.strategy_tags || []).filter(Boolean);
  const hardRuleBlockers = Array.isArray(actual.hard_rule_blockers)
    ? actual.hard_rule_blockers.map(decisionString).filter(Boolean)
    : [];
  const handoffImpact = decisionHasHandoffImpact(decision);

  return (
    <Card className={cn('border-slate-800 bg-slate-900/50', handoffImpact && 'border-cyan-500/20')}>
      <div className="p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <FileText className="size-4 shrink-0 text-slate-500" />
              <span className="truncate text-sm text-slate-300" title={decision.summary || '未命名决策'}>
                {decision.summary || '未命名决策'}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              {decision.actor && <span>{decision.actor}</span>}
              {decision.stage && <span>{decision.stage}</span>}
              {decision.decision_id && <span title={decision.decision_id}>#{shortDecisionId(decision.decision_id)}</span>}
              <span>{formatTime(decision.timestamp)}</span>
            </div>
          </div>
          <Badge className={cn(
            isSuccess && 'bg-emerald-500/10 text-emerald-400',
            isFailure && 'bg-red-500/10 text-red-400',
            !isSuccess && !isFailure && 'bg-slate-500/10 text-slate-400',
          )}>
            {verdict}
          </Badge>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          <div className="rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5">
            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">Confidence</div>
            <div className="mt-1 text-xs font-medium text-slate-200">{confidence}</div>
          </div>
          <div className="rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5">
            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">Validation</div>
            <div className="mt-1 truncate text-xs font-medium text-slate-200" title={validatorResult || 'unknown'}>
              {validatorResult || 'unknown'}
            </div>
          </div>
          <div className="rounded border border-slate-800 bg-slate-950/70 px-2 py-1.5">
            <div className="text-[10px] uppercase tracking-[0.14em] text-slate-500">Handoff</div>
            <div className={cn('mt-1 text-xs font-medium', handoffImpact ? 'text-cyan-300' : 'text-slate-500')}>
              {handoffImpact ? 'PM → Chief Engineer → Director' : 'none'}
            </div>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-end">
          {hasEvidence && (
            <button
              onClick={() => setShowEvidence(!showEvidence)}
              className={cn(
                'flex cursor-pointer items-center gap-1 text-xs transition-colors',
                showEvidence ? 'text-cyan-400' : 'text-slate-400 hover:text-cyan-400',
              )}
            >
              <FileSearch className="size-3" />
              {showEvidence ? '隐藏证据' : '查看证据'}
            </button>
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
          {decision.stage && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              stage: {decision.stage}
            </span>
          )}
          {decisionSource && (
            <span className="rounded border border-cyan-500/20 bg-cyan-500/10 px-2 py-1 text-cyan-200">
              source: {decisionSource}
            </span>
          )}
          {evidenceSchema && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              evidence: {evidenceSchema}
            </span>
          )}
          {profileSchema && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              profile: {profileSchema}
            </span>
          )}
          {taskCount !== null && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              tasks: {taskCount}
            </span>
          )}
          {decision.run_id && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              run: {shortDecisionId(decision.run_id)}
            </span>
          )}
          {decision.task_id && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              task: {decision.task_id}
            </span>
          )}
          {decision.goal_id && (
            <span className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              goal: {shortDecisionId(decision.goal_id)}
            </span>
          )}
          {strategyTags.slice(0, 4).map((tag) => (
            <span key={tag} className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-300">
              tag: {tag}
            </span>
          ))}
          {hardRuleBlockers.map((blocker) => (
            <span key={blocker} className="rounded border border-amber-500/20 bg-amber-500/10 px-2 py-1 text-amber-200">
              blocker: {blocker}
            </span>
          ))}
        </div>
        {selectedOption && (
          <div className="mt-3 rounded border border-slate-800 bg-slate-950/70 p-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="font-medium text-slate-200">{selectedOption.label || selectedOption.option_id || 'selected option'}</span>
              {typeof selectedOption.estimated_score === 'number' && (
                <span className="text-slate-500">score {Math.round(selectedOption.estimated_score * 100)}%</span>
              )}
            </div>
            {selectedOption.rationale && (
              <div className="mt-1 text-xs text-slate-500">{selectedOption.rationale}</div>
            )}
          </div>
        )}
        {(decision.context_refs || []).length > 0 && (
          <div className="mt-2 truncate text-xs text-slate-500" title={(decision.context_refs || []).join(' · ')}>
            context: {(decision.context_refs || []).slice(0, 3).join(' · ')}
          </div>
        )}
        {evidenceRefs.length > 0 && (
          <div className="mt-2 truncate text-xs text-slate-500" title={evidenceRefs.join(' · ')}>
            evidence refs: {evidenceRefs.slice(0, 3).join(' · ')}
          </div>
        )}
        {affectedFiles.length > 0 && (
          <div className="mt-2 truncate text-xs text-slate-500" title={affectedFiles.join(' · ')}>
            files: {affectedFiles.slice(0, 3).join(' · ')}
          </div>
        )}
        {affectedSymbols.length > 0 && (
          <div className="mt-2 truncate text-xs text-slate-500" title={affectedSymbols.join(' · ')}>
            symbols: {affectedSymbols.slice(0, 4).join(' · ')}
          </div>
        )}
      </div>

      {showEvidence && decision.decision_id && (
        <div className="border-t border-slate-800 p-3">
          <EvidenceViewer
            decisionId={decision.decision_id}
            workspace={workspace}
            onClose={() => setShowEvidence(false)}
          />
        </div>
      )}
    </Card>
  );
}

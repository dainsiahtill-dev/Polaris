import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  Bot,
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
} from 'lucide-react';

import { EvidenceViewer } from './EvidenceViewer';
import { ExecutionProgressBar } from './ExecutionProgressBar';

import { useResident } from '@/hooks/useResident';
import type {
  ResidentDecisionPayload,
  ResidentGoalPayload,
  ResidentStatusPayload,
} from '@/app/types/appContracts';
import { Button } from '@/app/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/app/components/ui/card';
import { Input } from '@/app/components/ui/input';
import { Textarea } from '@/app/components/ui/textarea';
import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';

const TAB_OPTIONS = ['overview', 'goals', 'decisions'] as const;
type AgiTab = (typeof TAB_OPTIONS)[number];

interface ResidentWorkspaceProps {
  workspace: string;
  onBackToMain: () => void;
  residentSnapshot?: ResidentStatusPayload | null;
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
  const [showNewGoal, setShowNewGoal] = useState(false);
  const [expandedGoal, setExpandedGoal] = useState<string | null>(null);

  // New goal form state
  const [newGoalTitle, setNewGoalTitle] = useState('');
  const [newGoalDesc, setNewGoalDesc] = useState('');

  const isActive = Boolean(resident.residentRuntime?.active);
  const mode = resident.residentRuntime?.mode || 'observe';

  // Current focus - simplified
  const currentFocus = resident.residentAgenda?.current_focus?.[0] || null;
  const pendingGoals = resident.goals.filter(g => g.status === 'pending');
  const approvedGoals = resident.goals.filter(g => g.status === 'approved' || g.status === 'materialized');

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

  return (
    <div className="flex h-full flex-col bg-slate-950 text-slate-100">
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
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key as AgiTab)}
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
                    <span>新建目标</span>
                    <Button size="sm" variant="ghost" onClick={() => setShowNewGoal(false)}>
                      <X className="size-4" />
                    </Button>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <Input
                    placeholder="目标标题"
                    value={newGoalTitle}
                    onChange={(e) => setNewGoalTitle(e.target.value)}
                    className="border-slate-700 bg-slate-950"
                  />
                  <Textarea
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
                      创建
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
          <div className="space-y-2">
            {resident.decisions.map((decision) => (
              <DecisionItem
                key={decision.decision_id || decision.timestamp}
                decision={decision}
                workspace={workspace}
              />
            ))}
            {resident.decisions.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-500">
                暂无决策记录
              </div>
            )}
          </div>
        )}
      </div>
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
  onRun,
  disabled,
}: {
  goal: ResidentGoalPayload;
  execution?: import('@/app/types/appContracts').GoalExecutionView;
  expanded: boolean;
  onToggle: () => void;
  onApprove: () => void;
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
              <Button size="sm" onClick={onApprove} disabled={disabled} className="bg-emerald-500 text-black hover:bg-emerald-400">
                <CheckCircle2 className="mr-1 size-3" />
                批准
              </Button>
            )}
            {isApproved && (
              <Button size="sm" onClick={onRun} disabled={disabled} className="bg-cyan-500 text-black hover:bg-cyan-400">
                <Play className="mr-1 size-3" />
                执行
              </Button>
            )}
          </div>
        </div>
      )}
    </Card>
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

  return (
    <Card className="border-slate-800 bg-slate-900/50">
      <div className="p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <FileText className="size-4 text-slate-500" />
            <span className="text-sm text-slate-300">{decision.summary || '未命名决策'}</span>
          </div>
          <Badge className={cn(
            isSuccess && 'bg-emerald-500/10 text-emerald-400',
            isFailure && 'bg-red-500/10 text-red-400',
            !isSuccess && !isFailure && 'bg-slate-500/10 text-slate-400'
          )}>
            {verdict}
          </Badge>
        </div>
        <div className="mt-2 flex items-center justify-between">
          <div className="text-xs text-slate-500">
            {decision.actor} · {formatTime(decision.timestamp)}
          </div>
          {hasEvidence && (
            <button
              onClick={() => setShowEvidence(!showEvidence)}
              className={cn(
                'flex items-center gap-1 text-xs transition-colors',
                showEvidence ? 'text-cyan-400' : 'text-slate-400 hover:text-cyan-400'
              )}
            >
              <FileSearch className="size-3" />
              {showEvidence ? '隐藏证据' : '查看证据'}
            </button>
          )}
        </div>
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

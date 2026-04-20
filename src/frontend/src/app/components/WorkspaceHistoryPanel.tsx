import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch } from '@/api';
import {
  Activity,
  AlertTriangle,
  Calendar,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Download,
  GitBranch,
  History,
  RefreshCw,
  Search,
  ShieldX,
  XCircle,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { ScrollArea } from '@/app/components/ui/scroll-area';
import { Badge } from '@/app/components/ui/badge';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/app/components/ui/collapsible';

interface TaskItem {
  id: string;
  title: string;
  goal: string;
}

interface RoundItem {
  round_id: string;
  timestamp: string;
  focus: string;
  overall_goal: string;
  tasks?: TaskItem[];
  director_results?: {
    status?: string;
    successes?: number;
    total?: number;
    reason?: string;
    error_code?: string;
  };
  factory_flow?: {
    pipeline_status?: {
      status?: string;
      reason?: string;
      hard_block?: boolean;
    };
    non_director_execution?: {
      results?: Array<{
        task_id: string;
        assigned_to: string;
        status: string;
        summary: string;
        error_code: string;
        output?: Record<string, unknown>;
      }>;
      blocked_reasons?: string[];
    };
    defect_loop?: {
      generated_count?: number;
      generated_director_task_ids?: string[];
    };
    routing?: {
      director_task_ids?: string[];
      docs_only_task_ids?: string[];
      non_director_queue?: Array<{ id: string; assigned_to: string; title: string }>;
    };
  };
}

interface Summary {
  total_rounds: number;
  passed_rounds: number;
  blocked_rounds: number;
  failed_rounds: number;
  non_director_results: number;
  defect_followups_generated: number;
  hard_block_rounds: number;
  policy_gate_blocks: number;
  finops_blocks: number;
  auditor_failures: number;
}

const EMPTY_SUMMARY: Summary = {
  total_rounds: 0,
  passed_rounds: 0,
  blocked_rounds: 0,
  failed_rounds: 0,
  non_director_results: 0,
  defect_followups_generated: 0,
  hard_block_rounds: 0,
  policy_gate_blocks: 0,
  finops_blocks: 0,
  auditor_failures: 0,
};

function normalizeStatus(raw: string): string {
  const value = String(raw || '').toLowerCase();
  if (value.includes('success') || value === 'pass') return 'passed';
  if (value.includes('blocked')) return 'blocked';
  if (value.includes('fail')) return 'failed';
  if (value === 'in_progress' || value === 'pm_only') return value;
  return value || 'unknown';
}

function resolveRoundStatus(round: RoundItem): string {
  const pipeline = normalizeStatus(round.factory_flow?.pipeline_status?.status || '');
  if (pipeline !== 'unknown') return pipeline;
  return normalizeStatus(round.director_results?.status || '');
}

function statusIcon(status: string) {
  const normalized = normalizeStatus(status);
  if (normalized === 'passed') return <CheckCircle className="h-4 w-4 text-emerald-400" />;
  if (normalized === 'failed') return <XCircle className="h-4 w-4 text-red-400" />;
  if (normalized === 'blocked') return <ShieldX className="h-4 w-4 text-amber-400" />;
  if (normalized === 'in_progress' || normalized === 'pm_only') return <Activity className="h-4 w-4 text-blue-300" />;
  return <AlertTriangle className="h-4 w-4 text-gray-400" />;
}

function statusBadge(status: string) {
  const normalized = normalizeStatus(status);
  if (normalized === 'passed') return <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20">通过</Badge>;
  if (normalized === 'failed') return <Badge className="bg-red-500/10 text-red-400 border-red-500/20">失败</Badge>;
  if (normalized === 'blocked') return <Badge className="bg-amber-500/10 text-amber-400 border-amber-500/20">阻塞</Badge>;
  if (normalized === 'pm_only') return <Badge className="bg-cyan-500/10 text-cyan-300 border-cyan-500/20">PM 队列</Badge>;
  if (normalized === 'in_progress') return <Badge className="bg-blue-500/10 text-blue-300 border-blue-500/20">进行中</Badge>;
  return <Badge className="bg-gray-500/10 text-gray-400 border-gray-500/20">未知</Badge>;
}

function formatTime(timestamp: string): string {
  try {
    return new Date(timestamp).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return timestamp;
  }
}

function deriveSummary(rounds: RoundItem[]): Summary {
  const summary = { ...EMPTY_SUMMARY, total_rounds: rounds.length };
  for (const round of rounds) {
    const status = resolveRoundStatus(round);
    if (status === 'passed') summary.passed_rounds += 1;
    else if (status === 'blocked') summary.blocked_rounds += 1;
    else if (status === 'failed') summary.failed_rounds += 1;
    if (round.factory_flow?.pipeline_status?.hard_block) summary.hard_block_rounds += 1;
    const nonDirector = round.factory_flow?.non_director_execution?.results;
    if (Array.isArray(nonDirector)) {
      summary.non_director_results += nonDirector.length;
      for (const row of nonDirector) {
        if (!row) continue;
        if (row.error_code === 'POLICY_GATE_BLOCKED') summary.policy_gate_blocks += 1;
        else if (row.error_code === 'FINOPS_BUDGET_BLOCKED') summary.finops_blocks += 1;
        else if (row.error_code === 'AUDITOR_FAILS_WITH_DEFECT') summary.auditor_failures += 1;
      }
    }
    summary.defect_followups_generated += Number(round.factory_flow?.defect_loop?.generated_count || 0);
  }
  return summary;
}

export function WorkspaceHistoryPanel({ className }: { className?: string }) {
  const [rounds, setRounds] = useState<RoundItem[]>([]);
  const [summary, setSummary] = useState<Summary>(EMPTY_SUMMARY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch('/history/factory/overview?limit=100');
      if (response.ok) {
        const data = await response.json();
        const items = Array.isArray(data.rounds) ? data.rounds : [];
        setRounds(items);
        setSummary(data.summary || deriveSummary(items));
      } else {
        const fallback = await apiFetch('/history/rounds?limit=100');
        if (!fallback.ok) throw new Error('Failed to load history');
        const data = await fallback.json();
        const items = Array.isArray(data.rounds) ? data.rounds : [];
        setRounds(items);
        setSummary(deriveSummary(items));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load history');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    if (!query.trim()) return rounds;
    const q = query.toLowerCase();
    return rounds.filter((round) => {
      const text = [
        round.round_id,
        round.focus,
        round.overall_goal,
        ...(round.tasks?.map((task) => `${task.title} ${task.goal}`) || []),
        ...(round.factory_flow?.non_director_execution?.blocked_reasons || []),
      ]
        .join(' ')
        .toLowerCase();
      return text.includes(q);
    });
  }, [rounds, query]);

  const exportHistory = () => {
    const payload = JSON.stringify({ summary, rounds: filtered }, null, 2);
    const blob = new Blob([payload], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `factory-history-${new Date().toISOString().split('T')[0]}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className={`flex flex-col h-full bg-[var(--ink-indigo)] border-gray-800 ${className || ''}`}>
      <div className="flex items-center justify-between p-4 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <History className="h-5 w-5 text-blue-400" />
          <h2 className="text-lg font-semibold text-gray-200">工厂历史</h2>
          <Badge variant="outline" className="text-xs">{filtered.length} 轮</Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={load} disabled={loading} className="text-gray-400 hover:text-white">
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
          <Button variant="ghost" size="sm" onClick={exportHistory} className="text-gray-400 hover:text-white">
            <Download className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 p-4 border-b border-gray-800 text-xs">
        <div className="rounded border border-emerald-500/20 bg-emerald-500/5 p-2 text-emerald-200">通过: {summary.passed_rounds}</div>
        <div className="rounded border border-amber-500/20 bg-amber-500/5 p-2 text-amber-200">阻塞: {summary.blocked_rounds}</div>
        <div className="rounded border border-red-500/20 bg-red-500/5 p-2 text-red-200">失败: {summary.failed_rounds}</div>
        <div className="rounded border border-blue-500/20 bg-blue-500/5 p-2 text-blue-200">缺陷回流: {summary.defect_followups_generated}</div>
        <div className="rounded border border-amber-500/20 bg-amber-500/5 p-2 text-amber-200">PolicyGate 拦截: {summary.policy_gate_blocks}</div>
        <div className="rounded border border-amber-500/20 bg-amber-500/5 p-2 text-amber-200">FinOps 拦截: {summary.finops_blocks}</div>
        <div className="rounded border border-red-500/20 bg-red-500/5 p-2 text-red-200">Auditor 失败: {summary.auditor_failures}</div>
        <div className="rounded border border-amber-500/20 bg-amber-500/5 p-2 text-amber-200">硬阻塞轮次: {summary.hard_block_rounds}</div>
      </div>

      <div className="p-4 border-b border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <Input
            placeholder="搜索轮次、任务、阻塞原因..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="pl-10 bg-[#2a2a2a] border-gray-700 text-gray-200 placeholder-gray-500"
          />
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-3">
          {loading ? (
            <div className="text-center text-gray-500 py-8">加载中...</div>
          ) : error ? (
            <div className="text-center text-red-400 py-8">错误: {error}</div>
          ) : filtered.length === 0 ? (
            <div className="text-center text-gray-500 py-8">暂无历史记录</div>
          ) : (
            filtered.map((round) => {
              const status = resolveRoundStatus(round);
              const blockedReasons = round.factory_flow?.non_director_execution?.blocked_reasons || [];
              const generatedIds = round.factory_flow?.defect_loop?.generated_director_task_ids || [];
              const nonDirectorResults = round.factory_flow?.non_director_execution?.results || [];
              const isOpen = expanded.has(round.round_id);
              return (
                <Collapsible key={round.round_id} open={isOpen} onOpenChange={() => {
                  setExpanded((prev) => {
                    const next = new Set(prev);
                    if (next.has(round.round_id)) next.delete(round.round_id);
                    else next.add(round.round_id);
                    return next;
                  });
                }}>
                  <CollapsibleTrigger className="w-full">
                    <div className="bg-[#2a2a2a] rounded-lg p-4 border border-gray-700 hover:border-gray-600 transition-colors text-left">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-3">
                          {isOpen ? <ChevronDown className="h-4 w-4 text-gray-400" /> : <ChevronRight className="h-4 w-4 text-gray-400" />}
                          {statusIcon(status)}
                          <span className="font-mono text-sm text-blue-300">{round.round_id}</span>
                          {statusBadge(status)}
                        </div>
                        <div className="flex items-center gap-3 text-xs text-gray-400">
                          <span className="flex items-center gap-1"><Calendar className="h-3 w-3" />{formatTime(round.timestamp)}</span>
                          <span className="flex items-center gap-1"><Activity className="h-3 w-3" />{nonDirectorResults.length}</span>
                          {generatedIds.length > 0 && <span className="flex items-center gap-1 text-purple-300"><GitBranch className="h-3 w-3" />{generatedIds.length}</span>}
                        </div>
                      </div>
                      {round.focus && <div className="mt-2 text-sm text-blue-300">{round.focus}</div>}
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="mt-2">
                    <div className="bg-[#252525] rounded-lg p-4 border border-gray-700 space-y-3 text-xs">
                      {round.overall_goal && <div className="text-gray-300 break-words">{round.overall_goal}</div>}
                      <div className="text-gray-400">Director: {round.director_results?.successes || 0}/{round.director_results?.total || 0}</div>
                      {round.factory_flow?.routing && (
                        <div className="text-gray-300">
                          路由: Director {round.factory_flow.routing.director_task_ids?.length || 0} · PM-only {round.factory_flow.routing.docs_only_task_ids?.length || 0} · Non-Director {round.factory_flow.routing.non_director_queue?.length || 0}
                        </div>
                      )}
                      {nonDirectorResults.length > 0 && (
                        <div className="space-y-1">
                          <div className="text-gray-400">非 Director 执行</div>
                          {nonDirectorResults.map((row, idx) => (
                            <div key={`${round.round_id}-${row.task_id}-${idx}`} className="rounded border border-gray-700 bg-[#2a2a2a] px-2 py-1">
                              <span className="text-gray-200">{row.assigned_to} · {row.task_id}</span>
                              <span className="text-gray-400"> · {row.status}</span>
                              {row.error_code && <span className="text-amber-300"> · {row.error_code}</span>}
                              {row.summary && <div className="text-gray-400 mt-1 break-words">{row.summary}</div>}
                              {row.output && typeof row.output === 'object' && (
                                <div className="text-gray-500 mt-1 break-words">
                                  {'decision' in row.output && <span>decision: {(row.output as Record<string, unknown>).decision as string}</span>}
                                  {'budget_limit' in row.output && (
                                    <span>
                                      {'decision' in row.output ? ' · ' : ''}
                                      budget: {(row.output as Record<string, unknown>).estimated_units as number}/{(row.output as Record<string, unknown>).budget_limit as number}
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                      {blockedReasons.length > 0 && (
                        <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-amber-200 space-y-1">
                          <div className="font-semibold">阻塞原因</div>
                          {blockedReasons.map((reason, idx) => <div key={`${round.round_id}-reason-${idx}`} className="break-words">{reason}</div>)}
                        </div>
                      )}
                      {generatedIds.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {generatedIds.map((taskId) => (
                            <Badge key={`${round.round_id}-defect-${taskId}`} className="bg-purple-500/15 text-purple-200 border-purple-500/30">
                              {taskId}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  </CollapsibleContent>
                </Collapsible>
              );
            })
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

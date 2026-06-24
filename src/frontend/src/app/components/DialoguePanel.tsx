import {
  Bot,
  User,
  CheckCircle,
  MessageSquare,
  Activity as ActivityIcon,
  TrendingUp,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Trash2,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { DialoguePanelSkeleton } from './DialoguePanelSkeleton';
import { StatusBadge } from '@/app/components/ui/badge';

export interface DialogueEvent {
  seq?: number;
  eventId?: string;
  speaker: 'PM' | 'Director' | 'QA' | 'Reviewer' | 'System';
  type?: string;
  content: unknown;
  timestamp?: string;
  refs?: {
    task_id?: string;
    phase?: string;
  };
  meta?: Record<string, unknown>;
}

interface DialoguePanelProps {
  events: DialogueEvent[];
  live: boolean;
  loading?: boolean;
  onClearLogs?: () => void | Promise<void>;
  clearingLogs?: boolean;
}

const speakerStyles = {
  PM: {
    icon: User,
    iconBg: 'bg-blue-500/[0.15]',
    iconText: 'text-blue-400',
    nameText: 'text-blue-400',
    filterActive: 'bg-blue-500/20 text-blue-300',
    border: 'border-blue-500/30'
  },
  Director: {
    icon: Bot,
    iconBg: 'bg-slate-500/[0.15]',
    iconText: 'text-slate-400',
    nameText: 'text-slate-300',
    filterActive: 'bg-slate-500/20 text-slate-300',
    border: 'border-slate-500/30'
  },
  QA: {
    icon: CheckCircle,
    iconBg: 'bg-emerald-500/[0.15]',
    iconText: 'text-emerald-400',
    nameText: 'text-emerald-400',
    filterActive: 'bg-emerald-500/20 text-emerald-300',
    border: 'border-emerald-500/30'
  },
  Reviewer: {
    icon: ActivityIcon,
    iconBg: 'bg-amber-500/[0.15]',
    iconText: 'text-amber-400',
    nameText: 'text-amber-400',
    filterActive: 'bg-amber-500/20 text-amber-300',
    border: 'border-amber-500/30'
  },
  System: {
    icon: MessageSquare,
    iconBg: 'bg-accent/10',
    iconText: 'text-text-muted',
    nameText: 'text-text-muted',
    filterActive: 'bg-accent/15 text-accent-text',
    border: 'border-border'
  },
};

const STATUS_RANK: Record<string, number> = {
  ERROR: 4,
  FAIL: 4,
  FAILED: 4,
  BLOCKED: 3,
  SUCCESS: 2,
  PASS: 2,
};

function formatDialogueContent(content: unknown): string {
  if (typeof content === 'string') return content;
  if (content == null) return '';
  try {
    const serialized = JSON.stringify(content, null, 2);
    return typeof serialized === 'string' ? serialized : String(content);
  } catch {
    return String(content);
  }
}

function normalizeStatus(status: string): string {
  const raw = status.trim().toUpperCase();
  return raw === 'FAILED' ? 'FAIL' : raw;
}

function mergeStatus(previous: string | undefined, next: string): string {
  const normalizedNext = normalizeStatus(next);
  if (!previous) return normalizedNext;
  const normalizedPrevious = normalizeStatus(previous);
  return (STATUS_RANK[normalizedNext] ?? 0) > (STATUS_RANK[normalizedPrevious] ?? 0)
    ? normalizedNext
    : normalizedPrevious;
}

export function DialoguePanel({
  events,
  live,
  loading = false,
  onClearLogs,
  clearingLogs = false,
}: DialoguePanelProps) {
  const [filterSpeaker, setFilterSpeaker] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'tasks' | 'stream'>('tasks');
  const [expandedTasks, setExpandedTasks] = useState<Record<string, boolean>>({});

  const filteredEvents = filterSpeaker ? events.filter((e) => e.speaker === filterSpeaker) : events;

  const taskGroups = useMemo(() => {
    const groups = new Map<
      string,
      {
        taskId: string;
        title?: string;
        events: DialogueEvent[];
        status?: string;
        reviewerFindings: string[];
        modifiedCount?: number;
        attemptCurrent?: number;
        attemptTotal?: number;
        startTs?: string;
        endTs?: string;
        order: number;
      }
    >();

    const extractTitle = (content: string) => {
      const assignMatch = content.match(/Assigning task\s+\S+:\s*(.+)$/i);
      if (assignMatch?.[1]) return assignMatch[1].trim();
      const cnMatch = content.match(/任务《(.+?)》/);
      if (cnMatch?.[1]) return cnMatch[1].trim();
      return '';
    };

    const extractStatus = (content: string) => {
      const match = content.match(/(SUCCESS|PASS|FAILED|FAIL|BLOCKED|ERROR)/i);
      if (!match?.[1]) return '';
      return normalizeStatus(match[1]);
    };

    const extractReviewerFindings = (content: string) => {
      const markerIdx = content.search(/Reviewer[:：]/);
      if (markerIdx === -1) return [];
      const slice = content.slice(markerIdx);
      const parts = slice
        .split(/-\s+/)
        .slice(1)
        .map((part) => part.trim())
        .filter(Boolean);
      if (parts.length > 0) return parts;
      const tail = slice.replace(/Reviewer[:：]/, '').trim();
      return tail ? [tail] : [];
    };

    const extractModifiedCount = (content: string) => {
      const match = content.match(/Modified\s+(\d+)\s+files?/i);
      if (match?.[1]) return Number(match[1]);
      const cn = content.match(/改动文件数[:：]\s*(\d+)/);
      if (cn?.[1]) return Number(cn[1]);
      return undefined;
    };

    const extractAttempt = (content: string) => {
      const match = content.match(/attempt\s+(\d+)\s*\/\s*(\d+)/i);
      if (!match?.[1] || !match?.[2]) return null;
      return { current: Number(match[1]), total: Number(match[2]) };
    };

    events.forEach((event, index) => {
      const taskId = event.refs?.task_id || 'GLOBAL';
      const existing = groups.get(taskId);
      const group =
        existing || {
          taskId,
          events: [],
          reviewerFindings: [],
          order: index,
        };

      group.events.push(event);
      group.startTs = group.startTs || event.timestamp;
      group.endTs = event.timestamp || group.endTs;

      const contentText = formatDialogueContent(event.content);

      if (!group.title) {
        const title = extractTitle(contentText);
        if (title) group.title = title;
      }
      const status = extractStatus(contentText);
      if (status) group.status = mergeStatus(group.status, status);

      const findings = extractReviewerFindings(contentText);
      if (findings.length) group.reviewerFindings.push(...findings);

      const modified = extractModifiedCount(contentText);
      if (typeof modified === 'number') group.modifiedCount = modified;

      const attempt = extractAttempt(contentText);
      if (attempt) {
        group.attemptCurrent = attempt.current;
        group.attemptTotal = attempt.total;
      }

      if (!existing) groups.set(taskId, group);
    });

    return Array.from(groups.values()).sort((a, b) => a.order - b.order);
  }, [events]);

  const latestTaskId = taskGroups.length > 0 ? taskGroups[taskGroups.length - 1].taskId : '';

  const stats = useMemo(() => {
    const taskIds = new Set<string>();
    const resultByTaskId = new Map<string, string>();
    events.forEach((event) => {
      const taskId = event.refs?.task_id;
      if (taskId) {
        taskIds.add(taskId);
      }
      if (event.type === 'result' && taskId) {
        const match = formatDialogueContent(event.content).match(/(?:Result|Event receipt):\s*([A-Za-z]+)/i);
        if (match?.[1]) {
          resultByTaskId.set(taskId, mergeStatus(resultByTaskId.get(taskId), match[1]));
        }
      }
    });
    const totalTasks = taskIds.size;
    const completedTasks = resultByTaskId.size;
    const successCount = Array.from(resultByTaskId.values()).filter(
      (status) => status === 'SUCCESS' || status === 'PASS'
    ).length;
    const successRate = completedTasks > 0 ? Math.round((successCount / completedTasks) * 100) : 0;
    return { totalTasks, completedTasks, successRate };
  }, [events]);

  return (
    <div className="soft-panel-subtle h-full flex flex-col border-l-0 relative overflow-hidden">
      <div className="relative z-20 flex flex-col h-full">
        <div className="soft-panel-subtle px-4 py-4 border-b relative mx-2 mt-2 rounded-lg">

          <div className="relative z-10 mb-3 flex flex-wrap items-start justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <div className="soft-raised flex size-9 shrink-0 items-center justify-center rounded-lg text-text-muted">
                <MessageSquare className="size-5" />
              </div>
              <h2 className="whitespace-nowrap text-sm font-heading font-black text-text-main uppercase tracking-[0.18em] leading-tight">对话流</h2>
            </div>
            <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 text-[10px] text-text-muted font-mono">
              {onClearLogs ? (
                <button
                  onClick={() => {
                    onClearLogs();
                  }}
                  disabled={clearingLogs}
                  className="soft-chip flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-text-dim transition-colors hover:border-border-glow hover:text-text-main disabled:cursor-not-allowed disabled:opacity-50"
                  title={clearingLogs ? '清空中...' : '清空对话日志'}
                  aria-label={clearingLogs ? '清空中' : '清空对话日志'}
                >
                  <Trash2 className="size-3" />
                  <span className="sr-only">{clearingLogs ? '清空中' : '清空日志'}</span>
                </button>
              ) : null}
              <div className="soft-chip flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-sm px-2">
                <ActivityIcon className={`size-3 ${live ? 'text-emerald-400 animate-pulse' : 'text-text-dim'}`} />
                <span className={live ? 'text-emerald-400 font-bold' : 'text-text-dim font-bold tracking-widest'}>{live ? '实时' : '离线'}</span>
              </div>
              <div className="soft-inset flex shrink-0 items-center gap-1 rounded-sm p-0.5 no-drag">
                <button
                  onClick={() => setViewMode('tasks')}
                  className={`h-6 whitespace-nowrap rounded-sm px-2 transition-all font-black uppercase tracking-tighter text-[9px] ${viewMode === 'tasks'
                    ? 'bg-accent/15 text-accent-text border border-border-glow'
                    : 'text-text-dim hover:text-text-main'
                    }`}
                >
                  任务视图
                </button>
                <button
                  onClick={() => setViewMode('stream')}
                  className={`h-6 whitespace-nowrap rounded-sm px-2 transition-all font-black uppercase tracking-tighter text-[9px] ${viewMode === 'stream'
                    ? 'bg-accent/15 text-accent-text border border-border-glow'
                    : 'text-text-dim hover:text-text-main'
                    }`}
                >
                  日志流
                </button>
              </div>
            </div>
          </div>

          {viewMode === 'stream' ? (
            <div className="flex flex-wrap gap-1.5 pt-2">
              <button
                onClick={() => setFilterSpeaker(null)}
                className={`px-2 py-1 text-[10px] rounded-md border transition-all ${!filterSpeaker
                  ? 'bg-accent/15 text-accent-text border-border-glow'
                  : 'bg-accent/10 text-text-dim border-transparent hover:bg-accent/15'
                  }`}
              >
                全部
              </button>
              {Object.keys(speakerStyles).map((speaker) => {
                const style = speakerStyles[speaker as keyof typeof speakerStyles];
                return (
                  <button
                    key={speaker}
                    onClick={() => setFilterSpeaker(speaker === filterSpeaker ? null : speaker)}
                    className={`px-2 py-1 text-[10px] rounded-md border border-transparent transition-all ${filterSpeaker === speaker
                      ? style.filterActive
                      : 'bg-accent/10 text-text-dim hover:bg-accent/15'
                      }`}
                  >
                    {speaker}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar">
          {loading ? (
            <DialoguePanelSkeleton />
          ) : viewMode === 'tasks' ? (
            taskGroups.length === 0 ? (
              <div className="text-xs text-text-dim flex flex-col items-center justify-center h-40 opacity-50">
                <MessageSquare className="size-8 mb-2 opacity-50" />
                <span>(暂无任务)</span>
              </div>
            ) : (
              taskGroups.map((group) => {
                const isExpanded = expandedTasks[group.taskId] ?? (group.taskId === latestTaskId);
                const status = group.status || 'UNKNOWN';
                const statusColor =
                  status === 'SUCCESS' || status === 'PASS' ? 'success'
                  : status === 'FAIL' ? 'error'
                  : status === 'BLOCKED' ? 'warning'
                  : 'default' as const;
                const conflict = (status === 'SUCCESS' || status === 'PASS') && group.reviewerFindings.length > 0;
                const modifiedLabel = typeof group.modifiedCount === 'number' ? `${group.modifiedCount} files` : '-';
                const attemptLabel = group.attemptTotal
                  ? `attempt ${group.attemptCurrent ?? group.attemptTotal}/${group.attemptTotal}`
                  : '';
                const timeRange = group.startTs && group.endTs ? `${group.startTs} - ${group.endTs}` : group.endTs || '';

                return (
                  <div key={group.taskId} className="soft-panel rounded-lg p-4 transition-all hover:border-border-glow relative group/task">

                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 text-[10px] text-text-muted font-mono">
                          <span className="rounded px-1.5 py-0.5 bg-accent/10 border border-border">
                            {group.taskId}
                          </span>
                          {attemptLabel ? (
                            <span className="rounded px-1.5 py-0.5 bg-accent/10 opacity-70">{attemptLabel}</span>
                          ) : null}
                          {timeRange ? <span className="text-text-dim opacity-50">{timeRange}</span> : null}
                        </div>
                        <div className="mt-1 text-sm font-semibold text-text-main">
                          {group.title || (group.taskId === 'GLOBAL' ? '系统/未归类' : '任务进度')}
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-text-muted">
                          <StatusBadge color={statusColor} variant="soft" className="text-[10px]">结果: {status}</StatusBadge>
                          <StatusBadge color="default" variant="soft" className="text-[10px]">改动: {modifiedLabel}</StatusBadge>
                          <StatusBadge color="default" variant="soft" className="text-[10px]">
                            风险: {group.reviewerFindings.length || 0}
                          </StatusBadge>
                          {conflict ? (
                            <StatusBadge color="warning" variant="dot" className="text-[10px]">
                              <AlertTriangle className="size-3" /> 结论冲突
                            </StatusBadge>
                          ) : null}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedTasks((prev) => ({
                            ...prev,
                            [group.taskId]: !isExpanded,
                          }))
                        }
                        className="flex items-center gap-1 rounded px-2 py-1 text-[10px] text-text-dim hover:text-text-main hover:bg-accent/10 transition-colors"
                      >
                        {isExpanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
                        <span>{isExpanded ? '收起' : '展开'}</span>
                      </button>
                    </div>

                    {group.reviewerFindings.length > 0 ? (
                      <div className="mt-3 rounded-md border border-status-warning/30 bg-status-warning/5 px-3 py-2 text-xs text-status-warning">
                        <div className="mb-1 font-semibold flex items-center gap-2">
                          <AlertTriangle className="size-3" /> Reviewer 风险点
                        </div>
                        <ul className="list-disc pl-4 space-y-1 opacity-90">
                          {group.reviewerFindings.slice(0, 4).map((item, idx) => (
                            <li key={`${group.taskId}-finding-${idx}`}>{item}</li>
                          ))}
                          {group.reviewerFindings.length > 4 ? <li>...</li> : null}
                        </ul>
                      </div>
                    ) : null}

                    {isExpanded ? (
                      <div className="mt-3 space-y-2 relative">
                        <div className="absolute left-[11px] top-2 bottom-2 w-px bg-border"></div>
                        {group.events.map((event, idx) => {
                          const style = speakerStyles[event.speaker] ?? speakerStyles.System;
                          const Icon = style.icon;
                          return (
                            <div
                              key={event.eventId || `${event.speaker}-${event.seq ?? idx}-${event.timestamp ?? ''}`}
                              className="flex gap-3 relative z-10 pl-2 group/msg"
                            >
                              <div
                                className={`flex-shrink-0 w-6 h-6 rounded-full ${style.iconBg} flex items-center justify-center ring-2 ring-accent/20 transition-transform group-hover/msg:scale-105`}
                              >
                                <Icon className={`size-3 ${style.iconText}`} />
                              </div>
                              <div className="soft-panel-subtle min-w-0 flex-1 p-3 transition-all rounded-md">
                                <div className="flex items-center gap-2 text-[10px] text-text-dim font-mono mb-1">
                                  <span className={`${style.nameText} font-bold`}>{event.speaker}</span>
                                  <span className="opacity-50">{event.type || 'log'}</span>
                                  <span className="opacity-50 ml-auto">{event.timestamp}</span>
                                </div>
                                <div className="text-xs text-text-main whitespace-pre-wrap break-all leading-relaxed opacity-90">{formatDialogueContent(event.content)}</div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                );
              })
            )
          ) : filteredEvents.length === 0 ? (
            <div className="text-xs text-text-dim flex flex-col items-center justify-center h-40 opacity-50">
              <span>(暂无对话事件)</span>
            </div>
          ) : (
            filteredEvents.map((event, index) => {
              const style = speakerStyles[event.speaker] ?? speakerStyles.System;
              const Icon = style.icon;

              return (
                <div
                  key={event.eventId || `${event.speaker}-${event.seq ?? index}-${event.timestamp ?? ''}`}
                  className="flex gap-3 group/msg"
                >
                  <div
                    className={`flex-shrink-0 w-8 h-8 rounded-full ${style.iconBg} flex items-center justify-center ring-2 ring-transparent group-hover/msg:ring-accent/20 transition-all`}
                  >
                    <Icon className={`size-4 ${style.iconText}`} />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`text-sm font-semibold ${style.nameText}`}>
                        {event.speaker}
                      </span>
                      <span className="text-[10px] text-text-dim font-mono">{event.timestamp}</span>
                      {event.refs?.task_id && (
                        <span className="text-[10px] px-1.5 py-0 rounded bg-accent/10 text-text-dim border border-border">
                          {event.refs.task_id}
                        </span>
                      )}
                      {event.refs?.phase && (
                        <span className="text-[10px] px-1.5 py-0 rounded bg-accent/10 text-text-dim border border-border">
                          {event.refs.phase}
                        </span>
                      )}
                    </div>

                    <div className="soft-panel-subtle px-5 py-4 transition-all rounded-md">
                      <p className="text-sm text-text-main leading-relaxed break-all whitespace-pre-wrap">{formatDialogueContent(event.content)}</p>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="soft-panel-subtle border-t p-3 mx-2 mb-2 relative rounded-lg">

          <div className="flex items-center justify-between text-[9px] text-text-dim font-mono relative z-10">
            <div className="flex items-center gap-4">
              <span className="flex items-center gap-1"><div className="size-1 bg-slate-400 rounded-full animate-pulse" /> 总事件: {events.length}</span>
              <span className="flex items-center gap-1"><div className="size-1 bg-slate-500 rounded-full" /> 任务数: {stats.totalTasks}</span>
              <span className="flex items-center gap-1"><div className="size-1 bg-slate-500 rounded-full" /> 已完成: {stats.completedTasks}</span>
            </div>
            <StatusBadge color="success" variant="dot" pulse>
              <TrendingUp className="size-3" />
              <span className="font-black">成功率: {stats.successRate}%</span>
            </StatusBadge>
          </div>
        </div>
      </div>
    </div>
  );
}

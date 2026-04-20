/**
 * BlockingPointAlert - 阻塞点实时监控
 * 
 * 检测任务卡顿、工具调用失败、资源瓶颈
 * 告警级别：INFO / WARNING / ERROR
 */

import { cn } from '@/app/components/ui/utils';
import { useTasks, useWorkers, useRuntimeEvents, useBlockedTasks } from '@/runtime';
import { RuntimeTaskNode, RuntimeWorkerState, RuntimeEventV2, TaskState } from '@/runtime/v2';
import { useMemo, useState, useEffect } from 'react';

export type AlertLevel = 'info' | 'warning' | 'error';

export interface BlockingAlert {
  id: string;
  level: AlertLevel;
  title: string;
  message: string;
  source: string;
  timestamp: string;
  taskId?: string;
  workerId?: string;
}

/**
 * 检测任务卡顿（超过阈值时间未完成）
 */
function detectStalledTasks(
  tasks: RuntimeTaskNode[],
  thresholdMinutes: number = 5
): BlockingAlert[] {
  const alerts: BlockingAlert[] = [];
  const now = Date.now();
  
  tasks.forEach(task => {
    if (task.state === 'in_progress' || task.state === 'claimed') {
      // 模拟：基于任务进度判断卡顿
      // 实际应基于最后更新时间
      const stalled = task.progress < 10 && task.state === 'in_progress';
      if (stalled) {
        alerts.push({
          id: `stalled-${task.id}`,
          level: 'warning',
          title: '任务卡顿',
          message: `任务 "${task.title}" 长时间无进展`,
          source: '任务监控',
          timestamp: new Date().toISOString(),
          taskId: task.id,
        });
      }
    }
    
    if (task.state === 'blocked' && task.blocked_by.length > 0) {
      alerts.push({
        id: `blocked-${task.id}`,
        level: 'error',
        title: '任务阻塞',
        message: `任务 "${task.title}" 被 ${task.blocked_by.length} 个任务阻塞`,
        source: '任务依赖',
        timestamp: new Date().toISOString(),
        taskId: task.id,
      });
    }
  });
  
  return alerts;
}

/**
 * 检测工具调用失败
 */
function detectToolFailures(events: RuntimeEventV2[]): BlockingAlert[] {
  const alerts: BlockingAlert[] = [];
  
  const recentErrors = events.filter(
    e => e.severity === 'error' && 
    Date.now() - new Date(e.ts).getTime() < 30 * 60 * 1000 // 30分钟内
  );
  
  recentErrors.forEach(error => {
    const isToolError = 
      error.message.toLowerCase().includes('tool') ||
      error.message.toLowerCase().includes('执行') ||
      error.detail?.toLowerCase().includes('fail');
    
    if (isToolError) {
      alerts.push({
        id: `tool-error-${error.event_id}`,
        level: 'error',
        title: '工具调用失败',
        message: error.message,
        source: error.role ? `角色: ${error.role}` : '系统',
        timestamp: error.ts,
        taskId: error.task_id ?? undefined,
      });
    }
  });
  
  return alerts;
}

/**
 * 检测资源瓶颈（Worker 空闲率过高）
 */
function detectResourceBottlenecks(workers: RuntimeWorkerState[]): BlockingAlert[] {
  const alerts: BlockingAlert[] = [];
  
  if (workers.length === 0) return alerts;
  
  const idleWorkers = workers.filter(w => w.state === 'idle');
  const idleRatio = idleWorkers.length / workers.length;
  
  if (idleRatio > 0.7 && workers.length >= 2) {
    alerts.push({
      id: 'resource-idle',
      level: 'info',
      title: '资源空闲',
      message: `${idleWorkers.length}/${workers.length} Worker 处于空闲状态`,
      source: '资源调度',
      timestamp: new Date().toISOString(),
    });
  }
  
  const stuckWorkers = workers.filter(
    w => (w.state === 'claimed' || w.state === 'in_progress') &&
    Date.now() - new Date(w.updated_at).getTime() > 10 * 60 * 1000
  );
  
  if (stuckWorkers.length > 0) {
    alerts.push({
      id: 'worker-stuck',
      level: 'warning',
      title: 'Worker 停滞',
      message: `${stuckWorkers.length} 个 Worker 长时间无响应`,
      source: 'Worker 监控',
      timestamp: new Date().toISOString(),
      workerId: stuckWorkers[0]?.id,
    });
  }
  
  return alerts;
}

/**
 * 按级别排序告警
 */
function sortAlertsByLevel(alerts: BlockingAlert[]): BlockingAlert[] {
  const levelOrder: Record<AlertLevel, number> = { error: 0, warning: 1, info: 2 };
  return [...alerts].sort((a, b) => levelOrder[a.level] - levelOrder[b.level]);
}

function AlertItem({ alert }: { alert: BlockingAlert }) {
  const levelStyles = {
    error: {
      border: 'border-red-500/30',
      bg: 'bg-red-500/5',
      icon: 'text-red-400',
      badge: 'bg-red-500/20 text-red-300',
    },
    warning: {
      border: 'border-amber-500/30',
      bg: 'bg-amber-500/5',
      icon: 'text-amber-400',
      badge: 'bg-amber-500/20 text-amber-300',
    },
    info: {
      border: 'border-cyan-500/30',
      bg: 'bg-cyan-500/5',
      icon: 'text-cyan-400',
      badge: 'bg-cyan-500/20 text-cyan-300',
    },
  };
  
  const style = levelStyles[alert.level];
  
  return (
    <div className={cn('rounded-lg border p-3', style.border, style.bg)}>
      <div className="flex items-start gap-3">
        <div className={cn('mt-0.5', style.icon)}>
          {alert.level === 'error' && (
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          )}
          {alert.level === 'warning' && (
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          )}
          {alert.level === 'info' && (
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          )}
        </div>
        
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className={cn('text-[10px] px-1.5 py-0.5 rounded', style.badge)}>
              {alert.level === 'error' ? '错误' : alert.level === 'warning' ? '警告' : '信息'}
            </span>
            <span className="text-xs font-medium text-slate-200 truncate">
              {alert.title}
            </span>
          </div>
          <p className="text-xs text-slate-400 truncate">{alert.message}</p>
          <div className="flex items-center gap-2 mt-1 text-[10px] text-slate-500">
            <span>{alert.source}</span>
            <span>•</span>
            <span>{new Date(alert.timestamp).toLocaleTimeString()}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function BlockingPointAlert() {
  const tasks = useTasks();
  const workers = useWorkers();
  const events = useRuntimeEvents();
  const blockedTasks = useBlockedTasks();
  
  // 合并所有告警
  const alerts = useMemo(() => {
    const stalledTasks = detectStalledTasks(tasks);
    const toolFailures = detectToolFailures(events);
    const resourceIssues = detectResourceBottlenecks(workers);
    
    return sortAlertsByLevel([...stalledTasks, ...toolFailures, ...resourceIssues]);
  }, [tasks, workers, events]);
  
  const errorCount = alerts.filter(a => a.level === 'error').length;
  const warningCount = alerts.filter(a => a.level === 'warning').length;
  const infoCount = alerts.filter(a => a.level === 'info').length;
  
  // 告警级别摘要
  const highestLevel: AlertLevel = 
    errorCount > 0 ? 'error' : 
    warningCount > 0 ? 'warning' : 
    infoCount > 0 ? 'info' : 'info';

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="h-4 w-0.5 rounded-full bg-gradient-to-b from-amber-400/60 to-cyan-400/60" />
        <h2 className="text-xs font-bold tracking-[0.2em] text-amber-200/80">
          阻 塞 预 警
        </h2>
        
        {/* 告警计数徽章 */}
        <div className="flex items-center gap-1.5 ml-auto">
          {errorCount > 0 && (
            <span className="flex items-center gap-1 rounded-full bg-red-500/20 px-2 py-0.5 text-[10px] text-red-400">
              <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
              {errorCount}
            </span>
          )}
          {warningCount > 0 && (
            <span className="flex items-center gap-1 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] text-amber-400">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
              {warningCount}
            </span>
          )}
          {infoCount > 0 && (
            <span className="flex items-center gap-1 rounded-full bg-cyan-500/20 px-2 py-0.5 text-[10px] text-cyan-400">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
              {infoCount}
            </span>
          )}
        </div>
      </div>
      
      <div className="rounded-xl border border-slate-700/50 bg-slate-900/50 p-4">
        {alerts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-center">
            <div className="rounded-full bg-emerald-500/10 p-3 mb-2">
              <svg className="h-6 w-6 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <p className="text-xs text-slate-500">暂无阻塞点</p>
          </div>
        ) : (
          <div className="space-y-2 max-h-[240px] overflow-y-auto">
            {alerts.slice(0, 10).map(alert => (
              <AlertItem key={alert.id} alert={alert} />
            ))}
            {alerts.length > 10 && (
              <div className="text-center text-xs text-slate-500 py-2">
                还有 {alerts.length - 10} 个告警...
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

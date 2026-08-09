import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * BlockingPointAlert - 阻塞点实时监控
 *
 * 检测任务卡顿、工具调用失败、资源瓶颈
 * 告警级别：INFO / WARNING / ERROR
 */
import { cn } from '@/app/components/ui/utils';
import { useTasks, useWorkers, useRuntimeEvents, useBlockedTasks } from '@/runtime';
import { useMemo } from 'react';
/**
 * 检测任务卡顿（超过阈值时间未完成）
 */
function detectStalledTasks(tasks, thresholdMinutes = 5) {
    const alerts = [];
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
function detectToolFailures(events) {
    const alerts = [];
    const recentErrors = events.filter(e => e.severity === 'error' &&
        Date.now() - new Date(e.ts).getTime() < 30 * 60 * 1000 // 30分钟内
    );
    recentErrors.forEach(error => {
        const isToolError = error.message.toLowerCase().includes('tool') ||
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
function detectResourceBottlenecks(workers) {
    const alerts = [];
    if (workers.length === 0)
        return alerts;
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
    const stuckWorkers = workers.filter(w => (w.state === 'claimed' || w.state === 'in_progress') &&
        Date.now() - new Date(w.updated_at).getTime() > 10 * 60 * 1000);
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
function sortAlertsByLevel(alerts) {
    const levelOrder = { error: 0, warning: 1, info: 2 };
    return [...alerts].sort((a, b) => levelOrder[a.level] - levelOrder[b.level]);
}
function AlertItem({ alert }) {
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
            border: 'border-slate-500/30',
            bg: 'bg-slate-500/5',
            icon: 'text-slate-400',
            badge: 'bg-slate-500/20 text-slate-300',
        },
    };
    const style = levelStyles[alert.level];
    return (_jsx("div", { className: cn('rounded-lg border p-3', style.border, style.bg), children: _jsxs("div", { className: "flex items-start gap-3", children: [_jsxs("div", { className: cn('mt-0.5', style.icon), children: [alert.level === 'error' && (_jsx("svg", { className: "h-4 w-4", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" }) })), alert.level === 'warning' && (_jsx("svg", { className: "h-4 w-4", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" }) })), alert.level === 'info' && (_jsx("svg", { className: "h-4 w-4", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" }) }))] }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 mb-1", children: [_jsx("span", { className: cn('text-[10px] px-1.5 py-0.5 rounded', style.badge), children: alert.level === 'error' ? '错误' : alert.level === 'warning' ? '警告' : '信息' }), _jsx("span", { className: "text-xs font-medium text-slate-200 truncate", children: alert.title })] }), _jsx("p", { className: "text-xs text-slate-400 truncate", children: alert.message }), _jsxs("div", { className: "flex items-center gap-2 mt-1 text-[10px] text-slate-500", children: [_jsx("span", { children: alert.source }), _jsx("span", { children: "\u2022" }), _jsx("span", { children: new Date(alert.timestamp).toLocaleTimeString() })] })] })] }) }));
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
    const highestLevel = errorCount > 0 ? 'error' :
        warningCount > 0 ? 'warning' :
            infoCount > 0 ? 'info' : 'info';
    return (_jsxs("div", { className: "space-y-3", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: "h-4 w-0.5 rounded-full soft-divider" }), _jsx("h2", { className: "text-xs font-bold tracking-[0.2em] text-slate-300/80", children: "\u963B \u585E \u9884 \u8B66" }), _jsxs("div", { className: "flex items-center gap-1.5 ml-auto", children: [errorCount > 0 && (_jsxs("span", { className: "flex items-center gap-1 rounded-full bg-red-500/20 px-2 py-0.5 text-[10px] text-red-400", children: [_jsx("span", { className: "h-1.5 w-1.5 rounded-full bg-red-400" }), errorCount] })), warningCount > 0 && (_jsxs("span", { className: "flex items-center gap-1 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] text-amber-400", children: [_jsx("span", { className: "h-1.5 w-1.5 rounded-full bg-amber-400" }), warningCount] })), infoCount > 0 && (_jsxs("span", { className: "flex items-center gap-1 rounded-full bg-slate-500/20 px-2 py-0.5 text-[10px] text-slate-400", children: [_jsx("span", { className: "h-1.5 w-1.5 rounded-full bg-slate-400" }), infoCount] }))] })] }), _jsx("div", { className: "rounded-xl soft-panel p-4", children: alerts.length === 0 ? (_jsxs("div", { className: "flex flex-col items-center justify-center py-6 text-center", children: [_jsx("div", { className: "rounded-full bg-emerald-500/10 p-3 mb-2", children: _jsx("svg", { className: "h-6 w-6 text-emerald-400", fill: "none", viewBox: "0 0 24 24", stroke: "currentColor", children: _jsx("path", { strokeLinecap: "round", strokeLinejoin: "round", strokeWidth: 2, d: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" }) }) }), _jsx("p", { className: "text-xs text-slate-500", children: "\u6682\u65E0\u963B\u585E\u70B9" })] })) : (_jsxs("div", { className: "space-y-2 max-h-[240px] overflow-y-auto", children: [alerts.slice(0, 10).map(alert => (_jsx(AlertItem, { alert: alert }, alert.id))), alerts.length > 10 && (_jsxs("div", { className: "text-center text-xs text-slate-500 py-2", children: ["\u8FD8\u6709 ", alerts.length - 10, " \u4E2A\u544A\u8B66..."] }))] })) })] }));
}

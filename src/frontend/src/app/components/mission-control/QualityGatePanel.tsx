/**
 * QualityGatePanel - 质量门禁实时可视化
 * 
 * 显示门禁状态：PM 质量门、Director 安全门、QA 验收门
 * 颜色编码：GREEN / YELLOW / RED
 */

import { cn } from '@/app/components/ui/utils';
import { useRoles, useSummary, useRuntimeEvents } from '@/runtime';
import { RoleState, RoleType } from '@/runtime/v2';

export type GateStatus = 'green' | 'yellow' | 'red' | 'pending';

export interface QualityGate {
  id: string;
  name: string;
  status: GateStatus;
  description: string;
  detail?: string;
  lastCheck?: string;
}

/**
 * 评估 PM 质量门
 */
function evaluatePMGate(
  summary: { completed: number; failed: number; blocked: number; total: number },
  roleState: RoleState
): QualityGate {
  let status: GateStatus = 'pending';
  let detail = '';
  
  if (summary.total === 0) {
    status = 'pending';
    detail = '等待任务分配';
  } else if (summary.failed > 0) {
    status = 'red';
    detail = `${summary.failed} 个任务失败`;
  } else if (summary.blocked > 0) {
    status = 'yellow';
    detail = `${summary.blocked} 个任务被阻塞`;
  } else if (roleState === 'completed') {
    const completionRate = summary.total > 0 ? (summary.completed / summary.total) * 100 : 0;
    if (completionRate >= 90) {
      status = 'green';
      detail = `完成率 ${Math.round(completionRate)}%`;
    } else if (completionRate >= 70) {
      status = 'yellow';
      detail = `完成率 ${Math.round(completionRate)}%`;
    } else {
      status = 'red';
      detail = `完成率 ${Math.round(completionRate)}%`;
    }
  } else if (roleState === 'executing' || roleState === 'planning') {
    const completionRate = summary.total > 0 ? (summary.completed / summary.total) * 100 : 0;
    if (completionRate >= 50) {
      status = 'green';
      detail = `进行中 - ${Math.round(completionRate)}%`;
    } else {
      status = 'yellow';
      detail = '规划/执行中';
    }
  } else {
    status = 'pending';
    detail = '等待启动';
  }
  
  return {
    id: 'pm-gate',
    name: 'PM 质量门',
    status,
    description: '任务规划与执行质量',
    detail,
    lastCheck: new Date().toISOString(),
  };
}

/**
 * 评估 Director 安全门
 */
function evaluateDirectorGate(
  roleState: RoleState,
  events: ReturnType<typeof useRuntimeEvents>
): QualityGate {
  const recentErrors = events.filter(
    e => e.severity === 'error' && 
    Date.now() - new Date(e.ts).getTime() < 30 * 60 * 1000
  );
  
  const unauthorizedEvents = events.filter(
    e => e.detail?.includes('unauthorized') || e.message.includes('unauthorized')
  );
  
  let status: GateStatus = 'pending';
  let detail = '';
  
  if (roleState === 'failed') {
    status = 'red';
    detail = 'Director 执行失败';
  } else if (unauthorizedEvents.length > 0) {
    status = 'red';
    detail = `${unauthorizedEvents.length} 次越权事件`;
  } else if (recentErrors.length > 3) {
    status = 'red';
    detail = `${recentErrors.length} 个错误事件`;
  } else if (recentErrors.length > 0) {
    status = 'yellow';
    detail = `${recentErrors.length} 个警告事件`;
  } else if (roleState === 'completed') {
    status = 'green';
    detail = '执行完成';
  } else if (roleState === 'executing') {
    status = 'green';
    detail = '执行中';
  } else if (roleState === 'idle') {
    status = 'pending';
    detail = '等待执行';
  } else {
    status = 'pending';
    detail = roleState;
  }
  
  return {
    id: 'director-gate',
    name: 'Director 安全门',
    status,
    description: '代码执行与工具调用安全',
    detail,
    lastCheck: new Date().toISOString(),
  };
}

/**
 * 评估 QA 验收门
 */
function evaluateQAGate(
  roleState: RoleState,
  summary: { completed: number; failed: number; total: number }
): QualityGate {
  let status: GateStatus = 'pending';
  let detail = '';
  
  if (summary.total === 0) {
    status = 'pending';
    detail = '等待任务';
  } else if (summary.failed > 0) {
    status = 'red';
    detail = `${summary.failed} 个任务未通过`;
  } else if (roleState === 'completed') {
    if (summary.completed === summary.total) {
      status = 'green';
      detail = '全部验收通过';
    } else {
      status = 'yellow';
      detail = `${summary.completed}/${summary.total} 通过`;
    }
  } else if (roleState === 'executing' || roleState === 'verification') {
    status = 'yellow';
    detail = '验收中';
  } else {
    status = 'pending';
    detail = '等待验收';
  }
  
  return {
    id: 'qa-gate',
    name: 'QA 验收门',
    status,
    description: '质量验收与测试通过',
    detail,
    lastCheck: new Date().toISOString(),
  };
}

function GateItem({ gate }: { gate: QualityGate }) {
  const statusStyles = {
    green: {
      border: 'border-emerald-500/30',
      bg: 'bg-emerald-500/5',
      icon: 'text-emerald-400',
      badge: 'bg-emerald-500/20 text-emerald-300',
      glow: 'shadow-[0_0_12px_rgba(52,211,153,0.2)]',
    },
    yellow: {
      border: 'border-amber-500/30',
      bg: 'bg-amber-500/5',
      icon: 'text-amber-400',
      badge: 'bg-amber-500/20 text-amber-300',
      glow: 'shadow-[0_0_12px_rgba(251,191,36,0.2)]',
    },
    red: {
      border: 'border-red-500/30',
      bg: 'bg-red-500/5',
      icon: 'text-red-400',
      badge: 'bg-red-500/20 text-red-300',
      glow: 'shadow-[0_0_12px_rgba(248,113,113,0.2)]',
    },
    pending: {
      border: 'border-slate-600/30',
      bg: 'bg-slate-800/30',
      icon: 'text-slate-400',
      badge: 'bg-slate-500/20 text-slate-300',
      glow: '',
    },
  };
  
  const style = statusStyles[gate.status];
  
  return (
    <div className={cn('rounded-xl border p-4', style.border, style.bg, style.glow)}>
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className={cn('rounded-lg p-1.5', style.icon)}>
            {gate.status === 'green' && (
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
            {gate.status === 'yellow' && (
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
            {gate.status === 'red' && (
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
            {gate.status === 'pending' && (
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
          </div>
          <div>
            <div className="text-sm font-medium text-slate-200">{gate.name}</div>
            <div className="text-[10px] text-slate-500">{gate.description}</div>
          </div>
        </div>
        
        <span className={cn('text-[10px] px-2 py-0.5 rounded', style.badge)}>
          {gate.status === 'green' ? '通过' : gate.status === 'yellow' ? '警告' : gate.status === 'red' ? '失败' : '待检'}
        </span>
      </div>
      
      {gate.detail && (
        <div className="text-xs text-slate-400 mt-2 pl-8">
          {gate.detail}
        </div>
      )}
    </div>
  );
}

export function QualityGatePanel() {
  const roles = useRoles();
  const summary = useSummary();
  const events = useRuntimeEvents();
  
  // 评估各门禁状态
  const pmGate = evaluatePMGate(summary, roles.PM.state);
  const directorGate = evaluateDirectorGate(roles.Director.state, events);
  const qaGate = evaluateQAGate(roles.QA.state, summary);
  
  const gates = [pmGate, directorGate, qaGate];
  
  // 总体状态
  const passedGates = gates.filter(g => g.status === 'green').length;
  const failedGates = gates.filter(g => g.status === 'red').length;
  const warningGates = gates.filter(g => g.status === 'yellow').length;
  
  const overallStatus: GateStatus = 
    failedGates > 0 ? 'red' : 
    warningGates > 0 ? 'yellow' : 
    passedGates === gates.length ? 'green' : 
    'pending';

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="h-4 w-0.5 rounded-full bg-gradient-to-b from-amber-400/60 to-cyan-400/60" />
        <h2 className="text-xs font-bold tracking-[0.2em] text-amber-200/80">
          质 量 门 禁
        </h2>
        
        {/* 状态摘要 */}
        <div className="flex items-center gap-2 ml-auto">
          <div className="flex items-center gap-1 text-xs">
            <span className="h-2 w-2 rounded-full bg-emerald-400" />
            <span className="text-emerald-400">{passedGates}</span>
          </div>
          <div className="flex items-center gap-1 text-xs">
            <span className="h-2 w-2 rounded-full bg-amber-400" />
            <span className="text-amber-400">{warningGates}</span>
          </div>
          <div className="flex items-center gap-1 text-xs">
            <span className="h-2 w-2 rounded-full bg-red-400" />
            <span className="text-red-400">{failedGates}</span>
          </div>
        </div>
      </div>
      
      <div className="space-y-2">
        {gates.map(gate => (
          <GateItem key={gate.id} gate={gate} />
        ))}
      </div>
    </div>
  );
}

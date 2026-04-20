/**
 * MetricsDashboard - 北极星指标仪表盘
 * 
 * 展示关键交付指标：交付成功率、PM 评分、Director 安全状态
 */

import { cn } from '@/app/components/ui/utils';
import { useSummary, useRoles, useRuntimeEvents, useCurrentPhase } from '@/runtime';
import { RoleState, Phase } from '@/runtime/v2';

interface MetricCardProps {
  label: string;
  value: string | number;
  status: 'good' | 'warning' | 'critical' | 'neutral';
  icon: React.ReactNode;
  subValue?: string;
}

function MetricCard({ label, value, status, icon, subValue }: MetricCardProps) {
  const statusStyles = {
    good: 'border-emerald-500/30 bg-emerald-500/5',
    warning: 'border-amber-500/30 bg-amber-500/5',
    critical: 'border-red-500/30 bg-red-500/5',
    neutral: 'border-slate-700/50 bg-slate-800/30',
  };

  const valueColors = {
    good: 'text-emerald-400',
    warning: 'text-amber-400',
    critical: 'text-red-400',
    neutral: 'text-slate-400',
  };

  return (
    <div className={cn('rounded-xl border p-4', statusStyles[status])}>
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">
            {label}
          </div>
          <div className={cn('text-3xl font-bold', valueColors[status])}>
            {value}
          </div>
          {subValue && (
            <div className="text-xs text-slate-500">{subValue}</div>
          )}
        </div>
        <div className={cn('rounded-lg p-2', status === 'good' && 'text-emerald-400', status === 'warning' && 'text-amber-400', status === 'critical' && 'text-red-400', status === 'neutral' && 'text-slate-400')}>
          {icon}
        </div>
      </div>
    </div>
  );
}

/**
 * 计算交付成功率
 */
function calculateDeliverySuccessRate(summary: { completed: number; failed: number; total: number }): number {
  if (summary.total === 0) return 0;
  return Math.round((summary.completed / summary.total) * 100);
}

/**
 * 获取 PM 评分（基于任务完成质量）
 */
function calculatePMScore(summary: { completed: number; failed: number; blocked: number; total: number }): { score: number; status: 'good' | 'warning' | 'critical' | 'neutral' } {
  if (summary.total === 0) return { score: 0, status: 'neutral' };
  
  const completionRate = summary.completed / summary.total;
  const failureRate = summary.failed / summary.total;
  const blockedRate = summary.blocked / summary.total;
  
  // 评分算法：完成率权重 70%，失败率权重 -20%，阻塞率权重 -10%
  let score = Math.round(completionRate * 100 - failureRate * 20 - blockedRate * 10);
  score = Math.max(0, Math.min(100, score));
  
  let status: 'good' | 'warning' | 'critical' | 'neutral' = 'good';
  if (score < 60) status = 'critical';
  else if (score < 80) status = 'warning';
  
  return { score, status };
}

/**
 * 获取 Director 安全状态
 */
function getDirectorSafetyStatus(
  roleState: RoleState,
  recentEvents: ReturnType<typeof useRuntimeEvents>
): { status: 'good' | 'warning' | 'critical' | 'neutral'; detail: string } {
  // 检查最近的错误事件
  const errorEvents = recentEvents.filter(e => e.severity === 'error');
  const toolFailures = recentEvents.filter(e => 
    e.severity === 'error' && 
    (e.message.includes('tool') || e.message.includes('Tool') || e.detail?.includes('unauthorized'))
  );
  
  if (roleState === 'failed') {
    return { status: 'critical', detail: 'Director 执行失败' };
  }
  if (toolFailures.length > 0) {
    return { status: 'critical', detail: `${toolFailures.length} 次工具调用失败` };
  }
  if (errorEvents.length > 2) {
    return { status: 'warning', detail: `${errorEvents.length} 个错误事件` };
  }
  if (roleState === 'executing') {
    return { status: 'good', detail: '执行中' };
  }
  if (roleState === 'completed') {
    return { status: 'good', detail: '已完成' };
  }
  return { status: 'neutral', detail: '空闲' };
}

export function MetricsDashboard() {
  const summary = useSummary();
  const roles = useRoles();
  const events = useRuntimeEvents();
  
  // 计算指标
  const deliveryRate = calculateDeliverySuccessRate(summary);
  const pmScore = calculatePMScore(summary);
  const directorSafety = getDirectorSafetyStatus(roles.Director.state, events);
  
  // 确定总体状态
  const overallStatus = 
    deliveryRate >= 90 && pmScore.score >= 80 && directorSafety.status === 'good' ? 'good' :
    deliveryRate >= 70 && pmScore.score >= 60 && directorSafety.status !== 'critical' ? 'warning' :
    'critical';

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="h-4 w-0.5 rounded-full bg-gradient-to-b from-amber-400/60 to-cyan-400/60" />
        <h2 className="text-xs font-bold tracking-[0.2em] text-amber-200/80">
          北 极 星 指 标
        </h2>
      </div>
      
      <div className="grid grid-cols-1 gap-3">
        {/* 交付成功率 */}
        <MetricCard
          label="交付成功率"
          value={`${deliveryRate}%`}
          status={deliveryRate >= 90 ? 'good' : deliveryRate >= 70 ? 'warning' : 'critical'}
          subValue={`${summary.completed}/${summary.total} 任务已完成`}
          icon={
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          }
        />
        
        {/* PM 评分 */}
        <MetricCard
          label="PM 质量评分"
          value={pmScore.score}
          status={pmScore.status}
          subValue={pmScore.status === 'good' ? '任务规划优秀' : pmScore.status === 'warning' ? '存在改进空间' : '需要关注'}
          icon={
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
            </svg>
          }
        />
        
        {/* Director 安全状态 */}
        <MetricCard
          label="Director 安全状态"
          value={directorSafety.status === 'good' ? '安全' : directorSafety.status === 'warning' ? '警告' : '危险'}
          status={directorSafety.status}
          subValue={directorSafety.detail}
          icon={
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          }
        />
      </div>
    </div>
  );
}

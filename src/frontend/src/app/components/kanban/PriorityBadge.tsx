import { memo } from 'react';
import { AlertOctagon, AlertCircle, Minus, ArrowDown } from 'lucide-react';

type Priority = 'low' | 'medium' | 'high' | 'urgent';

interface PriorityBadgeProps {
  priority: Priority;
  showLabel?: boolean;
}

const PRIORITY_CONFIG: Record<Priority, { label: string; icon: typeof AlertCircle; color: string; bg: string }> = {
  urgent: {
    label: 'Urgent',
    icon: AlertOctagon,
    color: 'text-red-400',
    bg: 'bg-red-500/20',
  },
  high: {
    label: 'High',
    icon: AlertCircle,
    color: 'text-orange-400',
    bg: 'bg-orange-500/20',
  },
  medium: {
    label: 'Medium',
    icon: Minus,
    color: 'text-yellow-400',
    bg: 'bg-yellow-500/20',
  },
  low: {
    label: 'Low',
    icon: ArrowDown,
    color: 'text-slate-400',
    bg: 'bg-slate-500/20',
  },
};

function PriorityBadgeComponent({ priority, showLabel = true }: PriorityBadgeProps) {
  const config = PRIORITY_CONFIG[priority] ?? PRIORITY_CONFIG.medium;
  const Icon = config.icon;

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${config.color} ${config.bg}`}
      title={`Priority: ${config.label}`}
    >
      <Icon className="size-3" />
      {showLabel && <span>{config.label}</span>}
    </span>
  );
}

export const PriorityBadge = memo(PriorityBadgeComponent);

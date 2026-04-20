/**
 * NavButton - DirectorWorkspace导航按钮组件
 */
import { cn } from '@/app/components/ui/utils';

interface NavButtonProps {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
}

export function NavButton({ icon, label, active, onClick }: NavButtonProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-10 h-10 rounded-xl flex flex-col items-center justify-center gap-0.5 transition-all duration-200',
        active
          ? 'bg-indigo-500/15 text-indigo-400 shadow-lg shadow-indigo-500/10'
          : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
      )}
      title={label}
    >
      {icon}
      <span className="text-[8px] font-medium">{label}</span>
    </button>
  );
}

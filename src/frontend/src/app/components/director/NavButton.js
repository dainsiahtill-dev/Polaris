import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * NavButton - DirectorWorkspace导航按钮组件
 */
import { cn } from '@/app/components/ui/utils';
export function NavButton({ icon, label, active, onClick }) {
    return (_jsxs("button", { onClick: onClick, className: cn('w-10 h-10 rounded-xl flex flex-col items-center justify-center gap-0.5 transition-all duration-200', active
            ? 'bg-indigo-500/[0.15] text-indigo-400 shadow-lg shadow-indigo-500/10'
            : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'), title: label, children: [icon, _jsx("span", { className: "text-[8px] font-medium", children: label })] }));
}

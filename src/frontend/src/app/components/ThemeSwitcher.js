import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Sun, Moon, Monitor } from 'lucide-react';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger, } from '@/app/components/ui/dropdown-menu';
import { Button } from '@/app/components/ui/button';
import { useTheme } from '@/app/hooks/useTheme';
const THEME_OPTIONS = [
    { value: 'light', label: '浅色', icon: Sun },
    { value: 'dark', label: '深色', icon: Moon },
    { value: 'system', label: '跟随系统', icon: Monitor },
];
export const ThemeSwitcher = () => {
    const { theme, setTheme } = useTheme();
    return (_jsxs(DropdownMenu, { children: [_jsx(DropdownMenuTrigger, { asChild: true, children: _jsxs(Button, { variant: "ghost", size: "icon", "aria-label": "\u5207\u6362\u4E3B\u9898", className: "relative", children: [_jsx(Sun, { className: "h-[1.2rem] w-[1.2rem] rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" }), _jsx(Moon, { className: "absolute h-[1.2rem] w-[1.2rem] rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" })] }) }), _jsx(DropdownMenuContent, { align: "end", children: THEME_OPTIONS.map((option) => {
                    const Icon = option.icon;
                    const isActive = theme === option.value;
                    return (_jsxs(DropdownMenuItem, { onClick: () => setTheme(option.value), className: isActive ? 'bg-accent' : '', children: [_jsx(Icon, { className: "mr-2 h-4 w-4" }), option.label, isActive && (_jsx("span", { className: "ml-auto text-xs text-muted-foreground", children: "\u5F53\u524D" }))] }, option.value));
                }) })] }));
};

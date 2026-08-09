import { jsx as _jsx } from "react/jsx-runtime";
import { cn } from "./utils";
function CyberInput({ className, type, variant = "default", ...props }) {
    const baseStyles = cn("flex h-9 w-full min-w-0 rounded-md border px-3 py-1 text-base transition-all duration-200 outline-none", "md:text-sm", "border-border bg-[rgba(6,15,28,0.88)]", "text-text-main placeholder:text-text-dim", "shadow-[inset_0_1px_0_rgba(178,245,255,0.14),inset_0_-1px_0_rgba(0,0,0,0.38)]", "hover:border-accent/45 hover:bg-[rgba(10,25,44,0.92)]", "focus:border-accent/70 focus:bg-[rgba(10,25,44,0.96)] focus:ring-2 focus:ring-accent/25", "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50", "aria-invalid:border-status-error/60 aria-invalid:ring-2 aria-invalid:ring-status-error/20", variant === "password" && [
        "font-mono tracking-wider",
    ], className);
    return (_jsx("input", { type: type, "data-slot": "cyber-input", className: baseStyles, ...props }));
}
function CyberTextarea({ className, ...props }) {
    return (_jsx("textarea", { "data-slot": "cyber-textarea", className: cn("flex w-full min-w-0 rounded-md border px-3 py-2 text-base transition-all duration-200 outline-none", "md:text-sm", "border-border bg-[rgba(6,15,28,0.88)] text-text-main placeholder:text-text-dim", "shadow-[inset_0_1px_0_rgba(178,245,255,0.14),inset_0_-1px_0_rgba(0,0,0,0.38)]", "hover:border-accent/45 hover:bg-[rgba(10,25,44,0.92)]", "focus:border-accent/70 focus:bg-[rgba(10,25,44,0.96)] focus:ring-2 focus:ring-accent/25", "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50", "aria-invalid:border-status-error/60 aria-invalid:ring-2 aria-invalid:ring-status-error/20", "min-h-[80px] resize-y", className), ...props }));
}
function CyberSelect({ className, ...props }) {
    return (_jsx("select", { "data-slot": "cyber-select", className: cn("flex h-9 w-full min-w-0 rounded-md border px-3 py-1 text-base transition-all duration-200 outline-none", "md:text-sm appearance-none", "border-border bg-[rgba(6,15,28,0.88)] text-text-main", "shadow-[inset_0_1px_0_rgba(178,245,255,0.14),inset_0_-1px_0_rgba(0,0,0,0.38)]", "hover:border-accent/45 hover:bg-[rgba(10,25,44,0.92)]", "focus:border-accent/70 focus:bg-[rgba(10,25,44,0.96)] focus:ring-2 focus:ring-accent/25", "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50", "cursor-pointer", "bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2300d8ff%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-10", className), ...props }));
}
export { CyberInput, CyberTextarea, CyberSelect };

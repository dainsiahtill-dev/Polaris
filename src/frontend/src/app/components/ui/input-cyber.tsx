import * as React from "react";
import { cn } from "./utils";

export interface CyberInputProps extends React.ComponentProps<"input"> {
  variant?: "default" | "password" | "glow";
}

function CyberInput({ className, type, variant = "default", ...props }: CyberInputProps) {
  const baseStyles = cn(
    // Base structure
    "flex h-9 w-full min-w-0 rounded-md border px-3 py-1 text-base transition-all duration-200 outline-none",
    "md:text-sm",
    
    // Cyberpunk Dark Theme - Background
    "bg-black/40",
    
    // Cyberpunk Border - subtle violet tint
    "border-white/10",
    
    // Text colors
    "text-slate-100 placeholder:text-slate-500",
    
    // Focus states - Cyberpunk glow effect
    "focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60",
    
    // Hover state
    "hover:border-violet-400/30 hover:bg-black/50",
    
    // Disabled state
    "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
    
    // Invalid state
    "aria-invalid:border-red-500/50 aria-invalid:ring-2 aria-invalid:ring-red-500/20",
    
    // Variant specific styles
    variant === "glow" && [
      "border-violet-500/30",
      "shadow-[0_0_10px_rgba(139,92,246,0.15)]",
      "focus:shadow-[0_0_15px_rgba(139,92,246,0.3)]",
    ],
    
    variant === "password" && [
      "font-mono tracking-wider",
    ],
    
    className
  );

  return (
    <input
      type={type}
      data-slot="cyber-input"
      className={baseStyles}
      {...props}
    />
  );
}

// Specialized Cyberpunk Textarea
function CyberTextarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="cyber-textarea"
      className={cn(
        "flex w-full min-w-0 rounded-md border px-3 py-2 text-base transition-all duration-200 outline-none",
        "md:text-sm",
        "bg-black/40 border-white/10 text-slate-100 placeholder:text-slate-500",
        "focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60",
        "hover:border-violet-400/30 hover:bg-black/50",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        "aria-invalid:border-red-500/50 aria-invalid:ring-2 aria-invalid:ring-red-500/20",
        "min-h-[80px] resize-y",
        className
      )}
      {...props}
    />
  );
}

// Cyberpunk Select
function CyberSelect({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      data-slot="cyber-select"
      className={cn(
        "flex h-9 w-full min-w-0 rounded-md border px-3 py-1 text-base transition-all duration-200 outline-none",
        "md:text-sm appearance-none",
        "bg-black/40 border-white/10 text-slate-100",
        "focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60",
        "hover:border-violet-400/30 hover:bg-black/50",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        "cursor-pointer",
        "bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2394a3b8%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-10",
        className
      )}
      {...props}
    />
  );
}

export { CyberInput, CyberTextarea, CyberSelect };

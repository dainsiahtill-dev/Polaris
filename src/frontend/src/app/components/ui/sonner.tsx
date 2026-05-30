"use client";

import { Toaster as Sonner, ToasterProps } from "sonner";

const POLARIS_TOAST_CLASSNAMES: NonNullable<ToasterProps["toastOptions"]>["classNames"] = {
  toast:
    "border border-white/10 bg-slate-950/95 text-slate-100 shadow-2xl shadow-black/45 backdrop-blur-md",
  title: "text-sm font-semibold text-slate-100",
  description: "text-xs text-slate-400",
  actionButton:
    "border border-cyan-400/30 bg-cyan-500/15 text-cyan-100 hover:bg-cyan-500/25",
  cancelButton:
    "border border-white/10 bg-white/5 text-slate-200 hover:bg-white/10",
  closeButton: "border border-white/10 bg-slate-900 text-slate-300 hover:bg-slate-800",
  icon: "text-cyan-300",
  success: "border-emerald-400/20",
  warning: "border-amber-400/25",
  error: "border-rose-400/25",
};

const Toaster = ({
  theme,
  toastOptions,
  visibleToasts,
  gap,
  offset,
  style,
  className,
  ...props
}: ToasterProps) => {
  const mergedClassNames = {
    ...POLARIS_TOAST_CLASSNAMES,
    ...toastOptions?.classNames,
  };

  return (
    <Sonner
      {...props}
      theme={theme ?? "dark"}
      className={["toaster group", className].filter(Boolean).join(" ")}
      visibleToasts={visibleToasts ?? 3}
      gap={gap ?? 8}
      offset={offset ?? { right: 20, bottom: 88 }}
      style={
        {
          "--normal-bg": "rgba(2, 6, 23, 0.96)",
          "--normal-text": "#e2e8f0",
          "--normal-border": "rgba(148, 163, 184, 0.24)",
          "--success-bg": "rgba(6, 78, 59, 0.94)",
          "--success-text": "#d1fae5",
          "--success-border": "rgba(52, 211, 153, 0.32)",
          "--warning-bg": "rgba(69, 46, 5, 0.94)",
          "--warning-text": "#fde68a",
          "--warning-border": "rgba(251, 191, 36, 0.34)",
          "--error-bg": "rgba(76, 5, 25, 0.94)",
          "--error-text": "#fecdd3",
          "--error-border": "rgba(251, 113, 133, 0.34)",
          ...style,
        } as React.CSSProperties
      }
      toastOptions={{
        ...toastOptions,
        classNames: mergedClassNames,
      }}
    />
  );
};

export { Toaster };

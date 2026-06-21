"use client";

import { Toaster as Sonner, ToasterProps } from "sonner";

const POLARIS_TOAST_CLASSNAMES: NonNullable<ToasterProps["toastOptions"]>["classNames"] = {
  toast:
    "border border-accent/35 bg-[rgba(6,15,28,0.92)] text-text-main shadow-2xl shadow-cyan-500/15 backdrop-blur-xl",
  title: "text-sm font-semibold text-text-main",
  description: "text-xs text-text-dim",
  actionButton:
    "border border-accent/35 bg-accent/15 text-accent-text hover:bg-accent/25",
  cancelButton:
    "border border-border bg-[rgba(10,25,44,0.78)] text-text-muted hover:bg-accent/10",
  closeButton: "border border-border bg-[rgba(10,25,44,0.82)] text-text-muted hover:bg-accent/10",
  icon: "text-accent",
  success: "border-status-success/35",
  warning: "border-status-warning/35",
  error: "border-status-error/35",
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
          "--normal-bg": "rgba(6, 15, 28, 0.94)",
          "--normal-text": "#eafcff",
          "--normal-border": "rgba(90, 217, 255, 0.30)",
          "--success-bg": "rgba(8, 34, 33, 0.94)",
          "--success-text": "#62ffcf",
          "--success-border": "rgba(98, 255, 207, 0.32)",
          "--warning-bg": "rgba(42, 28, 8, 0.94)",
          "--warning-text": "#ffb848",
          "--warning-border": "rgba(255, 184, 72, 0.32)",
          "--error-bg": "rgba(42, 10, 18, 0.94)",
          "--error-text": "#ff4d6d",
          "--error-border": "rgba(255, 77, 109, 0.32)",
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

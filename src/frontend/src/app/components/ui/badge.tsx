import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "./utils";

/**
 * Badge — unified semantic label component.
 *
 * Features:
 * - Basic variants: default, secondary, destructive, outline
 * - Status variants: success, warning, error, info, pending, gold, accent
 * - Priority variants: urgent, high, medium, low
 * - Style variants: soft, filled, outlined, dot (with optional pulse animation)
 *
 * Colors map to the project's theme tokens:
 *   success → status-success (jade green)
 *   warning → status-warning (gold)
 *   error   → status-error   (cinnabar red)
 *   info    → status-info    (jade cyan)
 *   default → text-dim / neutral
 */
const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full text-[11px] font-medium leading-none whitespace-nowrap transition-colors',
  {
    variants: {
      variant: {
        // Basic variants
        default: 'px-2 py-0.5 border bg-transparent text-text-dim border-white/10',
        secondary: 'px-2 py-0.5 border bg-transparent text-text-muted border-white/10',
        destructive: 'px-2 py-0.5 bg-status-error/20 text-status-error',
        outline: 'px-2 py-0.5 border border-white/20 text-text-dim',

        // Status variants (soft style)
        'status:success': 'px-2 py-0.5 border bg-status-success/10 border-status-success/20 text-status-success',
        'status:warning': 'px-2 py-0.5 border bg-status-warning/10 border-status-warning/20 text-status-warning',
        'status:error': 'px-2 py-0.5 border bg-status-error/10 border-status-error/20 text-status-error',
        'status:info': 'px-2 py-0.5 border bg-status-info/10 border-status-info/20 text-status-info',
        'status:pending': 'px-2 py-0.5 border bg-status-warning/10 border-status-warning/20 text-status-warning',
        'status:gold': 'px-2 py-0.5 border bg-gold/10 border-gold/30 text-gold',
        'status:accent': 'px-2 py-0.5 border bg-accent/10 border-accent/20 text-accent-text',

        // Priority variants
        'priority:urgent': 'px-2 py-0.5 border bg-status-error/20 border-status-error/30 text-status-error',
        'priority:high': 'px-2 py-0.5 border bg-status-warning/20 border-status-warning/30 text-status-warning',
        'priority:medium': 'px-2 py-0.5 border bg-status-info/10 border-status-info/20 text-status-info',
        'priority:low': 'px-2 py-0.5 border bg-white/5 border-white/10 text-text-dim',

        // Style variants with dot indicator
        'dot:default': 'pl-1.5 pr-2 py-0.5 border bg-white/5 border-white/10 text-text-dim',
        'dot:success': 'pl-1.5 pr-2 py-0.5 border bg-status-success/10 border-status-success/20 text-status-success',
        'dot:warning': 'pl-1.5 pr-2 py-0.5 border bg-status-warning/10 border-status-warning/20 text-status-warning',
        'dot:error': 'pl-1.5 pr-2 py-0.5 border bg-status-error/10 border-status-error/20 text-status-error',
        'dot:info': 'pl-1.5 pr-2 py-0.5 border bg-status-info/10 border-status-info/20 text-status-info',
        'dot:gold': 'pl-1.5 pr-2 py-0.5 border bg-gold/10 border-gold/30 text-gold',
        'dot:accent': 'pl-1.5 pr-2 py-0.5 border bg-accent/10 border-accent/20 text-accent-text',

        // Filled style variants
        'filled:default': 'px-2 py-0.5 bg-white/10 text-text-main',
        'filled:success': 'px-2 py-0.5 bg-status-success/20 text-status-success',
        'filled:warning': 'px-2 py-0.5 bg-status-warning/20 text-status-warning',
        'filled:error': 'px-2 py-0.5 bg-status-error/20 text-status-error',
        'filled:info': 'px-2 py-0.5 bg-status-info/20 text-status-info',
        'filled:gold': 'px-2 py-0.5 bg-gold/20 text-gold',
        'filled:accent': 'px-2 py-0.5 bg-accent/20 text-accent-text',

        // Outlined style variants
        'outlined:default': 'px-2 py-0.5 border border-white/20 text-text-dim',
        'outlined:success': 'px-2 py-0.5 border border-status-success/40 text-status-success',
        'outlined:warning': 'px-2 py-0.5 border border-status-warning/40 text-status-warning',
        'outlined:error': 'px-2 py-0.5 border border-status-error/40 text-status-error',
        'outlined:info': 'px-2 py-0.5 border border-status-info/40 text-status-info',
        'outlined:gold': 'px-2 py-0.5 border border-gold/40 text-gold',
        'outlined:accent': 'px-2 py-0.5 border border-accent/40 text-accent-text',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
);

const dotColorMap: Record<string, string> = {
  default: 'bg-text-dim',
  success: 'bg-status-success',
  warning: 'bg-status-warning',
  error: 'bg-status-error',
  info: 'bg-status-info',
  gold: 'bg-gold',
  accent: 'bg-accent',
};

export type BadgeVariant = VariantProps<typeof badgeVariants>['variant'];
export type BadgeColor = 'default' | 'success' | 'warning' | 'error' | 'info' | 'gold' | 'accent';
export type BadgeStyleVariant = 'soft' | 'filled' | 'outlined' | 'dot';

interface BadgeProps extends Omit<React.HTMLAttributes<HTMLSpanElement>, 'color'> {
  variant?: BadgeVariant;
  asChild?: boolean;
  /** Show a pulsing dot indicator before the label (only applies when using dot: variants) */
  pulse?: boolean;
}

function BadgeComponent({
  className,
  variant = 'default',
  asChild = false,
  pulse = false,
  children,
  ...props
}: BadgeProps) {
  const Comp = asChild ? Slot : 'span';

  // Determine dot color from variant
  let dotColor: string | undefined;
  if (variant && variant.startsWith('dot:')) {
    const colorKey = variant.replace('dot:', '') as BadgeColor;
    dotColor = dotColorMap[colorKey] ?? dotColorMap.default;
  }

  return (
    <Comp data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props}>
      {dotColor && (
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          {pulse && (
            <span className={cn('absolute inline-flex h-full w-full animate-ping rounded-full opacity-75', dotColor)} />
          )}
          <span className={cn('relative inline-flex h-1.5 w-1.5 rounded-full', dotColor)} />
        </span>
      )}
      {children}
    </Comp>
  );
}

// StatusBadge provides a convenient API for color+style combinations
export type StatusBadgeColor = BadgeColor;
export type StatusBadgeStyleVariant = BadgeStyleVariant;

interface StatusBadgeProps extends Omit<React.HTMLAttributes<HTMLSpanElement>, 'color'> {
  color?: StatusBadgeColor;
  variant?: StatusBadgeStyleVariant;
  pulse?: boolean;
}

function StatusBadgeComponent({
  className,
  color = 'default',
  variant = 'soft',
  pulse = false,
  children,
  ...props
}: StatusBadgeProps) {
  // Map style variant to badge variant
  const getBadgeVariant = (): BadgeVariant => {
    if (variant === 'soft') {
      const variantMap: Record<BadgeColor, BadgeVariant> = {
        default: 'default',
        success: 'status:success',
        warning: 'status:warning',
        error: 'status:error',
        info: 'status:info',
        gold: 'status:gold',
        accent: 'status:accent',
      };
      return variantMap[color] ?? 'default';
    }

    return `${variant}:${color}` as BadgeVariant;
  };

  const dotColor = dotColorMap[color] ?? dotColorMap.default;
  const badgeVariant = getBadgeVariant();

  return (
    <span data-slot="badge" className={cn(badgeVariants({ variant: badgeVariant }), className)} {...props}>
      {variant === 'dot' && (
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          {pulse && (
            <span className={cn('absolute inline-flex h-full w-full animate-ping rounded-full opacity-75', dotColor)} />
          )}
          <span className={cn('relative inline-flex h-1.5 w-1.5 rounded-full', dotColor)} />
        </span>
      )}
      {children}
    </span>
  );
}

export const Badge = React.memo(BadgeComponent);
export const StatusBadge = React.memo(StatusBadgeComponent);
export { badgeVariants };

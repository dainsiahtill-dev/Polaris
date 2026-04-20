import { useRef, useEffect } from 'react';
import { m, animate, useInView, useMotionValue, useTransform } from 'framer-motion';

interface AnimateCountUpProps {
  to: number;
  from?: number;
  duration?: number;
  toFixed?: number;
  once?: boolean;
  className?: string;
  /** Optional prefix, e.g. "#" */
  prefix?: string;
  /** Optional suffix, e.g. "%" */
  suffix?: string;
  /** Pad with leading zeros to this length */
  padStart?: number;
}

/**
 * Animated number counter — ported from minimal-kit, rewritten for Tailwind/framer-motion.
 * Counts from `from` to `to` when the element enters the viewport.
 */
export function AnimateCountUp({
  to,
  from = 0,
  duration = 1.2,
  toFixed = 0,
  once = true,
  className,
  prefix,
  suffix,
  padStart,
}: AnimateCountUpProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const count = useMotionValue(from);

  const rounded = useTransform(count, (v) => {
    const fixed = toFixed > 0 ? v.toFixed(toFixed) : String(Math.round(v));
    return padStart ? fixed.padStart(padStart, '0') : fixed;
  });

  const inView = useInView(ref, { once, amount: 0.5 });

  useEffect(() => {
    if (inView) {
      animate(count, to, { duration, ease: 'easeOut' });
    }
  }, [inView, to, duration, count]);

  // Re-animate when `to` changes (e.g. iteration number increments)
  useEffect(() => {
    const current = count.get();
    if (current !== to) {
      animate(count, to, { duration: 0.6, ease: 'easeOut' });
    }
  }, [to, count, duration]);

  return (
    <span ref={ref} className={className}>
      {prefix}
      <m.span>{rounded}</m.span>
      {suffix}
    </span>
  );
}

import { useRef, useState, useEffect, useCallback } from 'react';
import {
  m,
  useMotionValue,
  useTransform,
  useAnimationFrame,
  useMotionTemplate,
} from 'framer-motion';
import { cn } from './utils';

interface AnimateBorderProps {
  children?: React.ReactNode;
  className?: string;
  /** Color of the moving glow dot — any CSS color string */
  glowColor?: string;
  /** Size of the glow dot in px */
  glowSize?: number;
  /** Seconds for one full revolution */
  duration?: number;
  /** Border radius passed to the SVG rect */
  rx?: string;
  /** Extra classes for the outer wrapper */
  wrapperClassName?: string;
  /** Show a static outline border */
  showOutline?: boolean;
  outlineClassName?: string;
}

/**
 * AnimateBorder — a moving glow dot that travels along the element's border.
 * Ported from minimal-kit's AnimateBorder, rewritten for Tailwind + framer-motion.
 *
 * Usage:
 *   <AnimateBorder glowColor="#c85040" duration={4} showOutline>
 *     <div className="p-4">content</div>
 *   </AnimateBorder>
 */
export function AnimateBorder({
  children,
  className,
  glowColor = '#c85040',
  glowSize = 80,
  duration = 6,
  rx = '8',
  wrapperClassName,
  showOutline = true,
  outlineClassName,
}: AnimateBorderProps) {
  const svgRectRef = useRef<SVGRectElement>(null);
  const [hidden, setHidden] = useState(false);
  const progress = useMotionValue(0);

  // Hide when element is display:none (e.g. collapsed panels)
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const check = () => setHidden(getComputedStyle(el).display === 'none');
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  const getPoint = useCallback(
    (val: number) => {
      try {
        const rect = svgRectRef.current;
        if (!rect) return { x: 0, y: 0 };
        const len = rect.getTotalLength();
        const point = rect.getPointAtLength(val % len);
        return point ?? { x: 0, y: 0 };
      } catch {
        return { x: 0, y: 0 };
      }
    },
    []
  );

  useAnimationFrame((time) => {
    if (hidden) return;
    try {
      const rect = svgRectRef.current;
      if (!rect) return;
      const len = rect.getTotalLength();
      const pxPerMs = len / (duration * 1000);
      progress.set((time * pxPerMs) % len);
    } catch {
      // SVG not ready yet
    }
  });

  const x = useTransform(progress, (v) => getPoint(v).x);
  const y = useTransform(progress, (v) => getPoint(v).y);
  const transform = useMotionTemplate`translateX(${x}px) translateY(${y}px) translateX(-50%) translateY(-50%)`;

  return (
    <div
      ref={containerRef}
      className={cn('relative overflow-hidden', wrapperClassName)}
    >
      {/* Static outline */}
      {showOutline && (
        <div
          className={cn(
            'pointer-events-none absolute inset-0 rounded-[inherit] border border-white/10',
            outlineClassName
          )}
        />
      )}

      {/* SVG path tracker */}
      <svg
        xmlns="http://www.w3.org/2000/svg"
        preserveAspectRatio="none"
        width="100%"
        height="100%"
        className="pointer-events-none absolute inset-0"
        aria-hidden="true"
      >
        <rect
          ref={svgRectRef}
          fill="none"
          width="100%"
          height="100%"
          rx={rx}
          ry={rx}
        />
      </svg>

      {/* Moving glow dot */}
      <m.span
        aria-hidden="true"
        style={{
          transform,
          width: glowSize,
          height: glowSize,
          background: `radial-gradient(${glowColor} 0%, transparent 70%)`,
        }}
        className="pointer-events-none absolute opacity-70 blur-[6px]"
      />

      {/* Content */}
      <div className={cn('relative', className)}>{children}</div>
    </div>
  );
}

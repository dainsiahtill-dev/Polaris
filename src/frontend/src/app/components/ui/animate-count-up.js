import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useRef, useEffect } from 'react';
import { m, animate, useInView, useMotionValue, useTransform } from 'framer-motion';
/**
 * Animated number counter — ported from minimal-kit, rewritten for Tailwind/framer-motion.
 * Counts from `from` to `to` when the element enters the viewport.
 */
export function AnimateCountUp({ to, from = 0, duration = 1.2, toFixed = 0, once = true, className, prefix, suffix, padStart, }) {
    const ref = useRef(null);
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
    return (_jsxs("span", { ref: ref, className: className, children: [prefix, _jsx(m.span, { children: rounded }), suffix] }));
}

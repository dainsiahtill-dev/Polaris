/** Expedition-glyph icon set — thin custom strokes, no external icon font. */
import type { ReactElement, SVGProps } from 'react';

function Glyph({ children, ...props }: SVGProps<SVGSVGElement> & { children: ReactElement | ReactElement[] }): ReactElement {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {children}
    </svg>
  );
}

export function PolarisMark(props: SVGProps<SVGSVGElement>): ReactElement {
  return (
    <svg viewBox="0 0 48 48" fill="none" aria-hidden="true" {...props}>
      <circle cx="24" cy="24" r="21" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="24" cy="24" r="16.5" stroke="currentColor" strokeWidth="0.7" opacity="0.6" />
      <circle cx="24" cy="24" r="12" stroke="currentColor" strokeWidth="0.5" opacity="0.35" strokeDasharray="2 3" />
      {Array.from({ length: 8 }, (_, i) => {
        const angle = (i * 45 * Math.PI) / 180;
        const long = i % 2 === 0;
        const r1 = long ? 6 : 9.5;
        const r2 = long ? 16.5 : 13;
        return <line key={i} x1={24 + r1 * Math.sin(angle)} y1={24 - r1 * Math.cos(angle)} x2={24 + r2 * Math.sin(angle)} y2={24 - r2 * Math.cos(angle)} stroke="currentColor" strokeWidth={long ? 1.3 : 0.7} opacity={long ? 0.95 : 0.6} />;
      })}
      <path d="M24 10 26.6 21.4 38 24 26.6 26.6 24 38 21.4 26.6 10 24 21.4 21.4Z" fill="currentColor" />
      <circle cx="24" cy="24" r="2.2" fill="var(--cx-sidebar, #131722)" />
      <circle cx="24" cy="24" r="2.2" stroke="currentColor" strokeWidth="0.8" />
    </svg>
  );
}

export function IconMonitor(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></Glyph>;
}
export function IconInbox(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M3 13h5l1.5 2.5h5L16 13h5" /><path d="M5 6h14l2 7v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4Z" /></Glyph>;
}
export function IconLedger(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M6 3h12a1 1 0 0 1 1 1v16l-3-1.6-3 1.6-3-1.6L6 20V4a1 1 0 0 1 1-1Z" /><path d="M9 8h6M9 12h6" /></Glyph>;
}
export function IconContext(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="m12 3 9 5-9 5-9-5Z" /><path d="m3 13 9 5 9-5" /></Glyph>;
}
export function IconSliders(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M4 7h10M18 7h2M4 12h4M12 12h8M4 17h13M21 17h-1" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="19" cy="17" r="2" /></Glyph>;
}
export function IconFolder(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></Glyph>;
}
export function IconFile(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M6 3h8l4 4v14H6Z" /><path d="M14 3v4h4" /></Glyph>;
}
export function IconTerminal(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3M12.5 15H17" /></Glyph>;
}
export function IconGlobe(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.6 2.3 3.9 5.1 3.9 8.5s-1.3 6.2-3.9 8.5c-2.6-2.3-3.9-5.1-3.9-8.5s1.3-6.2 3.9-8.5Z" /></Glyph>;
}
export function IconSearch(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><circle cx="10.5" cy="10.5" r="6" /><path d="m15.5 15.5 5 5" /></Glyph>;
}
export function IconClipboard(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><rect x="6" y="4" width="12" height="17" rx="2" /><path d="M9 4a2 2 0 0 1 6 0M9 10h6M9 14h6" /></Glyph>;
}
export function IconCode(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="m8 7-5 5 5 5M16 7l5 5-5 5M13.5 4l-3 16" /></Glyph>;
}
export function IconClapper(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><rect x="3" y="9" width="18" height="11" rx="2" /><path d="m3 9 2-5 4.5 1M10 5l4.5 1L17 5l2.5 3.5" /></Glyph>;
}
export function IconShield(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M12 3 5 6v5c0 4.5 3 8.2 7 10 4-1.8 7-5.5 7-10V6Z" /><path d="m9 12 2 2 4-4" /></Glyph>;
}
export function IconChevronRight(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="m9 5 7 7-7 7" /></Glyph>;
}
export function IconPlus(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M12 5v14M5 12h14" /></Glyph>;
}
export function IconClock(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.5 2" /></Glyph>;
}
export function IconRadio(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M4 10a12.5 12.5 0 0 1 16 0M7 13.5a7.5 7.5 0 0 1 10 0" /><circle cx="12" cy="17" r="1.6" fill="currentColor" stroke="none" /></Glyph>;
}
export function IconCopy(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" transform="scale(0.86) translate(1.5 1.5)" /></Glyph>;
}
export function IconCheck(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="m4.5 12.5 5 5 10-11" /></Glyph>;
}
export function IconFlag(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M5 21V4" /><path d="M5 5h13l-2.5 3.5L18 12H5" /></Glyph>;
}
export function IconRefresh(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M20 11A8 8 0 0 0 5.6 6.6L4 8.5M4 13a8 8 0 0 0 14.4 4.4L20 15.5" /><path d="M4 4v4.5h4.5M20 20v-4.5h-4.5" /></Glyph>;
}
export function IconFlask(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M9.5 3h5M10.5 3v5L5 18a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18L13.5 8V3" /><path d="M7.5 15h9" /></Glyph>;
}
export function IconDatabase(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><ellipse cx="12" cy="5.5" rx="7.5" ry="2.8" /><path d="M4.5 5.5V18.5c0 1.6 3.4 2.9 7.5 2.9s7.5-1.3 7.5-2.9V5.5" /><path d="M4.5 12c0 1.6 3.4 2.9 7.5 2.9s7.5-1.3 7.5-2.9" /></Glyph>;
}
export function IconCompass(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><circle cx="12" cy="12" r="8.5" /><path d="m15.5 8.5-2 5-5 2 2-5Z" /></Glyph>;
}
export function IconGauge(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="M4 16a8 8 0 1 1 16 0" /><path d="m12 15 3.5-5" /><circle cx="12" cy="15.5" r="1.4" fill="currentColor" stroke="none" /></Glyph>;
}
export function IconX(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="m6 6 12 12M18 6 6 18" /></Glyph>;
}
export function IconInfo(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5" /><circle cx="12" cy="8" r="0.9" fill="currentColor" stroke="none" /></Glyph>;
}
export function IconLayers(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="m12 3 9 5-9 5-9-5Z" /><path d="m3 13 9 5 9-5" /></Glyph>;
}
export function IconUser(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><circle cx="12" cy="8" r="4" /><path d="M4.5 20.5c1.4-3.2 4.2-5 7.5-5s6.1 1.8 7.5 5" /></Glyph>;
}
export function IconBox(props: SVGProps<SVGSVGElement>): ReactElement {
  return <Glyph {...props}><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z" /><path d="M4 7.5 12 12l8-4.5M12 12v9" /></Glyph>;
}

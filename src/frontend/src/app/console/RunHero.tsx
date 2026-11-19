import { useMemo, type ReactElement } from 'react';
import type { EngineeringRole, EngineeringRunStatus, EngineeringRoleStatus } from '@/services/api.types';
import { ROLE_LABELS, RUN_STATUS_LABELS, formatElapsed, runShortId } from './format';
import { IconClock, IconCompass } from './icons';

/** Decorative topographic contour texture for the canopy hero. */
function Contours(): ReactElement {
  const paths = useMemo(() => [
    'M-20 40 C 120 10, 260 70, 420 34 S 720 66, 900 30',
    'M-20 78 C 140 52, 300 104, 470 66 S 760 96, 920 58',
    'M-20 116 C 110 96, 280 142, 450 106 S 740 132, 920 96',
    'M-20 154 C 150 138, 320 178, 500 144 S 780 168, 920 134',
    'M-20 192 C 130 178, 300 214, 480 184 S 760 204, 920 172',
    'M-20 230 C 160 220, 340 250, 520 224 S 800 240, 920 210',
  ], []);
  return (
    <svg aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full opacity-[0.16]" viewBox="0 0 900 260" preserveAspectRatio="xMidYMid slice">
      {paths.map((d, index) => (
        <path key={index} d={d} fill="none" stroke="#bfe9d2" strokeWidth={index % 2 === 0 ? 1 : 0.6} />
      ))}
      <text x="640" y="52" fill="#bfe9d2" fontSize="11" fontFamily="monospace">320</text>
      <text x="700" y="140" fill="#bfe9d2" fontSize="11" fontFamily="monospace">280</text>
      <text x="580" y="208" fill="#bfe9d2" fontSize="11" fontFamily="monospace">360</text>
    </svg>
  );
}

function CompassRose(): ReactElement {
  return (
    <svg aria-hidden="true" viewBox="0 0 96 96" className="h-20 w-20 opacity-80">
      <circle cx="48" cy="48" r="44" fill="none" stroke="rgba(191,233,210,0.5)" strokeWidth="1" />
      <circle cx="48" cy="48" r="34" fill="none" stroke="rgba(191,233,210,0.3)" strokeWidth="0.6" />
      {Array.from({ length: 12 }, (_, i) => {
        const angle = (i * 30 * Math.PI) / 180;
        const inner = i % 3 === 0 ? 38 : 41;
        return <line key={i} x1={48 + inner * Math.sin(angle)} y1={48 - inner * Math.cos(angle)} x2={48 + 44 * Math.sin(angle)} y2={48 - 44 * Math.cos(angle)} stroke="rgba(191,233,210,0.5)" strokeWidth={i % 3 === 0 ? 1.2 : 0.5} />;
      })}
      <path d="M48 16 52.5 48 48 52 43.5 48Z" fill="#ffb848" />
      <path d="M48 80 43.5 48 48 44 52.5 48Z" fill="rgba(191,233,210,0.55)" />
      <text x="44" y="12" fill="#dff3e8" fontSize="9" fontFamily="monospace">N</text>
      <text x="88" y="52" fill="#dff3e8" fontSize="9" fontFamily="monospace">E</text>
      <text x="45" y="94" fill="#dff3e8" fontSize="9" fontFamily="monospace">S</text>
      <text x="4" y="52" fill="#dff3e8" fontSize="9" fontFamily="monospace">W</text>
    </svg>
  );
}

/** Dark canopy hero summarising the active engineering run. */
export function RunHero({ goal, sessionId, workflow, status, roles, startedAtMs, nowMs }: {
  goal: string | null;
  sessionId: string | undefined;
  workflow: 'direct' | 'collaborative' | undefined;
  status: EngineeringRunStatus | undefined;
  roles: Record<EngineeringRole, EngineeringRoleStatus> | undefined;
  startedAtMs: number | undefined;
  nowMs: number;
}): ReactElement {
  const activeRole = roles ? (Object.entries(roles) as Array<[EngineeringRole, EngineeringRoleStatus]>).find(([, value]) => value === 'running') : undefined;
  const title = goal?.split('\n')[0].trim() || '新的工程运行';
  const hasRun = Boolean(sessionId);
  return (
    <section className="cx-canopy cx-enter cx-enter-1 relative overflow-hidden rounded-2xl" data-testid="run-hero">
      <Contours />
      <div className="absolute right-5 top-5 hidden md:block"><CompassRose /></div>
      <div className="relative z-10 px-6 py-5 md:pr-32">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="inline-flex items-center gap-1.5 rounded-md bg-[rgba(255,184,72,0.16)] px-2 py-1 text-[0.68rem] font-semibold tracking-wide text-[#ffcf7d]">
            <span className="h-2 w-2 rounded-[3px] bg-[#ffb848]" />
            {hasRun ? '当前工程运行' : '准备新的工程运行'}
          </span>
          <span className="font-mono text-[0.72rem] text-[#9fd4b8]">{hasRun ? runShortId(sessionId) : 'STANDBY'}</span>
          {hasRun && activeRole ? <span className="text-[0.7rem] text-[#9fd4b8]">· {ROLE_LABELS[activeRole[0]]} 执行中</span> : null}
        </div>
        <h2 className="mt-2.5 max-w-2xl truncate text-2xl font-bold tracking-tight text-[#f4faf6] md:text-[1.7rem]">{title}</h2>
        {goal && goal.split('\n').length > 1 ? <p className="mt-1 max-w-2xl truncate text-sm text-[#b9d8c6]">{goal.split('\n').slice(1).join('\n').trim()}</p> : null}
        {!hasRun ? <p className="mt-1.5 max-w-2xl text-sm text-[#9fd4b8]">输入一句工程目标，Director 将带着证据链自主完成整个项目。</p> : null}
        <div className="mt-3.5 flex flex-wrap items-center gap-2">
          <span className="cx-hero-chip">{workflow === 'collaborative' ? 'collaborative' : 'direct'}</span>
          <span className={`cx-hero-chip ${status === 'running' ? 'ring-1 ring-[rgba(124,227,174,0.45)]' : ''}`}>
            {status === 'running' ? <span className="cx-dot cx-dot--light cx-dot--pulse" /> : null}
            {status ? RUN_STATUS_LABELS[status] : '待机'}
          </span>
          {hasRun ? (
            <span className="cx-hero-chip">
              <IconClock className="h-3.5 w-3.5" />
              {formatElapsed(startedAtMs, nowMs)}
            </span>
          ) : null}
        </div>
      </div>
      <div className="relative z-10 flex items-center gap-2 border-t border-[rgba(255,255,255,0.08)] px-6 py-2">
        <IconCompass className="h-3.5 w-3.5 text-[#7ce3ae]" />
        <p className="font-mono text-[0.66rem] tracking-wide text-[#8fb8a1]">ENGINEERING V2 · EVIDENCE-CHAINED · SINGLE OWNER</p>
      </div>
    </section>
  );
}

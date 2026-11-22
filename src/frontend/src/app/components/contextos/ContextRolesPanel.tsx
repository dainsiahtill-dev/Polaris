import { useMemo, type ReactElement } from 'react';
import type { EngineeringEvent } from '@/services/api.types';
import { cn } from '@/app/components/ui/utils';

type RoleState = 'working' | 'idle' | 'disabled';

interface RoleView {
  id: 'distiller' | 'devourer' | 'cartographer';
  name: string;
  title: string;
  description: string;
  state: RoleState;
  current: string;
  completed: number;
  lastAt: number | null;
}

function DistillerIcon() {
  return (
    <svg viewBox="0 0 48 48" className="h-9 w-9" aria-hidden="true">
      <path
        d="M21 6h6v12l9 16a6 6 0 0 1-5 9H17a6 6 0 0 1-5-9l9-16V6Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      <path d="M14.5 30h19l3.5 6a4 4 0 0 1-3.5 6h-19a4 4 0 0 1-3.5-6l3.5-6Z" fill="currentColor" opacity=".22" />
      <circle cx="21" cy="36" r="1.6" fill="currentColor" opacity=".7" />
      <circle cx="26" cy="39" r="1.2" fill="currentColor" opacity=".55" />
      <circle cx="24" cy="33" r="1" fill="currentColor" opacity=".45" />
    </svg>
  );
}

function DevourerIcon() {
  return (
    <svg viewBox="0 0 48 48" className="h-9 w-9" aria-hidden="true">
      <path
        d="M24 7c9 0 16 5 16 12 0 3-2 5-4 7 3 2 5 4 5 7 0 6-7 10-17 10S7 40 7 34c0-3 2-5 5-7-2-2-4-4-4-7C8 12 15 7 24 7Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      <path d="M12 20c3-3 7-4 12-4s9 1 12 4c-2 4-6 6-12 6s-10-2-12-6Z" fill="currentColor" opacity=".22" />
      <circle cx="18" cy="18" r="1.5" fill="currentColor" opacity=".8" />
      <circle cx="30" cy="18" r="1.5" fill="currentColor" opacity=".8" />
      <path d="M17 30c2 2 4 3 7 3s5-1 7-3" fill="none" stroke="currentColor" strokeWidth="1.6" opacity=".6" />
    </svg>
  );
}

function CartographerIcon() {
  return (
    <svg viewBox="0 0 48 48" className="h-9 w-9" aria-hidden="true">
      <path
        d="M10 12h28v24H10z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinejoin="round"
      />
      <path d="M10 12l7-3 14 3 7-3v24l-7 3-14-3-7 3V12Z" fill="none" stroke="currentColor" strokeWidth="1.6" opacity=".55" />
      <path d="M17 9v24M31 12v24" stroke="currentColor" strokeWidth="1.2" opacity=".45" />
      <path
        d="M18 30c3-6 6-9 9-12 1 4 0 7-2 9s-5 4-7 3Z"
        fill="currentColor"
        opacity=".3"
      />
      <circle cx="33" cy="19" r="2.4" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M33 16v-3M36 19h3" stroke="currentColor" strokeWidth="1.4" opacity=".7" />
    </svg>
  );
}

function roleFromEvent(event: EngineeringEvent): 'distiller' | 'devourer' | 'cartographer' | null {
  const payload = event.payload as Record<string, unknown>;
  const agentRole = payload.agent_role;
  if (agentRole === 'distiller' || event.role === 'distiller') {
    return 'distiller';
  }
  if (agentRole === 'devourer' || event.role === 'devourer') {
    return 'devourer';
  }
  if (agentRole === 'cartographer' || event.role === 'cartographer') {
    return 'cartographer';
  }
  return null;
}

function describeEvent(event: EngineeringEvent): string {
  const payload = event.payload as Record<string, unknown>;
  if (payload.agent_role === 'cartographer' || event.role === 'cartographer') {
    const summary = typeof payload.summary === 'string' ? payload.summary : '';
    return summary ? `绘制运行地图 · ${summary.slice(0, 80)}` : '绘制当前阶段 RunCard';
  }
  if (payload.agent_role === 'devourer' || event.role === 'devourer') {
    const summary = typeof payload.summary === 'string' ? payload.summary : '';
    return summary ? `吞噬 ${String(payload.id ?? 'epoch')} · ${summary.slice(0, 80)}` : `吞噬 ${String(payload.id ?? 'epoch')}`;
  }
  const summaries = Array.isArray(payload.summaries) ? (payload.summaries as unknown[]) : [];
  const nodes = summaries
    .map((item) => (item as Record<string, unknown>).node_id)
    .filter((value): value is string => typeof value === 'string');
  return nodes.length ? `蒸馏节点：${nodes.slice(0, 3).join('、')}` : '蒸馏节点摘要';
}

export function ContextRolesPanel({ events, now = Date.now() }: { events: EngineeringEvent[]; now?: number }): ReactElement {
  const views = useMemo<RoleView[]>(() => {
    const distillerEvents = events.filter((event) => roleFromEvent(event) === 'distiller');
    const devourerEvents = events.filter((event) => roleFromEvent(event) === 'devourer');
    const cartographerEvents = events.filter((event) => roleFromEvent(event) === 'cartographer');
    const build = (
      id: 'distiller' | 'devourer' | 'cartographer',
      name: string,
      title: string,
      description: string,
      roleEvents: EngineeringEvent[],
    ): RoleView => {
      const last = roleEvents[roleEvents.length - 1];
      const lastAt = last?.created_at ?? null;
      const state: RoleState = roleEvents.length === 0 ? 'idle' : lastAt !== null && now - lastAt * 1000 < 120_000 ? 'working' : 'idle';
      return {
        id,
        name,
        title,
        description,
        state,
        current: last ? describeEvent(last) : '等待任务',
        completed: roleEvents.length,
        lastAt,
      };
    };
    return [
      build('distiller', 'Distiller', '蒸馏器 · 节点压缩', '把 FileCard / CommandCard 蒸馏成有界摘要', distillerEvents),
      build('devourer', 'Devourer', '吞噬兽 · Epoch 压缩', '吞噬已关闭的历史活动，产出阶段记忆', devourerEvents),
      build('cartographer', 'Cartographer', '制图师 · RunCard 智能化', '把当前运行状态绘制成阶段视图与下一步候选', cartographerEvents),
    ];
  }, [events, now]);

  return (
    <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="context-roles-panel" aria-label="ContextOS 智能角色">
      {views.map((view) => (
        <article
          key={view.id}
          data-testid={`context-role-${view.id}`}
          className="rounded-xl border border-white/[0.07] bg-black/20 p-3"
        >
          <div className="flex items-start gap-3">
            <span
              className={cn(
                'shrink-0',
                view.id === 'distiller'
                  ? 'text-cyan-300'
                  : view.id === 'devourer'
                    ? 'text-violet-300'
                    : 'text-amber-300',
                view.state === 'working' && 'animate-pulse',
              )}
            >
              {view.id === 'distiller' ? (
                <DistillerIcon />
              ) : view.id === 'devourer' ? (
                <DevourerIcon />
              ) : (
                <CartographerIcon />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <p className="text-xs font-semibold text-text-main">{view.name}</p>
                <span
                  className={cn(
                    'rounded px-1.5 py-0.5 text-[9px]',
                    view.state === 'working'
                      ? 'bg-emerald-400/15 text-emerald-300'
                      : 'bg-white/[0.05] text-text-dim',
                  )}
                  data-testid={`context-role-${view.id}-state`}
                >
                  {view.state === 'working' ? '工作中' : '待命'}
                </span>
              </div>
              <p className="mt-0.5 text-[10px] text-text-dim">{view.title}</p>
              <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
                <span className="text-text-dim">正在：</span>
                <span className="truncate" title={view.current}>{view.current}</span>
              </p>
              <p className="mt-1 text-[9px] text-text-faint">
                {view.description} · 已完成 {view.completed} 次
              </p>
            </div>
          </div>
        </article>
      ))}
    </section>
  );
}

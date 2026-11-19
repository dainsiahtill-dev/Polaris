import type { ReactElement } from 'react';
import type { EngineeringRole, EngineeringRoleStatus } from '@/services/api.types';
import { ROLE_LABELS, ROLE_ORDER, ROLE_STATUS_LABELS, roleStatusTone } from './format';
import { IconClipboard, IconCode, IconClapper, IconShield } from './icons';

const ROLE_GLYPHS: Record<EngineeringRole, (props: { className?: string }) => ReactElement> = {
  pm: ({ className }) => <IconClipboard className={className} />,
  chief_engineer: ({ className }) => <IconCode className={className} />,
  director: ({ className }) => <IconClapper className={className} />,
  qa: ({ className }) => <IconShield className={className} />,
};

const CARD_CLASS: Record<string, string> = {
  running: 'cx-rolecard--running',
  completed: 'cx-rolecard--done',
  failed: 'cx-rolecard--bad',
  cancelled: 'cx-rolecard--bad',
  interrupted: 'cx-rolecard--bad',
};

/** Four engineering role cards in a horizontal track. */
export function RoleTracks({ roles }: { roles: Record<EngineeringRole, EngineeringRoleStatus> }): ReactElement {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" role="list" aria-label="角色轨道">
      {ROLE_ORDER.map((role) => {
        const status = roles[role];
        const tone = roleStatusTone(status);
        const Glyph = ROLE_GLYPHS[role];
        const label = ROLE_STATUS_LABELS[status];
        return (
          <div key={role} role="listitem" data-testid={`engineering-role-${role}`} className={`cx-rolecard ${CARD_CLASS[status] || ''}`}>
            <span className="text-[var(--cx-ink-dim)]"><Glyph className="h-5 w-5" /></span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-bold text-[var(--cx-ink)]">{ROLE_LABELS[role]}</span>
              <span className={`cx-pill cx-pill--${tone} mt-1`}>
                {status === 'running' ? <span className="cx-dot cx-dot--run cx-dot--pulse" /> : null}
                {label}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

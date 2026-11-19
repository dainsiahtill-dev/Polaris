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

const LANE_CLASS: Record<string, string> = {
  running: 'cx-lane--running',
  completed: 'cx-lane--done',
  failed: 'cx-lane--bad',
  cancelled: 'cx-lane--bad',
  interrupted: 'cx-lane--bad',
};

const DOT_CLASS: Record<string, string> = {
  running: 'cx-dot--run cx-dot--pulse',
  completed: 'cx-dot--ok',
  failed: 'cx-dot--bad',
  cancelled: 'cx-dot--warn',
  interrupted: 'cx-dot--warn',
};

/** Four engineering role lanes on a vertical expedition rail. */
export function RoleTracks({ roles }: { roles: Record<EngineeringRole, EngineeringRoleStatus> }): ReactElement {
  return (
    <div className="relative space-y-1.5" role="list" aria-label="角色轨道">
      {ROLE_ORDER.map((role, index) => {
        const status = roles[role];
        const tone = roleStatusTone(status);
        const Glyph = ROLE_GLYPHS[role];
        const label = role === 'qa' && status === 'not_called' ? '等待复核' : ROLE_STATUS_LABELS[status];
        return (
          <div key={role} role="listitem" data-testid={`engineering-role-${role}`} className={`cx-lane ${LANE_CLASS[status] || 'cx-lane--idle'} ${index > 0 ? '' : ''}`}>
            {index < ROLE_ORDER.length - 1 ? <span aria-hidden="true" className="cx-rail-line" /> : null}
            <span className="cx-lane-glyph"><Glyph className="h-4.5 w-4.5" /></span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold leading-tight">{ROLE_LABELS[role]}</span>
              <span className="mt-0.5 block">
                <span className={`cx-pill cx-pill--${tone}`}>
                  {status === 'running' ? <span className={`cx-dot ${DOT_CLASS[status] || ''}`} /> : null}
                  {label}
                </span>
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

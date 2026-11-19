import type { ReactElement } from 'react';
import type { EngineeringProjectOutcome, EngineeringQAVerdict, EngineeringRunStatus, EngineeringVerification } from '@/services/api.types';
import {
  OUTCOME_BASIS_LABELS, OUTCOME_LABELS, QA_LABELS, RUN_STATUS_LABELS, VERIFICATION_LABELS,
  outcomeTone, qaTone, runStatusTone, verificationTone, type Tone,
} from './format';

function Cell({ label, value, tone, testId, hint }: { label: string; value: string; tone: Tone; testId?: string; hint?: string }): ReactElement {
  return (
    <div className="cx-verdict-cell" data-testid={testId}>
      <p className="text-[0.68rem] text-[var(--cx-ink-faint)]">{label}</p>
      <p className="mt-1.5"><span className={`cx-pill cx-pill--${tone}`}>{value}</span></p>
      {hint ? <p className="mt-1.5 text-[0.68rem] text-[var(--cx-ink-dim)]">{hint}</p> : null}
    </div>
  );
}

/** Final four-cell verdict strip: run / verification / QA / project outcome. */
export function VerdictBoard({ status, verification, qaVerdict, requireQa, projectOutcome, acceptanceBasis, pendingSnapshot, }: {
  status: EngineeringRunStatus | undefined;
  verification: EngineeringVerification | undefined;
  qaVerdict: EngineeringQAVerdict | undefined;
  requireQa: boolean | undefined;
  projectOutcome: EngineeringProjectOutcome | undefined;
  acceptanceBasis: 'checks_only' | 'independent_qa' | null | undefined;
  pendingSnapshot: boolean;
}): ReactElement {
  const blocked = pendingSnapshot;
  const cells: Array<{ label: string; value: string; tone: Tone; testId?: string; hint?: string }> = [
    { label: '运行', value: blocked ? '等待快照' : status ? RUN_STATUS_LABELS[status] : '未启动', tone: blocked ? 'warn' : runStatusTone(status) },
    { label: '验证', value: blocked ? '未知' : verification ? VERIFICATION_LABELS[verification] : '未知', tone: blocked ? 'warn' : verificationTone(verification), testId: 'engineering-verification' },
    { label: '独立 QA', value: blocked ? '未知' : qaVerdict ? QA_LABELS[qaVerdict] : '未调用', tone: blocked ? 'warn' : qaTone(qaVerdict), testId: 'engineering-qa-verdict', hint: requireQa ? '必需项' : undefined },
    {
      label: '项目',
      value: blocked ? '未知' : projectOutcome ? OUTCOME_LABELS[projectOutcome] : '未知',
      tone: blocked ? 'warn' : outcomeTone(projectOutcome),
      testId: 'engineering-project-outcome',
      hint: projectOutcome === 'accepted' && acceptanceBasis ? `依据：${OUTCOME_BASIS_LABELS[acceptanceBasis]}` : undefined,
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
      {cells.map((cell) => <Cell key={cell.label} {...cell} />)}
    </div>
  );
}

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

/** Final four-cell verdict: run / verification / QA / project outcome. */
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
  return (
    <div className="grid grid-cols-2 gap-2.5">
      <Cell label="运行状态" value={blocked ? '等待快照' : status ? RUN_STATUS_LABELS[status] : '未启动'} tone={blocked ? 'warn' : runStatusTone(status)} />
      <Cell label="验证结果" value={blocked ? '未知' : verification ? VERIFICATION_LABELS[verification] : '未知'} tone={blocked ? 'warn' : verificationTone(verification)} testId="engineering-verification" />
      <Cell label="QA 结论" value={blocked ? '未知' : qaVerdict ? QA_LABELS[qaVerdict] : '未调用'} tone={blocked ? 'warn' : qaTone(qaVerdict)} testId="engineering-qa-verdict" hint={requireQa ? '独立 QA 为必需项' : undefined} />
      <Cell
        label="项目验收"
        value={blocked ? '未知' : projectOutcome ? OUTCOME_LABELS[projectOutcome] : '未知'}
        tone={blocked ? 'warn' : outcomeTone(projectOutcome)}
        testId="engineering-project-outcome"
        hint={projectOutcome === 'accepted' && acceptanceBasis ? `依据：${OUTCOME_BASIS_LABELS[acceptanceBasis]}` : undefined}
      />
    </div>
  );
}

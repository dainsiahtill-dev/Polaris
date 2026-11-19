import { useMemo, type ReactElement } from 'react';
import type { EngineeringCheck } from '@/services/api.types';
import { IconGlobe } from './icons';

const METRIC_PATTERNS: Array<{ key: string; label: string; unit: string; pattern: RegExp }> = [
  { key: 'fps', label: '实时帧率', unit: 'FPS', pattern: /(\d+(?:\.\d+)?)\s*fps/i },
  { key: 'errors', label: '页面错误', unit: '错误', pattern: /(\d+)\s*(?:page\s*)?(?:errors?|页面错误)/i },
  { key: 'triangles', label: '场景复杂度', unit: '三角形', pattern: /([\d,]+)\s*(?:triangles?|三角形)/i },
];

function parseMetrics(content: string): Array<{ label: string; value: string; unit: string }> {
  return METRIC_PATTERNS.map(({ label, unit, pattern }) => {
    const match = content.match(pattern);
    return { label, unit, value: match ? match[1] : '—' };
  });
}

/** Browser QA render evidence; only rendered when a browser-family check exists. */
export function BrowserQaCard({ checks }: { checks: EngineeringCheck[] }): ReactElement | null {
  const browserCheck = useMemo(() => checks.find((check) => /browser|render|screenshot/i.test(check.tool)), [checks]);
  if (!browserCheck) return null;
  const ok = browserCheck.ok && browserCheck.exit_code === 0;
  const metrics = parseMetrics(browserCheck.content);
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--cx-line)]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--cx-line)] bg-[#fdfcf8] px-3.5 py-2.5">
        <span className="flex items-center gap-2 text-sm"><IconGlobe className="h-4.5 w-4.5 text-[var(--cx-green)]" />Chromium · SwiftShader</span>
        {ok ? <span className="cx-pill cx-pill--ok"><span className="cx-dot cx-dot--ok" />通过</span> : <span className="cx-pill cx-pill--bad">未通过</span>}
      </div>
      <div className="cx-terminal max-h-44 overflow-y-auto rounded-none">{browserCheck.content || '（无输出）'}</div>
      <div className="grid grid-cols-3 divide-x divide-[var(--cx-line)] border-t border-[var(--cx-line)] bg-[#fdfcf8]">
        {metrics.map((metric) => (
          <div key={metric.label} className="px-3 py-3 text-center">
            <p className="cx-mono text-lg font-semibold leading-none">{metric.value}<span className="ml-1 text-[0.66rem] font-normal text-[var(--cx-ink-faint)]">{metric.unit}</span></p>
            <p className="mt-1 text-[0.68rem] text-[var(--cx-ink-dim)]">{metric.label}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

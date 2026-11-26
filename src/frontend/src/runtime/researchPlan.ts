import type { EngineeringRunSnapshot, EngineeringStartOptions } from '@/services/api.types';

export type EngineeringResearchPlan = NonNullable<EngineeringStartOptions['research']>['plan'];
export type ResearchIdentity = NonNullable<EngineeringRunSnapshot['research']>;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function researchId(value: unknown): value is string {
  return typeof value === 'string' && /^[\p{L}\p{N}_.-]{1,100}$/u.test(value);
}

export function researchDigest(value: unknown): value is string {
  return typeof value === 'string' && value.length === 64 && /^[a-f0-9]{64}$/.test(value);
}

export function parseResearchPlan(value: unknown): EngineeringResearchPlan {
  if (!record(value) || value.schema !== 'engineering.research-plan.v1' || !researchId(value.project))
    throw new Error('研究计划需包含有效项目和 engineering.research-plan.v1 格式。');
  if (!Array.isArray(value.experiments) || value.experiments.length < 1 || value.experiments.length > 4096)
    throw new Error('研究计划需要包含 1 至 4096 项实验。');
  const identities = new Set<string>();
  for (const [index, experiment] of value.experiments.entries()) {
    if (!record(experiment) || !researchId(experiment.lab) || !researchId(experiment.id)
      || typeof experiment.scope !== 'string' || !experiment.scope.trim()
      || (experiment.next_question !== undefined && typeof experiment.next_question !== 'string'))
      throw new Error(`第 ${index + 1} 项实验的身份或范围无效。`);
    const key = `${experiment.lab}/${experiment.id}`;
    if (identities.has(key)) throw new Error(`实验身份重复：${key}`);
    identities.add(key);
    const report = experiment.report;
    if (report != null && (!record(report) || typeof report.manifest !== 'string' || !researchDigest(report.sha256)
      || !Array.isArray(report.references) || !report.references.every((ref: unknown) => record(ref)
        && typeof ref.path_pointer === 'string' && typeof ref.digest_pointer === 'string')))
      throw new Error(`实验 ${key} 的报告引用无效。`);
    if (experiment.depends_on !== undefined) {
      if (!Array.isArray(experiment.depends_on)) throw new Error(`实验 ${key} 的依赖列表无效。`);
      const dependencies = new Set<string>();
      for (const dep of experiment.depends_on) {
        if (!record(dep) || !researchId(dep.lab) || !researchId(dep.id)
          || (dep.sha256 != null && !researchDigest(dep.sha256)))
          throw new Error(`实验 ${key} 的依赖身份或报告 SHA-256 无效。`);
        const source = `${dep.lab}/${dep.id}`;
        if (dependencies.has(source)) throw new Error(`实验 ${key} 重复引用依赖 ${source}。`);
        dependencies.add(source);
      }
    }
  }
  return value as EngineeringResearchPlan;
}

/** JSON.parse discards duplicate keys; retain the host's unambiguous input contract. */
function rejectDuplicateKeys(text: string): void {
  const objects: Set<string>[] = [];
  const tokens = /("(?:\\[\s\S]|[^"\\])*")\s*(:)?|([{}])/g;
  for (const token of text.matchAll(tokens)) {
    if (token[3] === '{') objects.push(new Set());
    else if (token[3] === '}') objects.pop();
    else if (token[2]) {
      const keys = objects[objects.length - 1];
      const key: string = JSON.parse(token[1]);
      if (keys.has(key)) throw new Error(`计划包含重复 JSON 键：${key}`);
      keys.add(key);
    }
  }
}

export async function readResearchPlan(file: File): Promise<EngineeringResearchPlan> {
  if (file.size > 2 * 1024 * 1024) throw new Error('研究计划文件超过 2 MiB。');
  const bytes = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => reader.result instanceof ArrayBuffer ? resolve(reader.result) : reject(new Error('计划文件读取失败。'));
    reader.onerror = () => reject(new Error('计划文件读取失败。'));
    reader.readAsArrayBuffer(file);
  });
  let text: string;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { throw new Error('计划文件需要有效的 UTF-8 编码。'); }
  let value: unknown;
  try {
    value = JSON.parse(text, (_key: string, item: unknown) => {
      if (typeof item === 'number' && (!Number.isFinite(item) || (Number.isInteger(item) && !Number.isSafeInteger(item))))
        throw new Error();
      return item;
    });
  } catch { throw new Error('计划 JSON 无效或包含无法精确保留的数字。'); }
  rejectDuplicateKeys(text);
  return parseResearchPlan(value);
}

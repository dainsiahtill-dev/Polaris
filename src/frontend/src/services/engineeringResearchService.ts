import { isRecord } from '@/app/hooks/runtimeParsing';
import { normalizeRuntimeWorkspacePath } from '@/app/hooks/_runtimeEventFilter';
import { isEngineeringRunStatus, isEngineeringVerification } from '@/runtime/engineeringRunProjection';
import { parseResearchPlan, researchDigest, researchId, type EngineeringResearchPlan, type ResearchIdentity } from '@/runtime/researchPlan';
import type { ApiResult, EngineeringRunStatus, EngineeringVerification } from './api.types';
import { apiGet } from './apiClient';

export interface ResearchRunRow {
  session_id: string;
  lab: string;
  status: EngineeringRunStatus;
  verification: EngineeringVerification;
  plan_sha256: string;
  created_at: number;
}

export const RESEARCH_PAGE_SIZE = 20;
export interface ResearchRunPage { rows: ResearchRunRow[]; nextOffset: number | null; }

const boundWorkspace = (value: unknown, expected: string) =>
  typeof value === 'string' && Boolean(value.trim()) && Boolean(expected.trim()) && normalizeRuntimeWorkspacePath(value) === normalizeRuntimeWorkspacePath(expected);

function researchRow(row: unknown): row is ResearchRunRow {
  return isRecord(row) && typeof row.session_id === 'string' && Boolean(row.session_id.trim()) && researchId(row.lab)
    && researchDigest(row.plan_sha256) && isEngineeringRunStatus(row.status) && isEngineeringVerification(row.verification)
    && typeof row.created_at === 'number' && Number.isFinite(row.created_at);
}

// Browsers resolve dot path segments even when percent-encoded.
const exactSegment = (value: string) => value !== '.' && value !== '..' && !/[\/\\]/.test(value);

/** User-triggered discovery for one workspace. Every history row keeps its original plan identity. */
export async function getResearchRuns(workspace: string, project: string, lab: string | null, offset = 0): Promise<ApiResult<ResearchRunPage>> {
  if (!workspace.trim() || !researchId(project) || !exactSegment(project) || (lab !== null && !researchId(lab)) || !Number.isSafeInteger(offset) || offset < 0)
    return { ok: false, error: '实验室查询的工作区、项目或页码无效。' };
  const query = new URLSearchParams({ workspace, offset: String(offset), limit: String(RESEARCH_PAGE_SIZE) });
  if (lab !== null) query.set('lab', lab);
  const result = await apiGet<unknown>(`/v2/engineering/research/${encodeURIComponent(project)}/laboratories?${query}`, '读取实验室记录失败');
  if (!result.ok) return { ok: false, error: result.error || '读取实验室记录失败。' };
  const response = result.data;
  const error = { ok: false, error: '返回的实验室记录与当前查询不一致。' };
  if (!isRecord(response) || !boundWorkspace(response.workspace, workspace) || response.project !== project
    || (lab === null ? response.lab !== undefined : response.lab !== lab)) return error;
  const rows: unknown = lab === null ? response.laboratories : response.runs;
  if (!Array.isArray(rows) || rows.length > RESEARCH_PAGE_SIZE || !rows.every(researchRow)
    || rows.some((row) => lab !== null && row.lab !== lab)
    || new Set(rows.map((row) => lab === null ? row.lab : row.session_id)).size !== rows.length) return error;
  const next = response.next_offset;
  if (next !== null && (rows.length !== RESEARCH_PAGE_SIZE || next !== offset + RESEARCH_PAGE_SIZE)) return error;
  return { ok: true, data: { rows, nextOffset: next } };
}

export async function getFixedResearchPlan(workspace: string, sessionId: string, expected: ResearchIdentity): Promise<ApiResult<EngineeringResearchPlan>> {
  if (!workspace.trim() || !sessionId.trim() || !exactSegment(sessionId) || !researchDigest(expected.plan_sha256) || !researchId(expected.lab) || !researchId(expected.project))
    return { ok: false, error: '研究运行身份无效。' };
  const query = new URLSearchParams({ workspace });
  const result = await apiGet<unknown>(`/v2/engineering/runs/${encodeURIComponent(sessionId)}/research-plan?${query}`, '读取固定计划失败');
  if (!result.ok) return { ok: false, error: result.error || '读取固定计划失败。' };
  const response = result.data;
  const error = { ok: false, error: '返回的计划与选定运行的版本或身份不一致。' };
  if (!isRecord(response) || response.session_id !== sessionId || !boundWorkspace(response.workspace, workspace)
    || !isRecord(response.research) || response.research.lab !== expected.lab || response.research.plan_sha256 !== expected.plan_sha256) return error;
  try {
    const plan = parseResearchPlan(response.research.plan);
    return plan.project === expected.project && plan.experiments.some((experiment) => experiment.lab === expected.lab)
      ? { ok: true, data: plan } : error;
  } catch { return error; }
}

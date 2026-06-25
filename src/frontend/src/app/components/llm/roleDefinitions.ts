export const LLM_ROLE_IDS = [
  "pm",
  "chief_engineer",
  "director",
  "qa",
  "architect",
  "resident_agi",
  "scout",
  "cfo",
  "hr",
] as const;

export type LlmRoleId = (typeof LLM_ROLE_IDS)[number];

type RoleBindingVisibility = "default" | "configured";
type RoleBindingKind = "execution_role" | "governance_advisor";

export interface LlmRoleDefinition {
  id: LlmRoleId;
  label: string;
  description: string;
  bindingVisibility: RoleBindingVisibility;
  bindingKind: RoleBindingKind;
  requiresThinking: boolean;
  minConfidence: number;
  badge: string;
  color: string;
  testDescription: string;
}

export const LLM_ROLE_DEFINITIONS: Record<LlmRoleId, LlmRoleDefinition> = {
  pm: {
    id: "pm",
    label: "PM",
    description: "统筹任务、节奏与推进。",
    bindingVisibility: "default",
    bindingKind: "execution_role",
    requiresThinking: true,
    minConfidence: 0.8,
    color: "text-text-main",
    badge: "bg-white/[0.08] text-text-main border-white/[0.12]",
    testDescription: "检验结构化任务输出与验收条款（含 JSON 解析）。",
  },
  chief_engineer: {
    id: "chief_engineer",
    label: "Chief Engineer",
    description: "绘制技术蓝图，定体例与纲目（设计不编码）。",
    bindingVisibility: "default",
    bindingKind: "execution_role",
    requiresThinking: true,
    minConfidence: 0.85,
    color: "text-emerald-400",
    badge: "bg-emerald-600/[0.15] text-emerald-300 border-emerald-600/25",
    testDescription: "检验蓝图设计与体例规划的完整性。",
  },
  director: {
    id: "director",
    label: "Director",
    description: "负责实现、调度与技术裁断（实际编码）。",
    bindingVisibility: "default",
    bindingKind: "execution_role",
    requiresThinking: true,
    minConfidence: 0.9,
    color: "text-emerald-300",
    badge: "bg-emerald-500/[0.15] text-emerald-200 border-emerald-500/25",
    testDescription: "检验证据与执行指令输出（不直接生成补丁）。",
  },
  qa: {
    id: "qa",
    label: "QA",
    description: "主司审核与勘验，确保证据链完备。",
    bindingVisibility: "default",
    bindingKind: "execution_role",
    requiresThinking: false,
    minConfidence: 0.7,
    color: "text-blue-200",
    badge: "bg-blue-500/[0.15] text-blue-200 border-blue-500/25",
    testDescription: "检验 PASS/FAIL Reject结论与理由完整性。",
  },
  architect: {
    id: "architect",
    label: "Architect",
    description: "草拟项目规格与架构文档，定体例与纲目。",
    bindingVisibility: "default",
    bindingKind: "execution_role",
    requiresThinking: false,
    minConfidence: 0.6,
    color: "text-amber-300",
    badge: "bg-amber-500/[0.15] text-amber-200 border-amber-500/25",
    testDescription: "检验 spec.md 草拟质量与结构完整度。",
  },
  resident_agi: {
    id: "resident_agi",
    label: "Resident AGI",
    description: "平台级无人值守决策、审计交接与自治监督。",
    bindingVisibility: "default",
    bindingKind: "execution_role",
    requiresThinking: true,
    minConfidence: 0.85,
    color: "text-fuchsia-200",
    badge: "bg-fuchsia-500/[0.15] text-fuchsia-200 border-fuchsia-500/25",
    testDescription: "检验平台自治决策边界、审计交接与 advisory 防越权能力。",
  },
  scout: {
    id: "scout",
    label: "Scout",
    description:
      "只读代码/文档侦察（探子）。可选：仅当 scout_probe 升级为 LLM 侦察时才需要绑定模型。",
    bindingVisibility: "default",
    bindingKind: "execution_role",
    requiresThinking: false,
    minConfidence: 0.5,
    color: "text-text-main",
    badge: "bg-white/[0.08] text-text-main border-white/[0.12]",
    testDescription: "检验只读代码/文档侦察能力。",
  },
  cfo: {
    id: "cfo",
    label: "Cost Advisor",
    description: "可选治理视角：审计预算、Token 用量、资源成本与 ROI，不参与主执行链。",
    bindingVisibility: "configured",
    bindingKind: "governance_advisor",
    requiresThinking: false,
    minConfidence: 0.5,
    color: "text-text-main",
    badge: "bg-white/[0.08] text-text-main border-white/[0.12]",
    testDescription: "检验成本治理、预算审计与 Token 用量监控能力。",
  },
  hr: {
    id: "hr",
    label: "Model Governance Advisor",
    description: "可选治理视角：审计角色-模型配置、能力匹配与配置漂移，不参与主执行链。",
    bindingVisibility: "configured",
    bindingKind: "governance_advisor",
    requiresThinking: false,
    minConfidence: 0.5,
    color: "text-text-main",
    badge: "bg-white/[0.08] text-text-main border-white/[0.12]",
    testDescription: "检验模型治理、配置漂移与角色-模型连线审计能力。",
  },
};

export const GOVERNANCE_ADVISOR_LLM_ROLE_IDS = LLM_ROLE_IDS.filter(
  (roleId) => LLM_ROLE_DEFINITIONS[roleId].bindingKind === "governance_advisor",
);

export const DEFAULT_LLM_BINDING_ROLE_IDS = LLM_ROLE_IDS.filter(
  (roleId) => LLM_ROLE_DEFINITIONS[roleId].bindingVisibility === "default",
);

export const OPTIONAL_GOVERNANCE_LLM_ROLE_IDS = LLM_ROLE_IDS.filter(
  (roleId) => LLM_ROLE_DEFINITIONS[roleId].bindingVisibility === "configured",
);

export const REQUIRED_LLM_ASSIGNMENT_ROLE_IDS = [
  "pm",
  "chief_engineer",
  "director",
  "qa",
  "architect",
] as const satisfies readonly LlmRoleId[];

export function isKnownLlmRoleId(value: string): value is LlmRoleId {
  return Object.prototype.hasOwnProperty.call(LLM_ROLE_DEFINITIONS, value);
}

export function normalizeLlmRoleId(value: string): LlmRoleId | null {
  if (value === "docs") return "architect";
  return isKnownLlmRoleId(value) ? value : null;
}

export function getLlmRoleDefinition(roleId: LlmRoleId): LlmRoleDefinition {
  return LLM_ROLE_DEFINITIONS[roleId];
}

function hasConfiguredRoleBinding(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const roleCfg = value as Record<string, unknown>;
  const providerId = String(roleCfg.provider_id || "").trim();
  const model = String(roleCfg.model || "").trim();
  if (providerId || model) return true;
  const bindings = roleCfg.bindings;
  return Array.isArray(bindings) && bindings.length > 0;
}

export function getVisibleLlmBindingRoleIds(
  roles?: Record<string, unknown> | null,
  statusRoles?: Record<string, unknown> | null,
): LlmRoleId[] {
  const optionalRoles = OPTIONAL_GOVERNANCE_LLM_ROLE_IDS.filter((roleId) =>
    Boolean(hasConfiguredRoleBinding(roles?.[roleId]) || statusRoles?.[roleId]),
  );
  return [...DEFAULT_LLM_BINDING_ROLE_IDS, ...optionalRoles];
}

/**
 * ContextOS display constants (extracted losslessly from contextOSData.ts).
 */

import type { ContextOSEvent } from '../contextOSTelemetry';

export const EVENT_TYPE_META: ReadonlyArray<{ key: ContextOSEvent['category']; label: string; colorClass: string }> = [
  { key: 'projection', label: '投影', colorClass: 'bg-accent-secondary' },
  { key: 'call', label: '调用', colorClass: 'bg-gold' },
  { key: 'tool', label: '工具', colorClass: 'bg-accent' },
  { key: 'state', label: '状态', colorClass: 'bg-status-info' },
  { key: 'error', label: '错误', colorClass: 'bg-status-error' },
  { key: 'event', label: '其他', colorClass: 'bg-text-dim' },
];

/** 角色 → Decision Log 中 actor/speaker 的匹配别名（用于角色页签交叉过滤）。 */
export const ROLE_DECISION_ALIASES: Record<string, string[]> = {
  pm: ['pm'],
  architect: ['architect'],
  chief_engineer: ['chief', 'engineer'],
  director: ['director'],
  qa: ['qa', 'reviewer'],
};

/**
 * ContextOS 角色信号面对应的 5 个主角色（与后端 `ROLE_PROMPT_TEMPLATES` /
 * 统一角色对话 API「所有 5 个角色」一致）。scout 为只读辅助 sub-agent，按设计不入此面。
 */
export const ROLE_DEFINITIONS: ReadonlyArray<{ id: string; key: string; courtTitle: string; title: string }> = [
  { id: 'pm', key: 'pm', courtTitle: '尚书令', title: 'Project Manager' },
  { id: 'architect', key: 'architect', courtTitle: '中书令', title: 'Architect' },
  { id: 'chief_engineer', key: 'chief_engineer', courtTitle: '工部尚书', title: 'Chief Engineer' },
  { id: 'director', key: 'director', courtTitle: '工部侍郎', title: 'Director' },
  { id: 'qa', key: 'qa', courtTitle: '门下侍中', title: 'QA' },
];
/** 主角色总数（角色信号面 N/N 角色的分母）。 */
export const ROLE_COUNT = ROLE_DEFINITIONS.length;


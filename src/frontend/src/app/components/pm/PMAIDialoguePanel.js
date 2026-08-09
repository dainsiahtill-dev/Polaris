import { jsx as _jsx } from "react/jsx-runtime";
import { AIDialoguePanel } from '@/app/components/ai-dialogue';
function normalizeTaskId(value) {
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' && Number.isFinite(value))
        return String(value);
    if (typeof value === 'bigint')
        return String(value);
    if (value && typeof value === 'object') {
        const record = value;
        return normalizeTaskId(record.id ?? record.task_id);
    }
    return '';
}
/**
 * PM AI 对话面板
 *
 * 基于通用 AIDialoguePanel 组件，预配置为 PM 角色。
 * 这是 AIDialoguePanel 的一个具体使用示例。
 */
export function PMAIDialoguePanel({ pmRunning, workspace, taskCount, selectedTaskId, interactionBlockedReason = '', }) {
    const blockedReason = String(interactionBlockedReason || '').trim();
    const normalizedSelectedTaskId = normalizeTaskId(selectedTaskId);
    const welcomeMessage = blockedReason
        ? `PM 当前不可用：${blockedReason}`
        : 'PM 系统已就绪。您可以询问任务状态、请求生成新任务，或讨论项目规划。';
    return (_jsx(AIDialoguePanel, { dialogueRole: "pm", roleDisplayName: "PM", roleTheme: {
            primary: 'amber',
            secondary: 'amber-400',
            gradient: 'from-amber-500 to-amber-700',
        }, welcomeMessage: welcomeMessage, context: {
            workspace,
            task_count: taskCount,
            selected_task_id: normalizedSelectedTaskId || null,
            pm_running: pmRunning,
            blocked_reason: blockedReason,
        }, workspace: workspace, attachmentMode: normalizedSelectedTaskId ? 'attached_readonly' : 'isolated', attachedTaskId: normalizedSelectedTaskId || undefined, workflowExportTarget: "pm", workflowExportLabel: "\u5BFC\u51FAPM", interactionBlockedReason: blockedReason, statusNoticeMode: "compact" }));
}

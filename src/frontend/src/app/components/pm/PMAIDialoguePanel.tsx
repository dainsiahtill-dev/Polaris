import { AIDialoguePanel } from '@/app/components/ai-dialogue';

interface PMAIDialoguePanelProps {
  pmRunning: boolean;
  workspace?: string;
  taskCount?: number;
  interactionBlockedReason?: string;
}

/**
 * PM AI 对话面板
 *
 * 基于通用 AIDialoguePanel 组件，预配置为 PM 角色。
 * 这是 AIDialoguePanel 的一个具体使用示例。
 */
export function PMAIDialoguePanel({
  pmRunning,
  workspace,
  taskCount,
  interactionBlockedReason = '',
}: PMAIDialoguePanelProps) {
  const blockedReason = String(interactionBlockedReason || '').trim();
  const welcomeMessage = blockedReason
    ? `PM 当前不可用：${blockedReason}`
    : 'PM 系统已就绪。您可以询问任务状态、请求生成新任务，或讨论项目规划。';

  return (
    <AIDialoguePanel
      dialogueRole="pm"
      roleDisplayName="PM"
      roleTheme={{
        primary: 'amber',
        secondary: 'amber-400',
        gradient: 'from-amber-500 to-amber-700',
      }}
      welcomeMessage={welcomeMessage}
      context={{
        workspace,
        task_count: taskCount,
        pm_running: pmRunning,
        blocked_reason: blockedReason,
      }}
      interactionBlockedReason={blockedReason}
    />
  );
}

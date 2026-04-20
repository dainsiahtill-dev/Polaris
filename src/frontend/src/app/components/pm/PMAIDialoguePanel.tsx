import { AIDialoguePanel } from '@/app/components/ai-dialogue';

interface PMAIDialoguePanelProps {
  pmRunning: boolean;
  workspace?: string;
  taskCount?: number;
}

/**
 * PM AI 对话面板
 *
 * 基于通用 AIDialoguePanel 组件，预配置为 PM（尚书令）角色。
 * 这是 AIDialoguePanel 的一个具体使用示例。
 */
export function PMAIDialoguePanel({
  pmRunning,
  workspace,
  taskCount,
}: PMAIDialoguePanelProps) {
  return (
    <AIDialoguePanel
      dialogueRole="pm"
      roleDisplayName="尚书令"
      roleTheme={{
        primary: 'amber',
        secondary: 'amber-400',
        gradient: 'from-amber-500 to-amber-700',
      }}
      welcomeMessage="尚书令 PM 系统已就绪。您可以询问任务状态、请求生成新任务，或讨论项目规划。"
      context={{
        workspace,
        task_count: taskCount,
        pm_running: pmRunning,
      }}
    />
  );
}

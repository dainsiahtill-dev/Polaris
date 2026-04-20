import { AIDialoguePanel } from '@/app/components/ai-dialogue';

interface ArchitectAIDialoguePanelProps {
  workspace?: string;
  documentPath?: string;
}

/**
 * Architect AI 对话面板（中书令）
 *
 * 基于通用 AIDialoguePanel 组件，预配置为 Architect 角色。
 * 用于文档编写和技术架构讨论。
 */
export function ArchitectAIDialoguePanel({
  workspace,
  documentPath,
}: ArchitectAIDialoguePanelProps) {
  return (
    <AIDialoguePanel
      dialogueRole="architect"
      roleDisplayName="中书令"
      roleTheme={{
        primary: 'purple',
        secondary: 'purple-400',
        gradient: 'from-purple-500 to-purple-700',
      }}
      welcomeMessage="中书令 Architecture 系统已就绪。您可以讨论技术方案、审查架构设计，或协助文档编写。"
      context={{
        workspace,
        document_path: documentPath,
      }}
    />
  );
}

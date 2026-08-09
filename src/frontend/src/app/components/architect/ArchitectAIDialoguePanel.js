import { jsx as _jsx } from "react/jsx-runtime";
import { AIDialoguePanel } from '@/app/components/ai-dialogue';
/**
 * Architect AI 对话面板
 *
 * 基于通用 AIDialoguePanel 组件，预配置为 Architect 角色。
 * 用于文档编写和技术架构讨论。
 */
export function ArchitectAIDialoguePanel({ workspace, documentPath, }) {
    return (_jsx(AIDialoguePanel, { dialogueRole: "architect", roleDisplayName: "Architect", roleTheme: {
            primary: 'purple',
            secondary: 'purple-400',
            gradient: 'from-purple-500 to-purple-700',
        }, welcomeMessage: "Architecture \u7CFB\u7EDF\u5DF2\u5C31\u7EEA\u3002\u60A8\u53EF\u4EE5\u8BA8\u8BBA\u6280\u672F\u65B9\u6848\u3001\u5BA1\u67E5\u67B6\u6784\u8BBE\u8BA1\uFF0C\u6216\u534F\u52A9\u6587\u6863\u7F16\u5199\u3002", context: {
            workspace,
            document_path: documentPath,
        } }));
}

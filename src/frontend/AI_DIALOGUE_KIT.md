# AI Dialogue Kit - 统一角色对话套件

## 概述

AI Dialogue Kit 是一个基于 Polaris AI 平台层的通用角色对话组件套件，支持多角色、流式输出和思考过程显示。

## 核心组件

```
src/frontend/src/app/components/ai-dialogue/
├── AIDialoguePanel.tsx    # 通用对话面板组件
├── useRoleChat.ts         # React Hook
├── index.ts               # 导出
└── README.md              # 详细文档
```

## 使用方式

### 1. 直接使用通用组件

```tsx
import { AIDialoguePanel } from '@/app/components/ai-dialogue';

<AIDialoguePanel
  dialogueRole="pm"
  roleDisplayName="尚书令"
  roleTheme={{
    primary: 'amber',
    secondary: 'amber-400',
    gradient: 'from-amber-500 to-amber-700',
  }}
  welcomeMessage="尚书令已就绪"
  context={{ workspace, taskCount }}
/>
```

### 2. 使用 Hook 自定义 UI

```tsx
import { useRoleChat } from '@/app/components/ai-dialogue';

const { messages, inputValue, setInputValue, sendMessage } = useRoleChat({
  role: 'architect',
  welcomeMessage: '中书令已就绪',
});
```

### 3. 预配置角色组件

已有预配置角色组件：
- `PMAIDialoguePanel` - 尚书令 (PM)
- `ArchitectAIDialoguePanel` - 中书令 (Architect)

```tsx
import { PMAIDialoguePanel } from '@/app/components/pm';
import { ArchitectAIDialoguePanel } from '@/app/components/architect';
```

## 支持的角色

| 角色 | 标识 | 默认名称 | 主题色 |
|------|------|----------|--------|
| PM | `pm` | 尚书令 | Amber |
| Architect | `architect` | 中书令 | Purple |
| Director | `director` | 大将军 | Emerald |
| QA | `qa` | 御史大夫 | Rose |

## 后端 API

| 端点 | 方法 | 描述 |
|------|------|------|
| `/v2/role/{role}/chat` | POST | 非流式对话 |
| `/v2/role/{role}/chat/stream` | POST | 流式对话 (SSE) |
| `/v2/role/{role}/chat/status` | GET | 获取角色 LLM 状态 |
| `/v2/role/chat/roles` | GET | 列出支持的角色 |

## 与旧组件的关系

```
┌─────────────────────────────────────────────────────────┐
│                    AI Dialogue Kit                       │
│  ┌─────────────────────────────────────────────────┐   │
│  │  AIDialoguePanel (通用组件)                      │   │
│  │  useRoleChat (通用 Hook)                        │   │
│  └─────────────────────────────────────────────────┘   │
│                          │                              │
│          ┌───────────────┼───────────────┐              │
│          ▼               ▼               ▼              │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │PMAIDialogue  │ │ArchitectAI   │ │ 自定义角色    │   │
│  │Panel         │ │DialoguePanel │ │ 组件         │   │
│  └──────────────┘ └──────────────┘ └──────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## 优势

1. **统一架构**: 所有角色对话使用相同的 AI Platform 层
2. **流式支持**: 内置 SSE 流式输出，支持思考过程
3. **类型安全**: TypeScript 完整类型支持
4. **可复用**: 一套代码支持所有角色
5. **易扩展**: 添加新角色只需配置，无需复制代码

## 迁移状态

- [x] 创建通用 usecase (`role_dialogue.py`)
- [x] 创建通用路由 (`role_chat.py`)
- [x] 创建通用组件 (`AIDialoguePanel.tsx`)
- [x] 创建通用 Hook (`useRoleChat.ts`)
- [x] 重构 PMAIDialoguePanel 使用通用组件
- [x] 创建 ArchitectAIDialoguePanel 示例
- [ ] 替换 interview 流式实现 (可选)
- [ ] 替换 docs_dialogue 流式实现 (可选)

# AI Dialogue Kit - 通用角色对话套件

基于 Polaris AI 平台层的通用角色对话组件，支持流式输出和思考过程显示。

## 特性

- **多角色支持**: pm, architect, director, qa 等角色
- **流式输出**: SSE 实时流式响应
- **思考过程**: 自动解析并显示 LLM 思考过程
- **类型安全**: TypeScript 完整类型支持
- **可复用**: Hook + 组件双层抽象

## 快速开始

### 1. 使用组件（推荐）

```tsx
import { AIDialoguePanel } from '@/app/components/ai-dialogue';

// PM 对话面板
function PMPage() {
  return (
    <AIDialoguePanel
      role="pm"
      roleName="尚书令"
      welcomeMessage="尚书令已就绪"
      context={{ workspace, taskCount }}
    />
  );
}

// Architect 对话面板
function ArchitectPage() {
  return (
    <AIDialoguePanel
      role="architect"
      roleName="中书令"
      roleTheme={{
        primary: 'purple',
        secondary: 'purple-400',
        gradient: 'from-purple-500 to-purple-700',
      }}
      welcomeMessage="中书令已就绪"
    />
  );
}
```

### 2. 使用 Hook（自定义 UI）

```tsx
import { useRoleChat } from '@/app/components/ai-dialogue';

function CustomChat() {
  const {
    messages,
    inputValue,
    setInputValue,
    isLoading,
    sendMessage,
    chatStatus,
  } = useRoleChat({
    role: 'pm',
    welcomeMessage: '尚书令已就绪',
    context: { workspace: '/path' },
  });

  return (
    <div>
      {messages.map(m => (
        <div key={m.id}>{m.content}</div>
      ))}
      <input
        value={inputValue}
        onChange={e => setInputValue(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && sendMessage()}
      />
    </div>
  );
}
```

## API 参考

### AIDialoguePanel Props

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| dialogueRole | `DialogueRole` | 是 | 角色标识符: pm, architect, director, qa |
| roleDisplayName | `string` | 是 | 角色显示名称 |
| roleTheme | `object` | 否 | 主题色配置 |
| welcomeMessage | `string` | 否 | 欢迎消息 |
| context | `object` | 否 | 上下文信息传递给 LLM |
| visible | `boolean` | 否 | 是否显示，默认 true |

### useRoleChat Options

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| role | `DialogueRole` | 是 | 角色标识 |
| welcomeMessage | `string` | 否 | 欢迎消息 |
| context | `object` | 否 | 上下文信息 |

### useRoleChat Return

| 属性 | 类型 | 说明 |
|------|------|------|
| messages | `Message[]` | 消息列表 |
| inputValue | `string` | 输入框值 |
| setInputValue | `function` | 设置输入框值 |
| isLoading | `boolean` | 是否加载中 |
| chatStatus | `ChatStatus` | LLM 配置状态 |
| sendMessage | `function` | 发送消息 |
| clearMessages | `function` | 清空对话 |
| checkStatus | `function` | 检查 LLM 状态 |
| handleKeyDown | `function` | 键盘事件处理 |

## 后端 API

### 非流式对话

```http
POST /v2/role/{role}/chat
Content-Type: application/json

{
  "message": "用户消息",
  "context": { "key": "value" },
  "system_prompt": "可选自定义提示词"
}
```

### 流式对话 (SSE)

```http
POST /v2/role/{role}/chat/stream
Content-Type: application/json

{
  "message": "用户消息",
  "context": { "key": "value" }
}
```

### 获取状态

```http
GET /v2/role/{role}/chat/status
```

### 列出支持的角色

```http
GET /v2/role/chat/roles
```

## 架构

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  AIDialoguePanel │────▶│   useRoleChat    │────▶│  Backend API    │
│   (UI Component) │     │   (React Hook)   │     │ /v2/role/{role} │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                │
                                ▼
                        ┌──────────────────┐
                        │  AIExecutor /    │
                        │ StreamExecutor   │
                        │ (Platform Layer) │
                        └──────────────────┘
```

## 与旧组件对比

| 特性 | 旧组件 (PMAIDialoguePanel) | 新套件 (AIDialoguePanel) |
|------|---------------------------|-------------------------|
| 角色支持 | 仅 PM | 任意角色 |
| 复用性 | 低 | 高 |
| 流式输出 | 支持 | 支持 |
| 思考过程 | 支持 | 支持 |
| Hook 抽象 | 无 | 有 |
| 类型安全 | 部分 | 完整 |

## 迁移指南

### 从旧 PMAIDialoguePanel 迁移

旧代码:
```tsx
import { PMAIDialoguePanel } from './PMAIDialoguePanel';

<PMAIDialoguePanel pmRunning={pmRunning} workspace={workspace} taskCount={taskCount} />
```

新代码（自动迁移，无需改动）:
```tsx
// PMAIDialoguePanel 现在内部使用 AIDialoguePanel
import { PMAIDialoguePanel } from './PMAIDialoguePanel';

<PMAIDialoguePanel pmRunning={pmRunning} workspace={workspace} taskCount={taskCount} />
```

### 添加新角色对话

```tsx
// 创建 ArchitectAIDialoguePanel.tsx
import { AIDialoguePanel } from '@/app/components/ai-dialogue';

export function ArchitectAIDialoguePanel() {
  return (
    <AIDialoguePanel
      dialogueRole="architect"
      roleDisplayName="中书令"
      roleTheme={{
        primary: 'purple',
        secondary: 'purple-400',
        gradient: 'from-purple-500 to-purple-700',
      }}
      welcomeMessage="中书令已就绪"
    />
  );
}
```

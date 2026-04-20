# Phase 1: P0/P1 致命缺口修复蓝图

> **技术栈**: Electron + React 18 + TypeScript + Vite + Zustand
> **优先级**: 🔴 最高 (必须立即修复)
> **工期**: 1-2 周
> **目标**: 消除所有致命缺口，确保基本功能可用

---

## 🎯 任务清单

### T1: Factory API 路径修复 [CRITICAL]

**问题描述**:
- 前端调用 `/v2/factory/runs`
- 后端实现 `/factory/runs` (无 v2 前缀)
- 影响: Factory 无法从前端启动

**修复方案**:

```python
# src/backend/polaris/delivery/http/routers/factory.py

# 修改前
router = APIRouter(prefix="/factory", tags=["factory"], dependencies=[Depends(require_auth)])

# 修改后
router = APIRouter(prefix="/v2/factory", tags=["factory"], dependencies=[Depends(require_auth)])
```

**执行步骤**:
1. [ ] 读取 `src/backend/polaris/delivery/http/routers/factory.py`
2. [ ] 修改 `prefix="/factory"` → `prefix="/v2/factory"`
3. [ ] 搜索所有内部路由引用，更新为 `/v2/factory`
4. [ ] 运行 `ruff check` 和 `ruff format`
5. [ ] 运行 pytest 验证

---

### T2: EvidenceViewer URL 修复 [CRITICAL]

**问题描述**:
- 前端调用 `/api/v2/resident/decisions/.../evidence`
- 正确路径应为 `/v2/resident/decisions/.../evidence`
- 影响: 决策证据查看器 404

**修复方案**:

```typescript
// src/frontend/src/app/components/resident/EvidenceViewer.tsx (line 62)

// 修改前
`/api/v2/resident/decisions/${encodeURIComponent(decisionId)}/evidence`

// 修改后
`/v2/resident/decisions/${encodeURIComponent(decisionId)}/evidence`
```

**Why**: 移除错误的 `/api` 前缀，与后端路由保持一致

**执行步骤**:
1. [ ] 读取 `EvidenceViewer.tsx`
2. [ ] 定位所有 `/api/v2/` URL 调用
3. [ ] 统一移除 `/api` 前缀
4. [ ] 运行 `npm run typecheck`
5. [ ] 运行 `npm run lint`

---

### T3: Conversation API 响应解包 [HIGH]

**问题描述**:
- 前端期望 `Conversation[]` 数组
- 后端返回 `{conversations: [], total: n}` 包装对象
- 影响: 对话列表和创建功能运行时错误

**修复方案**:

```typescript
// src/frontend/src/services/conversationApi.ts
import { apiFetch } from '@/lib/apiFetch';
import { handleResponse } from '@/lib/handleResponse';

interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface ConversationListResponse {
  conversations: Conversation[];
  total: number;
}

export const conversationApi = {
  async listConversations(): Promise<ConversationListResponse> {
    const res = await apiFetch('/v2/conversations');
    const data = await handleResponse<{ conversations: Conversation[]; total: number }>(res);
    return {
      conversations: data.conversations || [],
      total: data.total || 0,
    };
  },

  async createConversation(title: string): Promise<Conversation> {
    const res = await apiFetch('/v2/conversations', {
      method: 'POST',
      body: JSON.stringify({ title }),
    });
    return handleResponse<Conversation>(res);
  },
};
```

**Why**: 使用显式 interface 定义 API 响应类型，符合 TypeScript Strict Mode 规范

**执行步骤**:
1. [ ] 读取 `conversationApi.ts`
2. [ ] 添加 `Conversation` 和 `ConversationListResponse` 接口
3. [ ] 修正响应解包逻辑
4. [ ] 运行类型检查

---

### T4: 类型安全合规修复 [HIGH]

**违规统计**: 42 处 `any` 使用

**修复方案**:

```typescript
// src/frontend/src/app/components/llm/adapters/types.ts

// 修改前
interface ViewActions {
  [key: string]: (...args: any[]) => void;
}

// 修改后
type ViewAction = (...args: unknown[]) => void;
interface ViewActions {
  [key: string]: ViewAction;
}
```

```typescript
// src/frontend/src/app/components/llm/adapters/StrictViewAdapter.ts

// 修改前
export class StrictViewAdapter {
  handleAction(action: string, ...args: any[]): void { ... }
}

// 修改后
interface ActionPayload {
  action: string;
  args: unknown[];
}

export class StrictViewAdapter {
  private readonly handlers: Map<string, (args: unknown[]) => void> = new Map();

  handleAction(payload: ActionPayload): void {
    const handler = this.handlers.get(payload.action);
    if (handler) {
      handler(payload.args);
    }
  }
}
```

```typescript
// src/frontend/src/app/components/ai-dialogue/ToolCallRenderer.tsx

// 修改前
const renderParams = (params: Record<string, any>) => { ... };

// 修改后
interface ToolCallParams {
  [key: string]: unknown;
}

const renderParams = (params: ToolCallParams): JSX.Element => { ... };
```

**Why**: 使用 `unknown[]` 配合类型守卫处理外部数据输入，符合"防御性编程"原则

**执行步骤**:
1. [ ] 逐文件修复，从适配器开始
2. [ ] 定义完整的类型 interface
3. [ ] 使用 `unknown` 配合类型守卫
4. [ ] 运行 `mypy` 验证 (如果需要)
5. [ ] 确保 `npm run typecheck` 通过

---

### T5: Electron 安全加固 [HIGH]

**修复方案**:

```typescript
// src/electron/main.cjs - BrowserWindow 配置

// 添加 sandbox 配置
const win = new BrowserWindow({
  width: 1200,
  height: 800,
  webPreferences: {
    preload: path.join(__dirname, 'preload.cjs'),
    contextIsolation: true,    // 已有 ✓
    nodeIntegration: false,     // 已有 ✓
    sandbox: true,             // 新增 - 启用沙箱
    webSecurity: true,         // 新增 - 启用 Web 安全
  }
});
```

**Why**: `sandbox: true` 确保渲染进程无法直接访问 Node.js API，所有操作必须通过 preload IPC 通道

**CSP 修复建议**:
```html
<!-- src/frontend/index.html -->
<!-- 使用 nonce 替代 unsafe-inline -->
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; script-src 'self' 'nonce-{random}'; style-src 'self' 'nonce-{random}';">
```

**执行步骤**:
1. [ ] 读取 `main.cjs`
2. [ ] 添加 `sandbox: true`
3. [ ] 添加 `webSecurity: true`
4. [ ] 评估 CSP unsafe-inline 风险
5. [ ] 验证 Electron 启动正常

---

## 📋 Phase 1 验收清单

- [ ] T1: Factory API `/v2/factory` 前缀统一
- [ ] T2: EvidenceViewer URL `/api` 前缀移除
- [ ] T3: Conversation API 响应解包
- [ ] T4: `any` 使用降至 0
- [ ] T5: Electron sandbox 启用
- [ ] `npm run typecheck` 通过
- [ ] `npm run lint` 通过
- [ ] pytest 测试通过

---

## 🔗 依赖关系

```
T1-T5 可并行执行，无相互依赖
```

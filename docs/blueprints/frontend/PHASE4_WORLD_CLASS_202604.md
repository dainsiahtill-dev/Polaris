# Phase 4: 世界顶级对标蓝图

> **技术栈**: Electron + React 18 + TypeScript + Vite + Zustand + Monaco Editor
> **优先级**: 🟢 长期
> **工期**: 6-8 周
> **前置条件**: Phase 3 完成
> **目标**: 对标世界顶级 AI Agent 产品 (Cursor, Copilot Workspace, Devin)

---

## 🎯 任务清单

### T1: Monaco Editor 集成 [HIGH]

**竞品对比**: Cursor AI, VS Code Web
**当前状态**: 无代码编辑器集成

**实现方案**:

```typescript
// src/frontend/src/app/components/editor/MonacoEditorPanel.tsx
import { useRef, useEffect, useCallback } from 'react';
import * as monaco from 'monaco-editor';
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';

// 配置 Monaco Worker
self.MonacoEnvironment = {
  getWorker(_, label) {
    return new editorWorker();
  },
};

interface MonacoEditorPanelProps {
  filePath: string;
  content: string;
  language?: string;
  readOnly?: boolean;
  onChange?: (value: string) => void;
  onSave?: (value: string) => void;
}

export const MonacoEditorPanel: React.FC<MonacoEditorPanelProps> = ({
  filePath,
  content,
  language = 'typescript',
  readOnly = false,
  onChange,
  onSave,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);

  // 初始化编辑器
  useEffect(() => {
    if (!containerRef.current) return;

    const editor = monaco.editor.create(containerRef.current, {
      value: content,
      language,
      theme: 'vs-dark',
      readOnly,
      minimap: { enabled: true },
      fontSize: 14,
      lineNumbers: 'on',
      roundedSelection: true,
      scrollBeyondLastLine: false,
      automaticLayout: true,
      tabSize: 2,
      wordWrap: 'on',
    });

    editorRef.current = editor;

    // 监听内容变化
    editor.onDidChangeModelContent(() => {
      const value = editor.getValue();
      onChange?.(value);
    });

    // 快捷键: Ctrl+S 保存
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      const value = editor.getValue();
      onSave?.(value);
    });

    return () => {
      editor.dispose(); // Cleanup: 防止内存泄漏
    };
  }, []); // 空依赖，仅初始化一次

  // 内容变化时更新
  useEffect(() => {
    const editor = editorRef.current;
    if (editor && editor.getValue() !== content) {
      const model = editor.getModel();
      model?.setValue(content);
    }
  }, [content]);

  // 语言变化时更新
  useEffect(() => {
    const editor = editorRef.current;
    if (editor) {
      monaco.editor.setModelLanguage(editor.getModel()!, language);
    }
  }, [language]);

  return <div ref={containerRef} className="h-full w-full" />;
};
```

**Why**:
1. Web Worker 配置避免阻塞主线程
2. useEffect cleanup 防止内存泄漏
3. 快捷键通过 `addCommand` 注册，符合 Electron 最佳实践

**执行步骤**:
1. [ ] 安装 `monaco-editor`
2. [ ] 配置 Vite 插件 `vite-plugin-monaco-editor`
3. [ ] 创建 `MonacoEditorPanel.tsx`
4. [ ] 实现文件读取/保存 API 集成
5. [ ] 添加语法高亮和 IntelliSense
6. [ ] 编写测试

**验收标准**:
- [ ] Monaco Editor 正常加载
- [ ] 文件内容正确显示
- [ ] 保存功能正常工作
- [ ] 语法高亮正确

---

### T2: 云端工作区同步 [HIGH]

**竞品对比**: Cursor AI Cloud, GitHub Codespaces
**当前状态**: 仅本地工作区

**实现方案**:

```typescript
// src/frontend/src/app/services/cloudSyncService.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface CloudSyncState {
  isEnabled: boolean;
  syncStatus: 'idle' | 'syncing' | 'error' | 'offline';
  lastSyncTime: string | null;
  conflictFiles: string[];

  enable: () => void;
  disable: () => void;
  syncNow: () => Promise<void>;
  resolveConflict: (filePath: string, resolution: 'local' | 'remote') => void;
}

export const useCloudSyncStore = create<CloudSyncState>()(
  persist(
    (set, get) => ({
      isEnabled: false,
      syncStatus: 'idle',
      lastSyncTime: null,
      conflictFiles: [],

      enable: () => set({ isEnabled: true }),

      disable: () => set({
        isEnabled: false,
        syncStatus: 'idle',
      }),

      syncNow: async () => {
        if (!get().isEnabled) return;

        set({ syncStatus: 'syncing' });

        try {
          // 获取本地变更
          const localChanges = await fileService.getChangesSince(
            get().lastSyncTime
          );

          // 同步到云端
          await apiPost('/v2/sync/push', { changes: localChanges });

          // 获取远程变更
          const remoteChanges = await apiGet<Change[]>('/v2/sync/pull');

          // 检测冲突
          const conflicts = detectConflicts(localChanges, remoteChanges);

          if (conflicts.length > 0) {
            set({
              syncStatus: 'error',
              conflictFiles: conflicts.map((c) => c.path),
            });
            return;
          }

          // 应用远程变更
          await fileService.applyChanges(remoteChanges);

          set({
            syncStatus: 'idle',
            lastSyncTime: new Date().toISOString(),
          });
        } catch (error) {
          set({ syncStatus: 'error' });
          throw error;
        }
      },

      resolveConflict: (filePath, resolution) => {
        // 应用冲突解决方案
        // ...
        set((state) => ({
          conflictFiles: state.conflictFiles.filter((f) => f !== filePath),
        }));
      },
    }),
    { name: 'polaris:cloud-sync' }
  )
);
```

**Why**: Zustand persist 确保同步状态在页面刷新后保持

**执行步骤**:
1. [ ] 设计云端同步 API
2. [ ] 实现冲突检测算法
3. [ ] 创建同步状态 UI
4. [ ] 实现手动/自动同步
5. [ ] 编写测试

**验收标准**:
- [ ] 变更同步到云端
- [ ] 冲突正确检测
- [ ] 冲突解决正常工作

---

### T3: 实时协作编辑 [HIGH]

**竞品对比**: Figma, Google Docs
**当前状态**: 无
**目标**: 多用户同时编辑

**实现方案**:

```typescript
// src/frontend/src/app/components/collaboration/CollabEditor.tsx
import { useEffect, useState, useCallback, useRef } from 'react';
import { LiveMap, LiveList, LiveObject } from '@liveblocks/client';
import { LiveblocksProvider, RoomProvider, useOthers, useSelf } from '@liveblocks/react';

interface CollabUser {
  id: string;
  name: string;
  color: string;
  cursor?: { x: number; y: number };
}

interface CollabEditorProps {
  filePath: string;
  initialContent: string;
}

export const CollabEditor: React.FC<CollabEditorProps> = ({
  filePath,
  initialContent,
}) => {
  const [content, setContent] = useState(initialContent);
  const contentRef = useRef(new LiveObject({ text: initialContent }));
  const others = useOthers();
  const self = useSelf();

  const handleChange = useCallback((newContent: string) => {
    setContent(newContent);
    contentRef.current.set('text', newContent);
  }, []);

  return (
    <div className="collab-editor">
      {/* 编辑器内容 */}
      <Editor content={content} onChange={handleChange} />

      {/* 远程用户光标 */}
      <div className="remote-cursors">
        {others.map((other) => (
          <RemoteCursor
            key={other.connectionId}
            user={other.presence}
            color={other.presence.color}
          />
        ))}
      </div>

      {/* 用户头像列表 */}
      <div className="collab-avatars">
        {others.map((other) => (
          <Avatar
            key={other.connectionId}
            name={other.presence.name}
            color={other.presence.color}
          />
        ))}
      </div>
    </div>
  );
};

// App.tsx 中配置
function App() {
  return (
    <LiveblocksProvider publicApiKey={import.meta.env.VITE_LIVEBLOCKS_KEY}>
      <RoomProvider
        id={`workspace:${workspaceId}`}
        initialPresence={{ cursor: null, name: '', color: '' }}
        initialStorage={{ content: new LiveObject({ text: '' }) }}
      >
        <CollabEditor filePath={filePath} initialContent={content} />
      </RoomProvider>
    </LiveblocksProvider>
  );
}
```

**Why**: Liveblocks 提供基于 CRDT 的实时协作，无需自建协同服务器

**执行步骤**:
1. [ ] 选择 Liveblocks 作为协作后端
2. [ ] 配置 Room 和权限
3. [ ] 实现光标同步
4. [ ] 实现文本协同编辑
5. [ ] 编写测试

**验收标准**:
- [ ] 多用户实时看到他人修改
- [ ] 光标位置同步
- [ ] 无编辑冲突

---

### T4: 完整无障碍访问 (a11y) [MEDIUM]

**竞品对比**: VS Code Accessibility
**目标**: WCAG 2.1 AA 合规

**实现方案**:

```typescript
// src/frontend/src/app/components/common/AccessibleButton.tsx
import { forwardRef, ButtonHTMLAttributes } from 'react';

interface AccessibleButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string; // 按钮的文本标签
  shortcut?: string; // 键盘快捷键
}

export const AccessibleButton = forwardRef<HTMLButtonElement, AccessibleButtonProps>(
  ({ label, shortcut, children, onClick, ...props }, ref) => {
    // 注册全局快捷键
    useEffect(() => {
      if (!shortcut) return;

      const handler = (e: KeyboardEvent) => {
        // 检查是否聚焦在输入框中
        const activeElement = document.activeElement;
        if (
          activeElement?.tagName === 'INPUT' ||
          activeElement?.tagName === 'TEXTAREA' ||
          (activeElement as HTMLElement)?.isContentEditable
        ) {
          return;
        }

        const keys = shortcut.toLowerCase().split('+');
        const modifiers = {
          ctrl: keys.includes('ctrl'),
          shift: keys.includes('shift'),
          alt: keys.includes('alt'),
          meta: keys.includes('meta'),
        };
        const key = keys.find((k) => !['ctrl', 'shift', 'alt', 'meta'].includes(k));

        if (
          e.key.toLowerCase() === key &&
          e.ctrlKey === modifiers.ctrl &&
          e.shiftKey === modifiers.shift &&
          e.altKey === modifiers.alt &&
          e.metaKey === modifiers.meta
        ) {
          e.preventDefault();
          (props as { onClick?: () => void }).onClick?.();
        }
      };

      window.addEventListener('keydown', handler);
      return () => window.removeEventListener('keydown', handler); // Cleanup
    }, [shortcut, onClick]);

    return (
      <button
        ref={ref}
        onClick={onClick}
        aria-label={label}
        aria-keyshortcuts={shortcut}
        {...props}
      >
        {children}
        {shortcut && (
          <span className="sr-only" aria-hidden="true">
            ({shortcut})
          </span>
        )}
      </button>
    );
  }
);
```

**执行步骤**:
1. [ ] 审计所有交互组件的 ARIA 属性
2. [ ] 实现焦点管理 (Focus trap)
3. [ ] 添加全局快捷键支持
4. [ ] 测试屏幕阅读器兼容性
5. [ ] 添加高对比度模式

**验收标准**:
- [ ] 所有按钮有 `aria-label`
- [ ] 模态框有 `role="dialog"` 和 `aria-modal`
- [ ] 快捷键正常工作
- [ ] 屏幕阅读器可正常导航

---

### T5: 工作区模板系统 [MEDIUM]

**竞品对比**: GitHub Codespaces Templates
**当前状态**: 无

**实现方案**:

```typescript
// src/frontend/src/app/types/workspaceTemplate.ts
interface WorkspaceTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: 'blank' | 'web-app' | 'api' | 'fullstack' | 'custom';
  config: {
    files: TemplateFile[];
    dependencies?: Record<string, string>;
    scripts?: Record<string, string>;
  };
}

interface TemplateFile {
  path: string;
  content: string;
  encoding: 'utf-8' | 'base64';
}

// 使用示例
const templates: WorkspaceTemplate[] = [
  {
    id: 'react-vite',
    name: 'React + Vite',
    description: '现代化的 React 项目模板',
    icon: '⚛️',
    category: 'web-app',
    config: {
      files: [
        { path: 'package.json', content: '{...}', encoding: 'utf-8' },
        { path: 'src/main.tsx', content: '...', encoding: 'utf-8' },
      ],
      dependencies: {
        react: '^18.2.0',
        'react-dom': '^18.2.0',
        vite: '^5.0.0',
      },
    },
  },
];
```

**执行步骤**:
1. [ ] 设计模板 schema
2. [ ] 实现模板市场 UI
3. [ ] 实现模板实例化逻辑
4. [ ] 添加自定义模板导入/导出
5. [ ] 编写测试

**验收标准**:
- [ ] 模板列表正常显示
- [ ] 模板可正常实例化
- [ ] 自定义模板导入导出正常

---

## 📋 Phase 4 验收清单

- [ ] T1: Monaco Editor 集成
- [ ] T2: 云端工作区同步
- [ ] T3: 实时协作编辑
- [ ] T4: 完整无障碍访问
- [ ] T5: 工作区模板系统
- [ ] 所有新功能通过测试
- [ ] `npm run typecheck` 通过
- [ ] `npm run test:e2e` 通过

---

## 🎯 世界顶级对标总览

| 功能 | Polaris | Cursor | Copilot | Devin | 状态 |
|------|-------------|--------|---------|-------|------|
| Monaco Editor | 🔄 开发中 | ✅ | ✅ | ✅ | Phase 4 |
| 云端同步 | 🔄 开发中 | ✅ | ✅ | ✅ | Phase 4 |
| 实时协作 | 🔄 开发中 | ✅ | ✅ | ✅ | Phase 4 |
| Kanban 看板 | ✅ Phase 3 | ✅ | ✅ | ✅ | Phase 3 |
| 依赖图 | ✅ Phase 3 | ✅ | ✅ | ✅ | Phase 3 |
| 主题切换 | ✅ Phase 3 | ✅ | ✅ | ✅ | Phase 3 |
| 托盘通知 | ✅ Phase 3 | ✅ | ✅ | ✅ | Phase 3 |
| LLM 多角色 | ✅ 独有 | ❌ | ❌ | ❌ | **领先** |
| 审计日志 | ✅ | ✅ | ✅ | ✅ | **持平** |

---

**最终目标**: 成为世界顶级 AI Agent 自动化软件开发工具

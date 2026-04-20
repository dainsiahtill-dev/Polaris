# Workspace 持久化与刷新机制

本文说明 Polaris 中 `workspace` 的真相源、刷新链路，以及为什么前端修改后不能再发生“路径回退”。

## 设计目标

- 前端设置 `workspace` 后，后端内存状态立即切换
- `POLARIS_WORKSPACE` 进程环境立即同步
- 全局持久化配置立即落盘
- Electron 主进程与 backend 读取同一份全局 settings
- 旧请求不能覆盖新的 `workspace` 设置结果

## 真相源

当前 `workspace` 的统一真相源有三层，但优先级必须一致：

1. 当前 backend 进程内的 `state.settings.workspace`
2. 当前 backend 进程环境变量 `POLARIS_WORKSPACE`
3. 全局持久化文件 `~/.polaris/config/settings.json` 或等价平台目录

约束：

- 进程内状态是运行时真相
- 环境变量是 env-driven 代码路径的桥接层
- 持久化文件是重启后的恢复源

三者必须在一次设置更新后保持一致。

## 当前实现

### Backend

- `src/backend/app/routers/system.py`
  - `/settings` 更新后立即同步进程环境
  - 再刷新 PM / Director 相关运行态
  - 最后保存持久化 settings

- `src/backend/app/settings_utils.py`
  - `sync_process_settings_environment()`
  - `save_persisted_settings()`
  - `load_persisted_settings()`

- `src/backend/api/main.py`
  - 应用启动时先用当前 `Settings` 同步进程环境，避免 `create_app(Settings(...))` 与 env 脱节

- `src/backend/core/startup/backend_bootstrap.py`
  - bootstrap 会把合并后的 `ConfigSnapshot` 真正物化为 `Settings`
  - `workspace` 不再在启动阶段丢失
  - 进程环境变量也会优先使用 snapshot 中解析出的 `workspace`

### Frontend

- `src/frontend/src/hooks/useSettings.ts`
  - 使用请求序号，保证“后发请求赢”
  - 旧的 `GET /settings` 响应不能覆盖新的 `POST /settings` 结果

- `src/frontend/src/app/hooks/useRuntime.ts`
  - 当 `workspace` 由父组件控制时，不再额外自动拉取 settings
  - 避免 `settings_changed` 事件和受控 `workspace` 产生竞态重连

### Electron

- `src/electron/config-paths.cjs`
  - 与 backend 使用同一套 Polaris 根目录解析逻辑

- `src/electron/main.cjs`
  - 读取与 backend 相同的全局 `settings.json`
  - 避免 Windows 下 Electron 与 backend 读写不同目录
  - 启动 backend 时，默认显式传入“上次持久化的 workspace”
  - 除非设置 `POLARIS_WORKSPACE_FORCE=1`，否则持久化 workspace 优先于临时环境变量

## 已修复的问题

### 1. 前端 workspace 回退

根因：

- 初始 `GET /settings` 与用户后续 `POST /settings` 并发
- 旧 GET 返回较晚时，会把新的 `workspace` 覆盖掉

修复：

- 在 `useSettings` 中引入请求序号
- 只有最新请求的响应允许写回状态

### 2. `POLARIS_WORKSPACE` 不刷新

根因：

- `/settings` 更新只改了 `state.settings.workspace`
- 没有同步 `os.environ["POLARIS_WORKSPACE"]`

修复：

- 在 settings 更新和应用启动时统一调用 `sync_process_settings_environment()`

### 3. Electron 与 backend 配置路径不一致

根因：

- Electron 主进程和 backend 在 Windows 上可能解析出不同的 Polaris 全局配置目录

修复：

- 抽出共享规则到 `src/electron/config-paths.cjs`
- 让 Electron 的全局 settings 路径与 backend 对齐

### 4. 第三次启动回退到仓库根目录

根因：

- Electron 启动 backend 时，在“已有持久化 workspace”的情况下没有显式传 `--workspace`
- 新 bootstrap 又没有把 merged config 中的 `workspace` 正确注入最终 `Settings`
- 结果 backend 会按当前工作目录启动，通常就是仓库根目录

修复：

- Electron 启动时默认把持久化 `workspace` 作为 backend 启动参数传入
- backend bootstrap 把 `ConfigSnapshot` 完整物化为 `Settings`
- `workspace` 在启动期不再丢失

## 回归验证

Backend：

```bash
python -m pytest -q src/backend/tests/test_workspace_settings_sync.py
```

Frontend：

```bash
npm run test -- src/frontend/src/hooks/useSettings.test.ts
npm run typecheck
```

Electron 配置路径：

```bash
node --test src/electron/config-paths.test.cjs
```

Backend 启动 workspace 注入：

```bash
python -m pytest -q src/backend/tests/test_backend_bootstrap_workspace.py
```

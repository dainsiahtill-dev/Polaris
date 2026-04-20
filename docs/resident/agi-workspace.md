# AGI 工作台

## 1. 入口

主入口：

- `src/frontend/src/app/components/ControlPanel.tsx`
  - 下拉菜单增加 `AGI 工作区`

挂载点：

- `src/frontend/src/app/App.tsx`
  - `activeRoleView='agi'`

主组件：

- `src/frontend/src/app/components/resident/ResidentWorkspace.tsx`

侧栏摘要：

- `src/frontend/src/app/components/ContextSidebar.tsx`

## 2. 工作台结构

### 左侧

- `AGI Identity`
  - 名称、使命、所有者、运行模式
- `Agenda`
  - 当前焦点、风险登记、下一步
- `Capability Graph`
  - 高置信能力节点与能力缺口

### 右侧

- `总览`
  - metrics、治理摘要、最新 insight
- `目标`
  - 创建目标、筛选、approve / reject / stage / 写入 PM / 交给 PM
- `决策`
  - 决策轨迹、备选方案、证据、置信度
- `学习`
  - 技能工坊、反事实实验、自改提案

## 3. 支持的动作

- 启动 AGI
- 停止 AGI
- 手动 `tick`
- 保存身份
- 创建目标
- 批准目标
- 拒绝目标
- 暂存目标
- 写入 PM 运行态
- 将目标送交 PM
- 刷新技能
- 运行反事实实验
- 生成自改提案

## 4. 数据来源

前端 hook：

- `src/frontend/src/hooks/useResident.ts`

API 客户端：

- `src/frontend/src/services/api.ts`

状态来源：

- `/v2/resident/status?details=true`
- `/state/snapshot` 中的 `resident`

## 5. 验证

前端单测：

- `src/frontend/src/app/components/resident/ResidentWorkspace.test.tsx`

推荐命令：

```bash
npm run typecheck
npm run test -- src/frontend/src/app/components/resident/ResidentWorkspace.test.tsx
```

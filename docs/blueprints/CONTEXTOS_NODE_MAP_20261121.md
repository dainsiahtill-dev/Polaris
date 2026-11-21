# ContextOS 节点地图与折叠状态可视化（2026-11-21）

## 目标

在「查看最终请求」旁同时显示 ContextOS 逐轮节点地图：用户目标、RunCard、工具调用、日志、verification、artifact 与最终请求节点。默认只显示摘要与分布，不展开完整历史。

## 节点状态

- `ACTIVE`：仍存在于当前最终请求。
- `FOLDED`：已折叠为 WorkingState / RunCard 状态投影。
- `REFERENCE`：原始内容已退出当前请求，但 observation / artifact / journal 引用仍保留。
- `EVICTED`：不在当前请求中，只剩历史 journal。
- `UNKNOWN`：没有足够证据证明状态，禁止推断。

## 数据与联动

- 当前快照：`/v2/context/{hash}` 与 `/v2/context/{hash}/final-request`。
- `EngineeringContextView` 额外传入 `engineering.projection.events`；没有 journal 时明确提示折叠历史不完整。
- 点击节点查看折叠原因；有 evidence ref 时可切回 messages 并搜索同一引用。
- RunCard 显示 revision、verification、phase、角色与约 token；approx 值明确标注。

## 视觉与可访问性

- 节点类别使用固定、经验证的 dark palette；CVD/normal/contrast 检查通过。
- 折叠状态同时使用文字、图标和 border，不单独依赖颜色。
- 提供图例、筛选、键盘可操作的节点按钮和 UNKNOWN 状态。
- 桌面双栏，窄屏自然堆叠。

## 验证

- 节点模型与组件测试：3 个新测试通过。
- ContextOS 全套：16 files / 319 tests passed。
- TypeScript strict typecheck 与 Vite build 通过。
- Playwright 实际渲染截图检查通过；临时预览文件已删除。

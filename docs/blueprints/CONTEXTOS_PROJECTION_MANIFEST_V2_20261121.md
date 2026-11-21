# ContextOS Projection Manifest V2（2026-11-21）

## 目标

把 V1 的前端推断节点图升级为后端权威折叠证明：每个节点都有 RunCard 父子关系、fold_state、projection_ref、evidence_ref、原始/投影 token 和可选 request_message_index。

## 后端

`GET /v2/context/{hash}/manifest`

- 通过 `context_snapshot_ref` 找到同一 engineering run 与 journal。
- `build_projection_manifest` 从 run、events、当前 snapshot 生成 `context.projection_manifest.v1`。
- `run_updated`：当前 revision 标记 `FOLDED / working-state:<rev>`，被后续 revision 取代则 `EVICTED`。
- tool/verification/artifact：退出请求但 observation 引用存在时标记 `REFERENCE`。
- 当前请求仍包含 ref 时标记 `ACTIVE` 并给出 `request_message_index`。
- RunCard 按 turn_started 聚合子节点，记录 phase、revision、verification。
- summary 给出 original/projected token、compression_ratio、fold/kind counts；token 明确为近似值。

## 前端

- ContextViewerModal 按需拉取 manifest，不影响 snapshot/final-request 的既有错误路径。
- RunCard 可展开/收起子节点；按 fold 状态筛选。
- 新增压缩分布条，展示 ACTIVE/FOLDED/REFERENCE/EVICTED/UNKNOWN 投影占比。
- 点击证据引用：有 message index 时直接滚动到该消息；否则回退到引用搜索。
- manifest 不可用时保留 V1 journal/snapshot fallback，不伪造后端证明。

## 验证

- 后端 manifest 单测 2 个通过；Context router + manifest 共 43 tests passed。
- 前端模型/组件 4 个相关测试通过；ContextOS 全套 16 files / 320 tests passed。
- ruff/mypy、TypeScript strict、Vite build 通过。
- Playwright V2 实际渲染截图通过；临时预览文件已删除。

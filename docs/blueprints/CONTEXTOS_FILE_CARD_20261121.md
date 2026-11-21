# FileCard：read_file 的内容寻址折叠（2026-11-21）

## 问题

旧 read_file 把文件原文作为普通 tool message 累积，只有超过 32k 工作集时才按完整工具轮次淘汰。没有路径去重、文件级摘要或显式 content_ref。

## FileCard v1

每次 read_file 返回 `engineering.file_card.v1`：

- `path / revision / sha256 / size_bytes / line_count`
- `symbols`：最多 24 个宿主确定性提取的符号
- `summary`
- `content_ref = filecard:<sha256>`
- `content_preview`：默认最多 8KB（含截断标记预留），需要时仍可按 start_line/end_line/max_bytes 读取
- `content_bounds`
- `stale`

完整 observation 仍写入同一 engineering SQLite。模型可用 `read_file(content_ref=...)` 从当前 run journal 找回历史卡片，不必重新读取文件；如果当前磁盘 sha 已变化，卡片标记 `stale=true` 并给出 `current_sha256`，不冒充当前源码。

## Manifest

`/v2/context/{hash}/manifest` 识别 FileCard：

- `kind=file_card`
- `projection_ref=content_ref`
- `original_tokens` 按完整文件大小估算
- `projected_tokens` 按预览/卡片元数据估算
- 退出当前请求但引用保留时为 `REFERENCE`

## 边界

- 不猜测模型语义、不自动修复文件内容。
- 默认 8KB 预览并不减少 SQLite 中的真实证据；只是减少重复上下文。
- 显式 range/max_bytes 仍允许更大读取。
- 旧文件内容被折叠后必须能按 content_ref 找回；无法证明当前一致时标 stale。

## 验证

- FileCard 与 manifest 5 个目标测试通过。
- ContextOS 前端 16 files / 320 tests passed。
- ruff/mypy 与 TypeScript strict 通过。

## 生命周期增强（后续实现）

- **SourceSnapshot 持久化**：完整源字节先写入 `.polaris/runtime/filecards/<sha256>.json`，再暴露 content_ref；不存在才创建，已存在则校验复用，不覆盖历史版本。
- **精确恢复**：`read_file(content_ref, start_line/end_line/max_bytes)` 从不可变快照恢复指定范围，不偷读现场文件；缺快照或 hash 不一致时明确失败。
- **同路径去重**：请求投影中，同一路径只让最新 FileCard 携带预览；旧卡折叠并指向最新 content_ref，观察记录仍全部保留。
- **pin**：当前 edit 目标与验收条款相关文件保留预览及原因，不默认淘汰。
- **WorkingState**：增加 FileCard 投影（路径、revision、sha、symbols、content_ref、stale、pin_reason、fold_state）。
- **stale**：现场 sha 与快照不同则标记，恢复仍返回承诺的旧快照，不把新文件伪装成历史。
- **manifest**：同路径旧卡标记 `FOLDED / superseded`，pin 卡标记原因，分布区分原始与投影 token。

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

# 真实运行审计：pelican-bike（2026-11-19）

Run: session `e6a88b2fa2a845bba0f1cc1c7c202825`，direct/agnes-3.0-flash，
workspace `/home/dains/pelican-bike`，UI（5191）真实提交，人工取消于 QA 前。

## 经济学

| 指标 | 值 |
|---|---|
| 墙钟 | ~30 分钟 |
| LLM 调用 / 估算 token | 280 / ~2.6M（avg 22k） |
| 工具调用 | 140（read_file 82, browser_observe 29, repo_tree 9, execute_command 9, edit_file 8, write_file 1, verify_project 1） |
| 传输重试 / provider 失败 | 0 / 0（agnes 全程稳定） |
| 首次验证通过前的工具调用 | 120（严重浪费） |

## 根因链（按损失排序）

1. **门禁消息与代码自相矛盾（最大损失，61/82 次 read_file 被拦）**。
   写满 5 次后门禁触发；拦截消息明确说"read_file to inspect one
   specific file"是允许的，但代码里 `read_file` 分支在 index.html 存在后
   同样拦截。弱模型按消息指引反复重试 read_file → 全部被拦 → 直到偶然
   执行 verify_project 才解锁。已修：read_file 不再被门禁拦截（有
   max_bytes 天然有界），消息与实际允许集一致。
2. **门禁风暴绕过精确循环检测**。loop 检测按 (tool, args-json) 精确匹配，
   被拦 read 的参数每次不同（不同 range/文件）→ 永不触发 loop_detected。
   已修：整轮全部被拦截构成 gate_block_streak，2/4 次时注入转向指引。
3. **read_file range 教条款**。`range_required:true` 缺 start_line 直接
   ValueError。已修：缺省边界自动补齐（start→1，end→EOF）。
4. **原始 traceback 给模型**。FileNotFoundError 原文回流。已修：友好中文
   提示 + 指引 write_file。
5. 真文件错误极少：FileNotFound×2（写前读，弱模型时序）、range 错×1。
   **82 次 read 里 79 次是平台自造循环，不是模型读文件的需求本身。**

## 有界读取问题的回答

系统**早已支持**有界读：`read_file(start_line, end_line, max_bytes)`，
whole-file 默认也被 `max_tool_output_bytes` 封顶；52/82 次本来就是
range 读。用户感知的"总是整文件读取"实际是门禁风暴的表象。本轮进一步
放宽部分 range 并保留 byte 封顶。

## 产物质量快照（取消时）

- `index.html` 525 行/24KB + `assets/three.min.js`（633KB，curl 下载）
- 内联脚本语法检查通过；verify_project 已通过一次（node --check）
- QA 未开始即被取消

## 仍开放

- 取消时 QA（独立复核）尚未跑；恢复运行可续。
- 视觉类测试在本机多进程跑时随机失败（Playwright screenshot protocol
  error，单跑全绿，干净树同样复现）——环境类已知问题。
- 叙述预览（message text excerpt）需后端重启后对新事件生效。


## 验证运行链（修复逐步生效）

| Run | 底座状态 | 结果 |
|---|---|---|
| 1 (e6a88b2f) | 修复前 | 门禁风暴：65 次拦截/120 次调用才首次验证通过，人工取消 |
| 2 (6e61f355) | 门禁对齐后 | 0 风暴但暴露下一层：文本历史 153k 顶窗，单次裁剪失败即死 |
| 3 (00118acc) | 迭代裁剪后 | 验证通过+QA 启动，但 QA 视觉史因**压缩与准入两种计价**（base64 当文本 vs 图像序列化）必死 |
| 4 (RUN-AACE) | 计价对齐后 | **completed / passed / approved / accepted**，~8 分钟端到端 |

## 通用根因总结（三层）

1. **门禁消息与代码矛盾** → 弱模型被机器制造的循环消耗（已修：允许有界读 + 拦截 streak 转向指引）。
2. **原生工具历史无预算路径的兜底太脆** → 长对话必须可裁剪（已修：按 1.0/0.75/0.5/0.3 迭代裁剪直至投影合身）。
3. **压缩器与准入模型计价不一致** → 任何含图像的历史必死（已修：message_cost 注入，data_base64 文本/图像分离计价，与保守序列化代理对齐）。

产物：/home/dains/pelican-bike/index.html（708 行/30KB，含鹈鹕/单车/海浪/相机按钮，离线可开）。

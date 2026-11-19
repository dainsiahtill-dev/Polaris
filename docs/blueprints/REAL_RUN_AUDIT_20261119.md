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


## 第四层：验收自欺（用户质询"完成质量太糟糕"后追加）

Run 4 的 accepted 是假阳性：verification=passed 的实际内容是
`node --check assets/three.min.js`（对第三方库做语法检查），QA 是同弱模型
家族看截图自我盖章。平台缺陷：**验收回路自评，无目标保真度核对**。

修复与验证（Run 5）：
- **宿主 Web 探针**（engineering_web_probe）：机器客观判定页面错误、
  canvas/WebGL、两帧像素差证明动画真实在动、按钮点击真实生效；失败则
  降级 verification 并回流给 Director。
- **条款保真职责**注入 Director/QA 提示词：每个具体需求条款必须可见实现，
  QA 须逐条裁决——缺条/视觉错误即 major finding，即使应用无报错。
- Run 5 实证：QA 首次复核**逐条列举证据**（颈/橙喙喉囊/翅膀/菱形车架/
  10 辐条轮/脚蹬/腿/海面浮标/太阳）并发现真实源码缺陷（车轮不转，
  `bike.add()` 返回值被丢弃，index.html:392/423-24）→ **reject**；Director
  修复后（运行时状态探针 frontWheelSpin 由 0 变为正常）→ approved →
  accepted。弱模型艺术的审美上限仍在，但"能跑+条款全真+有证据"成为
  平台可认证的底线。


## 第五层：系统性缺陷修复（D1-D5 + 跨运行学习）

生产验证（v4, pelican-bike-v3, collaborative）：QA approved、verification
passed，但 outcome=**unknown** —— fidelity 维度未测（QA 未提交
clause_findings 矩阵），平台列出全部缺失目标条款并阻断 accepted。
"未知即通过"被堵死；维度徽章（功能·通过 / 保真·未测）已在控制台渲染。

落地机制：
- 验收维度本体论（function/fidelity），无通过测量 = unknown，阻断 accepted
- 目标条款机械化提取 + QA 指令内嵌条款清单 + report_review 结构化
  clause_findings（fail 条款强制 approved=false）
- 门禁消息与拦截集单源化（引导与行为不可能再漂移）
- 压缩-准入计价对账协议锁（系统性测试模块）
- workspace 级跨运行教训回流（QA 拒绝沉淀，下轮 Director 注入）

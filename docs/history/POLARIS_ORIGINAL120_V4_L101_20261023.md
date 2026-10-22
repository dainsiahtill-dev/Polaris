# 原目录120项：v4 L1-01 实测与亲检

本批实际尝试1项，成功交付0项，119项未启动。L1-01 构建与核心测试通过，但 QA 中断，ROOT 亲检也发现实际功能失败。该结果不覆盖其他历史批次。

## 冻结与真实运行

- 候选：`fd6e56b6846013726d0eabed56d5aa56fc1efea0`；9464份源码／驱动文件运行前后 SHA 对账未变。
- 原目录：`projects_v2.json`，120项，SHA256 `d3222aaa1b6d490b92c3fc3db0e7fa84def340f39b5416e1889b805befa14999`；原需求与检查未降低。
- 运行：`af2409991b3e432b9b86fec990a2e7e5`；全新独立实例 `engineering-original120-v4-l1-01`，正式 PM→CE→Director→QA，5400秒项目预算／6000秒外层守护。
- 工作区：`/home/dains/Documents/polaris-bench-projects/original120-20261023-v4/L1-01`；唯一 runtime 为该目录 `.polaris/runtime`。
- 用时1750.652秒。当前 revision89，实际 `npm run check`、`npm run build`、`npm test` 成功，测试22项通过。曾有测试输出目录失败，Director 在原权限内自行修复并复验，PM／CE未重启。
- 当前原生事实：`interrupted / verification passed / QA unknown / ProjectOutcome unknown`。独立 QA 已读取源码、实际检查并发现种子／虫数问题，尚未提交 `report_review`。

## 第一处平台失败

实际 QA 请求图像由0增至17、71张；下一轮准备73张时触发 `native_tool_history_budget_exceeded`，未建立该轮物理发送证据。全部当前候选截图被反复保留，base64序列化的字符估算精确解释了输出上限降至69768；真实视觉token仍为 UNKNOWN。不能把73张辅助准备当成实际请求，或把快照数量当成HTTP次数。

原“先验证、再捕获最终图像”的修复在本例中推进到了 QA，本次未复现此前的重复构建／旧图失效循环。新的失败位于历史图片投影与预算交接，不能据此宣称整个循环已稳定。

[有限修复计划](../superpowers/plans/2026-10-23-native-visual-working-set.md)保留原始图像与 native 配对，复用 KernelOne 预算选择已送达的可选历史图像，并通过现有工具精确回看。当前尚无该修复的正式新项目交付证据。

## ROOT 真实质量亲检

ROOT 另行运行只读静态入口与真实浏览器控制，采集56张未修改截图，亲看12张关键画面。源码／产物60份文件前后 SHA 相同；未编辑业务代码、安装依赖、代跑项目验收命令或写入原生收据。

| 问题 | 最小实际复现 | 首次偏差 |
| --- | --- | --- |
| 合法种子被拒绝 | 页面输入8并离开输入框；提示必须整数，恢复7 | DOM字符串直接进入整数校验 |
| 虫数输入与实际群体不符 | 输入1或50，再单步；实际仍18 | 同种子更新只改月相，重置仍使用旧群体数；两输入属于同一根因 |
| 暂停调月相提前生效 | ticks0时从新月调满月，单步前天空与光晕已改变 | 待生效参数直接写入渲染所读的当前状态 |

桌面有真实移动、暂停、单步、花朵选择和月相亮度变化；不是空产物。但上述功能失败使当前质量不能通过。额外移动视口存在画布缩成细条的问题，原题未要求移动支持，记录为使用限制，不新增强制门禁。

同花悲伤／兴奋十秒闪光次数、完整逐帧重放仍只有部分外部观察和独立的原生核心测试证据；不以截图哈希或说明文字冒充完整验证。非法输入回退后，“无法开始”提示仍在而播放能运行，反馈语义也需澄清，不重复计为已证实的独立根因。

## 资源与证据

原生实例由既有 Supervisor 停止，backend／frontend进程与端口已关闭；外部检查的浏览器、Playwright driver、静态服务也关闭。主实例与共享NATS原进程身份保持。项目共享 Registry／日志的历史外部存储债务仍存在，不能声称所有KFS迁移完成。

- 原生报告：主仓 `.polaris/bench/original120-20261023-v4/reports/001-L1-01/report.json`。
- 亲检结论：目标 `.polaris/audits/root-personal-quality.json`；详细观察位于 `root-quality-final-002`、`root-quality-final-003`。
- 上下文首败：目标 `.polaris/audits/root-qa-context-growth/report.json` 和 `builder-check.json`。
- 原始事件／请求：目标 `.polaris/audits/root-runtime-archive/`，302份原始快照，记录末尾seq1782，缺失0；该归档为外部审计，不是新事实源。
- 两次外部检查器失败分别保留：固定数据库容量假设、完整Chromium启动失败；后者根因 UNKNOWN。修检查器后使用本轮原生实际使用的 headless shell，原报告不覆盖。

实际HTTP发送总数、模型账单及人工介入分钟仍 UNKNOWN。下一步为关闭有限平台边界、冻结新候选，再继续原120串行实测和逐项亲检；[原特性实验队列](../experiments/POLARIS_FEATURE_EVALUATION_20261022.md)继续保留，长期记忆不做。

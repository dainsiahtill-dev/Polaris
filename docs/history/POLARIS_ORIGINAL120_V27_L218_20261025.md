# 原目录 v27：L2-18 真实运行与个人质量结案

日期：2026-10-25。结论：**原生 completed/passed、QA approved；完整交付未通过**。不增加严格成功数，不改写原生 QA、旧失败或 UNKNOWN。

## 身份与版本

- 项目：原目录第18项 L2-18「流星愿望队列」。原始需求、检查描述未降低；workflow=collaborative。
- 源码冻结：`c85937f7b618837d51628f516e12dbd167d3dd59`；最终候选205。
- run：`b193d5a6e8774ee68f9fcbdd3b69d5cf`；instance：`engineering-original120-v27-l2-18`。
- workspace：`/home/dains/Documents/polaris-bench-projects/original120-20261025-v27/L2-18`。
- PM、CE各运行一次；QA依次拒绝78、102、128、151、190，再批准205。每次反馈回到同一 Director，没有重启 PM/CE，没有外部代修。

## 真正成立的证据

原生运行约72.14分钟。最终验证事件6544、QA事件6814、结束事件6816。7条真实命令 exit0，包含67项单元测试；但 build 明确在只读失败后降级为语法步骤，**不能把 exit0 当作完整干净构建已通过**。现存 dist/src 与当前源码一致；完整 bundle 转换与清空目录重建的等价性未证明。52111是JS字符串长度、60589是UTF-8字节数，这个差异本身不证明产物过期。

最终QA确实执行了UI动作：6次浏览器调用，5次成功，其中4次分别有20/21/18/9个动作，1次取消操作因新上下文未设置而失败，随后完整设置重做成功。5张当前候选、当前QA自己产生的图像均进入最终物理 provider request。没有本样本丢图证据；这不证明模型图像理解或验收覆盖充分。

Root在文档声明的原始file入口、现成bundle上发出230个观察器请求：148fill、66click、2checkpoint、2reload、10全新浏览器上下文、1次明确的合成坏存档、1close。其中一次对已退回pending的A查找完成按钮，被观察器在点击前拒绝；不是新的产品缺陷。Root亲自查看初始、排班、刷新丢失、完成历史和容量4轨迹的图像。

已亲验成立：左右开边界、不可跨窗口、容量1/2/3/4、重复安排、最早时间与窗口编号排序、完成释放容量且保留历史、三种失败原因、非法输入保持旧队列。取消回填与预登记oracle的解释存在差异，未追认成通过。

## 三项未闭合

1. **实际公开入口丢数据。** 正常界面创建A30/B30/C1和[0,60)容量1窗口，排班正确；实际localStorage始终为空，刷新后愿望和窗口全部丢失，无错误提示。只读分析表明prime吞掉GET失败，启动层保留server adapter；写入仅改内存、吞掉异步PUT失败，localStorage回退未启用。HTTP服务模式未由Root裁决，不能把file入口故障扩展成所有模式失败。
2. **资源停止结算 UNKNOWN。** 原观察脚本退出143，来源未知；原生运行继续至完成。Root只沿原身份、原期限执行一次正式停止。原PID消失、端口释放、health显示stopped，但stop返回UNKNOWN，登记仍保留旧PID、last_stopped_at空且无持久stop_progress。未重放；物理关闭不冒充结算成功。
3. **历史最终请求引用失效。** 882个事件引用经正式候选链核对，323可读且身份匹配，559仍not_found。当前store约523431114B，默认上限524288000B、TTL7天、无pins；源码为无pin保存后执行淘汰，事件引用没有保活。字节配额淘汰解释有强证据，单文件删除原因没有逐项收据，不能声称已直接证明。旧缺口未从私人副本回填。

第三项不改变当前候选字节完整性：9563项Source冻结核对、17项既有资格文件、配置和输入均匹配；个人质量后742项完整输入、730项排除输出后的输入仍与Verifier/QA完全一致。它说明“当前验收绑定正确”和“全部历史请求可复现”是不同要求。

## 数量与限制

累计22次原目录尝试，18个不同项目均已有原生终态，102项尚未启动。历史有限严格成功仍为2；不同版本和干预程度不能合并成当前版本可靠性成功率。

本轮439个去重逻辑调用ID、882条请求观察，不能冒充物理POST数。584条工具观察中38条失败；QA自身探针错误和安全拒绝均保留。实际物理发送次数、费用为UNKNOWN。没有与同模型、同预算简单Agent的对照数据，不宣称优于Codex/Claude或传统上下文。

## 证据位置与下一步

准备根：`/home/dains/Documents/polaris/.polaris/bench/original120-20261025-next-l218-preparation`。

目标根内：`.polaris/audits/ROOT-personal-quality/final-assessment.json`、`ROOT-first-quality-failure-c205/first-failure.json`、`ROOT-file-entry-failure-review/report.json`、`terminal-integrity/report.json`、`final-QA-audit/report.json`及`build-semantics-review.json`。

源码冻结已在保留失败后结束，目标候选永久只读。先关闭既有Supervisor的停止结算边界，并用隔离合成实验验证ContextStore的引用/淘汰机制；浏览器刷新能力与QA采用方式另做有限反例。组件修复需再冻结新版本、重新真实运行；本结案不宣布阶段或平台完成。

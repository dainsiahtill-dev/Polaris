# Polaris 文档中心（人类读者）

此目录面向 **人类读者**，聚焦产品定位、使用方式与高层架构理解。

> 架构总原则：**数据即真相，视图即表现**。系统以单一数据源承载真实状态，界面与报表仅做投影展示。

> 可选类比理解：数据即真相 ↔ 法性/实相；视图即表现 ↔ 相/现象；适配器 ↔ 方便法门；单一真相源 ↔ 不二法门。

> 进一步可理解为“因果闭环”：用户需求/PM 任务/Director 计划/Policy 是因，代码库状态/工具可用性/模型能力/成本模型是缘，事件与产物是果，工具调用与文件读写记录构成业；全链路可由 `run_id + events + artifacts` 回放并反向追溯。

---

## 📚 推荐阅读顺序

1. 项目总览：[`../../README.md`](../../README.md)
2. 产品规格：[`../product/product_spec.md`](../product/product_spec.md)
3. 需求文档：[`../product/requirements.md`](../product/requirements.md)
4. 测试与自动化：[`../testing/PLAYWRIGHT_ELECTRON_AUTOMATION.md`](../testing/PLAYWRIGHT_ELECTRON_AUTOMATION.md)
5. LLM 测试边界规范：[`./llm_test_boundary_spec.md`](./llm_test_boundary_spec.md)
6. Agent 与工程细节：[`../agent/README.md`](../agent/README.md)

---

## 👀 如果你是…

- **产品/业务**：先读产品规格与需求文档
- **工程负责人**：再读 Agent 文档（架构/不变量/参考手册）
- **首次试用**：直接看项目总览与快速开始

---

## 📝 文档更新日志

| 日期       | 更新内容 |
| ---------- | -------- |
| 2026-02-12 | 新增 LLM 测试分层与边界规范文档，明确连通性测试与深度测试边界 |
| 2026-02-09 | 新增 Electron Playwright 自动化手册入口 |
| 2026-02-02 | 重构为人类/Agent 双入口文档结构 |
| 2026-02-04 | 同步性能策略说明；更新面试后模型绑定策略；新增通知通道展望 |

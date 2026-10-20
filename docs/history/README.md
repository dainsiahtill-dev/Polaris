# 历史经验报告与阅读索引

整理日期：2026-10-20。保留历史原件，用完整报告、分章与来源索引改善阅读。

## 阅读入口

- [Launcher 历史经验报告](http://127.0.0.1:59977/launcher?view=foundation&topic=history-experience-report)
- [完整报告全文（docs 交付版）](历史经验报告.md)
- [完整重构方案](../blueprints/POLARIS_ROLE_AGENT_REFACTOR_20261020.md)
- [逐批实施计划](../superpowers/plans/2026-10-20-polaris-role-agent-refactor.md)
- [页面使用的报告原稿](../../src/frontend/src/app/launcher/foundation/papers/04-history-experience-report.zh.md)
- [带行号来源摘录](../../src/frontend/public/reports/polaris-history-sources.html)
- [完整文档目录](../../src/frontend/public/reports/polaris-history-document-catalogue.md)
- [机器可读目录](../../src/frontend/public/reports/polaris-history-document-catalogue.json)
- [来源快照、范围与文件指纹](../../src/frontend/public/reports/polaris-history-evidence.json)
- [本次文档交付验证](delivery-verification.json)

报告共十章、68 个案例，引用 67 份原件；生成前目录覆盖 1,285 份材料，发现 23 组字节完全相同的副本。元数据入目录、选段审阅、历史运行和当前验收分别说明。

## 内容维护

Foundation 直接导入完整报告原稿，再按一级章标题分章；不复制维护十份正文。下载版 `src/frontend/public/reports/polaris-history-experience.md` 与页面原稿字节一致；docs 交付版保留全文，仅把 `/reports/` 来源链接改为仓库相对路径，便于本地阅读。

修改正文后须同步下载版；变更章标题结构时，核对 `historyExperience.ts` 中的十章映射。来源 HTML 与 evidence JSON 是 2026-10-20 的读取快照；追加结论时应更新相应来源摘录、行号与哈希，不用当前文件内容冒充旧快照。

历史文件名日期不等于最后更新日；本目录不覆盖原有规范与模块 owner 的权威关系。本次未重跑历史产品门禁，也未启动新的 Factory/Bench。

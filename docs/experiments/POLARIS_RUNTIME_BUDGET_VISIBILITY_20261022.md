# 运行预算可见性：r06 原生交付与独立质量

日期：2026-10-22。T24 本次冻结实验已完成；本文件同步外部证据，不是平台运行事实源，也不是新的验收门禁。本次文档同步没有重新运行工程、模型或浏览器。

## 本次结果

冻结源码 `b41245103c47dcfae9dc7f4063861cc1ee9b1cac`，原生 run `7dd30e15505e42c098daf90602625d94`，隔离项目 `pelican-web-vision-20261022-r06/01-PM_CE_D-r1`。原5400秒项目／6000秒外层预算不变，耗时5173.18秒，约86.2分钟。

| 层次 | 本次证据与结论 |
|---|---|
| 原生工程 | PM→CE→Director→QA；completed／verification passed／QA approved／accepted |
| 当前性 | 正式检查真实exit0，无tail管道；当前709项输入绑定与3个产物哈希和QA批准一致 |
| 原生图像 | QA自主3次捕图、10张独立图像，实际后续provider request包含对应字节；不证明其每项视觉断言都正确 |
| ROOT独立质量 | task_success=yes，质量中等、保留缺陷；鹈鹕3、车2、骑乘3、运动3、交互3、呈现3，原0–4描述量表不按总分自动接受 |
| 外部介入 | 无目标外部代修、依赖安装或代跑原生验收；原生运行内冻结源码和关键资产不变 |
| 资源 | 原owner停止实例，已知原backend/frontend PID消失、49978／5174释放；已知主实例／NATS出生身份不变；ROOT baseline及两次补充观察的已知进程／端口另行闭合 |

原生accepted、当前检查、独立QA、ROOT质量和资源闭合是分别支持的结论，不互相替代。历史r03平台accepted但ROOT质量FAIL、r04失败、r05截止失败及更早失败均保留，见[能力取舍与质量实验](POLARIS_FEATURE_EVALUATION_20261022.md)。本次成功不回写那些候选或检查结果。

## 实际质量及保留缺陷

ROOT记录200张最终原始截图、亲自查看其中16张，并核桌面／手机 × 普通／reduced-motion四配置、真实Tab／Space／Enter／Home／ArrowRight及DOM时间序列。Space暂停期间采样的轮／曲柄角度稳定，Enter恢复推进；0.5／1／2档周期约14.1／7.1／3.5秒，2档相对0.5档实际速率约3.95–4.00倍，有采集时差。初始／选速窗口覆盖完整采样曲柄周期；所看画面未见明显脱踏。DOM锚点和稀疏画面不能证明全部未观察瞬间、遮挡或所有视觉接触。离线原字节页面未观察到相关浏览器错误或失败应用资源请求。

两项问题留在本次不可改写候选中：

- 后齿轮抬到轮轴上方18个SVG单位，角变化约为轮子的1/36，传动细节不准确；整体骑车动作仍可辨认，车维度评分2。
- reduce初始实际range.value为0.25，读数／aria为0.35，实际运动约为1倍档的0.35。之后真实键盘选择0.5／1／2有效。QA未测slider.value便扩述滑块正确；独立捕图、图像被消费和批准不等于所有表述都有测量支持。

不新增写实、配色、解剖、固定圆形元素数量或截图数量硬要求；数值和原图是本次描述证据，不成为平台通用门禁。

## 失败、检查器与费用口径

| 观察量 | 本次记录及限制 |
|---|---|
| 工具 | 401条durable观察，16失败，0pending；按真实根因保留，不把重复拒绝算成独立平台缺陷 |
| prepared角色分布 | PM3、CE4、Director377、QA13，合计397 |
| 请求快照相位 | prepared397／terminal397；不是397或794次物理HTTP发送 |
| harness用量摘要 | 仅18条观察，event coverage未知；子集unknown_usage_attempts=0或cached_tokens=0不能外推全部请求或零缓存／零成本 |
| 真实HTTP次数 | UNKNOWN |
| 全量usage、实际账单、人工分钟 | UNKNOWN，不能记零，也不能算可信每次成功成本 |

QA一次尝试在workspace生成派生缩放页面，被真实只读sandbox拒绝，原失败反馈进入下一实际请求；没有成功写入该派生目录。随后QA合法改用browser_observe并消费自有截图；实际缩放倍率未测量。其余失败与视觉当前性拒绝见原始分类清单；原生终态成功不抹除16条失败。

外部补充检查器第一次R1因HTTP路由回调参数错误退出2、not_evaluable，清理回调错误令wrapper resources_closed=false；原始报告和旧脚本保留。ROOT另核已登记PID消失、端口关闭；仅修外部检查器后第二次R2实际采集成功并核资源闭合。这两次是ROOT外部操作器记录，不改平台16条失败、不计入原生验收、不改变冻结候选或源码版本。静态检查未发现该回调错误的历史也保留。

## T24机制、资格与可解释范围

[原蓝图](../blueprints/POLARIS_RUNTIME_BUDGET_VISIBILITY_20261022.md)新增17行生产代码，只在现有owner的Director请求尾部呈现观测：host原deadline、准备时刻及近似剩余秒数。保留原system、稳定前缀、权限、工具、图像、cap、timeout、retry、检查与完成判定；不把临时时钟累积到会话历史，不增加停止规则、controller、表或配置。T23未证收益的交接提示词未合入。

资格记录分开保存：r1 Python3.13整合584通过／5项缺PIL失败，实际原生3.12相关36项复验通过；r2实际3.12完整589项为588通过／1项测试将JSON尾部合法预算文本一起解析的失败。保全实际wire后只修消费测试，受影响两模块9项通过，产品17行未再改；未第三次整跑，不能把重叠结果相加或写成单次589通过。r2 K1 ALL十二阶段完成（368 passed／1 skipped，含release tests）；严格目录159条既存报告字节保持、增量零新增。组件资格不是模型收益证据。

本次实际预算说明已送达并被注意，r06在原deadline内完成全部链路，只支持该冻结候选和本例的可行性。r05/r06同时存在T22读取优化、H2机制和随机模型输出差异，缺少相同条件重复对照，不能单独归因于H2；也不能证明缓存／经济收益、最佳角色组合、ContextOS优于历史策略、任意恢复、长期稳定性或全120项通过。长期记忆及其它研究不因本例自动接回。

seq3893／3925两次拒绝中，授权数学命令后完整709项输入含元数据、scope/profile/required_checks/verification_seq均一致，仅workspace_revision推进，随后视觉投影和guard按revision判失效。该反例由ROOT独立复算，形成T25下一项真实生产／消费路径实验；同输入、实际输入改变、未知效果、网络／权限、恢复等边界尚待研究。本次不把T25修复、去掉视觉门禁或新增流程标为完成。

## 相对证据链接

以下均是本机外部审计资料；平台工程事实仍归原工程SQLite。仓库文档不复制原始provider内容或将外部报告作为新SSoT。

- [ROOT总报告](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-report.md)
- [ROOT最终质量](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-quality-final.json)
- [原生描述性指标](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-native-metrics.json)
- [原生终态及独立QA核证](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/runtime-audits/terminal-qa/report.json)
- [原生资源独立核对](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-terminal-resource-check.json)
- [ROOT数值独立复算](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-quality-preparation/supplement-final-002/root-numeric-check.json)
- [原始baseline](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/renderer/root-terminal-baseline/report.json)
- [补充检查器R1失败](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-quality-preparation/supplement-final-001/report.json)
- [R1物理清理独立核对](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-quality-preparation/supplement-final-001/root-closure-reconciliation.json)
- [检查器修正及旧版保留](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-quality-preparation/callback-repair.json)
- [补充检查器R2实际观察](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-quality-preparation/supplement-final-002/report.json)
- [ROOT亲看帧与资源记录](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/root-quality-preparation/supplement-final-002/root-observations-and-closure.json)
- [revision反例及未决边界](../../../../.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task24-runtime-budget-visibility/native-web-r06/runtime-audits/visual-precondition-cause/report.json)

[实施计划](../superpowers/plans/2026-10-22-runtime-budget-visibility.md)第4—6项按本次事实核勾；本次只更新文档及Launcher论文源文，没有build／deploy或验证主Launcher已加载新文本。

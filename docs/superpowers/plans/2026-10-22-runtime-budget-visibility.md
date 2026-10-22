# 运行预算可见性候选实施

目标：在现有工程 owner 中呈现真实预算，完成一次新冻结原生网页实验；完整范围见[蓝图](../../blueprints/POLARIS_RUNTIME_BUDGET_VISIBILITY_20261022.md)。不承诺 H2 收益。直接在 main 工作，不创建分支或 worktree，目标项目只读。

1. [x] 核 T23 负结果和 H2 四份真实请求／host deadline；跳过无鉴别力的单步筛查。
2. [x] 实现者仅修改 `internal/engineering_loop.py` 与 `tests/test_engineering_budget_visibility.py`。r1 system放置12行保留；r2改请求尾部17行，以3失败／4通过的机制RED开始，实际3.12的8模块122项通过（含新7项），静态检查通过。中间夹具／类型错误原样保留，分别见 `implementation/` 和 `implementation-r2/`。
3. [x] ROOT 已核真实循环／新service恢复／两个 native codec，迁移三处声明；r2独立代码审查无P0/P1/P2。r1整合584通过／5缺PIL失败，相关原生环境36复验通过；r2实际3.12完整589项为588通过／1测试JSON尾文本假设失败。ROOT归档原始wire并只修消费测试（另属ROOT文件范围），原检查来源／退出码断言保留，受影响两模块9项复验及静态通过；未第三次整跑全套。r2 K1 ALL十二阶段（含release tests）通过；目录原159条字节一致、增量零新增。未新增effect／公共接口或广泛生成packs。
4. [x] ROOT 核最终版本并提交 main，以 `b41245103c47dcfae9dc7f4063861cc1ee9b1cac` 冻结原任务／配置／源码与执行资产。ROOT为唯一原生启动者，相关资产在原运行内未改；未重启主实例或续写r05。
5. [x] 正式入口启动全新隔离r06 `7dd30e15505e42c098daf90602625d94`，保持5400／6000秒预算，5173.18秒完成原生completed／passed／QA approved／accepted。工具与产物由Polaris自身执行；外部无代写目标、装依赖或代跑原生验收。
6. [x] terminal后ROOT亲看16张最终原图，结合200张原始截图、实际键盘与DOM序列，判task_success=yes、质量中等；车2、其余五维3，两项缺陷保留。原生与ROOT已知资源闭合；保留401工具观察中的16失败、0pending、检查器R1失败／R2成功与HTTP／全量usage／账单／人工分钟UNKNOWN。完整结果见[实验报告](../../experiments/POLARIS_RUNTIME_BUDGET_VISIBILITY_20261022.md)，不等于H2因果胜出或全平台完成。

实现者／ROOT 互斥：ROOT拥有声明、文档、验证卡、集成和运行操作器；子 Agent 不能写目标、改配置、启动模型／浏览器／服务或扩文件范围。普通可逆工程决策在已授权范围内自主进行，不新增确认环节。

第4—6项现按实际提交、freeze、原生终态、ROOT独立质量及资源记录核勾。r1／r2资格失败与修测试后的9项复验不相加成单次589通过。T25同输入revision-only视觉失效只形成后续机制研究，尚未修复；本计划勾选不预先完成T25、角色／ContextOS对照或全120项。

# 原生工具与当前工作状态闭环实施计划

基线、假设、证据、反证和接受标准见[蓝图](../../blueprints/POLARIS_NATIVE_TOOL_CONTEXT_CLOSURE_20261023.md)。用户已授权主线可逆实现，无新分支。v5原生两运行closed且source/input guard通过，目标代码不可代修。

1. ROOT保存v5终态、完成L1-02个人质量；写知识记录并保留CANCELLED/UNKNOWN。
2. KernelOne实现者互斥拥有tool_spec_registry.py、token_estimator.py、invoke/stream预算消费者及其测试。先原反例RED，再通用投影/等价计数/共享预算GREEN；不改roles。
3. 角色工具实现者互斥拥有engineering_browser.py、engineering_roles.py及浏览器/reinspection测试。补原子fill/select与完整流程；producer互斥与新动作完整schema对接上一项通用投影。原host权限和K1 process owner不变。
4. 第三专家只做2pair当前工作状态模型实验准备；ROOT审阅后有限执行。若A已前进或B未改善，保留结果，不扩样。生产working-state改动待结果再决定，不能热改或恢复旧TurnEngine准入。
5. ROOT核diff/越界/原始实验，独立交叉审查，按相关pytest及Ruff/format/mypy验证；跑test_kernelone_release_gates和run_kernelone_release_gate.py --mode all及所需工程架构；不以测试数代替交付。
6. 提交main，冻结新source/config/models/catalog后，全新隔离原L1-01 PM→CE→Director→独立QA。ROOT在资源闭合后亲检实际画面/操作/失败保护。成功后逐项推进原120，失败封存第一偏差再有限处理。

临时材料/图片/缓存仅所属项目.polaris；保护主实例与无关改动。图谱/Cell README/文档由ROOT评估和同步，作者不广泛生成资产、清理或提交。

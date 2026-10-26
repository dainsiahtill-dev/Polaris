# TaskRuntime 效果身份与 Director 依赖解环

状态：待最小反例和golden验证的局部重构设计。服从全仓收敛计划，不是新控制器。

生产实际导入图存在run_ledger/director.runtime/task_runtime三Cell循环。TaskRuntime closure从Director读取的内容是immutable payload、typed hash和自身inventory operation身份验证，不是修复执行。由此把状态记录依赖到了执行器。

决定：把原样typed immutable Map/Sequence与tagged hash编码移到既有KernelOne effect技术owner的窄模块；TaskRuntime自身operation subject/identity DTO及验证归自身public接口；Director保留指向同一类／函数对象的公开别名并消费TaskRuntime。closure只依赖K1和自身public subject，移除task_runtime->director.runtime。RunLedger taxonomy仍保留真实owner，移除registry中无用import。

禁止用看似通用JSON/prewrite/verification hash代替，已知编码域不等价。旧record不重写、schema不换、wire/hash字节不变；新旧alias对象身份、93个既有调用点、异常与UNKNOWN/cold读取均验。

最小红例先证明closure存在Director执行层依赖；固定现行hash样本和immutable行为，修后golden不变，三个Cell的实际SCC消失。所有消费者与持久化/恢复路径同时迁移。不得把工具执行、权限、receipt或新状态事实放进K1 DTO层。

Scope、证据及精确文件见`.polaris/audits/technical-debt-clearance-20261025/graph-effects-audit/small-scc-design.json`。大25Cell SCC另行关闭，不能以本局部解环声称全仓完成。

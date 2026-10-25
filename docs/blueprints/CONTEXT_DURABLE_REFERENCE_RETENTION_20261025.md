# ContextOS 持久引用的保留边界

状态：组件修复、独立审阅与发布门禁通过；真实模型／原生交付待验证。原始基线main 0afe97f18；不涉及长期记忆。

## 问题、事实与反证

L2-18事件中的882个24hex引用有559个在完整同workspace候选链仍not_found。接近500MiB的store无pin，默认TTL7天。不能据此伪造逐文件删除原因。

隔离A/B已证明机制：同样74023字节、64KiB夹具配额，只写SQLite引用的A对象被GC删除且事件未变；有效旧pin控制的B对象保留，普通垃圾仍回收。原始数据见`.polaris/audits/l218-context-retention-gap-20261025/experiment/`。这些是合成组件事实，不是原生交付。

第一偏差在引用发布边界：executor无pin保存后立即GC，随后capture发布available，再由EngineeringRunService持久化事件。observer之后补pin太晚；引用事实可以比对象活得久。

## 所有权与数据流

EngineeringRunService host身份 -> 通用技术owner side channel -> NativeRequestCapturePort/AIExecutor -> 同一KernelOne CAS/pin仓储锁内保存原始字节及有效pin -> 原有GC -> available观察 -> 同一工程SQLite事件。

roles.runtime继续独占工程身份与事件；KernelOne只实现技术引用保留。ContextOS查询只读，24hex API不变。没有新Cell、账本、总控、模型字段或Factory新运行准入。

## 决定

1. 现有final_request_evidence增加通用host owner及V2 pin，原V1合同、文件名与Factory查询语义保持兼容。新工程不制造旧Factory字段。
2. 保存原始字节与pin在同一现有跨进程/物理锁内完成并复读验证，之后才能GC和发布available。原bytes不重新序列化；owner不进入provider body/messages/tools。
3. host resolver必须绑定当前workspace/run/role/turn。不得从模型输出、工具参数、AIRequest.context或provider body取得保留授权。构造器side channel默认None保持旧技术调用行为。
4. 事件owner核验对应pin再提交available。失败明确unavailable/error，不能静默发布可读引用，也不能因审计失败重复发送物理请求。
5. 冷恢复在同runtime原owner静止后、允许新GC之前，按现有SQLite引用保护仍在且身份匹配的旧对象；查询不偷写pin。已缺对象保持缺失，禁止私人副本回填、旧事件重写或操作重放。
6. pin后事件前崩溃留下受保护孤儿并报告；不自动unpin。pin前失败不发布引用。安全删除和压缩去重后续验证。
7. 原GC继续回收无引用缓存。受保护字节超过缓存配额时保留事实，明确protected/unreclaimable bytes和budget_satisfied=false；不提高生产配额、TTL或伪称预算满足。磁盘写失败明确上报。

## 修改范围

KernelOne既有final_request_evidence合同/导出、context_store_retention、executor、native_request_capture及其测试；roles.runtime既有engineering_service、engineering_session、上下文审计/新保留测试；只在必要处更新Runtime Cell/roles_execution_pipeline说明。context HTTP读取面无重命名或写副作用。

## 证据门槛和停止条件

先用真实生产capture+工程SQLite、合成provider输入复现GC后available引用不可读的红例；修后通过。再验证sameworkspace冷进程与两个ContextOS读取面、V1/V2共存、普通垃圾回收、全部受保护超预算、跨进程writer/pinner/sweeper、pin/事件前后故障与权限/symlink/corrupt pin反例。

这些都属于本条持久引用边界，不能用更多Bench或配额上调替代。收益是对象与合法引用同寿命；代价是持久证据占用磁盘及保守孤儿保留，分别计量。不以组件结果宣称旧559缺口恢复、平台完成或ContextOS比较优势。相关门禁和独立审查通过后结束本主题，冻结新版本再做有限真实运行核对所有新引用。

## 组件证据与当前限制

完整生产者/消费者/pin仓储/冷恢复/读取投影迁移已实现，原字节与provider body保持不变。独立审阅发现并关闭AUTH-CALL-01：使用持久request_prepared中的host call_id或既有canonical host标识，拒绝可变context伪造值；事件提交与冷恢复也重验。旧V1和原三段owner pin保留，合法当前引用获得完整关联，缺失对象未重建。

核心343、证据57、扩展61及修后相关50测试通过，集合互相覆盖，不相加为独立总数。Root复核17个源码/测试指纹；双域独立复审无剩余发现；最终KernelOne12阶段368通过/1skip。目录治理保留159处/115个旧指纹、无新增、manifest mismatch0。原始红例、准备失败、review反例和修后证据均保留在主仓`.polaris/audits/context-reference-retention-implementation-20261025/`。

这只是组件资格；尚未用新冻结真实模型链路签发项目交付。pin-only孤儿保留及受保护证据磁盘占用是明确限制，自动删除/压缩/分层未实现。历史L2-18仍559引用缺失、Root质量失败、停止UNKNOWN，不由此修复追改。

# Polaris 项目存储与 KFS 收敛

状态：设计与分批实施；尚未宣称全量统一。2026-10-22 用户明确要求所有运行时、缓存和 LLM 所看截图归所属项目 `.polaris`，所有平台文件 I/O 统一 KFS。原目录120项串行评测与每项ROOT亲检继续保留；L1-01旧CANCELLED/UNKNOWN不改写。

## 修改前基线与问题

- 当前默认目标 runtime 已是 `<workspace>/.polaris/runtime`，SQLite与ContextOS内同一JPEG字节已实证；不再另造截图事实源。
- storage.layout仍允许外部 runtime/cache/ramdisk 写根，冷解析会直接创建探针。
- 业务会话/树/manifest/ContextOS读与清理/实例日志仍有原生文件调用；sandbox与主实例有全局或私有tmpfs临时根。
- KFS原子写暂存仍位于目标parent，写源码时可能在`.polaris`之外；迁移必须保留同文件系统rename、目录身份与清理。
- 静态清单2868生产Python、6054候选及Node三文件27处，只是候选，不是缺陷或执行次数。

原证据：`.polaris/audits/project-storage-20261022/audit.json`、`.polaris/audits/kfs-inventory-20261022/report.md`。现有KernelDatabase及adapter须先复用评估；不能把SQLite转成文本或新建账本。

## 架构和唯一owner

```text
项目或平台所属workspace
  -> existing storage.layout（唯一根解析）
  -> KernelOne FS 能力 / native adapter 的受控句柄与生命周期
  -> <owner-workspace>/.polaris/{runtime,cache,audits}
  -> 原有SQLite / ContextOS / Instance / NATS状态owner读写恢复
```

业务源码仍在正常项目位置；运行数据不进入源码。共享NATS、InstanceRegistry和平台日志归主Polaris项目owner，目标项目数据归各自workspace。现有共享配置/凭证不是项目运行缓存，不因本任务自动复制。

KFS负责解析、受控目录/文件/元数据、临时空间、日志句柄、身份和清理；SQLite/Chromium/NATS等native引擎保留实体协议。仅经resolver取字符串不等于I/O经过KFS。复用现有guarded目录/snapshot/text_ops/lease与KernelDatabase，不新增controller、ledger或双写根。

## 逐批范围

1. **B1 唯一项目runtime写根与KFS准备**：现有KernelOne storage.layout统一新writer；原`<workspace>/runtime`继续规范化为canonical；外部显式runtime、启用的ramdisk、旧runtime-cache写重定向拒绝且不产生外部效果。canonical只读解析不探针/不初始化，旧外部根仅历史发现。目录和写探针经已有KFS能力。
2. **B2 KFS基础能力**：补齐必要metadata/list/readlink、受控临时/staging和native DB句柄桥；先核现有KernelDatabase。保留nofollow、dirfd/inode、单链接、原子rename/fsync、锁与WAL恢复。staging不能跨文件系统降级为非原子写。
3. **B3 工程与截图上下文链**：Session、repo_tree、manifest、ContextOS生成/读取/retention与冷恢复迁移到同一能力；截图内存字节→durable图像→物理provider request保真；目录、元数据和清理也纳入。
4. **B4 执行临时空间与cache**：现有KernelOne进程owner分配所属项目`.polaris`下每调用私有空间，维持sandbox/no-network与控制目录保护，不给模型整个`.polaris`写权。
5. **B5 平台owner与其余消费者**：主项目cache/tmp/log/registry/NATS采用同一归属；Electron和其余主动/可选/历史入口逐点清单关闭。活跃owner不热切根、不复制活跃JetStream；离线切换必须先有可审查方案和恢复验证。
6. **B6 集成验收**：重跑清单并核每条真实owner，剩余UNKNOWN不算完成。完成原生验证/视觉排序缺陷的既有设计，冻结新候选，继续原目录真实交付与ROOT质量检查。

这些批次不是产品运行协议或新Bench门禁；每批闭合相邻生产者、持久化、读取、恢复和错误路径后进入下一批。

## B1 假设、实验和停止条件

假设：在已有唯一解析器收紧新写根，并经KFS准备/探针，可阻止外部默认/显式写路由，同时保留读取历史与正常推进。

最小反例：真实临时workspace内调用resolve_storage_roots，显式外部root当前被接受并可能创建探针。修后外部root应明确拒绝、无外部目录；默认/显式canonical与legacy bare-root仍正确。读取API不创建任何文件，历史候选只读发现。

反证：缓存或已注册业务resolver仍返回外部writer；只读调用发生写入；KFS准备递归调用layout导致无法推进；原workspace身份、历史查找或配置owner被改错。任何一项出现都不接受B1。

接受：对应真实文件系统RED→GREEN、相邻Polaris resolver/Root identity/coldread测试及类型/格式通过；后续批次与主实例迁移仍未完成时如实保留。不能仅凭代码/清单数量宣称统一。

## 资源和证据

实验/pytest缓存/pycache/临时文件均放主项目`.polaris`，实验workspace自身运行数据再放自己的`.polaris`。旧外部档案只读保留。主18128、NATS18035、前端18660保护，不在本批停止或热迁移。

## B1 当前证据（后续批次未完成）

已有修改限定于现有`storage.layout`及相邻测试：canonical新写根、缓存/业务resolver拒绝外部writer、KFS目录与探针、无副作用冷读取及历史根发现。实际文件系统策略测试先RED 15失败/8通过，增补边界后GREEN 25通过；相邻188通过/1跳过，消费者98通过，冷HTTP 4通过。独立审查12文件哈希匹配，5项物理检查通过，无阻断发现。

ROOT原生集成首轮1053通过/18失败/1跳过，原始结果保留。5项为测试依赖旧外部根或硬编码默认HOME；夹具已迁移到当前语义。13项冷子进程原始失败根因仍UNKNOWN，不能把后续通过解释成已证明字节码缓存是根因。4项代表恢复检查已通过，退出前只读SQLite存档可复核真实提交、UNKNOWN和不重放。最终完整工程复验1071通过/1跳过/2警告，退出码0，14个冻结文件指纹一致；这证明当前回归通过，不是长期稳定性或真实Bench交付证明。

同一产品源码指纹的KernelOne ALL已通过12阶段，368通过/1跳过。ROOT对全部11个Python修改文件执行Ruff、format和标准作用域mypy通过；strict模式3项未改动的既有错误已用HEAD字节副本复现，不宣称全仓strict通过。测试临时工作区原先写入全局HOME的遗漏已改为本仓`.polaris/test-workspaces`，仍保留sandbox隔离与清理语义。

Catalog按仓CI命令执行仍为exit1/new159、manifest mismatch0：CI所写基线路径不存在，实际issue基线为空，纠正路径也不能令历史债务消失。独立审查逐字节比对80个问题源文件及相关规则/输入，共196文件与BASE相同，159事件/115唯一指纹是B1之前已有问题；本批不改基线、不把该门禁声称通过。相应风险和原始报告继续保留。

证据：`.polaris/audits/project-storage-20261022/b1/`，含原始RED/GREEN、独立审查、`root-integration/results.json`、`root-integration/kernelone-release.json`、`root-integration-r2/results.json`、`integration-triage.json`及`integration-followup/`。B1接受仅限本批根策略与准备机制。B2–B6尚未实施；主实例和NATS仍使用原活跃根，未停止或热迁移；没有新增Bench交付成功。

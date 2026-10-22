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

证据：`.polaris/audits/project-storage-20261022/b1/`，含原始RED/GREEN、独立审查、`root-integration/results.json`、`root-integration/kernelone-release.json`、`root-integration-r2/results.json`、`integration-triage.json`及`integration-followup/`。B1接受仅限本批根策略与准备机制。B1冻结时B2–B6尚未实施；主实例和NATS仍使用原活跃根，未停止或热迁移；没有新增Bench交付成功。

## 下一切片 B2a：只读观察能力

现有KFS已公开`read_guarded_regular_file_snapshot`，直接复用，不再增加同义快照接口。缺口限定为`KernelFileSystem.workspace_lstat`、`workspace_list_directory(max_entries=...)`和`workspace_readlink(expected=...)`。

元数据保留device/inode/mode/nlink/size/mtime_ns/ctime_ns及根身份；链接只读原始文本，不读取目标。单层枚举至多扫描限制值加一项，返回排序后的已观察子集及唯一`complete`标记；该标记只表示本次迭代器到达EOF，不证明返回时成员集一致。不承诺截断子集是全局排序前N项，不把不完整枚举绑定为完整manifest。根目录观察单独处理，不放宽现有写入的路径入口。

实现复用原有descriptor和目录身份witness，初始缺失、观察后漂移、权限错误、能力不支持分别报错；不初始化runtime或缓存。现有快照对特殊文件/硬链接的限制保留，元数据观察本身不打开这些内容。即使是单向成员插入，也可能不改变七项目录字段；逐调用核对不能冒充目录或全树原子快照，后续manifest消费者仍须独立核对前后成员和文件内容。

接受范围仅是基础读能力：六组有限真实文件系统反例、相邻回归、作用域类型/格式与独立审查。运行时消费者、SQLite、原子暂存及资源释放迁移仍是后续独立工作。现有Session保护和native连接生命周期将优先迁入KFS与KernelDatabase，不把未要求的任意同UID攻击防御或自定义VFS变成迁移前置门槛；实际未保证的能力不写成既有事实。

后续ContextOS共享读取计划使用B2元数据中的实际文件大小，给现有guarded snapshot提供有限读取边界，再核对身份、大小和原始字节SHA256；不因迁移新增固定的小快照上限。源码核对未发现现有单快照字节准入限制，500MiB retention整树配额不是单次读取限制。此为待验证的兼容方案，不是已实现机制；也不改模型窗口或输出上限。相关只读决策保留在`.polaris/audits/project-storage-20261022/b3-context-preparation/read-bound-decision.json`。

### B2a 当前实验边界

实现及首轮修复已经产生136通过的定向/相邻检查，两个错误语义问题已独立复审：expected已观察身份失效保留drift，非法原生文件名以可UTF-8落盘且可还原的诊断表示。ROOT首轮完整FS/存储集成1失败/483通过的原始结果继续保留；同一冻结产品的KernelOne ALL通过12阶段、368通过/1跳过。该失败测试假定EOF后新增文件必使目录字段变化，前后stat未采样，因此原单例精确原因继续UNKNOWN。

有限新进程实验中，32次原生单文件插入有25次目录七字段完全相同；32次KFS同seam插入有16次字段相同并返回旧枚举、16次字段变化并正确报drift。没有sleep、utime、重试或stat替身；这是可复现反例，不是总体概率估计，也不证明实际并发或消除了成员遗漏。独立审查据此确认测试前提过强；测试已改为原生mode确定变化、同目录FD观察并在finally恢复权限的场景，原始失败与反例保留。四个产品文件未因此修改。最终ROOT完整FS/存储复验484通过，八个冻结文件指纹一致；三项反馈独立复审闭环，Ruff/格式/mypy五个Python文件通过。接受仅限这三个基础读取接口，项目验收要求不变。

证据：`.polaris/audits/project-storage-20261022/b2a/`中的`review/`、`root-integration/`、`root-integration-r3/`、`integration-triage/probe.json`及`integration-triage/report.json`。基础接口通过后还须迁移真实消费者，不能以这些测试声称运行时、缓存或截图链路已经全量统一。

## B3a 当前交接边界

普通ContextOS按hash查询存在两处初始化旁路：active candidate调用写入型root resolver；registry candidate查询构造`InstanceRegistry`，先`mkdir`再读。两处须连同共享读取一并迁移，保留既有registry严格解码和degraded错误投影。只读路径不得升级成writer，也不通过supervisor执行生命周期操作。

两个hash HTTP入口及public final-request query应读取同一组guarded bytes，验证原始文件字节与24位key的绑定后再投影messages/provider request。已有producer实际使用compact UTF-8 JSON字节计算key，保持其算法、图像内容和存储owner。缺失、损坏、unsafe、身份变化及registry不可用要保留事实区分；已有有效canonical快照不应因无关的registry查询失败而失去可读性。

本批接受范围是普通hash查询、其registry发现和冷恢复，不包含pin/stats/retention、工程SQLite、临时目录或共享主根迁移。普通查询已完成：复用原有registry严格解码器，新增显式只读读取，不创建supervisor、不探测进程、不发布事件；canonical快照有效时不访问registry。两个HTTP入口及public query共用KFS metadata与guarded bytes，实际字节绑定原key，首个存在但损坏的候选不会被历史副本掩盖。UTF-8/JSON解码错误保留原READ_ERROR语义；合法非object JSON仍为FORMAT_ERROR。

作者定向95通过/33项真实生命周期测试未运行，相邻147通过，九个Python文件Ruff/格式/mypy通过。独立审查无阻断，并实际检验观察后删除registry或快照的错误分类及恢复后合法推进。冷进程回读实验没有产品写入，原字节与mtime不变；11项Python字节码缓存写入单独归入授权的项目`.polaris`。ROOT冻结11个文件后集成204通过，KernelOne ALL通过12阶段、368通过/1跳过，结束时指纹一致；此后只更新接受状态文档。真实服务、模型、浏览器与Bench未运行。

证据：`.polaris/audits/project-storage-20261022/b3a/`中的`report.json`、`read-recovery-evidence.json`、`review/report.json`、`root-integration/results.json`和`root-integration/kernelone-release.json`。原外部writer fixture失败及中间失败保留，不能改写成首次通过。剩余路径继续记录，不能把本批说成全量ContextOS或平台已统一。

## B3b pin、统计与清理

静态准备确认已有`ContextSnapshotAuditPinRepository`拥有pin与删除的共同authority；普通快照producer尚未经过它。下一切片在原owner中补只读构造和查询，把已序列化原字节委托原repository持久化，普通写入、pin与清理共用同一authority，producer释放后才尝试retention。保留原key算法、内联图像、锁根与身份规则；不另建图片仓库、控制器或锁authority。共享锁根的物理迁移仍属B5。

统计使用已有KFS metadata、有限目录枚举及guarded bytes，不初始化目录；清理先完整观察再通过现有guarded compare-and-remove执行。目录EOF只证明枚举结束，共同锁只排除协作writer；逐文件身份与字节检查保障实际删除对象，不声称全局原子目录。读取不全、pin不可用或最终观察失败必须保留未知；部分删除后的报告保留实际计数并标记不可用，HTTP在成功数值投影前返回结构化503，不写伪造的完整统计或throttle。

准备证据`.polaris/audits/project-storage-20261022/b3b-preparation/{plan.json,brief.md}`仅为静态方案。初始实现限定原retention/repository、executor原字节写入口、public pin query与HTTP stats/sweep及四个已有测试文件。接受需覆盖冷读零产品写入与恢复成功、原字节/图像、完整性错误、替换对象保护、同authority真实线程交接、TTL/count/bytes与部分效果，以及canonical writer/历史只读边界。

初始八文件候选取得作者565通过（保留一个既有fork警告）和ROOT相邻260通过，但独立审查拒绝了它：实际create已经rename、目录fsync失败后，持续故障下的相同字节重试仍返回成功，目录同步尝试为零。可读取不等于完成原有持久化保证。另有ROOT KernelOne ALL结果367通过/1失败/1跳过，失败来自新增夹具空`pass`使既有预算245超出244；不修改预算。原候选、物理探针和失败均保留于`b3b/`，不声称接受。

R1据此修正“已有KFS能力完全足够”的准备假设：在既有guarded regular-file模块补一个`guarded_confirm_regular_file_durability(root, expected, *, max_bytes)`，复用已持有的nofollow目录/文件身份与原字节检查，实际同步文件及父目录后复核；不创建、替换、重写、enroll或增加authority。已有StreamLease同步需要分别enroll逻辑文件，不能为此改变当前共同锁；已有text helper会吞同步错误，不能代用。consumer仅在写入发布时调用，新错误保留阶段、已做同步、未确认效果及原生OSError语义；只读查询不得调用它。相同故障下重试必须仍失败，解除故障后不重写原文件即可合法推进；旧失败事实不改写。限定三个既有KFS源码/测试文件和原retention源码/测试，原八文件中的其余六个保持指纹不变，另做独立R1复审和新候选回归。

B3b现已通过上述限定范围的接受：R1作者592通过，保留一个既有fork警告；原异常预算检查1通过，阈值不变；11个Python文件Ruff/格式/mypy通过。独立R1复审实际重现持续故障下file/parent同步后返回EIO/UNKNOWN，解除故障后原字节、inode、mtime、ctime不变且成功，原P1与空夹具问题关闭。ROOT新候选集成391通过，KernelOne ALL12阶段通过、368通过/1跳过；验证期间14个冻结源码、测试、文档指纹不变，随后只更新接受文档。原首版失败未被覆盖。证据：`b3b/fix-round-1/`中的`report.json`、`review/report.json`、`review/durability-probe.json`、`root-integration/results.json`及`root-integration/kernelone-release.json`，均相对本文的项目审计根。

该接受覆盖原pin/query/stats/retention和普通快照producer交接，不代表工程SQLite、源码原子写staging、命令/browser scratch、共享主根或全平台已统一；未运行真实模型或Bench。统计使用metadata，不整库读取快照payload；枚举工作预算1000000耗尽时明确不可用，大规模吞吐与成本尚未测量。原全120项批次仍为0项验收交付、1次取消、119项未启动，当前没有项目运行。

## 后续原子写入与原生数据库边界

S1迁移具有明确runtime owner的`workspace_write_text_atomic`，由原KFS产生binding并交给现有adapter的`write_managed_text_atomic`执行，staging置于owner的canonical runtime下`fs/staging`。移除该路线的目标目录临时文件与手写fallback；逻辑config路径、path-only helper和guarded CAS待各自调用者绑定后再迁移，不能从目标祖先、cwd或环境猜owner。旧普通Local atomic路线没有CAS或fsync保证，不将其误写成已有机制；新路线验证实际临时文件位置、UTF-8字节、普通覆盖语义、same-filesystem原子替换、失败前后效果和资源释放。

真实rename后发生fsync/清理错误时，不能将效果已知未知混为普通“未执行”。现有工程owner有显式resume与单一write_file精确回读的恢复路径，但它是否能承接新增异常仍是未运行假设。有限接受实验必须实际rename后注入一次失败，检查原始durable call与同进程合法恢复；不能凭后来completed改写原调用事实，edit/command未知效果也不得被文件回读豁免。设计证据`.polaris/audits/project-storage-20261022/b2-staging-preparation/plan.json`。

ROOT已对未修改的KFS facade/native adapter执行一次有限合成实验（两文件指纹与B3a提交一致）：实际临时文件分配在fixture源码父目录，未位于该owner`.polaris`；实际原子替换完成后，注入既有receipt路径投影ValueError，目标仍包含完整新字节。证据`.polaris/audits/project-storage-20261022/b2b-baseline/{probe.py,report.json}`。它证明现有两处机制问题及可达次序，不证明自然发生频率、修后收益或工程恢复已通过。首次准备遇到并发B3b冷import环，未写夹具即失败；修正后才执行该实验，原始错误保留。

S1调用方审计另发现原准备假设不完整：KFS构造仅保存路径，没有确立项目owner；Registry使用技术`home`作为文件命名空间，若直接据此创建staging，将新增`<registry-home>/.polaris/runtime`。因此须在同一现有KFS边界区分不可扩大的文件命名空间与host显式runtime owner。普通项目默认两者相同；Registry在构造时使用现有`default_polaris_root()`所绑定的共享Polaris owner，或host明确传入的owner，不能使用子实例record/workspace、目标祖先或cwd推断。只读Registry不解析或创建写入owner。

staging必须消费现有`storage.layout.canonical_workspace_runtime_root`等无初始化路径能力，不在native writer重写`.polaris/runtime`解释。命名空间仍决定原有目标路径权限，runtime owner仅决定私有staging；不同owner不能扩大目标范围。Registry数据仍写原有唯一文件，本批不热迁移全局registry/NATS，不新建第二账本；旧数据位置的离线迁移仍属B5。已有managed process journal的`<workspace>/runtime/evidence`写读对也作为独立消费者迁移项保留，不把staging通过冒充其目标位置合规。S1初始九文件候选及其496通过结果仍需相邻owner调用方补齐后才接受。

S1最终11个Python文件已完成限定接受：作者522通过、零跳过/排除，保留一个既有fork警告；Ruff/格式/mypy均通过。独立审查`APPROVED_SCOPED`，ROOT完整相关FS/storage、恢复、ContextOS、QA与安全Registry集成723通过（同一既有警告）；KernelOne ALL12阶段、368通过/1跳过。期间14个源码、测试和文档指纹一致，随后仅更新接受文档。初始九文件候选仍保留为未接受版本，owner修复的RED/GREEN与冷进程证据见`.polaris/audits/project-storage-20261022/b2b-s1/owner-integration/`。

真实隔离T5证明：实际rename后同步失败保留原始write调用`started/NULL`与UNKNOWN；同一service恢复进行独立真实回读与reconciliation，不重放原写入，原调用事实不改写；后续`completed/not_run`不作验收成功。四份SYNTHETIC请求快照版本只对应两次观察到的loopback交换，不是四次真实模型请求；模型费用仍UNKNOWN。缺少完整项目在注入故障后的验收、power-cut与任意同UID攻击证明；逻辑atomic、CAS、path-only、原生DB、命令/browser缓存、legacy journal及共享主根仍待收敛。当前实跑Bench验收数不因此增加。

原生数据库后续以既有Session安全包络为基线：把目录、regular DB/lock身份和已持有句柄迁入KFS管理，由现有KernelDatabase/SqliteAdapter拥有native连接生命周期；Session继续拥有同一schema和事务，不建新DB或租约表。保留现有workspace.lock、WAL、nofollow、权限与恢复行为，不以任意同UID主机攻击者为由引入未经因果证据支持的自定义VFS或独立DB进程。有限裁决见`.polaris/audits/project-storage-20261022/b2-preparation/bounded-native-db-ruling-options.json`，这些迁移尚未实现。

## 仍在运行的主实例边界

一次只读PID/FD与guarded registry观察见`.polaris/audits/project-storage-20261022/platform-root-observation/`：主backend runtime已在主仓`.polaris/runtime`；main/NATS配置的home、tmp、cache和已打开日志仍有外部路径。4个进程身份稳定，11个可写普通文件FD中8个在已证明owner项目`.polaris`外。配置和可写FD分别只证明路径意图与打开权限，不证明观察到了写效果。共享NATS仍归main平台owner，后续B5须协调既有owner离线切换，不热拷贝、不双写、不抢占现有锁或进程。

source-only切换准备见同目录`transition-plan.json`。`KERNELONE_HOME`同时选择持久配置和NATS根，直接换HOME会隐式切配置；须在现有owner中拆清main共享runtime与显式config归属，子实例scratch/cache则按各自workspace绑定。现有NATS migration只接管原物理store，跨根transfer/rebind未具备接受证据；准备记录不是已部署迁移。原main外部env导出的启动脚本来源仍UNKNOWN，未来应提供显式可复现启动配方，不把找到该历史脚本作为新启动实现的前置条件。

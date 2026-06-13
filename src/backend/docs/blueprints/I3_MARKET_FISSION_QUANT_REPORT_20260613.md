# I3 量化报告：三层裂变市场模式 vs 串行旁路（L2-12 对照）

日期: 2026-06-13 ｜ 状态: r9/r10 全链数据 + 八修闭环；r11 验证跑进行中
蓝图: `THREE_TIER_TASK_DECOMPOSITION_BLUEPRINT_20260612.md` §8 实施序 I3
法医工具: `scripts/factory_bench/market_forensics.py`（步级成功率/重试吸收/门干预/墙钟，源=task_market.db transitions）

## 1. 实验设置

- 项目: factory-bench L2-12（经典打砖块，requirements 同 seed）
- 角色绑定（全局配置，未改动）: PM/CE/QA → MiniMax-M3 云；Director → 本地 qwen3.6-27b-int4 @16k
- 市场模式: `KERNELONE_TASK_MARKET_MODE=mainline-full` + `KERNELONE_CE_STEP_FISSION=1`（内联有界消费循环）
- 对照（串行旁路基线）: 同项目 workflow 串行派发三跑（前日数据）
- 通路迭代: r1–r8 逐层揭障（驱动入口/缓存根/analysis_runner 分层/ADR 旧格式/市场投毒/CE 调用三死因/输出形态漂移/depends_on 命名空间），r9 = 首个全链完整跑

## 2. r9 头条数字（vs 串行基线）

| 指标 | 市场裂变模式 (r9) | 串行旁路基线（3 跑均值） |
|---|---|---|
| 任务装配 | 2 PM 任务 → CE 一拆多 → 7 步（4+3），CE 门 0 误杀 0 requeue | 2-3 任务直派，无步契约 |
| 步级 QA PASS | 4/7 resolved（步成功率 0.571） | 无步概念；任务级 0–1/3 成功 |
| 实质交付（盘上验证） | **5/7 步产物通过其机器 verify**（S3 死信但 main.js 在盘通过自身 verify） | r1 审计: main.js **node --check FAIL**（截断语法错） |
| 最终工件 | index.html + style.css + **main.js 387 行语法干净**，paddle/ball/brick/level/restart 内容齐；缺 readme.md | html 可有、js 坏、内容部分 |
| 市场重试吸收 | 10 次 exec attempts，4 次 requeue 被市场吸收（无人工干预） | 重试在角色内部梯队，链外不可见 |
| 墙钟 | 6678s（exec 段 6524s） | 1197/1133/1074s |

**墙钟口径必须诚实**：市场模式慢 ~6×，但串行基线交付的是**语法损坏**的 main.js（输出顶截断），市场模式交付语法干净、逐步 verify 的工件。墙钟差主因：①两个失败步各烧 3×~16min 尝试（共 ~96min）②内联消费循环每角色单 worker 串行轮询，**并发红利未兑现**（E1 设计的 worker 池并发需 durable 模式）③本地 27B 单步 ~10-16min 是常数底。

## 3. 法医归因：3 个失败步中 2 个是市场语义误杀，非能力失败

r9 的 transitions 法医（22 条状态迁移）把失败拆解为：

1. **PM-0001-1-S3（main.js 核心引擎）— 被「领取时重试豁免」误杀**：
   两次 EXEC_FAILED 后第三次尝试成功（15:56 ack 进 pending_qa，产物在盘且通过自身 verify），
   但 16:50 QA 队列扫描 claim 时 `attempts(3)>=max_attempts(3)` 直接死信——
   **attempts 不随阶段推进重置，执行阶段烧掉的预算在 QA 阶段被清算**。干完活的步被杀，QA 从未裁决。
2. **PM-0001-2-S2（main.js 多关卡扩展）— 真实能力失败**：三次尝试零物化变更（no_materialized_changes）。
   唯一一个归因于 Director 能力的死信。
3. **PM-0001-2-S3（readme）— 依赖搁浅**：S2 死信后永滞 pending_exec（搁浅类危害的活体实例）。

另有一个**假阳性 resolved**：PM-0001-1-S1（readme 步）Director 写了 index.html 而非 readme.md，
但「有任何变更即过」的证据门 + QA 不执行步级 verify（`test -f ./readme.md` 一跑即炸却 PASS score 10）让错误产物一路绿灯。

**能力修正后的步级成功率**：若市场语义正确（S3 算成功、S1 被打回重试），Director 实质完成 5/7 步交付物——
弱模型在 ≤120 行步契约 + 签名骨架 + 机器 verify 下的可收敛性得到第二次独立证实（首证 = I3-r5 的 PM-0001-2 全链）。

## 4. 根修闭环（全部测试钉住，当日完成）

r9 暴露的缺陷全部当日根修（非工作区侧绕过）：

| # | 缺陷 | 修复 | 测试 |
|---|---|---|---|
| 1 | attempts 不随阶段推进重置（成功步被领取时豁免误杀） | `acknowledge_task_stage` 阶段推进时 `attempts=0`（每阶段独立预算） | `test_stage_advance_resets_attempt_budget` |
| 2 | Director 证据门不查步级 target_file（写错文件也过） | `EXEC_TARGET_MISSING` 教学性 requeue（声明目标必须在 changed_files 中） | `test_step_target_not_in_changed_files_requeues` 等 2 项 |
| 3 | QA 对步级机器 verify 盲（不存在的文件 PASS score 10） | QA claim 后先跑 `construction_step.verify`（60s 超时，败→requeue pending_exec） | `test_qa_step_verify.py` 5 项 |
| 4 | 死信依赖的后继永久搁浅（DLQ 不可见） | claim 时级联清扫（`dependency_terminal_failure`，可批量重放；定向 claim 不触发；不走补偿避免递归） | `TestDependencyTerminalCascade` 7 项 |
| 5 | 父归并裸写 status（无 stage 无 DLQ 条目）+ 报告把死簇误报 unresolved | reconcile 走 DLQManager 三齐 + 报告移到 reconcile 后按 lineage 折叠（防跨跑污染 BLOCKER） | reconcile/lineage-fold 测试 3 项 |
| 6 | 审计链 `dead_letter→dead_letter` 假迁移（from_status 在 mutation 后取） | 先取后变 | （由 transitions 法医回归覆盖） |

对抗复核 workflow（3 lens × 独立 agent）对修复 #4/#5 的初版又抓出 5 簇缺陷（跨跑污染 BLOCKER、在飞误杀、
DLQ 原子性、回执毒丸、依赖环），全部二次根修。合计回归面：market 296 + orchestration 832 + CE 195 + director 320 + QA 28 全绿。

## 5. r10：诚实的 0/9（六修机制全部正确开火，揭出下一层两根因）

r10（六修齐上，9 步裂变，墙钟 10115s，16 attempts）步级 0/9——比 r9 的 4/7 更低，但**全部新机制按设计工作**：
QA verify 拦下 4 个本会假 PASS 的步；级联清扫把 4 个依赖死信即时落 DLQ（零搁浅）；双父归并三齐；
报告按 lineage 列全 11 项死簇。r9 的 4/7 含假阳性，r10 的 0/9 是诚实失败。法医揭出两新根因：

1. **CE verify 形态漂移（主导根因）**：本跑 CE 输出 verify 为 JSON 数组，normalize 的裸 `str()`
   把列表变成 Python-repr 垃圾入契约 → 任务 1 全部步无论产物质量 QA 必败。
   （云模型输出形态漂移第二例；第一例 = r6 think+围栏。）
2. **verify 反弹盲重试死亡螺旋**：QA 教学消息存 `last_error`，但 claim 结果只携带 payload——
   Director 重领后看不到失败原因：文件看似完成 → 零变更 → no_materialized_changes 死信。
   task2-S1 同时实证接口定名漂移（模型写 `id="level"`，合同要 `id="levelDisplay"`——教学注入正是对策）。

**Fix-7/8（当日闭环）**：⑦ normalize+QA 双侧把数组 verify 以 `" && "` 连接为单命令；
⑧ 反弹教学闭环——`fail_task_stage` 把失败摘要并入 `payload.last_failure` → director consumer 转发 adapter
context → 蓝图步骤卡渲染「上次尝试失败(代码): 消息 + 不要原样重写」；ack 阶段推进时清除教学。
回归：market/director/QA/CE/roles-kernel 全绿。

## 6. 结论与 r11 预期

- **架构论证**：CE 步契约（≤120 行/单文件/签名/机器 verify）+ 市场 claim/retry/QA 链路，让 16k 本地 27B 产出
  串行模式从未达到的语法干净多文件工件——三层分解的「智能上移、执行下沉」假设在 L2-12 上成立。
- **代价**：墙钟 6× 于串行（失败步重试 + 串行轮询）。降低路径：durable 模式 worker 池并发（设计已有）、
  失败步教学性 re-ask（W 队列）、步粒度再校准。
- **r11（八修）**：1/9 真 resolved（PM-0001-2-S1 经真实 verify 执行后 QA PASS——Fix-7 生效）；
  反弹教学链全程贯通（教学卡在 Director prompt 渲染 7 次，含完整 verify 命令）。
  残余结构性根因：**反馈环太长**——exec→QA→bounce→exec 一圈 ~3 个市场周期（~30min），
  27B 每圈只有一次盲猜机会，接口定名（合同 `id="game-canvas"` vs 模型习惯 camelCase）纠不过来。
- **Fix-9（写后即查——蓝图 §2 原设计终于接线）**：`construction_step.verify` 接入 Director 执行轮内的
  质量修复梯（`_collect_step_verify_errors` 进 4 个质量错误采集点）——verify 失败在**秒级**反馈给修复轮，
  复用进度感知预算/修复指令全套既有机制；残错走 `director_materialization_quality_failed` 市场兜底。
- **r12（运行中，九修齐上）**：判定标准不变：步级成功率 ≥5/7 且零假阳性 resolved。
  中段法医（S2 死信链）实锤新层根因并当日落地 **Fix-10/11**：
  - **r12 现场证据**：S2（style.css）**7/8 个 verify 子句全过，只差 `wc -l ≤ 120`**（写超 158 行），
    但执行轮教学只给整条 400 字符命令 + exit 1，市场反弹教学只有泛化标记
    `director_materialization_quality_failed`——模型修齐了 7 个选择器检查却不知道要删行，
    第 3 次尝试零 diff 重写 → EXEC_NO_EVIDENCE 死信，S3/S4 级联清扫（机制正确，零搁浅）。
  - **Fix-10（子句级 verify 诊断，三落点）**：失败时逐子句二分定位，教学消息**前置**
    `failing clause [k/n]: …`（步骤卡 240 字截断保护）；市场反弹消息携带第一条具体质量错误。
    诊断只增锐教学、整条命令仍是判定真相；引号切坏（sh -n 门）/状态承载子句（cd/VAR=）/顶层 `||`
    一律放弃诊断——对抗复核实证「点名错误子句」比不教更糟。
  - **Fix-11（target_file enum 钉靶）**：把步契约 target_file（含 `./` 变体）钉进 5 个写工具
    `file`/`path`/`filepath`/`file_path` 参数的 enum，接线在 6 个 schema 构建点——严格集/强制集
    是构建集的名字过滤子集，故全逃逸梯贯穿、**首次尝试即生效**（r9-S1 假阳性正是首试写错文件）。
    W1.10 行替换收窄改为保留源 file enum（组合性）。CE 门同步验形 target_file
    （glob/逗号表/绝对路径 → corrective re-ask），执行侧对畸形目标拒钉（安全降级）。
    已知残口：edit_blocks 的 blocks 字符串内嵌 `:filepath`、`execute_command`/`repo_apply_diff`
    不受 enum 约束——由步靶证据门 + QA verify 兜底。
  - 对抗复核（12 agents，3 lens + 逐发现反驳验证）抓 5 实锤全修、2 主张被反驳组否决；
    回归 2675 + 1731 全绿。

## 7. r12 终局（九修）：3/8 真 resolved、零假阳性 + 第十二修

**r12 数字**（forensics）：8 步（4+4 裂变，CE 门零误杀），**3/8 resolved（37.5%）、零假阳性**，
墙钟 5699s（exec 段 5636s），9 attempts / 4 requeues 被市场吸收，7 死信
（2 根因 + 3 依赖级联 + 2 父归并，级联/归并/lineage 报告全部正确开火）。
三个 resolved 步（1-S1 index.html / 2-S1 重启版 index.html / 2-S4 readme）全部**首试一次过 +
QA verify 真实裁决**——轮内写后即查（Fix-9）下，契约干净的步不再需要市场重试。

**两个根因死信，各自实锤一个已修/新修缺陷**：

1. **1-S2（style.css）**：7/8 verify 子句全过、只差 `wc -l ≤ 120`（写到 158 行），教学盲 →
   第 3 次零 diff 重写 EXEC_NO_EVIDENCE。——**Fix-10 的活体动机**（子句级教学会直接点名删行）。
2. **2-S2（main.js LEVELS）**：第 3 次尝试死于
   `Prompt exceeds model context window even after compression. requested=9349, allowed=4659, compressed=4978`
   ——反弹教学+修复轮上下文累积膨胀，压缩后**仅差 319 token** 仍被整轮 raise RuntimeError。
   法医揭出 `prompt_budget._enforce_with_budget` 的**不对称缺陷**：already_compressed 分支超额时
   hard-trim 降级放行，首轮压缩分支超额时却 allowed=False 硬拒。
   **Fix-12**：首轮压缩不足时对压缩产物 hard-trim 降级放行（strategy 加 `+hard_trim`、
   quality_flag=degraded）——degraded prompt 严格优于必败的整轮失败。77 测试绿。

**r13（十二修：九修 + Fix-10 子句教学 + Fix-11 enum 钉靶 + Fix-12 预算 fail-open）**：
判定标准不变：步级成功率 ≥5/7（8 步制 ≥6/8）且零假阳性 resolved。

## 8. r13 终局（十二修）：创建模式 4/4 全绿、编辑模式 0/5——失败面收敛为单一行为模式

**r13 数字**：9 步（4+5 裂变），**4/9 resolved、零假阳性，墙钟 3152s**（r12 5699s 近乎减半，
对串行基线从 6× 降到 ~2.9×）。7 attempts / 2 requeues，CE 门零误杀。

**决定性分裂**：
- **任务 1（创建模式）4/4 全部首试一次过 + QA PASS——含两个历史死点**
  （style-sheet = r11/r12 的 wc-l 死点；game-core = r9/r12 的 main.js 死点）。
  **父任务首次完整 resolved（4/4 子步全绿 reconciled）**。r12 的两类根因死亡
  （教学盲、预算硬炸）在十二修下零复发。工件：index.html 40 行 + style.css 224 行 +
  main.js **354 行 node --check 干净** + readme 62 行。
- **任务 2（编辑模式）0/5**：2-S1（向已完成的 main.js 追加 LEVELS）三次尝试
  全部零物化变更（EXEC_NO_EVIDENCE×3——模型读到完整 main.js 判定「已完成」
  拒绝动笔），S2–S5 级联清扫。**三次重试零新信息**——理论报告法则 4
  （零新信息重试 ≈ 纯浪费）迄今最纯净的活体标本。

**结论**：十二修把「创建模式」做到了 100% 首试通过率；残余失败面收敛为单一
行为模式 = **弱模型的编辑回避**（edit-reluctance）。对策已在理论报告工程预测中排队：
T2 残差教学的**前摄变体**（领取时即在步骤卡渲染「当前盘上 verify 失败的子句清单」，
把"编辑既有文件"转译为"补齐这些缺失项"）、信息门控重试 + 多样性阶梯
（attempt-3 强制换执行形态）、pending_ce_revision 降率出口。

## 9. r14 终局（十三修：+Fix-13 现状勘察缺陷清单）——揭出比执行更深的根因：跨父接口漂移

**r14 数字**：9 步（4+5→实际 4+3 裂变），**4/7 resolved（1-S2/1-S3/1-S4/2-S1）、墙钟约 5.4k s**。
Fix-13 缺陷清单**直接命中编辑回避**：2-S1（r13 的编辑死点，向已有 index.html 注入新元素）
**首试一次过**——领取时的「现状勘察(缺陷清单)」把"编辑既有文件"成功转译为"补齐缺失项"。

**但 r14 揭出一个比执行层更深、且会让"resolved 计数高估真实成功"的根因——跨父/跨文件接口漂移。**

L2-12 的 PM 分解是**正确**的：一个打砖块游戏拆成「基座任务 PM-0001-1（建 index/style/main/readme）」
+「增强任务 PM-0001-2（加关卡/重启/补文档，depends_on 基座）」——这正是增量开发的常态，
两任务**合理地共享文件**。缺陷在 **CE 裁变层**：两个父任务被**独立裁变**，各自为同一 DOM 元素
**发明互相冲突的接口标识**：

- 1-S1 给画布定名 `id="game"`；2-S1 给同一画布定名 `id="gameCanvas"`。
- 1-S3 的 main.js 调 `getElementById('game')`。

归档工件实锤（`L2-12_runs/r14/artifacts`）：
- index.html 终态是 2-S1 的 `gameCanvas/restartBtn`，1-S1 的 `game/score/lives/message` **全被覆盖消失**
  → 2-S1 的编辑**整文件重写**清掉了 1-S1 的标记 → 1-S1 的 QA verify（`grep id="game"`）在验收时失败
  → 反弹回 exec → 文件已存在但"内容不对" → 编辑回避 → `EXEC_NO_EVIDENCE` 死信。
- main.js 仍调 `getElementById('game')`，而 index.html 只暴露 `id="gameCanvas"` → **画布查找返回 null
  → 游戏开局即死**。**即便 3/4 步 "resolved"，产物根本跑不起来。**

每个步都过了**自己的** `grep`，但**没有任何步校验文件之间的接口**——本地单文件 verify 对跨文件
接口漂移**结构性失明**。这是理论报告**组合律**（assume-guarantee）最具体的活体标本，也直接证明
当前 "resolved 步计数" 高估了真实可运行性。

CE 漂移之严重在 RAW_RESPONSE 里可见：**同一父任务的不同重试间**，CE 都在 `id="game"` 与
`id="gameCanvas"` 之间反复横跳——无冻结契约时，名字本质上是每次裁变随机的。
另一时序事实：`PM-0001-2` 在 `PM-0001-1` **之前**被领取裁变（设计阶段领取无视父级 depends_on，
就绪门只在 pending_exec 开火）——即便有账本也会被乱序读取。

**对策（本批 F1–F3，蓝图 `CROSS_FILE_INTERFACE_COHERENCE_BLUEPRINT_20260613.md`）**：
- **F1 设计阶段父级排序门**：consumer 父在 producer 父离开设计阶段前不可领取裁变（生产者先冻结接口）。
- **F2 跨父接口账本**：CE 裁变后把每文件已声明的 `interface_names/signatures` 落账
  （`runtime/contracts/interface_ledger.json`，先到先得冻结）；后续父裁变前读账本并注入
  「这些文件已定名，必须复用，严禁重命名」的冻结契约。语言无关（只搬 CE 自己声明的标识符字符串）。
- **F3 子句诊断上限 12→24**：r14 的 1-S3 背 15 条机器义务，旧上限下静默丢失子句级教学。

**待验**：r15（同输入 L2-12，验证根因是否闭合：index.html 标识符是否一致、产物是否可跑）
+ L2-11（留出泛化，其两父**不共享文件**=对该修复"无害"且检验整链能否完成未见项目）。


## 10. r15 终局（F1/F2/F3 已上线）——跨父接口闭合，但暴露更深一层：弱执行者被组织架构反向卡死

**r15 数字**：步成功率 **2/7 (0.286)**（r14 的 4/7 倒退）、**可运行率 3/7 (0.429)、product_coherent=False**、
墙钟 5824s、dead_letters=7。market_forensics 现自动产出四项（步成功率/可运行率/墙钟/根因）
并内建 `node --check / py_compile` 语法门，使「可运行率」不再被 grep 蒙混。

**确认的胜利（F1/F2 真实闭合 r14 根因）**：CE 账本把画布统一为 `gameCanvas`，跨父不再 game/gameCanvas
分裂；main.js `getElementById('gameCanvas')` 与 index.html `id="gameCanvas"` 一致。r14 的开局即死消失。

**但原始计数倒退，根因是弱执行者（本地 qwen）执行力 + 一个放大器，全部定位**：

1. **edit_blocks 形参畸形 121 次**（`missing required argument: blocks or start`）——弱模型会说“改这个文件”
   却给不出“改哪里”（无 start 行号、无 SEARCH 锚）。归一化器已能修多种形状，但模型连定位都不给。
2. **弱模型写出坏 JS**（`speed: 4;` / `lives: MAX_LIVES;`——对象字面量用 `;` 当 `,`；以及未闭合 `{`）→
   `node --check` 失败 → 质量门（正确地）拒收。
3. **语法修复指令把弱模型反向卡死**（本轮最关键）：指令逼模型用 edit_blocks 做窄编辑（它形不出），
   同时**禁止** write_file 整写（它唯一会的）——无可执行修复路径，main.js 直接死信。这是
   「组织架构主动妨碍农民工」的活体标本。
4. **CE 裁出过约束线性 DAG**（S2←S3←S4，多为伪依赖：style.css 不依赖 index.html、main.js 不依赖 style.css）
   → 根步 index.html 一死，S3/S4 **未执行即被级联清扫**。r14 同形但根步存活掩盖了脆性；r15 根步死 → 全垮。

**本轮已修（质量门全绿）**：
- **语法修复解卡**（`execute_method._build_materialization_quality_repair_message`）：去掉“禁止整写”陷阱，
  改为给出可执行路径——优先 write_file 只改坏行、edit_blocks 作为复制原行的备选，仍约束“只改坏行 byte-for-byte”。
- **CE 依赖最小化**（`ce_consumer` 裁变提示）：depends_on 仅在本步代码确实引用他步 interface_names 时填写，
  独立文件留空，避免单步失败级联拖垮父任务。
- **可运行率语法门**（`market_forensics.replay_runnable`）：通用 node--check/py_compile，product_coherent
  现要求“所有步 verify 对终态仍过 + 所有代码文件能解析”。

**仍待治（下一层，留作下一修复轮）**：edit_blocks 定位缺失的弱模型补全（从质量门已知坏行号反推锚点）；
对象字面量 `;`→`,` 类机械错的确定性自愈；编辑模式（向既有文件追加）的物化稳健性。

**待验**：r16（同输入，验证语法解卡 + DAG 最小化是否提升 product_coherent / 步成功率）。


## 11. r16 终局（r15 三修已上线）——结构层全闭，触底到弱执行者「空输出墙」

**r16 数字**：步成功率 **1/7 (0.143)**（仅 style.css 一步 resolved）、**可运行率 2/7 (0.286)、product_coherent=False**、
墙钟 **8051s（134min，本系列最慢）**、dead_letters=7。

**结构层修复全部确认生效**（非回归）：
- CE 依赖最小化 → 本轮 DAG 明显变平（多数 step dep=[]）；
- **独立步在级联中存活**：PM-0001-2-step-3 在 step-1 死后仍进 pending_qa（r15 的线性链会连坐清扫它）；
- 语法解卡 → index.html 一次写满 3757b（r15 截断 591b 消失）。

**触底的根因 = 弱执行者「空输出墙」（本系列最深一层，task #27）**：
本地 qwen3.6-27b-int4 在难步上反复返回 `output_length=0`（空可见输出）→ 无 provider 级自愈
→ mutation-contract 重试再得空 → `director_no_materialized_changes` 死信。**直接探针实锤根因**：
qwen3.6 是**推理型模型**，`POST /v1/chat/completions` 返回 `content:null` 且文本落在 `reasoning` 字段、
`finish_reason:length`——**推理吃光 token 预算，可见 content 为空**。这与 MiniMax 的空输出同源,
但「空可见输出自愈（预算翻倍）」目前**只在 minimax_provider 内**（minimax_provider.py:690-713），
openai_compat/本地路径**没有**等价自愈，也未必抽取 reasoning。叠加 16k 窗口下难步 prompt 膨胀
（`Token budget exceeded after compression`），难步几乎必空。

**串行墙钟实锤并发价值**：单 Director worker 把整整 ~134min 几乎全耗在一个难步（反复空输出重试）上，
**独立的 S1/S2 全程 att 0/3 未被领取**——双后端并发本可让它们并行完成（见 §12 并发实测）。

**收敛叙事**：r14=蓝图接口层（已修）→ r15=过约束 DAG/语法陷阱（已修）→ r16=弱执行者原始执行力
（空输出墙）。组织/架构层已尽力把活拆好、契约理顺、修复路径打通；**剩下的是农民工的手在最难的活上
直接交白卷**，且组织对「交白卷」无兜底——这正是下一修复轮（task #27）的靶心：本地推理模型空输出自愈
+ 16k 难步上下文预算 +（降率律）难步再裂变。需借真机 qwen 验证，不冒进塞热路径。

**待验**：L2-11 留出泛化（检验结构修复是否泛化 + 空输出墙是否普遍）；并发实测见 §12。


## 12. 并发实测（Director 双后端：本机 + 局域网 qwen3.6-27b）

**LLM 层直测**（2026-06-13，r16 跑完后本机空闲，max_tokens=150 中等请求）：
- 单请求时延：本机 localhost:8189 = 21.3s；局域网 192.168.10.166:8189 = 17.2s（局域网机更快）。
- **串行** 2 请求（仅本机，现状）：43.4s。
- **并行** 2 请求（本机 + 局域网，conc=2）：20.4s ≈ max(单时延)。
- **加速比 = 2.12x**（双后端的近理想 2x；并行墙钟≈较慢单后端时延，证明真并行无串行化）。

**机制已被单测证明**：worker 池经市场租约领取**互异**叶步、各 worker 经 contextvars override 路由到
各自后端（穿透 asyncio.run + asyncio.to_thread）、`_exec_claim_ready` 保 DAG 序。

**全链可兑现度**：市场链的端到端加速取决于**同时可并行的独立步数量**。r15 的 DAG 最小化已让 CE 产出
更平的 DAG（多数 dep=[]）→ 存在独立步 → r16 那种「单 worker 困在一个难步 134min、两独立步全程闲置」
的串行墙会被显著缓解（独立步并行落到第二后端）。**但并发只解速度,不解 §11 的空输出正确性墙**
（task #27）——并行让失败更快发生,不会把失败变成功。

**配置已激活**：`roles.director.provider_pool=[local, lan]`、`concurrency=2`（两者均 qwen3.6-27b-int4,
角色绑定铁律不变;备份 llm_config.json.bak.pre-multibackend）。下一步 r17 全链双后端实测端到端墙钟。

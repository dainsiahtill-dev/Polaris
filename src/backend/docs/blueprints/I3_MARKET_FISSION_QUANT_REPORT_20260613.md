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

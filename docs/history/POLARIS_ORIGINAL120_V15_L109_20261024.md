# Original120 v15：L1-09 实测、失败与亲检

L1-09「星尘炼金配方生成器」没有完成无人值守交付。原生持久状态为 `interrupted / verification passed / revision127 / QA unknown / outcome unknown`；QA 到期超时，资源释放需要 ROOT 通过现有 InstanceSupervisor 收尾。ROOT 后续35次只读 CLI 观察及5张图像亲检判定产物质量 **FAIL（两个缺陷族）**。原始 UNKNOWN、误通过和中断都保留，不被后续人工观察改写。

## 固定条件

源码 `3b2b5aaa08d75b0add90f905e14132803e3008d5`，与 `9bf5063e` 的32个已资格化文件逐字节相同；新增提交只有文档。全程9517文件/181796590字节冻结匹配，未热改。

Run `e075cac217b245b5820927ceff3da4ec`；实例 `engineering-original120-v15-l1-09`；workspace `/home/dains/Documents/polaris-bench-projects/original120-20261024-v15/L1-09`。原目录完整目标与深度要求保留，workflow collaborative，MiniMax-M3，配置输出上限524288/窗口1000000；原5400秒预算与6000秒外层保护不变。

四份原输入字节保留。仅新增显式 Rust host：只读挂载已安装 `stable-x86_64-unknown-linux-gnu` 工具链，声明其 bin。启动前四项真实合成对照证明原host不可见、显式配置可编译链接运行、空bin仍为空、未挂载路径拒绝。没有外部安装或修改目标。这个环境证明不等于项目验收；完整工具链字节没有资格化。

## 原生执行与第一失败

1. PM39轮、CE4轮完成；Director145个逻辑响应，真实生成Rust工程、测试、示例、规则表、CLI与SVG。普通编译/测试/参数问题回到同一Director自行修复，没有重启PM或外部修补目标。
2. 第一次正式验证 event2225 错误显示通过。三条配方都以 `tail` 结束，Cargo被只读文件系统拒绝，脚本删除输出也失败，但外层POSIX管道退出0。K1如实返回shell状态；首个语义错误在Verifier把这个状态当作检查通过。不能用这个记录证明编译/测试通过。
3. Director在2234自行识别了错误内容，使用原授权创建dist，把Cargo输出移到 `.cargo/config.toml` 声明的 `dist/build`，修改脚本输出后复验。event2345三项、event2409当前四项确实有真实成功内容：构建、25测试+1doctest、全验证脚本与编译后二进制入口。旧误通过仍在。
4. QA最后才开始：2421开始、2426只有辅助 `request_prepared`，没有实际final provider request捕获；2427 TimeoutError，2429 verdict unknown，2431 run_ended/interrupted、release_allowed=true。5400秒预算到期，run_ended约5550秒；没有QA批准，不能计成功。

另一个交接反例已封存：正常Director Scope允许整个workspace写入，原生三次成功写dist的反馈都在最终请求里；每轮最新尾部却再次带上非授权静态声明的 `existence_observed:false / grants_current_write_permission:false`。turn1021的三次WRITE_OK之后，assistant1026再次解释为当前拒写；1089删除dist。提示字面说明“非当前授权”，实际权限没有缩减；“重复置于最新host消息导致时序/权限混淆”仍是候选因果，未经真实模型对照。

## ROOT亲检：外层成功不等于业务正确

ROOT固定最终已有 `dist/build/debug/stardust-alchemy`，SHA256 `e31b5cfa51f94c465ccdeb6e13c9e38a28a5fccc1e8e8aa63fd107416ac26e4d`，在私有K1沙箱运行其原字节副本，没有rebuild/test/install或目标源码、链接修改。303个产品文件、66269621字节前后hash完全一致；私有输入、缓存、输出全部在项目.polaris。

35次观察包括33次forge及help/rules；10项无效输入都报具体错误且不改输出，9项已公开温度阈值边界正确。同配方药名/颜色可复现，单独改变元素或几何有实际效果，反向梯度也改变位置温度及解释。实际功能比空壳丰富，但下面的反例使质量不合格。

**缺陷族1：CLI丢失温度与碎片身份的绑定。** 同一合法配方只将数组从1,2,3改成3,1,2，坐标仍1@(0,0)、2@(5,8)、3@(10,0)，梯度0..100：

| 碎片 | 应有温度 | 实际CLI | 实际SVG |
| --- | --- | --- | --- |
| 1，x=0 | 0°C | 50°C | 0°C |
| 2，x=5 | 50°C | 100°C | 50°C |
| 3，x=10 | 100°C | 0°C | 100°C |

这违反原需求“每个碎片获得自身位置温度”，不是新增验收条件。首处偏差在 `src/main.rs:190`：用数组位置zip；positions保留输入顺序，而 `rules.rs:522` 将temperatures按ID排序。SVG用ID关联所以仍正确。它们虽然共用结果对象，两个消费者仍解释成不同身份关系。现有测试比较温度值/图结构，没有覆盖这个真实CLI交接反例。未外部修改该错误。

**缺陷族2：公开主温度档规则与实现不符。** README写“没有温度档超过一片时按均温决定”；默认0/50/100均温50应为warm，却输出cold。`rules.rs:632` 只在四个计数全部等于1时才用均温，遗漏了未使用档为0的情况。单片阈值本身正确，这个问题是主档聚合的公开规则不一致。

ROOT另亲看3张当前原生产物（triangle/reversed/column）和2张私有反例图（shuffled/eight）。SVG基础可读，反向温度、三点/共线与颜色有真内容；8片标签拥挤。上述有限样本中的通过不代表全部输入空间验收。原生前后两帧也能看到裁切与换行改善，但这不是有无视觉的受控因果实验。外部图像/CLI观察不填补原生QA。

观察器曾因Cargo合法hard link被单链接snapshot拒绝，后改用既有KFS公开只读API并核对前后metadata；没有降低产品KFS策略或拆目标链接。SVG文字读取先用错HTMLElement接口，失败材料保留后改text_content。观察器错误与产品缺陷分开记。

## 资源、上下文与成本

原工程已interrupted但测试进程仍在。ROOT外层观察句柄结束143，已脱离的driver继续存活到外层保护结束，不能把观察句柄当作原运行已停止。初次公共stop因前端退出未确认而失败；父进程结束回收前端后，重新观察再通过公共stop关闭剩余backend。最终原backend/frontend/driver/observer都消失、49978/5174空闲，保护main/NATS/frontend原PID出生值不变。这个释放是外部协助，不是无人值守能力证明。

正式harness report仍是LAUNCHED/NOT_EVALUATED；driver-terminal与external-case-result缺失，没有补造。归档收集380份raw、missing0、last_seq2431，但因缺少driver闭合结果，archive_terminal=false，观察器最终exit2；不能称完整原生归档收尾。

全部380份当前原始快照的key、完整physical hash、身份/目标/工具/配置核对通过。188个逻辑调用对应190个请求身份（同一Director调用含两次失败尝试），不是380次HTTP请求。26份多模态最终token/窗口估计保留UNKNOWN，未用base64序列化proxy替代。

188个成功逻辑响应的provider字段：input255354、output100706、cache_read11940162、cache_creation0；失败尝试的usage、实际HTTP总次数、账单、人工分钟均UNKNOWN。

性能线索：L1-08的17次成功浏览器工具中位8.841秒、首绑定939文件；L1-09的9次中位52.448秒、首绑定53310文件/约1.80GB。不同项目、动作和依赖范围，不能直接归因。原运行及进程资源关闭后，已实际执行一组A→B合成模块对照：A完整Rust root、B仅显式bin/lib，其余条件相同（共同关闭网络）。两者真实离线构建、单测、doctest、CLI均通过，浏览器JPEG原字节完全相同；真实前后manifest不变。A绑定52792文件，B为763；两次绑定合计49.6599秒降为3.9545秒。这支持此合成场景的最小授权配置候选；一对样本仍有顺序/页缓存偏差，不证明整轮超时的唯一原因、全平台提速或任意Rust依赖可用。没有隐藏仍被授权的输入，没有改本次五份冻结输入或回写原生结果。Root已独立重算manifest数量、哈希和图片字节，证据为 `rust-readroot-cost-20261024/results/` 与 `ROOT-results.json`。

## 模块实验、停止条件和下一步

管道问题已有三个真实生产Verifier/K1/SQLite合成实验：`(exit17)|cat` 为0/passed，显式Bash pipefail为17/failed，合法成功管道为0/passed（0.39秒，无模型/HTTP/替身）。它确认失败传播机制；不是产品修复或完整服务/QA验收。下一项应在原生冻结结束后，收敛Verifier专用执行策略及持久化/恢复/相邻接口，避免regex判成功、语言特例或重试加码。普通Director的POSIX行为应保持兼容；pipefail不能解决`;echo0`、脚本吞错或故意空检查，head等提前截断还可能产生SIGPIPE。

PM阶段问题已有真实服务/codec/KFS的4次本地替身预检，A首请求精确匹配归档，B只增加阶段问题，8个CAS与接收body相等。没有真实模型效果数据。尾部提示四次决策对照也仅准备，三个fake transport控制通过；真实槽未运行。它们不成为当前/下一项目的新门禁，不声称收益。

当前cohort共11次尝试、9个不同项目、11次终态亲检，严格交付仍1项；产物质量7 FAIL /3所测场景通过 /1 INCOMPLETE；111项未启动。本轮中断、资源协助和两个产物缺陷不追认成功，也不把一个限定成功扩大为平台完成。先关闭已复现的Verifier错误传播边界，再以新冻结版本继续原L1-10；性能及角色问题按有限实验决定是否引入或删除机制。

## 证据

项目 `.polaris/audits/`：`root-personal-quality.json`、`root-quality-defects.json`、`root-usage-observation.json`、`root-first-formal-verification-false-positive.json`、`root-resource-stop-*.json`、`ROOT-quality-review/`、`root-runtime-archive/`。

主仓 `.polaris/bench/original120-20261024-v15/cases/009-L1-09/`：`terminal-evidence-audit/`、`verification-hint-audit/`、冻结输入与source-freeze。主仓 `.polaris/audits/`：`verification-pipeline-boundary-20261024/`、`role-stage-handoff-20261024/`、`verification-hint-temporal-study-20261024/`、`rust-readroot-cost-20261024/`。这些开发证据不是第二产品事实源，权威工程事实仍是本项目sessions.sqlite3。

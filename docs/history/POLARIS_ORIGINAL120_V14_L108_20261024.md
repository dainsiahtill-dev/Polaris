# Original120 v14：L1-08 实测与亲检

L1-08「纸飞机航线实验室」在本次限定环境与原始需求范围内完成交付。原生结果为 completed / verification passed / independent QA approved / accepted；ROOT 实际操作网页后，所观察功能通过，实例已释放。它证明这一案例能完成，不能证明 120 项整体通过、长期稳定、ContextOS 更优或当前机制优于简单 Agent。

## 固定条件与结果

- 源码：`6b68efd33c99bd716d90841c8f43efea8f83afcc`；生产源码与 `9bf5063e` 相同，间隔仅文档。全程 9516 文件、181788011 字节冻结核验无变化。
- 原目录身份、完整需求与深度要求保留。workflow collaborative；MiniMax-M3 四角色；配置输出上限 524288、窗口 1000000；项目预算 5400 秒。未热改、未重启规划、未外部修改目标或安装依赖。
- run `079bac5a1f1646ccb599cd3c570e767c`，实例 `engineering-original120-v14-l1-08`，workspace `/home/dains/Documents/polaris-bench-projects/original120-20261024-v14/L1-08`。
- 用时约 1793.84 秒。最终 revision 72，verification seq 1953；7 项实际检查全通过：已有依赖检查、TypeScript 类型、构建、30 项测试、HTML、实际 HTTP 服务入口、额外运行检查。QA 独立临时重建及 30 项测试通过。
- 当前候选、持久检查、QA 起止输入绑定与 accepted 投影一致；939 个绑定输入文件 hash 当前匹配。250 次工具观察均已收尾，73 次技术失败保留。

## 亲检，不用模型自报替代

ROOT 使用最终已有 HTML/编译 JS 的原字节，只读观察，未运行目标 build/test/install/server。自有隔离浏览器观察资源均在本项目 `.polaris`，结果不写入原生工程数据库或收据。前后 32 个产品文件清单 fingerprint 均为 `02fd1a1dedfd64cbd8dd35a95322c3f0092dc608e97bfdc6249315bfa90d14f4`。

实际完成 10 组交互，以及默认示例和 6 个有效边界样本，亲看 11 张截图：

- 30 度无风、速度 5 的两次发射，显示数据和 Canvas PNG 完全相同；落点 3.17 米。改速度 10 后为 9.43 米，两条不同颜色真实航迹与各自数值可比较。
- +3 / -3 风的落点分别为 9.67 / 9.19 米，图形确实变化，页面明确向东为正。
- 角度 0、速度空、0、21 都指出对应字段，点击前后有效 Canvas 原字节保持。
- 暂停三次采样跨 1028ms，飞行时间固定 0.06 秒、位置 x=0.31/y=2.13；恢复即时保留相同数值，随后正常落地 15.51 米，与基线一致。
- 75度/20/无风发射中将控件改为30度/5/+3，当前结果仍为15.51米；下一发变为3.27米。不能只凭控件回显认定本项。
- 重置确实清空，下一发只有新纪录。5/80度、1/20米每秒、-5/+5风六个单字段有效边界都可正常发射。

功能质量为 `PASS_IN_OBSERVED_SCENARIOS`。界面清楚但朴素，部分图内标签拥挤、轴边文字裁切，提示中有字面 Markdown 星号；不称作高品质视觉作品。ROOT 没有逐点公开采样接口证据；完整采样确定性、固定步长与上限分支来自实际原生独立测试，不能称为 ROOT 浏览器逐点检查。默认合法参数无法触达60秒上限，ROOT未改模型制造触发。

## 失败与剩余问题

1. PM 用了54次模型调用、76次工具调用，57次工具失败，主要重复读取尚不存在的文件。114份实际快照证明原目标、PM身份与此前工具反馈完整，未发现 parser 丢参或上下文反馈遗失。模型职责理解/重复决策有事实支持；“明确本阶段规划问题可以改善”仅是假设，尚未合入提示词或加重试规则。
2. Director 早期测试真实出现3项失败，即使管道 `tail` 让 shell exit 0，也不能当测试通过；它随后修复并复验。普通 Director 工具在 turn792 已真实安装3个包；turn1499 又删除 node_modules 后真实重装3个包并构建。正式只读 Verifier 中两次 `npm install` 写受保护 lockfile 失败，最终验证改用 `npm ls`；这是避免重复安装，不能省略此前真实依赖准备。当前不经管道的 `verification-1938-2` 构建 exit0。旧失败保留，不将修复效果倒写历史。
3. QA 有7次成功浏览器 capture、14张JPEG，全部进入随后真实 provider body；最后批准请求含14图。图像传输不证明模型看懂。它独立重建并审阅当前候选，真实证据存在；但 resume 连续性和有效飞行中改参两项浏览器表述超出了截图直接覆盖。ROOT后来实际确认功能，仍不补造QA的原生证据。
4. QA 将初始高度0的8.83米真空范围与初始高度1的产物比较，错误地解释为约1.4米 Euler 漂移。正确 h=1 参考为10.3109米，产物无阻力约10.2352米。这个解释错误不构成本题未声明的空气动力精度门禁，却说明 QA 的理由也必须核查。
5. 本次外部观察器最初的 CSP 阻止 `wait_for_function` 字符串 eval，已保留失败、关闭资源；改用有限 DOM 读取后重新观察，未改目标 CSP/源码。终态外部审计器也曾误把转义JSON包含检查及投影字段子集差异判为平台异常；ROOT独立复核全部358份原始快照后否定。首版报告字节未保存，已如实记录不可恢复和算法重建，不能伪造旧证据。

## 上下文、成本与资源

179轮：PM54、CE3、Director94、QA28。358份快照是179对 prepared/terminal，不是358次物理请求。ROOT重新核验所有当前原始快照的key、完整wire hash、角色、目标、输出上限和事件审计子集，无错误；无需归档fallback。部分含图请求的真实视觉token未知，不能用base64序列化估算冒充窗口占用。

provider原字段累计 input_tokens=329402、output_tokens=68841、cache_read_input_tokens=7233941。归一化字段未包含这些cache_read，不能把cached_tokens=0当成实际缓存零。底层实际POST次数、账单、人工分钟仍UNKNOWN。

本次driver/archive/backend/frontend四PID消失，49978/5174在TCP/TCP6均无监听；main/NATS/frontend原PID birth保持。两个亲检浏览器/自有HTTP资源均关闭，主实例未受影响。

## 证据与下一步

本次原生及ROOT材料位于 workspace `.polaris/audits/`：`root-personal-quality.json`、`root-quality-defects.json`、`root-all358-final-requests.json`、`root-runtime-archive/`、`ROOT-quality-review/`。外部审计在主仓 `.polaris/bench/original120-20261024-v14/cases/008-L1-08/` 的 `pm-repetition-audit/`、`qa-request-audit/`、`terminal-evidence-audit/`；这些是开发审计，不是产品事实源。

当前 cohort：10次尝试、8个不同项目、10次终态亲检、112项未启动；产物质量6 FAIL /3所测场景通过 /1 INCOMPLETE。按本题原需求、实际原生验证、当前候选独立QA、ROOT功能亲检及资源闭合综合，本次可计为1次限定范围交付。此前严格失败不变；不称8项全通过，也不追认早期同题失败。

继续原L1-09，目标尚未创建。准备期已完成四项合成对照：原 host 找不到 cargo/rustc（exit127）；显式增加既有 Rust 工具链只读 root 与 bin 后，当前生产 Settings/Profile/PermissionScope/EngineeringTools/KernelOne bwrap 链实际离线编译、链接、运行 hello（exit0）；显式空 bins 仍不可见（exit127）；未挂载 bin 在 resolver 拒绝，进程启动0。只改变两项静态 host 声明，不改网络、QA、预算、原需求或模型，四份原输入保留。候选配置 SHA256 `d13d3d61c994a1f4c42bbc33b20696cacecf66a689b254e5726f088cff5bf6bc`；证据在 v15 case 的 `ROOT-quality-preparation/rust-toolchain-experiment/`。这证明已有显式工具链机制可用，不证明 L1-09 交付或依赖下载，也不将四次合成实验计为 Bench 项目。

下一运行采用单独冻结的显式 Rust host，标清与上一环境的差异；完整安装工具链字节未资格化，主版本生产源码不变。PM阶段问题及QA证据语义继续有限实验，不将这些研究扩成下一题前置门禁。

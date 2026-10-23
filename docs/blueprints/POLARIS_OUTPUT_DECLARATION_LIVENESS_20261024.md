# 输出许可声明与工程推进

状态：已实施并完成组件资格，待main集成和新冻结原生结果；未宣称模型交付收益。基线d152c0e49c94b23cf4fa170c5f684774fa4f87ca。

L1-06的Director在原权限内清理可选dist（1353工具请求、1354真实成功），正式SVG/TXT仍在examples。下一LLM请求准备却因Verifier强制要求dist已存在而在1360失败；尚未正式验证或进入QA，项目不成功。证据在项目.polaris/audits/root-first-output-binding-failure.json及root-terminal-resource-recheck.json。输出目录确实缺失，不是inode漂移或工具反馈丢失。

第一偏差：现有writable_output_roots未来许可被EngineeringVerification构造和_manifest重新解释为当前必交目录。视觉请求绑定消费这个Verifier，派发下一合法普通工具前已抛ValueError。owner仍为roles.runtime，不新增ledger/controller/状态表。

数据流：持久原输出许可 -> 验证全部声明与原权限/当前policy -> 为单次attempt选择当前真实存在的可写输出目录 -> 实际隔离检查 -> 当前完整输入/QA绑定。不存在的许可不自动变成产物要求，不偷偷mkdir，也不添加权限。声明全集仍来自原run row；Verifier只保留内存副本。原全集每次manifest/执行前复核，不能过滤后忘掉未出现的危险路径。

有限假设：分开声明与有效挂载，可使合法清理后继续普通工具和不依赖该目录的真实只读检查；需要目录的检查应真实失败并回同一Director准备。每个新attempt重算有效subset，单个attempt内冻结；implicit重试不得沿用之前的空subset。

实现候选仅engineering_verification.py与engineering_service.py。原公开DTO/Store编码/权限/deadline不变。_profile、源排除和实际命令使用本attempt effective roots；旧passed/QA在目录缺失或替换后必须UNKNOWN。冷读保留原声明和旧行fallback，不从当前host推断新权限。未选声明若变symlink/outside/file仍拒绝；显式空权限不扩展；未知效果不重放。

研究终点：真实Service/Store/K1/本地HTTP/浏览器夹具先复现“下一合法请求无法到达”；生产回归先RED后最小改动GREEN。覆盖readonly检查推进、缺目录写拒绝、Director准备后下一implicit attempt能写、旧pass/QA失效、冷读、全集symlink/outside/空权限和unknown。原机制反例+修后推进+必要安全反例及相邻接口通过即集成，不扩成任意坏工作区自动修复框架。

所有角色原生响应可脚本化，替代边界必须明示；不能借组件approved声明真实模型交付。ROOT完成本项目个人复查后，源资格/独审通过再main提交与新冻结项目。L1-06原失败不升级成功。缓存/环境载体及ContextOS保留策略分开记录，不作为本修复的新增前置。

实际结果：真实capture+清理目录基线在第3请求前失败（2本地HTTP）；同权限无初始capture对照10请求可完成。最终2文件修后原capture反例10请求也能完成真实检查与独立QA夹具，pending0、原权限/声明/deadline不变。生产回归21先9失败12通过，修后21通过；156项相邻、最终36重验、ROOT另47相关和40架构、KernelOne ALL通过。目录历史159条保留且新增/不一致均0，独审无源码P0/P1/P2。项目配置mypy4文件通过；额外strict在完整导入路径下只有1条与HEAD影子完全相同的旧测试辅助函数Any诊断，生产源码无诊断，未声称strict全过。25文件源码联合指纹与原始记录见`.polaris/audits/output-declaration-liveness-20261024/root-acceptance.json`。

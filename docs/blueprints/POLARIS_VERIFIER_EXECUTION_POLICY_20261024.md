# Verifier 执行策略与管道失败传播（2026-10-24）

状态：实现候选已完成限定回归、独立修复复审、最终全变更审阅及ROOT架构/KernelOne ALL/catalog门禁，已具备main集成资格；新冻结项目验收尚待完成。以下不证明任意配方有意义或项目必定交付。

## 问题、基线与接受标准

原目录L1-09的event2225包含三个只读文件系统错误，但配方形如 `cargo build 2>&1 | tail -20`，POSIX shell准确返回最后一个tail的0，Verifier据此投影passed。第一处语义偏差是将外层管道状态当作构建/测试结果。权限层正确拒绝了写入，K1也正确传递了shell状态；这不是缺收据。

三个实际生产Verifier/Tools/K1/SQLite合成实验建立基线：`(exit17)|cat` 为0/passed；显式Bash pipefail为17/failed；合法有限成功管道为0/passed。反证条件是新Verifier裸失败管道仍passed、合法状态不能继续、普通Director被改义、权限被绕过、旧事实被升级或恢复重放。接受标准是新生产者选择、实际执行、耐久绑定、冷恢复及必要相邻消费者都闭合，再进入新冻结原项目。

## 选择与状态所有权

保留唯一 EngineeringRunService 与 EngineeringSessionStore。新列 `runs.verification_execution_policy` 由host在新运行准入时耐久写入，模型不填写策略、hash或命令身份。既有数据库增加nullable列，历史NULL保留原POSIX语义，原ID/idempotent request复用保存值，不自动升级。

私有不可变 `CommandExecutionPolicy` 一处产生launcher及其绑定：

| 策略 | 实际launcher |
| --- | --- |
| `posix_sh_v1` | `/bin/sh -c ORIGINAL_RECIPE` |
| `bash_pipefail_v1` | `/bin/bash --noprofile --norc -o pipefail -c ORIGINAL_RECIPE` |

新运行的Verifier由host选择后者；普通Director和QA的execute_command仍用前者。公开ToolSpec、模型参数、aliases、ToolObservation未增加shell控制。未知策略明确拒绝，不回落。

Verifier将原配方及该resolver生成的实际argv列表写到既有attempt input_binding.execution_policy，再经同一EngineeringTools.execute调用同一个对象。begin_verification在effect前验证策略和合并后的原commands；归一化、enabled-tools、当前host授权、路径、OS sandbox、deadline、取消与进程drain均复用原链。没有裸进程、第二controller、ledger或语言专项规则。

## 持久化与恢复

策略与commands、当前input manifest、actual observed completion一起校验。current-result、父verify_project恢复和当前候选QA沿既有Store消费同一绑定。策略错配保留待调查，未来未知版本不执行；不得因新的实现存在就盲目重跑可能已有作用的旧操作。

独立审阅发现过真实迁移缺口：为历史没有该key的复合结果无条件追加execution_policy:null，会改变完整ToolObservation编码，使已observed、message未提交的旧父调用不能恢复。修复只在原binding实际含该key时投影；真实旧K1/SQLite夹具恢复为一条原编码message、幂等且不重跑。历史NULL语义与历史编码两者都保留。

另修复测试的冷启动前提：通过既有根的guarded mkdir自建审计目录，不依赖未跟踪目录预先存在。这个修复不改变产品策略。

## 验证证据与停止条件

生产链回归先RED：新Service裸失败管道实际0/passed；再GREEN：17/failed，同一Director收到失败后用合法配方得到0/passed。最终18项覆盖真实进程argv与effect前durable绑定、普通D/QA POSIX、required checks合并、QA冷读、历史NULL/旧表迁移、未知策略、取消drain、权限deadline及冷恢复；全新不存在审计根也重跑18项通过。相关selection/output-policy/recovery48项通过。

邻接212项通过，另一个既有ownership测试仅假设JSON后马上是budget，BASE同样失败；它已适配既有声明段，全部事实/origin断言保留，同文件2项通过。未修改生产outputhint。

独立初审P1/P2均保留原失败证据，定向复审已在真实旧夹具确认关闭。Ruff/格式/mypy与diff检查通过。最终全候选架构、KernelOne ALL、catalog baseline门禁及main集成状态应以同目录最终资格记录为准；不能用修复前门禁冒充最终候选证据。新冻结L1-10的真实运行仍未发生。

原始证据：主仓 `.polaris/audits/verification-pipeline-boundary-20261024/` 下的 `experiment-results.json`、`ROOT-implementation-ruling.json`、`implementation/`、`review-package/`、`independent-review/`、`final-change-review/`、`root-checks/final/`。合成夹具/模型替身的边界在各报告明确；它们不进入Bench成功数量或产品事实源。

## 已知边界

pipefail不处理 `; echo0`、显式吞错、nested shell掩盖或无意义检查；成功退出仍不等于用户目标验收，独立QA保留。未加入-e/-u、编译器输出regex、特例重试或超时增长。提前关闭消费者（如head）可能导致生产者SIGPIPE，不能一律忽略141；tail一般读取全输入。Bash是额外解释器依赖，不可用时明确失败，不能静默降级。

本修改关闭的是一个执行语义边界。PM阶段问题、反复尾部提示、输入绑定性能和资源收尾仍是各自实验/问题，不混入该补丁，不据此声称平台完成或优于其它Agent。

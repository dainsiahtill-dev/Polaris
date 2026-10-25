# 实例停止与资源结算边界

状态：组件修复已实现并通过下述验证；不是新冻结版本的产品交付证明。原版本 c859 的 L2-18 全量验收失败，冻结已按 ROOT-source-freeze-release.json 结束，目标候选205永久只读。

## 用户问题与证据

工程运行 b193d5a6e8774ee68f9fcbdd3b69d5cf completed/passed，独立 QA6814 approved。唯一一次正式 stop 返回 UNKNOWN / owner_stop RuntimeError；原后台和前台 PID 都已消失，端口释放，但登记保留旧 PID，last_stopped_at 空且没有 durable stop_progress。旧 UNKNOWN 不得重写。

证据：目标 `.polaris/audits/terminal-integrity/stop-unknown-audit.json`、准备目录 `attempt-1/stop-result.json`、`ROOT-post-stop-readonly.json`。实际异常文本未持久化，因此不能直接断言实际运行的异常发生在哪一行。

## 所有权与不变量

正式入口/CLI -> 既有 InstanceSupervisor -> 既有 InstanceRegistry/KFS -> 当前实例子进程。

工程终态与 quiescence 仍由 roles.runtime 的 public resource facet 决定；实例身份、进程关闭和结算只归现有 Supervisor/Registry。不得新增 controller、表或外部审计事实源，不得停止 main 或重放旧 stop。

稳定实例身份和允许的健康投影变化应分开判断。单纯 health metadata 更新不能让已授权的关闭失败；实例、workspace、runtime、端口、token、PID/birth 或启动身份变化必须阻止后续错误进程操作。成功结算必须有真实进程退出和资源释放，不以 metadata 修改代替。

## 反例与假设

假设：stop 当前把完整 metadata 当作身份，在子进程退出与最终写入之间，合法健康刷新也会触发 identity changed；确认的关闭进度因最终拒绝而未提交。源码支持这个解释，真实异常起点仍未知。

最小实验：既有 Registry/stop 调用中，在进程 drain 的可控屏障处由独立更新者只改健康字段，比较 stop 返回、进程作用与最终登记。替身进程实验不能证明真实 OS 关闭；之后增加有限真实并发实验。

推翻条件：仅健康字段更新不能在旧机制产生相同异常，或实际异常证据定位在另一条边界。此时先改解释，禁止按猜测删检查。

## 修改边界

仅 `polaris/cells/instances/internal/service.py` 及对应现有测试。优先使用现有 registry mutation_lock、身份检查和 engineering_stop_progress；公共 stop/settlement 输入输出保持不变。

候选机制：以稳定所有权检查保护副作用与最终提交，在既有锁下合并当前 metadata 和当前 owner 确认的生命周期事实，保留相邻写入者的健康字段。具体实现以最小反例确定，不把删除 metadata 检查视作充分修复。

## 验证与停止条件

旧机制反例红，修后绿；健康并发可推进，真实所有权更换仍拒绝且不误杀后续子进程；既有 partial-stop/cold recovery 与相邻 HTTP/CLI 测试通过。执行范围 Ruff、format、mypy、相关 pytest，必要 K1 release gate。有限真实并发实例实验与单测分开报告。

达到这些条件即结束该主题；ContextOS 淘汰、浏览器动作/验收覆盖和目标应用缺陷分别推进，不能塞进此修复。源码组件通过后仍需另起冻结原任务批次证明产品结果。

## 实验发现的相邻输入语义（2026-10-25）

首个真实进程夹具因未继承既有KERNELONE_HOME而在NATS所有权校验处失败，未进入stop；3次内建启动尝试及失败均保留。第二个独立夹具正确继承正式共享服务authority后成功启动，但在stop前置校验被拒绝：start_frontend=False、frontend_pid=None、frontend_port=5173。只读调用原validator已复现准确的engineering settlement scope rejected，未执行任何停止或修改登记。

同模块的资源判定需与现有“disabled、PID-less frontend不拥有监听端口”语义一致：只有启用前端或frontend_pid非None时，前端保留端口才参与拒绝；后端、main、自停、shared binding、namespace与实例身份保护不变。PID=0不能被当作显式None。先红后绿验证无前端实例可以推进，并验证启用/PID-bearing/零PID的保留端口仍被拒绝。

已运行的前一版组件证据：9条新参数化测试、190项相邻测试和独立审阅通过；KernelOne完整门禁修正测试临时目录准备后通过，最初88项setup错误保留。它们不自动证明本新增分支或真实进程并发通过，需各自完成验证。旧L2-18 UNKNOWN永不重写。

## 已实现与实际验证

当前实现仅将既有health投影的backend_health/frontend_health/status_reason与稳定身份分离；其余metadata及实例/启动字段仍受保护。最终读取、校验、合并、保存共用现有Registry锁。第二处修复只允许显式disabled且PID为None的前端不拥有端口；PID=0、启用或真实PID仍拒绝保留端口。

先后红绿证据均保留：health回归旧版4失败/5替换控制通过，修后9通过；disabled前端旧版1失败/3控制通过，修后4通过。最终相邻套件194通过，Ruff/format/mypy通过，最终KernelOne十二阶段368通过/1skip。Root重新执行9项健康测试通过；两次独立审查通过，留一处非阻断参数重复说明，4个health参数覆盖3种不同交错，不扩大为4个独立缺陷。

真实Registry与后台PID51632的关闭过程中有8次health字段写入完成于stop调用区间。最终后台退出、登记PID清空、last_stopped_at和confirmed backend progress落盘；主实例三PID birth不变。health输入是明确合成夹具，精确位于私有identity-read之后仍UNKNOWN；该后台在第二个guard修复前启动，最终停止控制器为最终指纹，不冒充源码全程冻结的完整产品验收。

Source组件接受记录：主仓`.polaris/audits/instance-stop-settlement-20261025/ROOT-component-acceptance.json`。该主题达到组件停止条件并进入集成；旧L2-18 UNKNOWN和严格交付失败不变，新冻结版本仍需真实工程运行。

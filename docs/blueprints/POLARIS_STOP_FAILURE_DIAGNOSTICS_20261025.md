# 停止失败诊断：保留第一处偏差

状态：已实现并通过源码资格验证，待后续新冻结项目消费；不代表历史停止失败已修复。修改前的固定六次视觉实验已结束，ROOT核对9557份源码、全部输入及12份原始捕获后才解冻，未热改实验。

## 问题与事实

L2-14后续运行真实退出2，停止确认UNKNOWN；事后只读确认物理资源已经关闭。原CLI仅保留RuntimeError类型，无法区分停止调用中异常与返回后确认异常。两个调用现有正式stop函数的有限合成反例在不同位置失败，却产生相同诊断。这是已证实的报告信息丢失；实际原生RuntimeError内部位置仍UNKNOWN。

最先偏差在 `delivery.cli.engineering_acceptance.stop` 的异常投影，不在工程事实或进程所有权。实例Supervisor继续拥有进程/Registry效果；现有工程资源查询继续拥有settlement准入；CLI只拥有其内部观察报告。

## 现有边界内的修改

```text
原binding / 原资源与身份检查
    -> 原SupervisorPort.stop_instance（最多一次）
    -> 原health与PID确认
    -> 原_record / KFS operations与report.json
    -> CLI输出及内部驱动cleanup
```

仅在既有异常报告增加 `failure_stage`、`stop_dispatch_started`、`stop_call_returned`、`error_code`。不新建Cell、控制器、表、账本、重试或fallback。普通STOPPED与UNKNOWN判定、权限、settlement参数、实际stop、health/probe数量及消费者 `harness_status == STOPPED` 条件不变。

| 阶段 | 覆盖的原操作 |
| --- | --- |
| recover_instance | 原实例恢复读取 |
| canonical_read | 原activity与工程resource读取 |
| admission_health | 停止前健康与身份校验 |
| settlement_prepare | 准备原settlement参数 |
| owner_stop | 唯一owner停止调用 |
| closure_health | 返回后的健康与身份校验 |
| closure_confirmation | 原PID探测与关闭谓词 |
| report_write | 正常或拒绝报告的原持久化 |

调用进度只描述本地调用边界：开始不证明已发信号，返回不证明物理关闭，也不是耐久effect receipt。参数在设置dispatch标记前准备；异常后不重放stop。冷读直接保留新增JSON字段，旧报告没有这些字段时仍未知，不补造迁移或改写历史。

错误码只按instances公开异常类型映射固定字面值：`instance_registry_corrupt`、`instance_registry_read_error`、`instance_registry_error`，其余 `unclassified_stop_failure`。不复制任意异常消息、URL、动态code或traceback。没有跨Cell私有依赖或新增graph边；当前目录审计零新增问题。不改变descriptor或恢复已退役准入。

## 假设、反证与实际验证

假设：保留有限阶段和调用进度足以区分前述两个故障边界，同时不改变原安全与推进语义。若基线新断言已通过、修后泄露私密信息、改变关闭判定、追加stop/探测、把返回当关闭，或BaseException被吞掉，就推翻候选方案。

实际先仅应用测试：12项因缺少failure_stage/error_code而失败，1项BaseException正控通过，无setup错误；再改生产者后13项通过。八阶段、效果后owner抛错、owner返回后确认抛错、私密字符串/URL持久化排除、伪造动态错误码、未知类型、BaseException和原有正常关闭均有组件证据。使用FakeSupervisor/内存效果替换真实停止，不证明物理关停。

ROOT从最终候选独立执行：

- CLI、实例服务/HTTP与工程资源相邻回归：201通过（其中完整CLI59项）。
- 架构检查40通过。
- KernelOne ALL十二阶段通过，实际测试368通过、1跳过。
- 目录审计159项历史问题、零新增、manifest mismatch零新增。
- 两个修改Python文件Ruff、format、mypy及diff检查通过。
- 独立审阅无Blocker/Important；另执行23个只替换effect ports的内存反例及无副作用公开导入检查。ROOT核对报告、原始RED日志和所有实现日志SHA。

以上不是总仓全部测试，也不是新原生项目或实际停止故障注入。前端未改变，未为此扩跑前端。一个Starlette/httpx既有弃用警告保留。

## 集成与停止条件

本主题达到接受标准：原信息丢失已由真实正式函数反例复现，新诊断字段区分各边界，原拒绝与正向关闭继续通过，消费者无须改判定，必要发布检查与独审通过。结束本诊断主题并集成主线；新项目仍须正式执行与资源结算，不把源码资格当交付。

精确修改范围为 `engineering_acceptance.py`、其现有CLI测试，以及本蓝图、verification card、研究报告与文档入口。没有目标项目修改、依赖安装、原生stop重放、旧收据重写或主实例重启。

## 已知边界与证据

binding读取及Supervisor构造仍在原try外，可能直接抛出；持续报告存储失败仍可能无法持久化诊断。原_record先写operation再写report，部分写失败可留下旧STOPPED operation后追加UNKNOWN；这是原有非事务边界，不能据旧operation单独判成功。本修改不另造总账掩盖它，也不能恢复旧异常内部栈。

- 原始有限反例、实现RED/GREEN及独审：`.polaris/audits/engineering-stop-diagnostics-20261025/`。
- ROOT各项实际命令、日志、退出码、源码pins：同目录 `ROOT-qualification/*/result.json`。
- ROOT资格结论与实际集成commit：同目录 `ROOT-acceptance.json`。
- [L2-14质量及六响应实验](../history/POLARIS_L214_LAYOUT_QA_EXPERIMENT_20261025.md)分别记录实际产物失败与未引入几何门禁的原因。

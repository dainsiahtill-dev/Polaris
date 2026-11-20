# 内部工程评估的固定样本比较

运行前从当前 catalog 选择互不重叠的开发、阈值校准及隐藏项目，每个项目只出现一次。两臂从新 workspace 开始，使用同一份 catalog、模型配置、权限、用户检查和时间预算。将两个源代码版本或同一版本的两组策略分别配置好，执行 `run_factory_bench.py arm-identity --host-config-file HOST.json` 记录每臂本地 Python 源码指纹、实际冻结 host 文件和服务器带默认值的有效策略摘要。提前选定运行顺序、独立审查人、完整契约审查规程及阈值，保存 UTF-8 JSON 计划；向评估负责人登记其 SHA-256。每臂使用新分配的 32 位小写 hex batch ID。沿用工程 Bench 通常的 Python 解释器和冻结配置参数。

计划 JSON 的 `schema_version` 为 `engineering.paired_plan.v1`。合同字段如下。

| 字段 | 值 |
|---|---|
| development、calibration、holdout | 三个非空 canonical project ID 数组，holdout 数组给出固定运行顺序 |
| reviewer、protocol_sha256 | 独立审查人身份，以及完整契约审查规程原始字节的 SHA-256 |
| arms | baseline 与 candidate 各自含 batch_id，以及 arm-identity 的完整输出 |
| frozen_inputs | catalog、host_config、llm_config 的文件 SHA-256，有显式用户检查时同时填写 checks |
| workflow、permission_profile_id | 与实际命令一致；采用 host 默认 profile 时后者写 null |
| confidence、max_release_error、noninferiority_margin | 同时单侧置信度、候选已放行作品中的最大错误率、相对基线允许的成功率下降量，均按比例填写 |
| max_cost_usd | 每项目所有尝试的正数美元预算 |

比较源代码改动时，两臂共享 host 原始文件，使用不同的源码指纹。比较单项 host 策略时，使用同一个源码版本，在计划添加 `host_treatment`，并对两臂执行 `arm-identity --host-config-file HOST.json --host-treatment FIELD`。可用 FIELD 为 max_output_bytes、max_tool_output_bytes、counterexample_refs 或 routing_record。此时 frozen_inputs 省略 host_config，计划改用每臂独立冻结的 host_config_sha256。两臂除处理项外的有效 host 策略摘要必须相同。routing_record 将路径及其校验值作为一个处理项，并核对实际文件内容。路由实验要求 direct 工作流。每个隐藏项目须保留终态事件和按 call_id 关联的 Director 物理请求；候选必须确实激活已登记的路由记录，并成功使用至少一个不同于该项目基线的物理 provider/model。离线 compare 会重新读取原始 snapshots 和 contexts，核对运行时保存的 SHA。移动汇总 JSON 时保留同目录的项目证据。缺少物理请求、只有同一绑定、记录未激活或证据漂移时，assignment_threshold_verdict 仍描述分组结果，路由效果资格保持 UNKNOWN。counterexample_refs 当前只作影子投影，其分组比较同样不能获得上线资格。启动器丢弃的环境覆盖不参与实验身份。

运行正式内部 harness 时附加 `--comparison-plan plan.json --comparison-arm baseline` 或 `--comparison-arm candidate`，按计划顺序指定 `--project-ids` 和对应的源代码及策略。第一项开始前，harness 会写入计划 SHA、固定批次和项目清单。显式失败提前停止与 dry-run 不参与注册执行。在同一个工作目录中复用已存在的 batch ID 会被拒绝。所有目标独立启动并保留原始报告。

普通执行的每条记录保留平台适配器原始 exit_code。bench_gate 综合终态放行观察、独立 catalog floor 和资源闭合。实际执行仅在所选项目的 bench_gate 全部 PASS 时以 0 退出；dry-run 单独标为 NOT_RUN。完整契约正确率在取得独立标签后由 compare 计算。

## 收集独立审查和账单

在两臂完成后，独立审查人员对每个项目按冻结规程核验完整契约，账单负责人核对该 run 的全部尝试。保存两套外部来源。目录结构为 `evidence/<batch_id>/<project_id>/review.json` 和同目录的 `bill.json`。两个文件都含 plan_sha256、batch_id、project_id、run_id。review.json 还包含以下字段。

- kind 固定为 external_full_contract_review。
- reviewer 和 protocol_sha256 对应计划中的审查人、规程。
- artifact_sha256 对应终态库存中按 path 排序的文件条目。通过 `run_factory_bench.py artifact-fingerprint summary.json PROJECT_ID` 查询，再核对审查人实际检查的候选。
- verdict 为 PASS 或 FAIL。审查不完整时保留 UNKNOWN，整项比较返回 UNKNOWN。

bill.json 的 kind 为 settled_billing，currency 为 USD，all_attempts 为 true，amount 为所有尝试实付总额。费用缺失时保留原始未知情况。审查人和账单负责人应保留外部原始凭据。评估器核对引用和摘要，来源真实性由独立取证方负责。不得把平台 QA、结构 floor、token 估价换算为独立完整契约标签或实付账单。

独立取证方使用 `run_factory_bench.py evidence-fingerprint evidence` 发布冻结包 SHA，再评估该固定包。

```sh
rtk proxy .venv/bin/python src/backend/scripts/factory_bench/run_factory_bench.py compare \
  --plan plan.json --baseline baseline/summary.json --candidate candidate/summary.json \
  --evidence-dir evidence --evidence-sha256 "$INDEPENDENT_EVIDENCE_SHA256" --output paired-result.json
```

退出码 0、1、2 分别表示 PASS、FAIL、UNKNOWN。输出文件独占创建。任何必需项目、账单或绑定证据缺失，整体资格保持 UNKNOWN。没有独立成功时，单位成功成本记为 null。

## 解读

成功交付要求同一项目有完整 OBSERVED_ACCEPTED 观察、当前候选独立 QA 批准、非空交付物和通过的真实命令收据，另须独立正确标签、成本及执行时间达标。风险分母保留平台原始放行决定；若其与 harness 观察矛盾，单列 incoherent_releases 并保留未知资格。报告同时列出单独的放行覆盖、独立正确率、全尝试实付总额、单位独立成功成本、单位成功交付成本、配对收益与损失。误放行风险只在候选已放行项目中计算。

在预先固定、项目对独立同分布的前提下，将总 alpha 一半分配给候选错误率的单侧 Clopper–Pearson 上界；其余部分均分给配对改善与退化的二项界，得到成功率差的保守下界。资格要求上界不高于风险阈值、下界不低于负的容许差。一次注册只作一次固定样本评价。共享项目来源、重复调参、隐藏项目泄漏或缺少独立标签都会限制总体解释。运行时和服务端环境超出本地源码及配置指纹的部分，应由实验负责人另行记录。

可执行的数据样例见同目录 `test_paired_evaluation.py` 的 `inputs`。

相同源码、策略、预算与冻结输入下的多次运行可按执行先后传给 `run_factory_bench.py repeatability batch1/summary.json batch2/summary.json`。该命令分别计数首次通过、至少一次通过和全部通过，以项目为分母。中断或缺失的观察保留 UNKNOWN，结果仅描述内部 Bench 门禁。

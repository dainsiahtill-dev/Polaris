# Director 资格路由

路由由冻结的 engineering host JSON 配置。默认使用现有角色绑定顺序。只有直连工程的 Director 请求参与选模。咨询、QA、取消、恢复准入和 terminal 由原有 owner 决定。

## 采集条件指纹

将两个 UTF-8 字节 `{}` 写入主机文件 `/srv/polaris/qualification.json`，把以下字段合并到现有 host 配置。该摘要对应无换行的两个字节。

```json
{
  "routing_record_file": "/srv/polaris/qualification.json",
  "routing_record_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
  "counterexample_refs": []
}
```

正常启动工程后，从 Director 的 `request_prepared.payload.binding_selection.conditions_sha256` 读取条件指纹。空记录只生成观察。指纹绑定工具 schema、权限、模型端点配置、上下文窗口、历史预算、输出限额、检查及 QA 要求。候选模型还必须具备与当前工作集配置一致的窗口。时间预算按每个请求的剩余秒数复核。

## 启用一个经评估的任务类别

由主机运维在独立评估后提供资格文件并固定其字节 SHA-256。顶层包含 `task_class`、允许的原始目标 SHA-256 列表 `goals`、`conditions_sha256`、`minimum_remaining_seconds`、`maximum_remaining_seconds`、`minimum_success_rate`、预先固定的 `projects` 和 `fixed_sample_size`，以及 `plan_sha256`、`reviewer`、`protocol_sha256`。

每个 `bindings` 元素指定 `provider_id`、`model` 和每项目一条 `trials`。每条试验包含 `run_id`、`project`、`artifact_sha256`、`project_outcome`、`budget_seconds`、`elapsed_seconds` 以及独立评审 `review` 和结算记录 `bill`。评审与账单必须同时绑定项目、运行和评估计划。评审还绑定协议、评审人、产物和条件指纹；账单绑定 provider/model 及所有尝试的 USD 总费用。完整可执行的合成样例位于 `tests/test_engineering_qualified_routing.py` 的 `_evidence()`。主机负责审查评审独立性、不可变模型版本、账单来源、预先登记的样本及其独立性；文件摘要用于固定这批输入。

首版仅支持固定样本全成功情形。运行时要求每个项目均获独立 PASS、已放行且在时间预算内，用 Bonferroni 校正后的单侧 95% 精确下界筛选允许的绑定，按每次成功交付的全尝试平均成本排序。缺少合格绑定时沿用原角色绑定顺序。`binding_selection` 记录资格摘要、范围指纹及排序；实际调用身份仍由最终 provider request 确认。隐藏评估期间固定 host 配置与资格文件。历史反例可通过 `counterexample_refs` 的 `session_id`、`event_seq` 显式引用，只进入影子审计。

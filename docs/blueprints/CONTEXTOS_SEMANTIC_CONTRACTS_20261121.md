# SemanticAdvice 与 NodeSummary Contracts（2026-11-21）

## 目的

为可选本地小模型提供两套不同、不可混用的只读契约：相关性/表示建议，以及单节点摘要。小模型不能执行工具、决定完成、解除保留或改变权限。

## SemanticAdvice v1

`validate_semantic_advice` / `parse_semantic_advice`：

- `assessments <= 8`，`need_evidence <= 4`
- `relation`：EDIT_RELATED / FAILURE_CANDIDATE / REQUIREMENT_CANDIDATE / BACKGROUND / UNKNOWN
- `suggested_view`：KEEP_CURRENT / FULL / SLICE / CARD / REFERENCE / ABSTAIN
- SLICE 至少一个 keep_slice_id；其它 view 不得携带切片
- node/slice/evidence refs 必须来自本次 CandidateRefs，禁止重复和越界引用
- reason、uncertain 必填；非法 JSON、额外字段、未知枚举均拒绝

## NodeSummary v1

`validate_node_summary`：

- 只针对请求的 node_id + snapshot_id
- summary_kind 必须为 `model_inference`
- coverage 必须与请求完全一致，不能用摘要扩大覆盖
- summary 受 max_summary_tokens 限制
- evidence_refs 只能来自请求白名单与 snapshot_id
- 不把摘要当成事实、通过或完成证明

## 验证

`test_engineering_semantic_advice.py`：5 个测试通过；ruff/mypy 通过。真实模型接入与 shadow 评测待提供地址后进行。

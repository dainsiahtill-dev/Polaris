# 工程宿主策略配对对照规程（预注册，2026-11-20）

治疗字段 `max_tool_output_bytes`；两臂**同源码**（source_fingerprint 一致），仅宿主策略不同。
计划：`~/.polaris/audits/reliable-engineering-08-10 20261120/treatment-plan.json`（见下）。

| 臂 | max_tool_output_bytes | host_config_sha256 | batch_id |
|---|---|---|---|
| baseline | 默认 64000 | 67c1da8d00602cf0f85218d8cc44503959d75f2b7a28c18f8494f35abbe6fda6 | f7492222cf3e4bfea6548d2817364140 |
| candidate | 262144 | 6633c35c3b10bcc50e7232746aa80f9b73863002656f5ef1fcc3622d9f9d9092 | d2f2b4fc0c1f42049e1caa75687fa648 |

阈值：confidence .95 / max_release_error .10 / noninferiority .15 / max_cost_usd 20（每项目）。
拆分：development=[L1-01,L1-04]；calibration=[L1-02,L1-03]；holdout=[L1-05,L1-06]（固定顺序）。

规程见 《工程配对评估规程》 与 `review-protocol.md`（sha256 6b97d174…）。
登记：计划 sha256 由评估负责人在运行前记录；两臂各用独立 32-hex batch。

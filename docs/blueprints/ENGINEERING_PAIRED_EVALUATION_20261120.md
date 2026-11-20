# 工程配对评估规程（预注册，2026-11-20）

计划：`~/.polaris/audits/reliable-engineering-20261120/paired-plan.json`（sha256 f3897d906427eed6af9853a68247815a46df05c091515ff7bbfc1db61a7e659b）。
审查规程与 L1-04 独立验收证据见同目录。

# 完整契约独立审查规程（预注册）

审查人：独立审查方（非候选臂作者）。本规程在运行前冻结，`protocol_sha256` 登记于计划。

## 输入（只读）
- 项目终态事件（`runtime.events.jsonl` 等价快照）与 SQLite `runs/events/tool_calls`。
- 按 call_id 关联的 Director 最终物理 provider 请求（ContextOS）。
- 候选文件清单与 SHA-256（`final-manifest.json`）。
- 平台验收投影（`project_outcome`、`acceptance_dimensions`）。

## 逐项核验（每项目）
1. **成功交付**：同一项目存在完整 `OBSERVED_ACCEPTED` 观察；候选 QA 批准；交付物非空；真实命令收据通过。
2. **身份/绑定**：run 与事件属于同一 project；候选 SHA 与清单一致；必跑命令保留。
3. **条款保真**：每个抽取条款有可解析证据句；fail/缺失即 FAIL。
4. **验收一致性**：平台放行决定与 harness 观察是否矛盾；矛盾记入 `incoherent_releases`。
5. **资源闭合**：`resources_closed=true`，无活跃 run 遗留。
6. **契约覆盖**：结构下限（编译/vet/测试）与需求点均被真实执行覆盖。

## 判定
- PASS：1–6 全部满足，证据齐全。
- FAIL：任一不满足，或证据不足且无法复核。
- UNKNOWN：关键证据缺失（如无物理请求、清单缺失），不得推断为 PASS。

## 计费
- 账单方核对每个 run 的全部尝试实付总额（USD），缺失保留 UNKNOWN，不得用平台 QA/结构 floor/token 估价替代。

## L1-04 独立验收结果（2026-11-20）

真实 run `50112d8304d045ee98a4bc3916fd02f4`（outcome=unknown；verification=passed rev70；QA 未出裁决）。
在独立副本 + 网络隔离沙箱对**冻结源码**执行 8 组场景，**8/8 PASS**（Q01–Q08）。
证据：`~/.polaris/audits/reliable-engineering-20261120/l1-04-independent/{final-manifest.json,independent-results.json,outputs/}`。
备注：交接的 `sandbox_run.py` argparse `REMAINDER` 会吞掉 `--stdin`，实际用等价的 `build_workspace_sandbox_argv` 直跑。

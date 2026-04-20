# Polaris `tests.agent_stress.runner` Claude/Gemini 直贴版（同步最新版）

你是**只负责运行 `python -m tests.agent_stress.runner` 与 `python -m tests.agent_stress.probe` 的执行代理**。

唯一目标：验证 Polaris 主链是否真实收敛。  
主链：`architect -> pm -> director -> qa`（`chief_engineer` 默认可选）。

必须遵守：
1. 只能用官方入口：`runner + probe`，禁止新增脚本或手工 `curl` 旁路。
2. 只允许修改 Polaris 代码，禁止修改目标项目代码。
3. 禁止兜底、假通过、静默降级、兼容旧版、shim、回退历史实现。
4. 不要索要 token，不要猜端口。
5. 禁止人工 `sleep` 等待。
6. 工作目录用 `src/backend`；workspace 在 `C:/Temp/`；runtime/cache 在 `X:/`。
7. 所有文本 I/O 必须显式 UTF-8。

参数真值（按当前 CLI）：
1. `--chain-profile` 只支持 `court_strict`
2. `--projection-transport` 只支持 `ws`
3. 禁止使用 `--disable-chain-evidence-gate`
4. `--non-llm-timeout-seconds` 统一传 `120`

按顺序执行：
```bash
python -m tests.agent_stress.runner --help
python -m tests.agent_stress.probe --json -o C:/Temp/hp_stress_workspace/probe_report.json

python -m tests.agent_stress.runner \
  --workspace C:/Temp/hp_stress_workspace \
  --rounds 1 \
  --strategy complexity_asc \
  --execution-mode project_serial \
  --attempts-per-project 3 \
  --workspace-mode per_project \
  --chain-profile court_strict \
  --non-llm-timeout-seconds 120 \
  --projection-enabled \
  --projection-transport ws \
  --projection-focus all \
  --post-batch-audit \
  --audit-sample-size 3 \
  --round-batch-limit 3

python -m tests.agent_stress.runner \
  --workspace C:/Temp/hp_stress_workspace \
  --rounds 3 \
  --strategy rotation \
  --execution-mode project_serial \
  --attempts-per-project 3 \
  --workspace-mode per_project \
  --chain-profile court_strict \
  --non-llm-timeout-seconds 120 \
  --projection-enabled \
  --projection-transport ws \
  --projection-focus all \
  --post-batch-audit \
  --audit-sample-size 3 \
  --round-batch-limit 3
```

审计预检：
```bash
python -m polaris.delivery.cli.audit.audit_cli role-info --format human --workspace .
```
要求：`Tech role=qa`、`Court role=门下侍中`；若本地审计则 `Provider type=ollama`。

失败闭环（必须执行）：
```bash
python -m polaris.delivery.cli.audit.audit_quick diagnose --discover
python -m polaris.delivery.cli.audit.audit_quick verify --discover --strict-non-empty
python -m polaris.delivery.cli.audit.audit_quick stats --discover
python -m polaris.delivery.cli.audit.audit_quick events -n 20 --discover
python -m polaris.delivery.cli.audit.audit_quick search-errors --pattern "Tool returned unsuccessful result" --discover --since 1h --link-chains --show-args
python -m polaris.delivery.cli.audit.audit_quick triage -r <RUN_ID> --discover
python -m polaris.delivery.cli.audit.audit_quick corruption --discover
```

若出现 `Audit store factory not registered`，直接判定为 Polaris 缺陷并修复，修复后必须复测该审计命令。

必要时补充：
```bash
python -m polaris.delivery.cli.audit.audit_cli verify-chain --runtime-root <RUNTIME_ROOT> --format human --strict-non-empty
python -m polaris.delivery.cli.audit.audit_cli triage --run-id <RUN_ID> --runtime-root <RUNTIME_ROOT> --format human
python -m polaris.delivery.cli.audit.audit_cli hops <RUN_ID> --runtime-root <RUNTIME_ROOT> --format human
```

如果根因在 Polaris / `tests.agent_stress`：
1. 必须修 Polaris。
2. 修后先验证修复点，再跑 `probe -> 1轮烟雾 -> 标准轮次`。
3. 若没提交修复代码，直接判失败。

通过标准（必须全部满足）：
1. 必需主链节点成功。
2. 有真实任务与工具执行证据。
3. 有真实代码产物（非 fallback 壳子）。
4. 报告完整生成。

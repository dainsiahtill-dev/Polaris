# Hybrid UI Automation (Playwright + Semantic + Computer Use + OmniParser + Airtest/SikuliX)

This document defines the runtime stack:

1. `Playwright` as the deterministic main path.
2. `Semantic` (`Stagehand` adapter) as the selector-break recovery layer.
3. `Computer Use` as the visual fallback.
4. `OmniParser` as structured visual parsing assist for vision tools.
5. `Airtest` / `SikuliX` as the no-DOM / occlusion fallback.

## Entry Commands

```bash
# main hybrid runner
npm run test:e2e:hybrid -- "打开设置面板并验证某字段可输入"

# semantic fallback direct run (Stagehand adapter)
npm run test:e2e:semantic -- --prompt "打开主界面并进入PM工作区"

# omniparser adapter direct run
npm run vision:omniparser -- --evidence-json .polaris/logs/<run>.evidence.json

# task file mode
npm run test:e2e:hybrid -- --task-file infrastructure/e2e/tasks/anthropic-model-input.task.json

# keep retrying until pass
npm run auto:fix:hybrid -- "打开设置面板并验证某字段可输入"

# OpenAI Computer Use adapter dry-run
npm run computer:openai -- --dry-run --prompt "打开设置面板并验证某字段可输入"
```

> 默认会阻断 LLM 相关测试任务，避免覆盖本地 LLM 配置与角色连线。  
> 如需显式放开，设置 `KERNELONE_E2E_ALLOW_LLM_TESTS=1`。

## Architecture

Execution order is fixed:

1. `playwright` stage:
`npm run test:e2e:task -- ...`
2. `semantic` stage:
default command is `run-stagehand-panel-task`, can be overridden by `--semantic-cmd` or `KERNELONE_HYBRID_SEMANTIC_CMD`.
3. `computer_use` stage:
default command is `openai-computer-use-adapter`, can be overridden by `KERNELONE_COMPUTER_USE_CMD`.
4. `omniparser` stage:
structured screenshot parsing assist. This stage is advisory and does not directly mark pass/fail recovery.
5. `airtest` stage:
run external command defined by `KERNELONE_AIRTEST_CMD` or config.
6. `sikulix` stage:
run external command defined by `KERNELONE_SIKULIX_CMD` or config.

The runner emits a UTF-8 JSON audit report with:

- `status`
- `workspace`
- `rounds`
- `pm_quality_history`
- `leakage_findings`
- `director_tool_audit`
- `issues_fixed`
- `acceptance_results`
- `evidence_paths`
- `next_risks`

## Configuration

Default config file:

`infrastructure/e2e/hybrid-automation.config.json`

Important fields:

- `semantic.command`
- `computer_use.command`
- `vision_fallback.omniparser.command`
- `vision_fallback.airtest.command`
- `vision_fallback.sikulix.command`

Command templates support placeholders:

- `{prompt}`
- `{task_file}`
- `{workspace}`
- `{round}`
- `{evidence_json}`
- `{evidence_paths}`
- `{last_screenshot}`
- `{omniparser_json}`

## Environment Variables

- Required for OpenAI adapter:
  - `OPENAI_API_KEY`
- Optional for semantic Stagehand adapter:
  - `KERNELONE_STAGEHAND_MODEL` (default `gpt-4.1-mini`)
  - `KERNELONE_STAGEHAND_START_URL`
  - `KERNELONE_STAGEHAND_HEADLESS`
  - `KERNELONE_STAGEHAND_VERIFY_CMD`
  - `KERNELONE_PANEL_SEMANTIC_FALLBACK` / `KERNELONE_PANEL_SEMANTIC_CMD` (for `test:e2e:task`)
- Optional for OpenAI adapter:
  - `OPENAI_BASE_URL`
  - `KERNELONE_COMPUTER_USE_MODEL`
  - `KERNELONE_COMPUTER_USE_START_URL`
  - `KERNELONE_COMPUTER_USE_MAX_STEPS`
  - `KERNELONE_COMPUTER_USE_HEADLESS`
- `KERNELONE_HYBRID_SEMANTIC_CMD`
- `KERNELONE_COMPUTER_USE_CMD`
- `KERNELONE_HYBRID_OMNIPARSER_CMD`
- `KERNELONE_AIRTEST_CMD`
- `KERNELONE_SIKULIX_CMD`
- `KERNELONE_HYBRID_UNTIL_PASS` (`1` or `0`)
- `KERNELONE_HYBRID_MAX_ROUNDS` (`0` means unlimited)
- `KERNELONE_HYBRID_PLAYWRIGHT_TIMEOUT_MS`
- `KERNELONE_HYBRID_SEMANTIC_TIMEOUT_MS`
- `KERNELONE_HYBRID_COMPUTER_USE_TIMEOUT_MS`
- `KERNELONE_HYBRID_OMNIPARSER_TIMEOUT_MS`
- `KERNELONE_HYBRID_VISION_TIMEOUT_MS`

## Practical Command Examples

### Semantic (Stagehand) fallback

```bash
set OPENAI_API_KEY=<your_key>
set KERNELONE_HYBRID_SEMANTIC_CMD=node infrastructure/scripts/run-stagehand-panel-task.mjs --prompt "{prompt}" --task-file "{task_file}" --evidence-json "{evidence_json}" --round {round}
```

### Computer Use fallback

```bash
set OPENAI_API_KEY=<your_key>
set KERNELONE_COMPUTER_USE_CMD=node infrastructure/scripts/openai-computer-use-adapter.mjs --prompt "{prompt}" --task-file "{task_file}" --evidence-json "{evidence_json}" --round {round}
```

### OmniParser assist

```bash
set KERNELONE_HYBRID_OMNIPARSER_CMD=node infrastructure/scripts/run-omniparser-adapter.mjs --prompt "{prompt}" --task-file "{task_file}" --evidence-json "{evidence_json}" --round {round} --output-json "{omniparser_json}"
# Optional external OmniParser engine (called by adapter)
set KERNELONE_OMNIPARSER_ENGINE_CMD=python tools/omniparser/run.py --image "{image_path}" --output "{output_json}"
```

### Airtest fallback

```bash
set KERNELONE_AIRTEST_CMD=airtest run scripts/vision_fallback.air --log .polaris/logs/airtest --recording "{workspace}/test-results/electron/airtest_{round}.mp4" --args "{omniparser_json}"
```

### SikuliX fallback

```bash
set KERNELONE_SIKULIX_CMD=java -jar C:/tools/sikulix/sikulixide.jar -r scripts/vision_fallback.sikuli -args "{prompt}" "{evidence_json}" "{round}" "{omniparser_json}"
```

## Notes

1. The hybrid runner ships a default OpenAI Computer Use adapter command in `infrastructure/e2e/hybrid-automation.config.json`.
2. You can still override to Anthropic/other providers through `KERNELONE_COMPUTER_USE_CMD`.
3. `run-omniparser-adapter` can run without external engine and emits fallback grid boxes when no engine is configured.
4. All produced report files are written with UTF-8.

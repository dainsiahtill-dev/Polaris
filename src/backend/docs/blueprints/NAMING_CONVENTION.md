# Polaris Blueprint 命名规范

> 版本: 1.0
> 生效日期: 2026-04-24

## 1. 目标

统一 `docs/blueprints/` 目录下所有蓝图文件的命名格式，使文件名本身即可传达：主题、日期、时效状态。

## 2. 强制格式

```
{主题}_{描述}_{日期}_{状态}.md
```

### 2.1 主题 (Theme)

| 主题代码 | 含义 | 示例 |
|----------|------|------|
| `CONTEXTOS` | ContextOS 相关 | `CONTEXTOS_2_0` |
| `TXKERNEL` | TransactionKernel 相关 | `TXKERNEL_P0_RUNTIME` |
| `AGENT_INSTR` | Agent Instruction 相关 | `AGENT_INSTR_ALIGNMENT` |
| `COG_LIFE` | Cognitive Lifeform 相关 | `COG_LIFE_HARDENING` |
| `BENCHMARK` | Benchmark / Sandbox 相关 | `BENCHMARK_SANDBOX` |
| `AGI` | AGI / Phase / Skeleton 相关 | `AGI_PHASE2_ARCH` |
| `CLI` | CLI 相关 | `CLI_SUPER_MODE` |
| `SESSION` | Session Orchestrator 相关 | `SESSION_DEAD_LOOP` |
| `KERNELONE` | KernelOne 相关 | `KERNELONE_REFACTOR` |
| `POLARIS` | 全系统级 | `POLARIS_AUDIT` |

### 2.2 日期

- 格式: `YYYYMMDD`
- 必须 8 位数字，无分隔符
- 示例: `20260424`

### 2.3 状态 (Status)

| 状态 | 含义 | 使用场景 |
|------|------|----------|
| `ACTIVE` | 当前执行依据 | 日期 >= 2026-04-15 或经人工确认为当前权威 |
| `DRAFT` | 草案 / 待评审 | 尚未定稿，不应用于执行 |
| `ARCHIVED` | 已归档 | 日期 <= 2026-03-31 或已被后续蓝图覆盖 |
| `DEPRECATED` | 已废弃 | 明确被取代，内容已过时 |

## 3. 命名示例

| 旧文件名 | 规范命名 |
|----------|----------|
| `CONTEXTOS_2_0_BLUEPRINT.md` | `CONTEXTOS_2_0_20260417_ACTIVE.md` |
| `BP-20260420-TXCTX-FULL-REMEDIATION.md` | `TXKERNEL_FULL_REMEDIATION_20260420_ACTIVE.md` |
| `AGENT_INSTRUCTION_ALIGNMENT_BLUEPRINT_20260416.md` | `AGENT_INSTR_ALIGNMENT_20260416_ACTIVE.md` |
| `COGNITIVE_LIFE_FORM_HARDENING_BLUEPRINT_20260415.md` | `COG_LIFE_HARDENING_20260415_ACTIVE.md` |
| `BENCHMARK_SANDBOX_HARDENING_BLUEPRINT_20260408.md` | `BENCHMARK_SANDBOX_HARDENING_20260408_ARCHIVED.md` |
| `CONTEXTOS_EXECUTION_PLAN_20260330.md` | `CONTEXTOS_EXECUTION_PLAN_20260330_ARCHIVED.md` |

## 4. 目录结构

```
docs/blueprints/
├── README.md                 # 本索引文件
├── NAMING_CONVENTION.md      # 本命名规范
├── archive/                  # 归档目录（STALE / DEPRECATED / ARCHIVED）
│   ├── benchmark/
│   ├── llm_tool_calling/
│   └── ...
├── benchmark/                # 按主题子目录（可选）
├── llm_tool_calling/
├── research/                 # 研究材料
└── [ACTIVE 蓝图直接放根目录]
```

## 5. 迁移规则

1. **新建蓝图**: 必须严格遵循本规范命名
2. **现有蓝图**: 在下次修改时逐步重命名，不强制一次性全量迁移
3. **归档操作**: 状态变为 `ARCHIVED` 或 `DEPRECATED` 时，使用 `git mv` 移至 `archive/` 目录
4. **索引更新**: 每次新增、归档或重命名蓝图后，同步更新 `README.md`

## 6. 例外

以下文件不受本规范约束：

- `README.md`
- `NAMING_CONVENTION.md`
- 子目录内的 `README.md`
- 纯数据文件（`.csv`, `.txt`, `.json` 等）

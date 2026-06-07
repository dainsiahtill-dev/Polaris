# KernelOne 死代码审计与分阶段收敛蓝图 (2026-06-07)

## 1. 背景与目标

承接角色债收敛之后，对 `src/backend/polaris/kernelone/`（底层运行时基座，1152 个 `.py`
文件，798 个非测试模块）做一次全量可达性审计，量化"建好但从未接线"的 aspirational /
leftover 代码，并给出**分阶段、可验证**的收敛计划。

## 2. 方法论（静态可达性，AST 解析）

1. 用 `ast` 解析全仓 `polaris/**` 的每个模块，构建 import 图：
   - `import a.b` → `a.b`
   - `from a.b import c` → 同时记 `a.b` 与 `a.b.c`（关键：捕获属性式子模块导入）
   - 相对导入 `from ..x import y` → 解析为绝对 `pkg.x` + `pkg.x.y`
2. **live roots** = 所有非 `kernelone`、非测试模块（delivery / cells / application /
   domain / bootstrap / infrastructure）。
3. 从 live roots 做 BFS 传递闭包；`kernelone` 中**不在闭包内**的模块即"生产不可达"。

## 3. 核心发现

- `kernelone` 非测试模块 **798** 个；live 可达 **401**；**生产不可达 397（~107k 行，约占该层一半）**。
- 死代码高度集中在 aspirational 子系统（按行数）：
  | 子系统 | 死模块 | ~行数 | 性质 |
  |---|---|---|---|
  | context（死子集） | 71 | 24763 | 多套并行 context 实现 |
  | benchmark / holographic | 59 | 18558 | 基准/"全息"回放，仅自引用 |
  | audit（死子集） | 30 | 8639 | omniscient 审计管线 |
  | llm（死子集） | 43 | 7914 | ⚠️ 含动态发现 handler，**误报风险高** |
  | multi_agent / neural_syndicate | 11 | 5842 | 多智能体编排 |
  | cognitive | 29 | 5796 | perception/reasoning/personality/evolution |
  | akashic | 9 | 5013 | 知识管线 |
  | role | 23 | 3042 | 与 cells/roles + recipe composer 并行 |
  | single_agent（死子集） | 13 | 2681 | 已部分清理 |
  | …（其余 ~30 子系统） | | | |

## 4. 风险分级与误报源（务必先验证再删）

- **动态发现假阳性**：`ToolHandlerRegistry.load_all()` 按目录遍历加载
  `llm/toolkit/executor/handlers/**`，这些 handler 经 `register_handlers()` 注册、
  无静态 import，会被本方法**误判为死**。`llm` 的 43 个"死模块"必须逐一排除此情况。
- **Port-adapter 基座**：`effect` / `locks` / `scheduler` / `ws` / `messages` 实现了
  `contracts.technical.master_types` 声明的 Port（"replace with Redis/NATS"），属"先建基座、
  尚未接线"。删除会改变架构意图（声明的 Port 失去唯一实现）→ **保留并标记，删除前需确认**。
- **测试覆盖**：部分死子系统自带 passing 测试（如 observability 3、performance 2）。
  删除即连带删除其测试——对生产死代码是正确的，但需在 commit 说明。

## 5. 分阶段收敛计划

### Wave 1（本蓝图执行）：纯 aspirational、零导入者孤岛
零生产 + 零 kernelone 导入者、非 Port-adapter、不受动态发现影响，且与已被批准删除的
`dynamic_role`/`subagent_runtime` 同构。7 个子系统 / 33 文件 / 5654 行：
`agent`、`dialogue`、`distributed`、`learning`、`reasoning`、`performance`、`observability`。
（三处对 `agent` 的引用经核实均为**指向已不存在路径的过时注释/日志串**，非真实 import。
`learning`/`reasoning` 的唯一外部测试是 `agent/tests/test_phase3_integration.py`，随 `agent` 一并删除。）

### Wave 2（后续，需逐子树验证）：动态发现排除后的 llm 死子集
对 `llm` 43 个候选逐一确认是否为 `register_handlers()` 动态加载；排除后删除真死部分。

### Wave 3（后续，需 ADR）：大型 aspirational 子系统
`cognitive` / `akashic` / `multi_agent` / `benchmark`(holographic) / `context` 死子集 /
`audit` omniscient 死子集。体量大、含测试、可能为路线图脚手架 → 每子树独立
Verification Card + ADR，确认非路线图后再删。

### Wave 4（后续，需确认）：Port-adapter 基座
`effect` / `locks` / `scheduler` / `ws` / `messages` + `kernelone/__init__.py` 中对应的
死 `__all__`/`_LAZY_MODULES` 条目。要么接线，要么连同声明的 Port 一并下线。

## 6. 验证门禁（每个 Wave）

`ruff check --fix` + `ruff format` + `mypy`（受影响面）+ `pytest`（相关 + import-fence
`test_kernelone_release_gates` + `pytest --collect-only` 无新增 error）。fail-closed。

# Tool Normalization 架构收敛蓝图

**版本**: v1.0
**日期**: 2026-03-30
**问题根因**: 工具参数归一化架构碎片化 — `contracts.py` 与 `TOOL_NORMALIZERS` 分离维护，新工具注册时只改 contracts 忘记改 normalizers

Status: Active
Owner: `kernelone.tools` + `kernelone.llm.toolkit`

---

## 1. 问题陈述

### 1.1 架构现状（2026-03-30 快照）

存在 **两套归一化系统** 和 **两套工具契约**：

| 文件 | 规模 | 角色 | 状态 |
|------|------|------|------|
| `polaris/kernelone/tools/contracts.py` | 1015 行 | 工具规范定义 (`_TOOL_SPECS`, 29 tools) | **活跃** |
| `polaris/kernelone/llm/toolkit/tool_normalization/__init__.py` | 57 行 | 归一化分发入口 (`normalize_tool_arguments`) | **活跃** |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/__init__.py` | ~50 行 | 归一化注册表 (`TOOL_NORMALIZERS`) | **活跃** |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_*.py` | ~900 行 | 14 个工具的归一化函数 | **部分缺失** |
| `polaris/kernelone/llm/toolkit/tool_normalization.py` | 1089 行 | **旧单体文件（死代码）** | 待删除 |
| `polaris/kernelone/llm/tools/normalizer.py` | 98 行 | 兼容 shim（调用 toolkit 版本） | 待清理 |

**分发架构**：
```
LLM 请求 (query/find/text)
    ↓
normalize_tool_arguments()  ← 路由入口
    ↓
TOOL_NORMALIZERS[tool_name]  ← 查表
    ↓
per-tool normalizer()  ← 实际归一化
```

### 1.2 故障链条

```
contracts.py 定义了 precision_edit ✓
    ↓
TOOL_NORMALIZERS 没有注册 precision_edit ✗  ← 根因
    ↓
normalize_tool_arguments() 跳过（无 normalizer）
    ↓
query/find/text 没有被映射到 search
    ↓
_validate_arguments() 失败: "Missing required parameter: search"
```

### 1.3 影响范围

**缺失 normalizer 注册的工具（15+）**：

| 工具 | 类别 | 备注 |
|------|------|------|
| `repo_tree` | read | |
| `repo_read_around` | read | |
| `repo_read_slice` | read | |
| `repo_read_head` | read | |
| `repo_read_tail` | read | |
| `repo_diff` | read | |
| `repo_map` | read | |
| `repo_symbols_index` | read | TS 依赖 |
| `treesitter_find_symbol` | read | TS 依赖 |
| `treesitter_replace_node` | write | TS 依赖 |
| `treesitter_insert_method` | write | TS 依赖 |
| `treesitter_rename_symbol` | write | TS 依赖 |
| `skill_manifest` | read | |
| `load_skill` | read | |
| `background_run` | exec | |
| `background_check` | exec | |
| `background_list` | read | |
| `background_cancel` | exec | |
| `background_wait` | exec | |
| `todo_read` | read | |
| `todo_write` | exec | |
| `task_create` | exec | |
| `task_update` | exec | |
| `task_ready` | read | |
| `compact_context` | exec | |
| `repo_apply_diff` | write | 有散落在旧文件的逻辑，无专属 normalizer |

---

## 2. 收敛目标

### 2.1 单事实来源原则

```
contracts.py (arg_aliases + arguments)  ← 单一规范来源
        ↓ 自动生成
TOOL_NORMALIZERS 注册 + per-tool normalizer
```

### 2.2 清理清单

- [ ] 删除 `polaris/kernelone/llm/toolkit/tool_normalization.py`（旧单体文件，死代码）
- [ ] 删除 `polaris/kernelone/llm/tools/normalizer.py`（兼容 shim，无独立逻辑）
- [ ] 将 `polaris/kernelone/llm/tools/contracts.py` 的 re-export 注释更新为明确废弃标记
- [ ] 所有 29 个工具都有对应 normalizer（通过生成或人工实现）

### 2.3 自动生成机制

新增 `polaris/kernelone/llm/toolkit/tool_normalization/generator.py`：

```python
def generate_normalizers_from_contracts() -> dict[str, str]:
    """从 contracts.py arg_aliases 自动生成 normalizer 代码。"""
    # 读取 _TOOL_SPECS
    # 对每个工具的 arg_aliases 生成标准化归一化函数
    # 输出: {tool_name: normalizer_code}
```

### 2.4 CI 门禁

新增门禁：任何 `contracts.py` 新增/修改工具时，自动检测 `TOOL_NORMALIZERS` 是否同步更新，未同步则 PR 失败。

---

## 3. 架构原则

### 3.1 工具规范分层

| 层 | 文件 | 职责 |
|----|------|------|
| **规范层** | `polaris/kernelone/tools/contracts.py` | 工具名、参数 schema、arg_aliases、类别 |
| **归一化层** | `normalizers/__init__.py` + `_*.py` | 参数名映射、类型转换、默认值填充 |
| **执行层** | `executor/core.py` + `handlers/` | 参数校验、handler 分发、结果返回 |

### 3.2 禁止事项

- 禁止在 normalizer 中硬编码 contracts.py 已有的别名映射
- 禁止在 executor 中绕过 `normalize_tool_arguments()` 直接读取 kwargs
- 禁止在 handler 中重复做参数归一化（已在 normalizer 层处理）

### 3.3 必需事项

- 每个 normalizer 函数必须从 `contracts.py` 的 `arg_aliases` 声明驱动
- 每个 normalizer 必须有对应单元测试（输入别名→输出规范参数）
- 新工具加入 contracts.py 时，必须同时在 normalizers/ 注册

---

## 4. 执行计划

### 4.1 Phase 0: 清理死代码（独立 PR）

**Engineer A**

**任务**：
1. 删除 `polaris/kernelone/llm/toolkit/tool_normalization.py`
2. 删除 `polaris/kernelone/llm/tools/normalizer.py`
3. 更新 `polaris/kernelone/llm/tools/contracts.py` 废弃注释（标注 re-export 来自 `polaris.kernelone.llm.contracts.tool`）
4. 检查所有 import 上述两个文件的代码，确保无遗漏

**验证**：
```bash
# 确认文件已删除
ls polaris/kernelone/llm/toolkit/tool_normalization.py  # 应报错
ls polaris/kernelone/llm/tools/normalizer.py              # 应报错

# 确认所有依赖仍正常
python -c "from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments; print('OK')"
python -c "from polaris.kernelone.llm.tools.normalizer import normalize_tool_calls"  # 应报错（已删除）
```

### 4.2 Phase 1: 搭建自动生成机制（独立 PR）

**Engineer B**

**任务**：
1. 创建 `polaris/kernelone/llm/toolkit/tool_normalization/generator.py`
2. 实现 `generate_normalizer_code(tool_name: str, spec: dict) -> str` 函数
3. 实现 `generate_all_normalizers() -> dict[str, str]` 函数
4. 在 `normalizers/__init__.py` 添加 `assert` 检查：每个 `TOOL_NORMALIZERS` 条目的 normalizer 函数参数与 contracts.py 一致

**输出文件**：
- `polaris/kernelone/llm/toolkit/tool_normalization/generator.py`

**验证**：
```bash
python -c "
from polaris.kernelone.llm.toolkit.tool_normalization.generator import generate_all_normalizers
generated = generate_all_normalizers()
missing = [t for t in generated if t not in __import__('polaris.kernelone.llm.toolkit.tool_normalization.normalizers', fromlist=['TOOL_NORMALIZERS']).TOOL_NORMALIZERS]
print(f'Missing normalizers: {missing}')
"
```

### 4.3 Phase 2: 补齐缺失 normalizer + 收敛 precision_edit（独立 PR）

**Engineer C**

**任务**：
1. 为所有缺失的 15+ 工具创建 normalizer（使用 generator.py 生成骨架）
2. 重点：`precision_edit`、`repo_apply_diff`、`repo_tree`、`repo_read_*` 等高频工具
3. 每个 normalizer 配套单元测试

**分批**：
- Batch A（高优）：`precision_edit`、`repo_apply_diff`、`repo_tree`、`repo_read_around`、`repo_read_slice`
- Batch B（次优）：剩余 read 工具
- Batch C（低优）：background_*、todo_*、task_*、TS 依赖工具

**验证**：
```bash
python -m pytest polaris/kernelone/llm/toolkit/tool_normalization/normalizers/ -v
```

### 4.4 Phase 3: CI 门禁（独立 PR）

**Engineer A + Engineer B**

**任务**：
1. 创建 `docs/governance/ci/scripts/run_tool_normalization_gate.py`
2. 检测 `contracts.py` 与 `TOOL_NORMALIZERS` 同步状态
3. 写入 `docs/governance/ci/fitness-rules.yaml`
4. 在 `pytest.ini` 或 CI pipeline 中添加 gate 触发

**验证**：
```bash
python docs/governance/ci/scripts/run_tool_normalization_gate.py --workspace . --mode sync-check
# 应输出: "All 29 tools have normalizers registered"
```

---

## 5. 验收标准

| 标准 | 验证方式 |
|------|----------|
| `tool_normalization.py` 和 `llm/tools/normalizer.py` 已删除 | `ls` 报错 |
| `TOOL_NORMALIZERS` 覆盖全部 29 个工具 | gate 脚本输出 |
| `precision_edit` 支持 `query`/`find`/`text`/`pattern` 别名 | 单元测试通过 |
| 每个 normalizer 有测试覆盖 | `pytest --cov` |
| contracts.py 与 normalizers/ 同步状态可被 CI 检测 | gate 脚本运行成功 |
| 无死代码残留（grep 无未使用 import） | `ruff check` |

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Phase 0 删除旧文件导致 import 报错 | 先统计所有引用，确认无遗漏再删除 |
| Phase 1 生成代码与手写逻辑不一致 | generator 输出与现有 normalizer 逐一对比 diff |
| Phase 2 大量工具同时修改冲突 | 按 Batch 分批提 PR，每个 Batch 独立 review |
| Phase 3 gate 误报（新增工具正当理由） | gate 支持 `--skip-tools` 白名单参数 |

---

## 7. 文件变更清单

### 删除

| 文件 | 原因 |
|------|------|
| `polaris/kernelone/llm/toolkit/tool_normalization.py` | 死代码，被 `normalizers/` 包替代 |
| `polaris/kernelone/llm/tools/normalizer.py` | 无独立逻辑，纯转发，已由 toolkit 版本替代 |

### 修改

| 文件 | 变更 |
|------|------|
| `polaris/kernelone/llm/tools/contracts.py` | 废弃注释补充 |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/__init__.py` | 注册全部 29 工具 |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_precision_edit.py` | 新增（本次 bugfix） |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_repo_apply_diff.py` | 新增（替换散落逻辑） |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_repo_tree.py` | 新增 |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_repo_read_*.py` | 新增（5个文件） |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_background_*.py` | 新增 |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_todo_*.py` | 新增 |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_task_*.py` | 新增 |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/_treesitter_*.py` | 新增（TS 依赖工具） |
| `polaris/kernelone/llm/toolkit/tool_normalization/generator.py` | 新增 |

### 新增

| 文件 | 用途 |
|------|------|
| `polaris/kernelone/llm/toolkit/tool_normalization/generator.py` | 从 contracts 自动生成 normalizer |
| `docs/governance/ci/scripts/run_tool_normalization_gate.py` | CI 同步门禁 |
| `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/tests/` | 各 normalizer 单元测试 |

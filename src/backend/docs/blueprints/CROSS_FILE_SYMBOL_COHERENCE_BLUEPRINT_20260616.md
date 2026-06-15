# Cross-File Symbol Coherence Blueprint (2026-06-16) — ② core fix

## 1. 现象（batch r2 实证）

弱 Director 把多文件脚手架被 attrition 一轮一文件孤立写就，文件**各自语法正确**但**互不对齐符号/模块约定** → 产物整体不可运行：

- **L3-16**(Python 包): `tetris/__init__.py` 写 `from tetris.constants import (... SRS_ROTATION_STATES ...)`，
  但 `tetris/constants.py` 没定义 `SRS_ROTATION_STATES` → `import tetris` 抛 `ImportError`。
- **L2-12**(JS): `main.js` 是 IIFE 引用全局 `Game`，而 `state.js/physics.js` 是 ES 模块(import/export)
  → 模块系统不一致，`Game` 永远未定义。

两者都是**跨文件接口漂移**：A 文件引用 B 文件未提供的符号/约定。

## 2. 现有机制与缺口

已有（不重复造）：
- `kernelone/quality/file_ownership_ledger.py` — 跨**父**同文件防覆盖（first-writer-wins + EDIT-on-prior）。
- `kernelone/quality/assembly_merger.py::validate_fill_assembly` — 单**文件**骨架+填充合并（task #49，11 处接入 `director_consumer`）。
- `kernelone/quality/artifact_quality.py::scan_workspace_artifact_quality` — 质量门(fail-closed,喂回 Director 自愈)。
  其 `_scan_typescript_imports` 已校验 JS/TS：相对导入的**文件**必须存在(`_relative_import_exists`)、裸导入必须是声明依赖。

**缺口**：
1. JS 具名导入只校验**文件存在**，不校验**符号被导出**(`import { Game } from './x'` 而 x 未 export Game)。
2. **完全不校验 Python 导入**(`from .constants import SRS_ROTATION_STATES` 而 constants 未定义它)。
3. 模块系统一致性(全局引用 vs ES 模块)未检测。

## 3. 设计：在质量门加「跨文件符号解析」校验（语言通用、AST/正则、无业务码）

扩展 `scan_workspace_artifact_quality`，新增 `_scan_cross_file_symbols(root_full)`：

### 3.1 Python（AST，确定性）
- 对每个 in-workspace `.py`，`ast.parse` 提取 `from <mod> import a,b,c`（仅相对/包内 mod，解析到 workspace 内文件）。
- 对目标模块 AST 收集**顶层导出名**：`ast.FunctionDef/ClassDef/Assign(targets)/AnnAssign/ImportedNames/__all__`。
- 若 `a` 不在目标模块顶层名集合 → `errors.append("unresolved import symbol 'a' from <mod> in <rel>")`。
- 跳过 `import *`、动态/条件导入边界（保守：不确定不报，避免假阳性——见风险§5）。

### 3.2 JS（正则 + 轻量导出扫描，复用现有 _IMPORT_SPECIFIER_RE 模式）
- 对 `import { a, b } from './x'`，扫描 x 的 `export`（`export const/function/class a`、`export { a }`、`export default`）。
- 具名导入未在 x 导出 → 同形 error。
- **模块系统一致性**：若 workspace 内既有 ES 模块文件（含顶层 import/export）又有引用未定义全局符号的经典脚本，
  且 HTML 入口未用 `type=module` 装配 → 报 `module-system inconsistency`（保守触发，仅当能确证未定义全局来自某 ES 模块的导出名）。

### 3.3 接入
- `_scan_cross_file_symbols` 的 errors 并入 `scan_workspace_artifact_quality` 返回 → 既有 fail-closed 修复回路把
  "符号 X 在 <mod> 未定义" 喂回 Director，迫其当轮补定义/对齐 → 自愈。
- 沿用 seed-file 豁免（§"failing unrelated seed files"）：只校验本批 declared/created 文件，不误伤既有种子。

## 4. 为何这是 ② 的治本

attrition 逐文件写就的根因是**没有强制的跨文件接口真相**。本校验把"接口对齐"变成 fail-closed 质量门的一部分：
任何文件引用了兄弟文件不提供的符号，落盘后立即被门拦截并喂回，Director 必须补齐才能过门——
等价于给多文件脚手架补上"冻结接口契约"的**校验侧**（生成侧的 skel-law 由 assembly_merger 负责，本蓝图补校验侧）。

## 5. 风险与边界

- **假阳性是头号风险**（误报会像 readme.md 大小写那样烧 turn）。缓解：保守——
  - 只校验能确定解析到 workspace 内的相对/包内导入；外部/动态/条件导入一律放行。
  - Python 顶层名集合必须包含 re-export（`from x import y` 也算 mod 的导出名）、`__all__`、`globals()` 赋值。
  - 不确定即不报（fail-open on ambiguity），宁可漏报也不假阳性。
- 语言特定但**非业务码**：与现有 JS import 校验同级，属通用质量规则，符合 CLAUDE.md §8。
- 性能：仅在质量扫描时跑一次 AST/正则，O(文件数)，可接受。

## 6. 验证

- 单测（无长跑）：构造 L3-16 缩影（`__init__` 导入 constants 未定义符号 → 报错；定义后 → 通过）+
  L2-12 缩影（具名导入未导出 → 报错）+ 假阳性护栏（动态导入/外部包/`__all__`/re-export 不误报）。
- 接入 `test_artifact_quality` 既有套件回归。
- 活体：下一批 L3-16/L2-12 的跨文件漂移被门拦截并自愈。

## 7. 实施门槛（依赖 batch C 数据）

本蓝图实现**待 batch C 数据确认**：若 C 显示 L3-16/L2-12 仍卡在跨文件漂移（预期），则本校验是 ② 治本的高杠杆点，立即实施；
若 C 显示别的主导根因，则重排优先级。遵循"先测后定投入"。

# Polaris 技术债务与架构问题综合审计报告

## 审计日期: 2026-04-21
## 审计团队: Principal Architect + 4 Senior Engineers
## 审计范围: polaris/ 全量代码库 (~2501 Python 文件)

---

## 执行摘要

| 审计类别 | BLOCKER | HIGH | MEDIUM | LOW | 总计 |
|---------|---------|------|--------|-----|------|
| **架构问题** | 4 | 2 | 3 | 2 | 11 |
| **代码重复** | 2 | 3 | 4 | 3 | 12 |
| **技术债务** | 1 | 3 | 12 | 9 | 25 |
| **契约违规** | 6 | 6 | 4 | 0 | 16 |
| **总计** | **13** | **14** | **23** | **14** | **64** |

---

## 🔴 BLOCKER 级问题（需立即处理）

### 1. 架构问题 (ARCH-001 ~ ARCH-004)

| ID | 问题 | 影响 |
|----|------|------|
| ARCH-001 | 5 个 Cell 目录存在但未在 cells.yaml 声明 | `compatibility/`, `values/`, `cognitive/knowledge_distiller/`, `director/taskling/`, `director/task_consumer/` |
| ARCH-002 | `factory.cognitive_runtime` 声明了不存在的路径 `polaris/bootstrap/cognitive_runtime/**` | Graph 与文件系统不一致 |
| ARCH-003 | 多个 Cell 声称拥有 `polaris/kernelone/`, `polaris/domain/`, `polaris/application/` 等外部目录 | Cell 边界模糊，违反 Cell First 原则 |
| ARCH-004 | 多个 Cell 依赖未声明的 Cell（`kernelone.process`, `kernelone.trace`, `infrastructure.db`） | 图谱与实际依赖不一致 |

### 2. 代码重复 (DUP-001, DUP-002)

| ID | 问题 | 影响 |
|----|------|------|
| DUP-001 | `director_logic` 模块在 4 个文件中 95% 相同代码复制 | planning/execution 细胞间逻辑未共享 |
| DUP-002 | `PMWorkflowResult` 和 `DirectorWorkflowResult` 在 workflow_activity 和 workflow_runtime 中重复定义 | 90% 相似度 |

### 3. 契约违规 (CONTRACT-001 ~ CONTRACT-006)

| ID | 问题 | 影响 |
|----|------|------|
| CONTRACT-001 | `roles.runtime` 直接导入 `roles.kernel.internal.development_workflow_runtime` | 违反 Public/Internal Fence |
| CONTRACT-002 | `roles.runtime` 导入 `transaction.phase_manager` 内部模块 | 应使用 `turn_contracts` 公开契约 |
| CONTRACT-003 | `roles.runtime` 导入 `transaction.intent_classifier` 内部模块 | 应使用公开契约 |
| CONTRACT-004 | `roles.runtime` 导入 `llm_caller.tool_helpers` 内部模块 | 应通过公开 LLM caller 契约 |
| CONTRACT-005 | `roles.runtime` 导入 `cognitive_gateway` 内部模块 | 应通过 Kernel facade 访问 |
| CONTRACT-006 | `roles.runtime` 导入 `VERIFICATION_TOOLS` 常量 | 应通过公开契约暴露 |

### 4. 技术债务 (DEBT-001)

| ID | 问题 | 影响 |
|----|------|------|
| DEBT-001 | `context_os.models` (v1) 被 42 处代码使用，已标记 deprecated | 涉及核心模块，迁移难度 XL |

---

## 🟠 HIGH 级问题（需尽快处理）

### 架构问题

| ID | 问题 |
|----|------|
| ARCH-005 | cells.yaml 与 cell.yaml 的 owned_paths 声明不一致 |
| ARCH-006 | `director.tasking` 和 `director.execution` 的 state_owners 重叠 |

### 代码重复

| ID | 问题 |
|----|------|
| DUP-003 | `_read_file_safe` 在 5 处重复实现 (~75%) |
| DUP-004 | Error 类在多个层级重复定义（domain vs kernelone） |
| DUP-005 | `_iter_json_candidates` 在 normalizer.py 和 response_parser.py 中 95% 重复 |

### 契约违规

| ID | 问题 |
|----|------|
| CONTRACT-007 | `roles.runtime` 导入 `roles.profile.internal.registry` |
| CONTRACT-009 | `llm.control_plane` 导入 `storage.layout.internal.layout_business` |
| CONTRACT-010 | `llm.control_plane.public.service` 导入 `storage.layout.internal` |
| CONTRACT-012 | `roles.kernel` 声明 `state_owners: []` 但实际执行 fs.write 操作 |
| CONTRACT-013 | `roles.runtime` 声明的 effects 与实际执行不符 |
| CONTRACT-015 | `roles.kernel.transaction` 缺少公开契约，导致级联违规 |

### 技术债务

| ID | 问题 |
|----|------|
| DEBT-002 | `domain/models/task` vs `domain/entities/task` 命名混乱 |
| DEBT-003 | `infrastructure.compat.io_utils` 被 46 处使用，已标记 deprecated |

---

## 🟡 MEDIUM 级问题（计划处理）

| ID | 类别 | 问题 |
|----|------|------|
| ARCH-007 | 架构 | `delivery.api_gateway` 拥有过宽的路径范围 |
| ARCH-008 | 架构 | `factory.cognitive_runtime` 跨越 5 个根目录 |
| DUP-006 | 重复 | `truncate_text` 在 4 处重复 (~70%) |
| DUP-007 | 重复 | `extract_json` 在 6 处重复 (~65%) |
| DUP-008 | 重复 | Result/Response 类超过 200 个，命名体系混乱 |
| DUP-009 | 重复 | `_coerce_positive_int` 在 2 个 workflow models 中重复 |
| CONTRACT-008 | 契约 | 测试代码导入其他 Cell 内部模块 |
| CONTRACT-011 | 契约 | `director.execution` 仍有向后兼容存根导入 `director.tasking.internal` |
| CONTRACT-014 | 契约 | `audit.verdict` 声明过宽的 state_owners 和 effects |
| DEBT-004 | 债务 | `domain/__init__.py` 同时导出 entities 和 models 的 Task 类 |
| DEBT-005 | 债务 | `BackpressureBuffer` deprecated，需迁移到 `AsyncBackpressureBuffer` |
| DEBT-006 | 债务 | `ScriptDirectorAdapter` deprecated |
| DEBT-007 | 债务 | `storage.policy` artifact lifecycle 函数 deprecated |
| DEBT-008 | 债务 | `RuntimeBackend` 别名 deprecated，应使用 `RuntimeBackendPort` |
| DEBT-021 | 债务 | 5 个 Cell 未在 cells.yaml 声明 |
| DEBT-022 | 债务 | KERNELONE_ (769处) vs KERNELONE_ (225处) 环境变量前缀混用 |
| DEBT-025 | 债务 | ContextOS v1/v2 模型混用 |

---

## 🟢 LOW 级问题（持续改进）

| ID | 类别 | 问题 |
|----|------|------|
| ARCH-009 | 架构 | `roles.host` 被列为未注册但实际已注册（AGENTS.md 注释过时） |
| ARCH-010 | 架构 | 4 个 director 子角色被列为未注册但实际已注册 |
| ARCH-011 | 架构 | `kernelone.tools` 只有 `__init__.py`，内容稀疏 |
| DUP-010 | 重复 | helpers.py 模块功能重叠 |
| DUP-011 | 重复 | `ConfigValidationError` 多处独立定义 |
| DUP-012 | 重复 | `compact_str` 在不同模块有不同实现 |
| DEBT-009~020 | 债务 | 各种 deprecated 模块和命名不一致问题 |

---

## 🎯 修复优先级建议

### Phase 0: 止血 (1-2 周)

1. **ARCH-001**: 为 5 个未声明 Cell 创建 cell.yaml 或合并到现有 Cell
2. **CONTRACT-001~006**: 为 `roles.kernel.transaction` 创建公开契约，修复 `roles.runtime` 的内部导入
3. **CONTRACT-012~013**: 审查并修正 `roles.kernel` 和 `roles.runtime` 的 state_owners 和 effects 声明

### Phase 1: 债务清偿 (2-4 周)

1. **DEBT-001**: 迁移 `context_os.models` (v1→v2) - 影响 42 处
2. **DEBT-003**: 迁移 `io_utils` 使用者到 canonical 模块 - 影响 46 处
3. **DUP-001**: 合并 `director_logic` 到 `polaris/domain/services/`
4. **DUP-002**: 合并 `workflow` 模型到 `polaris/domain/entities/`

### Phase 2: 架构对齐 (4-8 周)

1. **ARCH-003**: 重新划分 Cell 对 `polaris/kernelone/` 等目录的声称边界
2. **ARCH-004**: 声明缺失的 Cell（`kernelone.process`, `kernelone.trace` 等）或重构依赖
3. **DUP-004**: 统一 Error 类层级，确立 `KernelOneError` 作为唯一标准

### Phase 3: 持续优化 (长期)

1. **DEBT-022**: 统一环境变量前缀 (KERNELONE_ 为主)
2. **DUP-008**: 建立 Result 类命名规范
3. **DEBT-025**: 完全迁移到 ContextOS v2

---

## 📊 估算工作量

| 阶段 | 估计工时 | BLOCKER | HIGH |
|------|----------|---------|------|
| Phase 0 | 1-2 周 | 7 | 8 |
| Phase 1 | 2-4 周 | 2 | 5 |
| Phase 2 | 4-8 周 | 4 | 1 |
| Phase 3 | 持续 | 0 | 0 |

---

## 附录：审计证据文件

- `ARCH-001~011`: `docs/graph/catalog/cells.yaml`
- `DUP-001~012`: 各 Cell 源码文件
- `DEBT-001~025`: 源代码中的 `@deprecated` 标记和迁移文档
- `CONTRACT-001~016`: `polaris/cells/*/internal/` 和 `polaris/cells/*/public/` 模块

---

*本报告由 Principal Architect 团队审计生成*
*遵循 AGENTS.md 规范*

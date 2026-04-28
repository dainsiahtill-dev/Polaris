# Polaris 项目治理改进蓝图 v2.0 — 全量收口

**文档类型**: Architecture Blueprint  
**日期**: 2026-04-26  
**作者**: Polaris Principal Architect + 6 Expert Engineers  
**状态**: Phase 2 Execution

---

## 1. 已完成基线（v1.0）

- **161 文件已改**: 前后端隐喻全清零
- **51 测试通过**: 核心角色/审计/集成测试 green
- **ruff 全过**: 零 lint error
- **前端 grep**: 0 隐喻残留
- **后端 grep**: 0 隐喻残留

---

## 2. 剩余 Gap（v2.0 目标）

| 优先级 | Gap | 根因 | 验收标准 |
|--------|-----|------|----------|
| P0 | **pytest 62 collection errors** | 导入路径断裂、模块未找到、循环依赖 | `pytest --collect-only -q` 输出 `0 errors` |
| P0 | **前端 TypeScript 构建** | TS 类型可能因文本替换断裂 | `npm run build` 或 `tsc --noEmit` 通过 |
| P1 | **测试目录合并收尾** | 5 个文件冲突未解决 | `src/backend/tests/` 仅保留冲突文件或清空 |
| P1 | **MyPy cells/ 扩展** | 当前仅 delivery+kernelone+bootstrap | `mypy src/backend/polaris/cells --ignore-missing-imports` 通过 |
| P2 | **空 scaffold 清理** | 12+ 仅含 `__init__.py` 的目录 | 删除或填充 |
| P2 | **coverage 提升路径** | 当前 23.3%，目标 30% | CI 通过 `--cov-fail-under=30` |

---

## 3. 架构图（质量门禁流水线）

```
Developer Workspace
  │
  ├─ git commit
  │   ├─ pre-commit: ruff + ruff format
  │   └─ pre-commit: mypy (bootstrap + kernelone + delivery)
  │
  ├─ git push
  │   └─ CI: quality-gates.yml
  │       ├─ ruff-lint
  │       ├─ ruff-format
  │       ├─ mypy (strict for kernelone, standard for delivery)
  │       └─ pre-commit hooks
  │
  └─ Pull Request
      └─ CI: ci.yml
          ├─ pytest polaris/tests (with --cov-fail-under=23)
          ├─ pytest polaris/bootstrap
          ├─ pytest polaris/kernelone
          ├─ frontend: tsc + vitest
          ├─ integration: governance gates
          └─ metrics: coverage badge + history
```

---

## 4. 专家分工矩阵

| 专家 | 代号 | 职责域 | 核心任务 | 交付物 |
|------|------|--------|----------|--------|
| **Expert A** | Test Infrastructure Lead | 测试基础设施 | 修复 62 pytest collection errors | 0 errors, 测试可全量收集 |
| **Expert B** | Frontend Build Engineer | 前端构建 | 验证 TS 类型 + 修复构建断裂 | `tsc --noEmit` 通过 |
| **Expert C** | Directory Hygiene Specialist | 目录清理 | 解决测试合并冲突 + 清理空 scaffold | 统一测试根 + 无空目录 |
| **Expert D** | Type Safety Engineer | 类型安全 | MyPy 扩展至 cells/ + 修复类型错误 | mypy cells/ 通过 |
| **Expert E** | CI/CD DevOps | 持续集成 | 验证 CI workflow 语法 + 补充 missing gates | CI green on PR |
| **Expert F** | Integration Validator | 集成验证 | 扩大 pytest 验证范围 + 回归测试 | 200+ 测试通过 |

---

## 5. 核心数据流

### 5.1 测试收集修复流

```
pytest --collect-only
  ├── ERROR: ModuleNotFoundError → 修复 import 路径
  ├── ERROR: SyntaxError → 修复文本替换意外破坏的语法
  └── ERROR: CircularImport → 重构跨模块依赖
```

### 5.2 前端类型安全流

```
TypeScript Source
  ├── uiTerminology.ts (已改)
  ├── *.tsx 组件 (已改)
  └── tsc --noEmit
      ├── Type error → 修复类型推断
      └── Import error → 修正模块路径
```

---

## 6. 技术选型理由

1. **先修复 collection errors 再跑测试**: 62 errors 导致大量测试无法运行，必须先让 pytest 能收集到所有测试
2. **渐进式 MyPy**: cells/ 有 1167 个 Python 文件，一次性 strict 检查不现实，先用 `--ignore-missing-imports` 建立基线
3. **前端 tsc 优先于 npm build**: `tsc --noEmit` 更快，先确保类型安全再验证完整构建
4. **保留空 scaffold 的清理作为 P2**: 不影响功能，但降低仓库噪音

---

## 7. 验收门禁

| 门禁 | 命令 | 通过标准 |
|------|------|----------|
| 隐喻清零 | `grep -r "尚书令\|中书令\|工部" src/` | 0 matches |
| 测试收集 | `pytest --collect-only -q` | 0 errors |
| 后端单元 | `pytest src/backend/polaris/tests -q` | >100 passed |
| 前端类型 | `cd src/frontend && tsc --noEmit` | 0 errors |
| Ruff | `ruff check src/backend/polaris` | 0 errors |
| MyPy | `mypy src/backend/polaris/kernelone src/backend/polaris/delivery` | 0 errors |

---

*本蓝图作为 Phase 2 全量收口的权威指导，6 位专家将并行执行各自任务域。*

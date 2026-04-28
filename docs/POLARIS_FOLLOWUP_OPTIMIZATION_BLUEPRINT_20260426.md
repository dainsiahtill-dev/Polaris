# Polaris 后续优化蓝图 v3.0

**文档类型**: Follow-up Optimization Blueprint  
**日期**: 2026-04-26  
**作者**: Polaris Principal Architect + 6 Optimization Experts  
**状态**: Phase 3 Execution

---

## 1. 已完成基线（v2.0）

- **224 文件已改**: 隐喻清零 + 测试合并 + MyPy 扩展 + CI 加固
- **6,000+ 测试验证**: 5,102 单元 + 817 集成 green
- **67 预存在失败**: 已识别根因，非本 PR 引入
- **97 空 scaffold 目录**: 已定位
- **覆盖率**: 23.3%，门槛 23%

---

## 2. 优化目标矩阵

| 优先级 | 目标 | 根因 | 验收标准 | 指派专家 |
|--------|------|------|----------|----------|
| P1 | **修复 67 预存在测试失败** | audit 重构未完成、Windows 兼容、CB 隔离 | `pytest -q` 全量 passed | Expert G (Audit) + Expert H (Platform) |
| P2 | **清理 97 空 scaffold 目录** | 早期过度设计、空目录噪音 | 删除或填充所有仅含 `__init__.py` 的目录 | Expert I (Hygiene) |
| P2 | **覆盖率 23% → 30%** | 大量模块 0% 覆盖 | `--cov-fail-under=30` CI 通过 | Expert J (Coverage) |
| P2 | **治理规则自动化** | 36/61 fitness-rules 未执行 | 新增 5+ 自动门禁 | Expert K (Governance) |
| P3 | **Descriptor Pack 再生** | generated/ 与源码不同步 | 运行生成器后无 diff | Expert L (Build) |
| P3 | **前端构建修复** | PolarisTerminalRenderer 缺失 | `npm run build` 通过 | Expert L (Build) |

---

## 3. 专家分工

### Expert G: Audit Subsystem Fixer
**任务**: 修复 audit 子系统相关测试失败 (~30 个)
- `kernelone/audit/` 路径绝对化问题
- `evidence_paths.py` Windows 路径拒绝
- `compute_contract_fingerprint` 空字典哈希非空
- schema registry 拒绝 `priority="info"`
- LLMEvent `prompt_preview` 超长

### Expert H: Platform Compatibility Engineer
**任务**: 修复平台相关测试失败 (~20 个)
- `ce_consumer_cli.py` 缺少 `import asyncio`
- `workspace.integrity.public.service` lazy-load bug
- Windows 路径 `/` 操作符误用
- `echo` 二进制假设（Windows 内置）
- circuit breaker 状态泄漏

### Expert I: Directory Hygiene Specialist
**任务**: 清理 97 个空 scaffold 目录
- 删除仅含 `__init__.py` 的目录（确认无引用）
- 或填充最小实现使其不再为空
- 更新 `.gitignore` 防止再生

### Expert J: Coverage Engineer
**任务**: 覆盖率 23% → 30%
- 识别 0% 覆盖模块（delivery 155 个、cells 103 个）
- 为关键模块编写最小测试集
- 更新 `--cov-fail-under=30`
- 生成覆盖率报告 diff

### Expert K: Governance Automation Engineer
**任务**: 将 5+ fitness-rules 接入自动化
- 从 `docs/governance/ci/scripts/` 选取高价值脚本
- 接入 `.github/workflows/ci.yml`
- 生成 fitness-rules 执行率追踪 JSON

### Expert L: Build Engineer
**任务**: 构建系统修复
- 运行 `descriptor_pack_generator.py` 再生所有 pack
- 补充/修复 `PolarisTerminalRenderer` 模块
- 验证 `npm run build` 通过

---

## 4. 技术选型

1. **Audit 修复优先于 scaffold 清理**: 测试 green 是 CI 门禁的前提
2. **覆盖率不追求 100%**: 30% 是现实目标，聚焦高价值模块
3. **治理规则选高 ROI**: 优先接入 catalog/dependency 类门禁
4. **Descriptor 再生最后**: 依赖源码稳定，避免重复生成

---

## 5. 验收门禁

| 门禁 | 命令 | 通过标准 |
|------|------|----------|
| 全量测试 | `pytest -q` | >13,000 collected, 0 errors, <5 failures |
| 覆盖率 | `pytest --cov=polaris --cov-fail-under=30` | PASS |
| 空目录 | `find src/backend/polaris -type d -empty` | 0 results |
| Ruff | `ruff check src/backend/polaris` | 0 errors |
| MyPy | `mypy src/backend/polaris/cells --ignore-missing-imports` | 0 errors |
| 前端构建 | `cd src/frontend && npm run build` | PASS |
| 隐喻 | `grep -r "尚书令\|中书令\|工部尚书\|工部侍郎\|门下侍中" src/` | 0 matches |

---

*本蓝图指导 Phase 3 全量优化，6 位专家将并行执行各自任务域，最终回归验证。*

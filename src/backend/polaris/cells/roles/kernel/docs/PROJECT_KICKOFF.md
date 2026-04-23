# Polaris Kernel 测试修复项目启动文档

## 项目启动会 (2026-04-01)

---

## 1. 项目背景

经过前期重构，Polaris Kernel 引入了 **167个测试失败**（共822个测试）。这些失败需要系统性修复，以达到工业级代码质量标准。

### 当前状态
```
总测试数:     822
通过:         654 (79.6%)
失败:         167 (20.3%)
跳过:         1
错误:         2
```

### 目标状态
```
通过:         822 (100%)
失败:         0
Ruff警告:     0
Mypy错误:     0
行覆盖率:     >= 90%
```

---

## 2. 团队组织架构

```
项目经理
├── Tech Lead (技术负责人)
│   ├── Team Alpha (Alex + Bob) [P0] LLM调用层
│   ├── Team Beta (Carol + David) [P0] 流式工具循环
│   └── Team Gamma (Eve + Frank) [P0] Stream一致性
├── Senior Engineer
│   ├── Team Delta (Grace + Henry) [P1] TurnEngine核心
│   └── Team Epsilon (Ivy) [P1] Context压缩
├── Engineer
│   ├── Team Zeta (Jack) [P1] 兼容性
│   ├── Team Eta (Kate) [P2] 事务控制
│   ├── Team Theta (Leo) [P2] 输出契约
│   └── Team Iota (Mia) [P3] 解析器与指标
└── QA Lead (质量负责人)
```

---

## 3. 执行时间线

### Week 1: 快速胜利期
```
Day 1 (周二)
├── 09:00 项目启动会 (全员)
├── 10:00 团队分组环境配置
├── 14:00 第一轮开发
└── 17:00 日站会

Day 2 (周三)
├── Team Zeta 完成 (3 tests) ✓
├── Team Epsilon 进行中
└── 其他团队并行开发

Day 3 (周四)
├── Team Epsilon 完成 (5 tests) ✓
├── Team Iota 进行中
├── Team Eta 启动
└── Alpha/Beta/Gamma 深入开发

Day 4 (周五)
├── Team Iota 完成 (15 tests) ✓
├── Team Eta 完成 (7 tests) ✓
├── Team Theta 完成 (12 tests) ✓
└── Week 1 集成测试

Day 5 (周六)
├── Week 1 集成测试
├── 问题修复
└── Week 2 计划确认
```

### Week 2: 核心攻坚期
```
Day 6-8 (周一-周三)
├── Team Alpha 完成 (48 tests) ✓
├── Team Beta 完成 (17 tests) ✓
└── Team Delta 完成 (35 tests) ✓

Day 8-11 (周三-周六)
├── Team Gamma 完成 (15 tests) ✓ [最难]
└── 每日集成测试
```

### Week 3: 收尾验收期
```
Day 11-12: 最终集成 + 回归测试
Day 12-13: 性能优化
Day 13-14: 文档完善
Day 14: 最终验收
Day 15: 项目交付
```

---

## 4. 每日工作流程

### 09:00 - 站会 (30分钟)
**议程**:
1. 昨日完成 (每人1分钟)
2. 今日计划 (每人1分钟)
3. 阻塞问题 (需要协助的)
4. 风险升级

### 09:30 - 12:30 - 开发时段
- 按任务清单执行
- 每完成一个测试，本地验证
- 遇到阻塞立即在群聊求助

### 12:30 - 14:00 - 午休

### 14:00 - 17:30 - 开发 + 审查
- 继续开发
- 15:00 开始代码审查
- 审查 checklist:
  - [ ] 测试通过
  - [ ] Ruff通过
  - [ ] Mypy通过
  - [ ] 类型注解完整
  - [ ] Docstring完整

### 17:30 - 17:45 - 日总结
- 更新任务状态
- 提交当日代码
- 预告明日计划

---

## 5. 代码提交流程

### 分支策略
```
main
└── feature/test-fix-project
    ├── alpha/llm-caller-fixes
    ├── beta/stream-tool-loop
    ├── gamma/stream-parity
    ├── delta/turn-engine-core
    ├── epsilon/context-compaction
    ├── zeta/compat-methods
    ├── eta/transaction-controller
    ├── theta/output-contract
    └── iota/parser-metrics
```

### 提交信息规范
```
fix(tests): [team-alpha] 修复LLM调用超时配置测试

- 更新_resolve_timeout_seconds函数
- 添加director角色600s默认超时
- 添加非director角色60s默认超时
- 添加环境变量覆盖支持
- 修复类型注解

Tests: 4 passed
Relates: #23
```

### PR模板
```markdown
## 修复内容
- 修复了 XXX 测试失败

## 根因分析
XXX 函数重构后，接口发生变更...

## 修复方案
1. 更新 XXX 逻辑
2. 添加边界条件处理
3. 更新测试断言

## 测试验证
- [ ] pytest xxx.py -v (通过)
- [ ] ruff check . (通过)
- [ ] mypy --strict (通过)
- [ ] 行覆盖率 > 90%

## 影响范围
- 仅影响测试文件
- 无生产代码变更 / 有生产代码变更(详细说明)
```

---

## 6. 质量门禁

### 6.1 个人级门禁 (提交前必须)
```bash
# 1. 测试通过
pytest <test_file>.py -v

# 2. Ruff检查
ruff check . --fix
ruff format .

# 3. 类型检查
mypy <modified_file>.py --strict
```

### 6.2 团队级门禁 (合并前必须)
```bash
# 1. 全量测试
pytest polaris/cells/roles/kernel/tests/ -v --tb=short

# 2. 代码覆盖率
pytest --cov=polaris/cells/roles/kernel --cov-report=html

# 3. 静态分析
bandit -r polaris/cells/roles/kernel/
```

### 6.3 项目级门禁 (交付前必须)
- [ ] 822个测试 100% 通过
- [ ] Ruff 零警告
- [ ] Mypy 零错误
- [ ] 行覆盖率 >= 90%
- [ ] 性能不下降 (>5%)
- [ ] 所有文档更新

---

## 7. 风险与应对

| 风险 | 可能性 | 影响 | 应对措施 |
|------|--------|------|----------|
| Team Gamma (Stream一致性) 超时 | 中 | 高 | 已分配2人，Eve和Frank都是Senior，预留1天缓冲 |
| 接口变更影响其他模块 | 中 | 高 | 每修改一个接口，必须运行全量测试 |
| 测试相互依赖导致连锁失败 | 高 | 中 | 识别依赖链，按顺序修复；每日集成测试 |
| 关键人员请假 | 低 | 中 | 每Team有backup；代码审查确保知识共享 |
| 修复引入新bug | 中 | 高 | 严格的代码审查；回归测试覆盖 |
| 环境配置问题 | 中 | 低 | 第一天统一环境配置；文档化环境要求 |

---

## 8. 沟通协议

### 实时沟通
- **Slack/Teams**: #polaris-kernel-test-fix
- **紧急升级**: @channel 仅用于阻塞问题

### 文档更新
- **技术决策**: `docs/governance/decisions/adr-xxxx-test-fix.md`
- **进度更新**: 项目管理工具 (Jira/Linear)
- **每日报告**: 17:45 自动汇总

### 会议安排
- **站会**: 每日 09:00 (30 min)
- **周中审查**: Day 7 14:00 (2 hours)
- **周回顾**: Day 14 16:00 (1 hour)

---

## 9. 环境准备

### 开发环境
```bash
# 1. Python版本
cd src/backend
python --version  # >= 3.11

# 2. 安装依赖
pip install -e ".[dev]"

# 3. 验证环境
pytest polaris/cells/roles/kernel/tests/test_service_integration.py -v
```

### 工具配置
```bash
# Ruff配置 (pyproject.toml已配置)
ruff --version  # >= 0.1.0

# Mypy配置
mypy --version  # >= 1.0.0

# Pytest配置
pytest --version  # >= 7.0.0
```

---

## 10. 参考文档

### 项目文档
- `KERNELONE_KERNEL_TEST_FIX_PROJECT.md` - 项目总览
- `TEAM_ASSIGNMENTS.md` - 团队详细任务分配
- `TURN_ENGINE_BUG_ANALYSIS.md` - 历史Bug分析
- `TURN_ENGINE_DI_GUIDE.md` - 依赖注入指南
- `MIGRATION_KERNEL_SERVICES.md` - 服务迁移文档

### 代码位置
- 测试代码: `polaris/cells/roles/kernel/tests/`
- 内核核心: `polaris/cells/roles/kernel/internal/kernel/core.py`
- TurnEngine: `polaris/cells/roles/kernel/internal/turn_engine/`
- 服务层: `polaris/cells/roles/kernel/internal/services/`

---

## 11. 成功标准

### 硬性指标
- [ ] 822个测试 100% 通过
- [ ] Ruff 零警告
- [ ] Mypy 零错误
- [ ] 行覆盖率 >= 90%
- [ ] 性能基准不下降 (>5%)

### 软性指标
- [ ] 所有团队按时完成任务
- [ ] 代码审查 100% 完成
- [ ] 文档更新 100% 完成
- [ ] 零回归缺陷

### 团队士气
- [ ] 每日站会参与度 100%
- [ ] 阻塞问题平均解决时间 < 2小时
- [ ] 知识共享会议 >= 3次

---

## 12. 附录

### A. 快速命令参考
```bash
# 运行单个测试
pytest polaris/cells/roles/kernel/tests/test_llm_caller.py::TestResolveTimeoutSeconds::test_director_role_gets_600_seconds -v

# 运行团队相关测试
pytest polaris/cells/roles/kernel/tests/test_llm_caller.py -v --tb=short

# 代码检查
ruff check polaris/cells/roles/kernel/ --fix && ruff format polaris/cells/roles/kernel/

# 类型检查
mypy polaris/cells/roles/kernel/internal/llm_caller/ --strict

# 覆盖率
pytest polaris/cells/roles/kernel/tests/ --cov=polaris/cells/roles/kernel --cov-report=html
```

### B. 关键联系人
| 角色 | 姓名 | 联系方式 | 职责 |
|------|------|----------|------|
| 项目负责人 | TBD | @project-lead | 整体协调 |
| Tech Lead | TBD | @tech-lead | 技术决策 |
| QA负责人 | TBD | @qa-lead | 质量门禁 |
| 架构师 | TBD | @architect | 架构审查 |

### C. 升级路径
```
Level 1: 单个测试问题 → Team内部解决 (队长)
Level 2: 模块级阻塞 → 上报Tech Lead
Level 3: 架构冲突 → 召集架构委员会
Level 4: 资源/时间风险 → 上报项目负责人
```

---

**项目开始日期**: 2026-04-01
**项目结束日期**: 2026-04-15
**总工期**: 15天

**让我们开始吧！** 🚀

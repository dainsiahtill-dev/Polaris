# Polaris Kernel 测试修复项目 - 10人团队执行计划

## 1. 项目概览

### 当前状态
- **总测试数**: 822
- **通过**: 654
- **失败**: 167
- **跳过**: 1
- **错误**: 2
- **成功率**: 79.6%

### 目标
100% 测试通过率，零警告，符合工业级 Python 工程标准。

---

## 2. 失败测试分类分析

### 2.1 按模块分类

| 模块 | 失败数 | 优先级 | 复杂度 | 指派团队 |
|------|--------|--------|--------|----------|
| `test_llm_caller.py` | 48 | P0 | 高 | Team Alpha |
| `test_kernel_stream_tool_loop.py` | 16 | P0 | 高 | Team Beta |
| `test_run_stream_parity.py` | 7 | P0 | 高 | Team Gamma |
| `test_stream_parity.py` | 8 | P0 | 高 | Team Gamma |
| `test_turn_engine_*.py` (4 files) | 35 | P1 | 中 | Team Delta |
| `test_transcript_leak_guard.py` | 3 | P1 | 中 | Team Epsilon |
| `test_turn_engine_policy_convergence.py` | 2 | P1 | 中 | Team Epsilon |
| `test_turn_engine_compat_methods.py` | 2 | P1 | 低 | Team Zeta |
| `test_turn_engine_semantic_stages.py` | 10 | P1 | 中 | Team Eta |
| `test_transaction_controller.py` | 5 | P2 | 中 | Team Theta |
| `test_stream_visible_output_contract.py` | 8 | P2 | 中 | Team Theta |
| `test_pydantic_output_parser.py` | 4 | P2 | 低 | Team Iota |
| `test_llm_caller_text_fallback.py` | 3 | P2 | 低 | Team Iota |
| `test_metrics.py` | 7 | P3 | 低 | Team Iota |
| `test_integration_transactional_flow.py` | 1 | P1 | 高 | Team Beta |
| `test_kernel_prompt_builder_integration.py` | 1 | P2 | 低 | Team Zeta |
| `test_turn_phase_renderer.py` | 4 | P3 | 低 | Team Iota |
| `test_regression_kernel_context.py` | 2 | P2 | 中 | Team Theta |
| **其他零散测试** | 1 | P3 | 低 | Team Iota |

### 2.2 按根因分类

| 根因类别 | 估计数量 | 修复策略 |
|----------|----------|----------|
| 重构后接口变更 | ~60 | 更新测试Mock和调用方式 |
| 依赖注入未正确设置 | ~30 | 使用新的DI模式 |
| Stream/Non-stream行为差异 | ~25 | 统一执行路径 |
| 类型注解不匹配 | ~20 | 添加/修复类型提示 |
| 异常处理变更 | ~15 | 更新异常断言 |
| 配置/环境变量 | ~10 | 修复配置加载 |
| 其他 | ~7 | 个案分析 |

---

## 3. 10人团队任务分配

### Team Alpha - LLM调用层修复 (2人)
**队长**: Senior Backend Engineer
**成员**: Backend Engineer

**负责文件**:
- `test_llm_caller.py` (48 tests)

**核心问题**:
- LLM调用器重构后接口变更
- Timeout/Retry配置解析变更
- Native tool calling支持变化
- 错误分类逻辑调整

**交付物**:
1. 修复所有48个测试
2. 更新LLMCaller接口文档
3. 添加类型注解覆盖

**时间**: 3天

---

### Team Beta - 流式工具循环修复 (2人)
**队长**: Senior Backend Engineer
**成员**: Backend Engineer

**负责文件**:
- `test_kernel_stream_tool_loop.py` (16 tests)
- `test_integration_transactional_flow.py` (1 test)

**核心问题**:
- 工具执行后流式响应继续逻辑
- Native tool calls处理
- 重复工具循环安全检测
- 大工具结果压缩
- 代码块示例过滤

**交付物**:
1. 修复17个测试
2. 统一stream/run执行路径
3. 工具循环边界情况处理

**时间**: 3天

---

### Team Gamma - Stream/Non-stream一致性 (2人)
**队长**: Senior Backend Engineer
**成员**: Backend Engineer

**负责文件**:
- `test_run_stream_parity.py` (7 tests)
- `test_stream_parity.py` (8 tests)

**核心问题**:
- Stream和Non-stream模式输出不一致
- 多轮对话历史传递差异
- 错误恢复行为差异
- 工具调用序列差异

**交付物**:
1. 修复15个测试
2. 实现Stream-First架构
3. Non-stream作为stream包装器

**时间**: 4天 (最高复杂度)

---

### Team Delta - TurnEngine核心修复 (2人)
**队长**: Senior Backend Engineer
**成员**: Backend Engineer

**负责文件**:
- `test_turn_engine_semantic_stages.py` (10 tests)
- `test_turn_engine_enrichment.py`
- `test_turn_engine_event_contract.py`
- `test_turn_engine_thinking_persistence.py`

**核心问题**:
- TurnEngine语义阶段处理
- 内容清洗和工具包装器剥离
- Thinking内容持久化
- 事件契约变更

**交付物**:
1. 修复35个测试
2. 更新TurnEngine文档
3. 语义阶段流程图

**时间**: 3天

---

### Team Epsilon - Context压缩与安全 (1人)
**队长**: Backend Engineer

**负责文件**:
- `test_transcript_leak_guard.py` (3 tests)
- `test_turn_engine_policy_convergence.py` (2 tests)

**核心问题**:
- Context压缩阈值处理
- Summary策略消息注入
- 策略收敛检测

**交付物**:
1. 修复5个测试
2. 压缩算法优化
3. 安全边界文档

**时间**: 2天

---

### Team Zeta - 兼容性方法修复 (1人)
**队长**: Backend Engineer

**负责文件**:
- `test_turn_engine_compat_methods.py` (2 tests)
- `test_kernel_prompt_builder_integration.py` (1 test)

**核心问题**:
- 向后兼容方法
- Prompt构建器集成

**交付物**:
1. 修复3个测试
2. 兼容性层文档

**时间**: 1天

---

### Team Eta - 事务控制器修复 (1人)
**队长**: Backend Engineer

**负责文件**:
- `test_transaction_controller.py` (5 tests)
- `test_regression_kernel_context.py` (2 tests)

**核心问题**:
- 工作流交接逻辑
- 最终答案路径
- 工具失败恢复

**交付物**:
1. 修复7个测试
2. 事务流程文档

**时间**: 2天

---

### Team Theta - 流式输出契约 (1人)
**队长**: Backend Engineer

**负责文件**:
- `test_stream_visible_output_contract.py` (8 tests)
- `test_turn_phase_renderer.py` (4 tests)

**核心问题**:
- 可见内容净化
- Reasoning内容保留
- 增量输出格式

**交付物**:
1. 修复12个测试
2. 输出契约规范

**时间**: 2天

---

### Team Iota - 解析器与指标 (1人)
**队长**: Backend Engineer

**负责文件**:
- `test_pydantic_output_parser.py` (4 tests)
- `test_llm_caller_text_fallback.py` (3 tests)
- `test_metrics.py` (7 tests)
- 其他零散测试 (1 test)

**核心问题**:
- Pydantic解析器回退
- 文本工具调用提取
- 指标收集

**交付物**:
1. 修复15个测试
2. 解析器文档

**时间**: 2天

---

## 4. 执行时间表

```
Week 1
├── Day 1-2: 启动 + Team Zeta完成 (3 tests)
├── Day 2-3: Team Epsilon完成 (5 tests)
├── Day 3-4: Team Iota完成 (15 tests)
├── Day 3-5: Team Eta完成 (7 tests)
├── Day 3-5: Team Theta完成 (12 tests)
├── Day 5: 第一轮集成测试

Week 2
├── Day 6-8: Team Alpha完成 (48 tests)
├── Day 6-8: Team Beta完成 (17 tests)
├── Day 8-10: Team Delta完成 (35 tests)
├── Day 8-11: Team Gamma完成 (15 tests - 最难)

Week 3
├── Day 11-12: 最终集成 + 回归测试
├── Day 12-13: 性能优化
├── Day 13-14: 文档完善
├── Day 14: 最终验收
├── Day 15: 交付
```

---

## 5. 工程标准与质量门禁

### 5.1 代码规范 (Ruff)
```bash
ruff check . --fix
ruff format .
```
- 零容忍: 所有Error必须修复
- 警告: 尽量修复，特殊情况文档说明

### 5.2 类型安全 (Mypy)
```bash
mypy polaris/cells/roles/kernel/ --strict
```
- 新代码: 100%类型注解
- 旧代码: 修改部分必须添加类型注解
- 允许: `# type: ignore[xxx]` 但必须有注释说明

### 5.3 测试覆盖
```bash
pytest --cov=polaris/cells/roles/kernel --cov-report=html
```
- 目标: 行覆盖率 > 90%
- 关键路径: 100%分支覆盖

### 5.4 静态分析
```bash
bandit -r polaris/cells/roles/kernel/
pylint polaris/cells/roles/kernel/
```

---

## 6. 工作流程

### 6.1 日常流程
1. **早站会** (30 min): 同步进度、阻塞问题
2. **开发** (6-7 hours): 按任务清单执行
3. **代码审查** (1 hour): 同行审查
4. **晚总结** (15 min): 更新任务状态

### 6.2 提交规范
```
fix(tests): [team-alpha] 修复LLM调用超时测试

- 更新timeout解析逻辑
- 添加边界条件处理
- 修复类型注解

Tests: 48 passed
Closes: #xxx
```

### 6.3 审查清单
- [ ] 测试通过: `pytest <test_file> -v`
- [ ] Ruff通过: `ruff check .`
- [ ] 类型检查: `mypy <modified_file>`
- [ ] 文档更新: docstring完整
- [ ] 无重复代码
- [ ] 异常处理完善

---

## 7. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| Stream/Non-stream统一复杂度高 | 高 | 高 | Team Gamma分配2人，预留缓冲时间 |
| 接口变更影响其他模块 | 中 | 高 | 每修改一个接口，运行全量测试 |
| 测试相互依赖 | 中 | 中 | 识别依赖链，按顺序修复 |
| 人员请假 | 低 | 中 | 每个Team有backup熟悉代码 |
| 重构引入新bug | 中 | 高 | 严格的代码审查和回归测试 |

---

## 8. 沟通协议

### 8.1 升级路径
```
问题级别1 (单个测试失败) → Team内部解决
问题级别2 (模块级阻塞) → 队长上报项目负责人
问题级别3 (架构级冲突) → 召集技术委员会
```

### 8.2 文档位置
- 技术决策: `docs/governance/decisions/`
- API文档: `polaris/cells/roles/kernel/docs/`
- 进度跟踪: 项目管理工具

### 8.3 会议安排
- 每日站会: 09:00 (30 min)
- 周中审查: Day 7 (2 hours)
- 周回顾: Day 14 (1 hour)

---

## 9. 成功标准

### 9.1 硬性指标
- [ ] 822个测试 100% 通过
- [ ] Ruff 零警告
- [ ] Mypy 零错误
- [ ] 行覆盖率 >= 90%

### 9.2 软性指标
- [ ] 所有修复有根因分析文档
- [ ] 新增代码100%类型注解
- [ ] 关键函数有详细docstring
- [ ] 性能不下降 (>5%)

---

## 10. 附录

### A. 快速启动命令
```bash
# 1. 环境准备
cd src/backend
pip install -e ".[dev]"

# 2. 运行单个测试
pytest polaris/cells/roles/kernel/tests/test_llm_caller.py -v

# 3. 运行全量测试
pytest polaris/cells/roles/kernel/tests/ -v --tb=short

# 4. 代码检查
ruff check . --fix && ruff format .
mypy polaris/cells/roles/kernel/ --strict

# 5. 覆盖率
pytest --cov=polaris/cells/roles/kernel --cov-report=html
```

### B. 关键联系人
- 项目负责人: [待指定]
- 技术架构师: [待指定]
- QA负责人: [待指定]

### C. 参考文档
- `TURN_ENGINE_BUG_ANALYSIS.md`
- `TURN_ENGINE_DI_GUIDE.md`
- `MIGRATION_KERNEL_SERVICES.md`

---

*文档版本: 1.0*
*创建日期: 2026-04-01*
*最后更新: 2026-04-01*

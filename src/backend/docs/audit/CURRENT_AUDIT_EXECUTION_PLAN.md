# ContextOS & TurnEngine 代码审计执行计划

**执行日期**: 2026-04-12  
**审计周期**: 4周 (Phase 1-2)  
**目标**: 识别架构缺陷、性能瓶颈、可靠性风险  

---

## 第一周执行计划 (Week 1)

### Day 1 (周一) - 团队组建与分工

**上午 09:00-12:00: 启动会议**

| 时间 | 议程 | 产出 |
|------|------|------|
| 09:00-09:30 | 审计目标说明 | 统一理解 |
| 09:30-10:30 | 代码库结构介绍 | 导航图 |
| 10:30-12:00 | 分组分工 | 责任矩阵 |

**分组安排**:

```
A组 - ContextOS核心 (2人)
  ├─ 负责人: [待指派]
  └─ 任务: models.py, runtime.py 审计

B组 - TurnEngine执行 (2人)
  ├─ 负责人: [待指派]
  └─ 任务: engine.py, tool_loop_controller.py 审计

C组 - 断路器系统 (2人)
  ├─ 负责人: [待指派]
  └─ 任务: recovery_state_machine.py, metrics.py 审计

D组 - 集成测试 (2人)
  ├─ 负责人: [待指派]
  └─ 任务: 端到端场景测试

E组 - 架构审查 (2人)
  ├─ 负责人: [待指派]
  └─ 任务: 组件边界、依赖关系审查
```

**下午 14:00-18:00: 环境准备**
- [ ] 代码仓库拉取最新代码
- [ ] 审计工具配置 (ruff, mypy, pylint)
- [ ] 测试环境搭建
- [ ] 日志样本获取

---

### Day 2-3 (周二-周三) - 深度代码审计

**各组并行执行审计任务**

#### A组: ContextOS审计清单

**文件**: `polaris/kernelone/context/context_os/models.py`

| 检查项 | 方法 | 预期结果 |
|--------|------|----------|
| compress()逻辑 | 代码走读 | 识别边界情况 |
| Turn-Block保护 | 构造测试用例 | 验证当前Turn完整性 |
| 紧急压缩触发 | 模拟60事件场景 | 观察压缩行为 |
| source_turns处理 | grep source_turns | 追踪使用位置 |

**审计问题模板**:
```markdown
### [A-XXX] 问题标题
- **位置**: 文件:行号
- **严重程度**: P0/P1/P2
- **问题描述**: 
- **复现步骤**:
- **建议修复**:
- **影响范围**:
```

**文件**: `polaris/kernelone/context/chunks/assembler.py`

| 检查项 | 方法 | 预期结果 |
|--------|------|----------|
| 意图切换检测 | 代码走读 | 动词列表覆盖度 |
| 摘要生成 | 构造测试用例 | 关键信息保留 |
| 中英文混合 | 检查动词列表 | 完整性评估 |

#### B组: TurnEngine审计清单

**文件**: `polaris/cells/roles/kernel/internal/turn_engine/engine.py`

| 检查项 | 方法 | 预期结果 |
|--------|------|----------|
| run()主循环 | 代码走读 | 识别递归风险 |
| Circuit Breaker集成 | grep ToolLoopCircuitBreakerError | 异常处理完整性 |
| Stream/Non-Stream | 对比两个方法 | 路径差异识别 |
| iteration传递 | grep iteration | 追踪传递链 |

**关键代码审查**:
```python
# 重点审查区域
1. _build_run_result() - 结果构建逻辑
2. while True: 循环 - 终止条件
3. _controller.append_tool_result() - 异常处理
```

**文件**: `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

| 检查项 | 方法 | 预期结果 |
|--------|------|----------|
| 断路器阈值 | 检查常量定义 | 合理性评估 |
| _track_successful_call() | 代码走读 | 计数准确性 |
| _detect_cross_tool_loop() | 构造ABAB测试 | 模式检测有效性 |
| _validate_thinking_compliance() | 构造测试用例 | 格式校验准确性 |

#### C组: 断路器与恢复系统审计

**文件**: `polaris/cells/roles/kernel/internal/recovery_state_machine.py`

| 检查项 | 方法 | 预期结果 |
|--------|------|----------|
| 状态机完整性 | 绘制状态图 | 缺失状态识别 |
| 恢复提示生成 | 代码走读 | 提示有效性评估 |
| 成功检测逻辑 | 构造测试用例 | 检测准确性 |

**文件**: `polaris/cells/roles/kernel/internal/metrics.py`

| 检查项 | 方法 | 预期结果 |
|--------|------|----------|
| 指标覆盖度 | 检查DeadLoopMetrics | 完整性评估 |
| 指标调用点 | grep get_dead_loop_metrics | 调用位置追踪 |

#### D组: 集成测试审计

**测试执行**:
```bash
# 执行现有测试
pytest polaris/cells/roles/kernel/internal/tests/ -v

# 检查测试覆盖率
pytest --cov=polaris/cells/roles/kernel/ --cov-report=html
```

**端到端场景**:
1. 构造死循环场景，验证断路器
2. 构造高迭代场景，观察行为
3. 构造意图切换场景，验证摘要

#### E组: 架构审查

**依赖关系分析**:
```bash
# 生成依赖图
pyreverse polaris/kernelone/context/context_os/ -o png
pyreverse polaris/cells/roles/kernel/internal/turn_engine/ -o png
```

**检查项**:
- [ ] 循环依赖检测
- [ ] 跨层调用审查
- [ ] 接口契约一致性
- [ ] 错误传播路径

---

### Day 4 (周四) - 问题汇总与评审

**上午 09:00-12:00: 分组汇报**

各组汇报发现 (每组30分钟):
- 发现的问题清单
- 测试用例结果
- 风险等级评估

**汇报模板**:
```markdown
## [组名] 审计汇报

### 审计范围
- 文件: xxx
- 代码行数: xxx
- 审计用时: xxx

### 发现问题 (按严重程度排序)

#### P0 - 严重
1. [问题描述]
   - 位置: 
   - 影响: 
   - 修复建议: 

#### P1 - 中等
...

#### P2 - 轻微
...

### 测试覆盖
- 新增测试: X个
- 通过: X个
- 失败: X个

### 架构建议
...
```

**下午 14:00-18:00: 问题评审会**

评审委员会 (5人):
- 各组组长
- 首席架构师

**评审议程**:
1. 问题去重与合并
2. 优先级确认
3. 修复责任人指派
4. 修复时间表制定

---

### Day 5 (周五) - 修复计划与第一周总结

**上午 09:00-12:00: 修复计划制定**

| 问题ID | 描述 | 负责人 | 截止日期 | 状态 |
|--------|------|--------|----------|------|
| CB-001 | Tool Call iteration=None | T-03 | Day 3 | ✅ 已修复 |
| CB-002 | breaker_type=unknown | T-03 | Day 3 | ✅ 已修复 |
| CB-003 | 断路器误报（same_tool）| T-03 | Day 5 | 实施中 |
| CB-004 | 断路器误报（stagnation）| T-03 | Day 5 | 实施中 |
| CB-005 | 断路器误报（cross_tool）| T-03 | Day 5 | 实施中 |
| CB-006 | Recovery write_tools 不完整 | C-02 | Day 4 | 待修复 |
| ... | ... | ... | ... | ... |

**下午 14:00-16:00: 第一周总结报告**

**报告内容**:
1. 审计范围统计
2. 问题汇总 (P0/P1/P2计数)
3. 修复计划时间表
4. 第二周工作计划

---

## 第二周执行计划 (Week 2)

### Day 6-9 (周一-周四) - 问题修复

**并行修复流程**:

```
Day 6: 修复启动
  ├─ 各组领取分配的问题
  ├─ 创建修复分支
  └─ 编写修复代码

Day 7-8: 修复实施
  ├─ 编码实现
  ├─ 单元测试编写
  └─ 自测验证

Day 9: 修复评审
  ├─ 组内代码审查
  ├─ 跨组评审 (关键修复)
  └─ 测试验证
```

**修复标准**:
- [ ] 代码通过 ruff + mypy
- [ ] 单元测试通过率 100%
- [ ] 修复有对应的测试用例
- [ ] 文档更新 (如需要)

### Day 10 (周五) - 修复验收与回归测试

**上午 09:00-12:00: 修复验收**

验收检查表:
- [ ] 所有P0问题已修复
- [ ] 代码审查通过
- [ ] 测试覆盖率未下降

**下午 14:00-18:00: 回归测试**

```bash
# 全量测试
pytest polaris/cells/roles/kernel/ -v --tb=short

# 性能基准
pytest benchmarks/ -v

# 集成测试
python scripts/run_integration_tests.py
```

---

## 第三-四周执行计划 (Week 3-4)

### Week 3: 增强测试与混沌测试

**新增测试覆盖**:
- 边界条件测试
- 并发场景测试
- 故障注入测试

**混沌测试场景**:
1. 模拟断路器触发后LLM无视提示
2. 模拟快速意图切换 (3秒内2次)
3. 模拟序列号溢出
4. 模拟压缩异常大数据

### Week 4: 最终验证与报告

**最终验证**:
- 全量测试通过
- 性能基准达标
- 架构审查通过

**审计报告内容**:
1. 执行摘要
2. 发现问题详细分析
3. 修复方案与验证
4. 架构改进建议
5. 后续工作计划

---

## 会议安排

### 每日站会 (Daily Standup)

**时间**: 09:00-09:15  
**参与**: 全员  
**形式**: 轮流回答:
1. 昨天做了什么？
2. 今天计划做什么？
3. 有什么阻碍？

### 周中评审 (Wednesday Review)

**时间**: 周三 16:00-17:00  
**参与**: 各组组长 + 架构师  
**议程**:
- 各组进度汇报
- 问题协调
- 资源调配

### 周总结会 (Friday Summary)

**时间**: 周五 16:00-18:00  
**参与**: 全员  
**议程**:
- 本周成果展示
- 问题评审
- 下周计划确认

---

## 产出物清单

### Week 1 产出
- [ ] 代码审计报告 (5份)
- [ ] 问题清单 (统一格式)
- [ ] 测试用例 (新增)
- [ ] 架构依赖图

### Week 2 产出
- [ ] 修复PR (X个)
- [ ] 单元测试 (新增)
- [ ] 回归测试报告
- [ ] 修复验证文档

### Week 3-4 产出
- [ ] 混沌测试报告
- [ ] 性能基准报告
- [ ] 最终审计报告
- [ ] 架构改进建议书

---

## 成功标准

### 审计阶段 (Week 1)
- ✅ 5个关键文件完成审计
- ✅ 问题发现率 ≥ 10个/人天
- ✅ 测试覆盖率数据基线

### 修复阶段 (Week 2)
- ✅ 100% P0问题修复
- ✅ ≥80% P1问题修复
- ✅ 全量测试通过

### 验证阶段 (Week 3-4)
- ✅ 新增测试 ≥50个
- ✅ 混沌测试通过率 ≥90%
- ✅ 性能无回归

---

## 附录: 审计工具配置

### ruff 配置
```toml
[tool.ruff]
line-length = 100
target-version = "py310"
select = ["E", "F", "W", "I", "N", "D", "UP", "B", "C4", "SIM"]
ignore = ["D100", "D104"]
```

### mypy 配置
```toml
[tool.mypy]
python_version = "3.10"
strict = true
warn_return_any = true
warn_unused_configs = true
```

### 审计脚本
```bash
#!/bin/bash
# run_audit.sh

echo "=== 代码风格检查 ==="
ruff check polaris/kernelone/context/ polaris/cells/roles/kernel/

echo "=== 类型检查 ==="
mypy polaris/kernelone/context/context_os/ --ignore-missing-imports
mypy polaris/cells/roles/kernel/internal/turn_engine/ --ignore-missing-imports

echo "=== 测试执行 ==="
pytest polaris/cells/roles/kernel/internal/tests/ -v --tb=short

echo "=== 覆盖率报告 ==="
pytest --cov=polaris.kernelone.context --cov=polaris.cells.roles.kernel --cov-report=term-missing
```

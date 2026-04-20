# INCIDENT-2026-03-11: 应急补救机制掩盖LLM质量问题

## 事件摘要

**时间**: 2026-03-11
**严重级别**: 高
**影响**: 压测频繁"卡住"，用户体验极差
**状态**: 已修复

## 现象

用户在运行压测时反复报告：
- "Director 阶段卡住不动"
- "每次都在 quality_gate 卡住"
- "看起来像是锁死"

## 调查过程

### 第一步：排除锁问题
检查了以下文件：
- `factory_run_service.py`: 已使用细粒度锁（64 锁桶）✅
- `factory_store.py`: 已使用 `asyncio.Lock` ✅
- `store_sqlite.py`: 已使用细粒度锁（32 锁桶）✅

**结论**: 不是锁死问题

### 第二步：分析日志
查看 `adapter_debug_*.jsonl`：
```json
{"event": "llm_response", "raw_error": "验证失败...未找到有效的JSON或补丁"}
```

查看 `events.jsonl`：
```json
{"event": "sparse_output_detected", "line_count": 67, "required_lines": 80}
```

**发现**:
1. 首轮 Director LLM 返回格式错误
2. 触发应急写入，产生 67 行模板代码
3. 不满足压测门槛（80 行），触发稀疏检测
4. 进入 120s 的 LLM 重试等待

### 第三步：根因确认

**真正的问题**: 应急补救机制在掩盖 LLM 质量问题

时间线：
```
T+0s   Director LLM 返回格式错误
T+1s   应急写入产生 67 行模板代码
T+2s   稀疏检测: 67 < 80, 触发重试
T+2s   开始 120s LLM 重试等待
T+122s 用户感知"卡住"，中断运行
```

## 影响分析

### 直接影响
- 压测失败率虚高（实际上是等待超时）
- 用户误以为系统锁死
- 产生无效的模板代码文件

### 间接影响
- LLM 质量问题被掩盖，未得到及时修复
- 应急代码污染工作区
- 增加调试难度（需要区分真实代码 vs 模板代码）

## 修复措施

### 立即修复 (2026-03-11)
1. **移除应急写入**: 删除 4 处 `_execute_emergency_write_plan()` 调用
2. **直接抛异常**: 格式失败立即抛出，不自动补救
3. **Factory 层重试**: 添加可配置的重试策略

### 长期措施
1. **审查所有自动补救逻辑**: 排查类似模式
2. **改进 LLM Prompt**: 降低格式错误率
3. **压测门槛调整**: 评估 80 行要求是否合理

## 证据链

### 代码证据
```python
# 问题代码 (已删除)
if self._should_trigger_emergency_write(raw_error, content):
    emergency_results = self._execute_emergency_write_plan(...)
```

### 日志证据
```
# adapter_debug_20260310.jsonl
{"event": "first_llm_response", "success": false,
 "raw_error": "验证失败...未找到有效的JSON或补丁"}

{"event": "sparse_output_detected",
 "current_line_count": 67, "required_lines": 80}
```

### 文件证据
- `src/backend/app/roles/adapters/director_adapter.py` (修改)
- `src/backend/app/services/factory_run_service.py` (修改)
- `docs/adr/ADR-025-移除应急补救机制-诚实暴露错误.md` (新增)

## 教训

### 技术层面
1. **自动补救 = 技术债务**: 看似解决眼前问题，实则掩盖根因
2. **快速失败优于长等待**: 120s 重试比立即失败更糟糕
3. **分层责任**: 底层只管执行，上层管重试策略

### 流程层面
1. **压测失败要立即分析**: 不要假设是"正常波动"
2. **日志要诚实**: 不要美化错误信息
3. **异常要有上下文**: 包含 line_count, required_lines 等诊断信息

## 后续行动

- [x] 移除所有应急补救调用
- [x] 添加 Factory 层重试策略
- [x] 创建 ADR 文档
- [x] 更新项目记忆
- [ ] 审查其他 adapter 是否有类似模式
- [ ] 评估 LLM Prompt 改进需求
- [ ] 压测门槛合理性评估

## 参考

- [ADR-025](../adr/ADR-025-移除应急补救机制-诚实暴露错误.md)
- [相关 PR](#) (待创建)
- [压测框架文档](../testing/agent-stress-testing.md)

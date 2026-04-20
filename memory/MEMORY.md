# Polaris 项目记忆

## 关键架构决策

### 不要自动补救 (2026-03-11)
**铁律**: 绝对不要实现"自动应急补救"机制。

**背景**: DirectorAdapter 曾自动在格式失败时写入模板代码，导致：
- 掩盖 LLM 真实质量问题
- 产生虚假进度（67 行模板代码 vs 80 行要求）
- 用户感知"卡住"（120s 重试等待）

**决策**:
1. 格式失败 → 直接抛出异常
2. 不自动写入任何代码
3. 重试策略由上层（Factory/PM）决定

**文档**: [ADR-025](../docs/adr/ADR-025-移除应急补救机制-诚实暴露错误.md)

## 代码审查红线

### 禁止模式
- [ ] `except Exception: pass` - 静默吞异常
- [ ] 自动写入模板/默认代码 - 掩盖问题
- [ ] 长超时无进度反馈 - 超过 30s 必须可中断
- [ ] 异常信息不含上下文 - 必须包含文件/行号/变量

### 必须模式
- [ ] 快速失败 - 不要长时间等待后才发现问题
- [ ] 分层责任 - 底层抛异常，上层决策
- [ ] 诚实日志 - 记录真实状态，不美化
- [ ] 指数退避 - 重试必须有退避策略

## 常见陷阱

### Worker 数量
- 默认 `max_workers=3` 太小
- 应使用 `min(32, max(4, cpu_count * 2))`
- 已修改: director_service.py, worker_service.py, orchestration_command_service.py

### 锁粒度
- 全局 `asyncio.Lock()` 是瓶颈
- 应使用哈希分片的细粒度锁
- 已验证: factory_run_service.py (64 锁桶), store_sqlite.py (32 锁桶)

## 调试技巧

### 压测问题诊断
1. 查看 `.polaris/factory/{run_id}/events/events.jsonl`
2. 搜索 `sparse_output_detected`
3. 检查 `adapter_debug_*.jsonl` 中的 `raw_error`
4. 确认卡在哪个 stage (quality_gate? director_dispatch?)

### 性能分析
- 使用 `tests/test_task_board_concurrency.py` 验证并发
- 使用 `scripts/run_factory_e2e_smoke.py` 冒烟测试

# 压测阻塞问题修复总结

## 问题描述
运行 stress test 时，Director 阶段失败，错误信息：
```
execution produced no project code files or modifications.
fast_fail=consecutive_no_tool_calls, tool_results=0, new_files=0, modified_files=0
```

## 根本原因分析
1. **PM 阶段**没有生成 `tasks/plan.json` 文件（X: 盘上不存在此文件）
2. **根本原因**：`factory flow` 没有注册角色适配器（role adapters）
3. **详细原因**：
   - `register_all_adapters()` 只在 API routers (`pm.py`, `director.py`) 中被调用
   - `factory.py` router 从未导入 `app.roles.adapters` 模块
   - 因此全局工厂注册 (`configure_orchestration_role_adapter_factory`) 没有执行
   - 当 `OrchestrationCommandService` 尝试执行 PM/Director 时，编排服务找不到角色适配器
   - 虽然全局工厂变量被设置（模块导入时的副作用），但服务实例没有被正确配置

## 修复方案
在 `orchestration_command_service.py` 中：
1. 导入 `register_all_adapters`
2. 在每个执行方法 (`execute_pm_run`, `execute_director_run` 等) 中调用 `register_all_adapters(service)`

### 修改的文件
- `src/backend/app/services/orchestration_command_service.py`

### 修改内容
```python
# 新增导入
from app.roles.adapters import register_all_adapters

# 在每个执行方法中确保适配器被注册
service = await get_orchestration_service()
register_all_adapters(service)  # 新增
```

## 验证
1. ✅ 代码导入测试通过
2. ✅ `test_orchestration_command_service.py` - 2/2 测试通过
3. ✅ `test_factory_run_service.py` - 28/28 测试通过

## 建议下一步
重新运行 stress test 验证修复：
```bash
# 清理之前的失败项目
rm -rf C:/Temp/hp_stress_workspace/projects/expense-tracker

# 运行 stress test
python -m tests.agent_stress.runner ^
  --workspace C:/Temp/hp_stress_workspace ^
  --ramdisk X:/ ^
  --max-concurrent 1 ^
  --rounds 1 ^
  --projects expense-tracker
```

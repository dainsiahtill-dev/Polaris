# Phase G: 清理旧代码 - 待执行清单

## 清理任务清单

### 1. 删除旧协议解析分支

由于 V2 协议已实现，以下旧代码可以标记为废弃 (deprecated)：

- [ ] `src/frontend/src/hooks/useWebSocket.ts` - 标记 @deprecated，指向新的 Runtime Store
- [ ] 移除 snapshot/line + channel text parsing 的直接解析逻辑

### 2. 删除重复的 status 映射逻辑

- [ ] 检查 `app/types/factory.py` 与 `app/types/runtime_v2.py` 的枚举是否统一
- [ ] 移除重复的角色状态映射

### 3. 清理过时组件入口

- [ ] 如果 Mission Control 成为默认入口，旧页面入口可以降级为兼容模式

### 4. 清理 appContracts.ts 中的松散字段推断

- [ ] `src/frontend/src/app/types/appContracts.ts` - 审查并清理松散字段推断

## 执行命令（建议分步执行）

```bash
# 1. 先运行测试确保功能正常
npm run test
pytest

# 2. 标记废弃代码（不删除）
# 修改 useWebSocket.ts 添加 @deprecated 注释

# 3. 确认功能正常后，删除废弃代码
# 注意：此步骤不可逆，建议先创建 backup branch
```

## 回滚方案

如果清理后出现问题：
1. 回滚到 `ui-realtime-v1-final` tag
2. 或者使用 git revert 撤销更改

---

**注意**：此文件仅供记录清理任务，实际清理操作需要人工确认后执行。

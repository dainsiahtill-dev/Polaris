# LLM 工具调用优化团队执行计划

**创建时间**: 2026-03-26
**基于**: `docs/audit/llm_tool_calling_audit_20260326.md`
**团队成员**: 6人
**完成状态**: ✅ 全部完成

---

## 执行状态总结

| 任务 | 负责人 | 状态 | 完成时间 |
|------|--------|------|----------|
| #1 统一解析层 | 首席工程师 | ✅ 完成 | 2026-03-26 |
| #2 Provider适配器基类 | 首席工程师 | ✅ 完成 | 2026-03-26 |
| #3 协议测试套件 | QA | ✅ 完成 | 2026-03-26 |
| #4 技术债务清理 | 架构师 | ✅ 完成 | 2026-03-26 |
| #5 参数校验增强 | 安全审计员 | ✅ 完成 | 2026-03-26 |
| #6 文档完善 | 技术作家 | ✅ 完成 | 2026-03-26 |

| 角色 | 负责任务 | 依赖关系 |
|------|----------|----------|
| **首席工程师** (chief_engineer) | #1 统一解析层, #2 Provider适配器基类 | Task #1 先完成 |
| **质量工程师** (qa) | #3 协议测试套件 | 依赖 Task #1 |
| **架构师** (architect) | #4 技术债务清理 | 无依赖 |
| **安全审计员** (security) | #5 参数校验增强 | 无依赖 |
| **技术作家** (tech_writer) | #6 文档完善 | 依赖架构确认后 |

---

## 执行阶段

### 阶段 1: 首席工程师并行任务 (Task #1, #2)

**Task #1: 统一解析层**
- **负责人**: 首席工程师
- **复杂度**: 中
- **预期收益**: 减少 20-30% 解析开销
- **开始条件**: 无

**Task #2: Provider 适配器基类**
- **负责人**: 首席工程师
- **复杂度**: 低
- **预期收益**: 代码复用
- **开始条件**: Task #1 完成后可并行

### 阶段 2: 并行执行 (Task #3, #4, #5)

**Task #3: 协议测试套件**
- **负责人**: QA
- **复杂度**: 中
- **开始条件**: Task #1 完成后

**Task #4: 技术债务清理**
- **负责人**: 架构师
- **复杂度**: 低
- **开始条件**: 无

**Task #5: 参数校验增强**
- **负责人**: 安全审计员
- **复杂度**: 中
- **开始条件**: 无

### 阶段 3: 文档收尾 (Task #6)

**Task #6: 文档完善**
- **负责人**: 技术作家
- **复杂度**: 低
- **开始条件**: 架构确认后 (Task #4 后)

---

## 任务详情

### Task #1: 统一解析层

**目标**: 将 `StreamingPatchBuffer` 中的 `SEARCH_REPLACE` 解析逻辑委托给 `ProtocolParser`

**涉及文件**:
```
polaris/kernelone/llm/toolkit/
├── streaming_patch_buffer.py    # 修改: 委托解析逻辑
├── parsers.py                   # 可能需要扩展 ProtocolParser
└── protocol_kernel.py            # 可能需要调整
```

**验收条件**:
- [ ] StreamingPatchBuffer 不再独立解析 SEARCH_REPLACE
- [ ] 所有协议解析统一经由 ProtocolParser
- [ ] 现有测试通过
- [ ] 性能测试显示解析开销下降

**实现步骤**:
1. 分析 StreamingPatchBuffer 中的解析逻辑
2. 识别可复用的解析器方法
3. 修改 StreamingPatchBuffer 委托给 ProtocolParser
4. 移除重复解析代码
5. 运行测试验证

---

### Task #2: Provider 适配器公共基类

**目标**: 提取 AnthropicMessagesAdapter 和 OpenAIResponsesAdapter 的公共逻辑

**涉及文件**:
```
polaris/kernelone/llm/provider_adapters/
├── base.py                           # 修改: 添加公共基类
├── anthropic_messages_adapter.py     # 修改: 继承基类
├── openai_responses_adapter.py       # 修改: 继承基类
└── factory.py                        # 可能需要调整
```

**验收条件**:
- [ ] 公共逻辑提取到 ProviderAdapterBase
- [ ] 两个适配器继承基类
- [ ] 现有功能不受影响

**实现步骤**:
1. 分析两个适配器的公共方法
2. 创建 ProviderAdapterBase
3. 迁移公共方法
4. 修改两个适配器继承基类
5. 测试验证

---

### Task #3: 协议测试套件

**目标**: 为 ProtocolParser 建立完整的协议测试套件

**涉及文件**:
```
polaris/tests/
├── unit/kernelone/llm/toolkit/
│   └── test_protocol_parser.py    # 新建
└── integration/kernelone/llm/
    └── test_streaming.py          # 新建
```

**验收条件**:
- [ ] 测试覆盖所有协议格式
- [ ] 测试边界条件
- [ ] 测试覆盖率 > 80%

**测试用例清单**:
- [ ] PATCH_FILE 协议解析
- [ ] SEARCH_REPLACE 协议解析
- [ ] Tool Chain 协议解析
- [ ] Native FC 响应解析
- [ ] 畸形输入处理
- [ ] 空输入处理
- [ ] 大文件解析性能

---

### Task #4: 技术债务清理

**目标**: 清理已废弃的 core/llm_toolkit/ 目录

**涉及文件**:
```
core/llm_toolkit/          # 待删除
```

**验收条件**:
- [ ] 目录删除或归档
- [ ] 全局搜索无残留引用
- [ ] 系统功能正常

**执行步骤**:
1. 全局搜索所有可能引用 core/llm_toolkit 的 import
2. 确认无有效引用
3. 删除或归档目录
4. 验证系统功能

---

### Task #5: 参数校验增强

**目标**: 对工具参数增加 JSON Schema 校验和路径隔离

**涉及文件**:
```
polaris/kernelone/llm/toolkit/
├── definitions.py              # 添加 schema 定义
└── tool_normalization.py       # 添加校验逻辑
```

**验收条件**:
- [ ] read_file/write_file 验证路径前缀
- [ ] execute_command 增加参数校验
- [ ] 异常情况返回明确错误信息

**校验规则**:
- read_file: 验证路径在 workspace 内
- write_file: 验证路径在 workspace 内
- execute_command: 验证命令在白名单内
- glob: 验证 pattern 无路径遍历

---

### Task #6: 文档完善

**目标**: 更新架构文档，确保工具调用协议指南完整

**涉及文件**:
```
docs/
├── KERNELONE_ARCHITECTURE_SPEC.md    # 更新
└── tools/
    └── TOOL_CALLING_PROTOCOL.md      # 新建
```

**验收条件**:
- [ ] 三种协议关系明确
- [ ] Provider 适配器开发指南完整
- [ ] 文档与代码一致

---

## 执行命令

```bash
# 1. 统一解析层验证
pytest tests/unit/kernelone/llm/toolkit/test_parsers.py -v

# 2. Provider 适配器验证
pytest tests/unit/kernelone/llm/provider_adapters/ -v

# 3. 协议测试套件
pytest tests/unit/kernelone/llm/toolkit/test_protocol_parser.py -v --cov

# 4. 技术债务清理验证
grep -r "core/llm_toolkit" --include="*.py" src/

# 5. 安全校验验证
pytest tests/unit/kernelone/llm/toolkit/test_tool_normalization.py -v

# 6. 文档检查
python -m markdown docs/tools/TOOL_CALLING_PROTOCOL.md --check
```

---

## 风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| Task #1 修改影响现有流式处理 | 高 | 先建立测试套件 (Task #3)，增量修改 |
| Task #4 删除后仍有隐藏引用 | 中 | 全局搜索 + 运行时导入检查 |
| Task #5 校验影响性能 | 中 | 使用缓存 + 懒加载 schema |

---

*执行负责人: Dains*
*创建时间: 2026-03-26*

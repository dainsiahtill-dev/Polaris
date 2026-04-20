# Polaris 工具系统增强方案

## 1. 背景与目标

### 1.1 当前状态
- Polaris 使用 `dict` + JSON Schema 定义工具参数
- 验证逻辑分散在 `validate_tool_step()` 中
- 缺乏类型安全和复杂的验证规则

### 1.2 目标
1. **短期**：增强现有 JSON Schema 定义，添加 `pattern`、`min/max` 支持
2. **中期**：统一参数验证逻辑，减少重复代码
3. **长期**：可选引入 `pydantic` 进行类型化

### 1.3 参考对标
- **OpenCode**：Zod Schema（TypeScript）
- **Polaris 当前**：dict + JSON Schema
- **建议方向**：增强现有设计 + 可选 pydantic

---

## 2. 问题分析

### 2.1 当前设计

```python
# polaris/kernelone/tools/contracts.py
"repo_rg": {
    "arguments": [
        {"name": "pattern", "type": "string", "required": True},
        {"name": "max_results", "type": "integer", "required": False, "default": 50},
    ],
}
```

### 2.2 不足之处

| 问题 | 严重程度 | 说明 |
|------|----------|------|
| 缺乏正则验证 | 建议改 | 无法限制 pattern 格式 |
| 缺乏数值范围 | 建议改 | 无法限制 max_results 范围 |
| 验证逻辑分散 | 必须改 | validate_tool_step() 逻辑复杂 |
| 类型转换隐式 | 建议改 | string/int/bool 类型转换不明确 |
| 缺乏默认值处理 | 必须改 | 部分工具未处理默认值 |
| 错误消息不统一 | 建议改 | 各工具错误格式不一致 |

### 2.3 根本原因
- JSON Schema 定义不够丰富
- 验证逻辑未抽象
- 类型系统不完整

---

## 3. 增强方案

### 3.1 Phase 1: 增强 JSON Schema 定义

#### 3.1.1 扩展参数定义

```python
# 扩展后的参数定义
"arguments": [
    {
        "name": "pattern",
        "type": "string",
        "required": True,
        # 现有字段
        "min_length": 1,
        "max_length": 1000,
        "pattern": r"^[\w\s\.\-\[\]\(\)]+$",  # 新增：正则验证
    },
    {
        "name": "max_results",
        "type": "integer",
        "required": False,
        "default": 50,
        "minimum": 1,      # 新增：最小值
        "maximum": 1000,   # 新增：最大值
    },
]
```

#### 3.1.2 支持的类型

| 类型 | 现有支持 | 建议增强 |
|------|----------|----------|
| `string` | `min_length` | `pattern`, `format` |
| `integer` | `default` | `minimum`, `maximum` |
| `array` | - | `min_items`, `max_items` |
| `boolean` | - | - (已完善) |
| `object` | - | `properties`, `required` |

### 3.2 Phase 2: 统一验证逻辑

#### 3.2.1 抽象验证器

```python
# 新增 validators.py
class BaseValidator:
    """参数验证器基类"""

    def validate(self, value: Any, spec: dict) -> tuple[bool, str]:
        """验证参数值"""
        raise NotImplementedError

class StringValidator(BaseValidator):
    """字符串验证器"""

    def validate(self, value: Any, spec: dict) -> tuple[bool, str]:
        # min_length, max_length, pattern 验证
        ...

class IntegerValidator(BaseValidator):
    """整数验证器"""

    def validate(self, value: Any, spec: dict) -> tuple[bool, str]:
        # minimum, maximum 验证
        ...
```

#### 3.2.2 统一错误消息

```python
# 统一错误格式
ValidationError = tuple[str, str]  # (error_code, error_message)

# 错误码规范
ERROR_INVALID_TYPE = "INVALID_TYPE"
ERROR_REQUIRED_MISSING = "REQUIRED_MISSING"
ERROR_MIN_LENGTH = "MIN_LENGTH_VIOLATION"
ERROR_MAX_LENGTH = "MAX_LENGTH_VIOLATION"
ERROR_PATTERN = "PATTERN_VIOLATION"
ERROR_MINIMUM = "MINIMUM_VIOLATION"
ERROR_MAXIMUM = "MAXIMUM_VIOLATION"
```

### 3.3 Phase 3: 可选 Pydantic 集成（长期）

#### 3.3.1 渐进式迁移

```python
# 阶段1: 保持现有 dict 定义
# 阶段2: 添加验证器支持
# 阶段3: 可选生成 pydantic 模型

def generate_pydantic_model(tool_spec: dict) -> type[BaseModel]:
    """根据 tool_spec 生成 pydantic 模型"""
    # 供高级用户可选使用
    ...
```

---

## 4. 实施计划

### 4.1 团队分工

| 阶段 | 任务 | 负责人 | 产出 |
|------|------|--------|------|
| **Phase 1** | 扩展 JSON Schema 定义 | Agent-1 | 增强的 arguments 定义 |
| **Phase 1** | 增强类型转换逻辑 | Agent-2 | 类型安全增强 |
| **Phase 2** | 抽象验证器基类 | Agent-3 | validators.py |
| **Phase 2** | 实现各类型验证器 | Agent-4 | String/Integer/Array 验证器 |
| **Phase 2** | 统一错误消息格式 | Agent-5 | 标准化错误码 |
| **Phase 3** | 单元测试覆盖 | Agent-6 | 测试用例 |
| **Phase 3** | 集成测试 | Agent-7 | 端到端测试 |
| **Phase 4** | 文档更新 | Agent-8 | API 文档 |
| **Phase 4** | 回归测试 | Agent-9 | 确保无破坏 |
| **Phase 4** | 代码审查 | Agent-10 | 质量把控 |

### 4.2 里程碑

| 里程碑 | 完成标准 |
|--------|----------|
| M1: Phase 1 完成 | 所有工具 arguments 定义扩展完成 |
| M2: Phase 2 完成 | 验证器覆盖所有类型，错误码统一 |
| M3: Phase 3 完成 | 单元测试覆盖率 > 90% |
| M4: 发布 | 文档完整，无回归 |

---

## 5. 风险评估

| 风险 | 级别 | 缓解措施 |
|------|------|----------|
| 验证逻辑变更引入回归 | 高 | 完整单元测试 + 回归测试 |
| 现有代码依赖旧逻辑 | 中 | 保持 API 兼容，添加弃用警告 |
| 性能影响 | 低 | 验证器惰性加载 |
| 文档不同步 | 中 | 同步更新文档 |

---

## 6. 测试策略

### 6.1 单元测试

```python
# tests/test_validators.py
class TestStringValidator:
    """字符串验证器测试"""

    def test_min_length_valid(self): ...
    def test_min_length_invalid(self): ...
    def test_pattern_valid(self): ...
    def test_pattern_invalid(self): ...

class TestIntegerValidator:
    """整数验证器测试"""

    def test_minimum_valid(self): ...
    def test_minimum_invalid(self): ...
    def test_maximum_valid(self): ...
```

### 6.2 集成测试

```python
# tests/test_validate_tool_step.py
class TestValidateToolStep:
    """工具步骤验证集成测试"""

    def test_repo_rg_with_valid_args(self): ...
    def test_repo_rg_with_invalid_pattern(self): ...
    def test_repo_rg_with_out_of_range_max_results(self): ...
```

### 6.3 边界测试

| 场景 | 测试用例 |
|------|----------|
| 最小值边界 | max_results = 1 |
| 最大值边界 | max_results = 1000 |
| 超界 | max_results = 1001 |
| 空字符串 | pattern = "" |
| 特殊字符 | pattern = `<script>` |

---

## 7. 代码审查清单

### 7.1 必须改

- [ ] 验证逻辑未覆盖的类型
- [ ] 错误消息未格式化的
- [ ] 缺乏类型注解的方法

### 7.2 建议改

- [ ] 重复的验证代码
- [ ] magic number/constant
- [ ] 过长函数（> 50 行）

### 7.3 可选优化

- [ ] 重复 docstring
- [ ] 未使用的导入
- [ ] 可简化的条件表达式

---

## 8. 成功标准

- [ ] 所有工具 arguments 定义扩展完成
- [ ] 验证器覆盖所有类型
- [ ] 单元测试覆盖率 > 90%
- [ ] 集成测试通过
- [ ] 无回归问题
- [ ] 文档完整更新

---

**创建时间**: 2026-03-28
**更新历史**:
- 2026-03-28: 初始版本

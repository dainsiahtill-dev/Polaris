# Instructor 集成方案（方案A详细设计）

## 目标
用 Instructor 替换手动 JSON 解析，实现类型安全的结构化输出。

## 现状问题
1. `_extract_json` 手动解析容易出错
2. JSON 格式错误需要多轮重试
3. 无类型检查，下游使用容易出错

## 集成设计

### 1. Pydantic 模型定义

```python
# app/roles/schemas/pm_schema.py
from pydantic import BaseModel, Field
from typing import List, Literal

class Task(BaseModel):
    id: str = Field(..., description="任务ID，如 TASK-001")
    title: str = Field(..., min_length=5, max_length=100)
    description: str = Field(..., min_length=20, max_length=500)
    target_files: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(..., min_items=1)
    priority: Literal["high", "medium", "low"]
    phase: Literal["bootstrap", "core", "polish"]
    estimated_effort: int = Field(..., ge=1, le=8)
    dependencies: List[str] = Field(default_factory=list)

class TaskListOutput(BaseModel):
    tasks: List[Task] = Field(..., min_items=1, max_items=20)
    analysis: dict
```

### 2. Instructor 客户端封装

```python
# app/llm/instructor_client.py
import instructor
from openai import OpenAI
from anthropic import Anthropic

class StructuredLLMClient:
    """结构化输出 LLM 客户端"""

    def __init__(self, provider: str, model: str, api_key: str):
        self.provider = provider
        self.model = model

        if provider == "openai":
            base_client = OpenAI(api_key=api_key)
            self.client = instructor.from_openai(base_client)
        elif provider == "anthropic":
            base_client = Anthropic(api_key=api_key)
            self.client = instructor.from_anthropic(base_client)
        else:
            # 使用通用模式
            self.client = None
            self.base_client = base_client

    async def create_structured(
        self,
        messages: list,
        response_model: type[T],
        max_retries: int = 3
    ) -> T:
        """生成结构化输出"""
        if self.client:
            return await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_model=response_model,
                max_retries=max_retries
            )
        else:
            # Fallback 到手动解析
            return await self._manual_fallback(messages, response_model)
```

### 3. 与 RoleExecutionKernel 集成

```python
# 在 kernel.py 的 _execute_single_turn 中
from app.roles.schemas import TaskListOutput, BlueprintOutput

# 根据角色选择输出模型
OUTPUT_MODELS = {
    "pm": TaskListOutput,
    "chief_engineer": BlueprintOutput,
    "qa": QAReportOutput,
}

async def _execute_single_turn(self, ...):
    # ... 准备 messages ...

    response_model = OUTPUT_MODELS.get(role)
    if response_model and self.use_instructor:
        # 使用 Instructor
        result = await self.structured_client.create_structured(
            messages=messages,
            response_model=response_model,
            max_retries=3
        )
        # 直接得到 Pydantic 对象，无需解析
        content = result.model_dump_json()
        validation = QualityResult(
            success=True,
            data=result.model_dump(),
            quality_score=100.0  # Instructor 保证格式正确
        )
    else:
        # 原有逻辑
        llm_response = await self.llm_caller.call(...)
        validation = self.quality_checker.validate_output(...)
```

### 4. 回退策略

如果 Instructor 失败（如不支持的工具调用格式），自动回退到手动解析：

```python
async def _manual_fallback(self, messages, response_model):
    """手动解析回退"""
    response = await self.base_client.chat.completions.create(...)
    content = response.choices[0].message.content

    # 提取 JSON
    json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
    if json_match:
        data = json.loads(json_match.group(1))
        return response_model(**data)
    raise ValueError("Failed to parse structured output")
```

## 实施步骤

1. **添加依赖** `pyproject.toml`:
   ```toml
   dependencies = [
       "instructor>=1.0.0",
       "pydantic>=2.5.0",  # 已存在
   ]
   ```

2. **创建 Schema 目录**:
   ```
   app/roles/schemas/
   ├── __init__.py
   ├── pm_schema.py      # PM 任务列表
   ├── ce_schema.py      # Chief Engineer 蓝图
   ├── architect_schema.py
   ├── director_schema.py
   └── qa_schema.py
   ```

3. **修改 llm_caller.py** 添加 structured_create 方法

4. **渐进式迁移**:
   - Phase 1: PM 角色启用 Instructor
   - Phase 2: Chief Engineer
   - Phase 3: 其他角色

## 预期收益

- JSON 解析错误率从 ~15% 降至 < 1%
- 减少 30% 的重试次数
- 类型安全，IDE 自动补全

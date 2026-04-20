# Polaris 强化 - 第三阶段：智能化强化

**版本**: v1.0.0
**日期**: 2026-04-06
**状态**: 待执行
**工期**: 8周
**人力**: 6人
**目标评分**: 80/100 (从76/100)

---

## 一、任务总览

| 任务 | 目标 | 工作量 |
|------|------|--------|
| S3-1: 自我评估 | Agent能力边界自评 | 48h |
| S3-2: 主动学习 | 从错误中提取模式 | 56h |
| S3-3: 常识推理 | 基础因果推理 | 64h |
| S3-4: 工具创造 | 从需求生成工具代码 | 72h |
| S3-5: 长期记忆 | 跨会话知识积累 | 48h |

---

## 二、任务详情

### S3-1: 自我评估

```python
# polaris/kernelone/agent/self_evaluation.py

class SelfEvaluator:
    """自我评估器 - 识别能力边界"""

    async def evaluate_capability(
        self,
        task: Task,
    ) -> CapabilityAssessment:
        """评估完成任务的自信度"""
        prompt = f"""
        任务: {task.description}
        要求: {task.requirements}

        请评估:
        1. 你有多大把握能完成这个任务? (0-100%)
        2. 主要挑战是什么?
        3. 需要什么帮助?
        4. 预计成功率和潜在失败模式?

        返回JSON格式评估。
        """
        response = await self._llm.complete(prompt)
        return self._parse_assessment(response)
```

### S3-2: 主动学习

```python
# polaris/kernelone/learning/active_learner.py

class ActiveLearner:
    """主动学习器 - 从错误中提取模式"""

    async def learn_from_error(
        self,
        error: Error,
        context: TurnContext,
    ) -> LearningResult:
        """从错误中学习"""
        prompt = f"""
        错误: {error.description}
        上下文: {context.summary}

        请分析:
        1. 错误的根本原因是什么?
        2. 如何避免类似错误?
        3. 需要学习什么新知识?
        4. 如何更新我的判断准则?
        """
        response = await self._llm.complete(prompt)
        return self._extract_patterns(response)
```

### S3-3: 常识推理

```python
# polaris/kernelone/reasoning/commonsense.py

class CommonsenseReasoner:
    """常识推理器 - 因果/反事实推理"""

    async def causal_inference(
        self,
        observation: str,
    ) -> CausalGraph:
        """因果推断"""

    async def counterfactual(
        self,
        scenario: str,
        hypothetical: str,
    ) -> CounterfactualResult:
        """反事实推理"""

    async def analogical_reasoning(
        self,
        source: str,
        target: str,
    ) -> AnalogyResult:
        """类比推理"""
```

### S3-4: 工具创造

```python
# polaris/kernelone/tool_creation/code_generator.py

class ToolGenerator:
    """工具代码生成器"""

    async def generate_tool(
        self,
        requirement: ToolRequirement,
    ) -> GeneratedTool:
        """从需求生成工具"""
        prompt = f"""
        需求: {requirement.description}
        输入: {requirement.input_schema}
        输出: {requirement.output_schema}

        请生成:
        1. 工具实现代码
        2. 工具规格说明
        3. 单元测试

        返回JSON格式。
        """
        response = await self._llm.complete(prompt)
        return self._parse_generated_tool(response)
```

### S3-5: 长期记忆

```python
# polaris/kernelone/memory/long_term.py

class LongTermMemory:
    """长期记忆 - 跨会话知识积累"""

    async def consolidate(
        self,
        session_id: str,
    ) -> None:
        """将会话记忆整合到长期记忆"""
        session_events = await self._session_store.get_events(session_id)
        knowledge = await self._extract_knowledge(session_events)
        await self._long_term_store.add(knowledge)

    async def retrieve_relevant(
        self,
        query: str,
    ) -> list[KnowledgeItem]:
        """检索相关长期记忆"""
```

---

## 三、验收清单

- [ ] S3-1: 能力边界识别准确率 > 80%
- [ ] S3-2: 错误模式提取准确率 > 75%
- [ ] S3-3: 因果推理准确率 > 70%
- [ ] S3-4: 工具生成可用率 > 80%
- [ ] S3-5: 跨会话知识保留率 > 90%

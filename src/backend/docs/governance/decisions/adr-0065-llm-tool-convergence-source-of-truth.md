# ADR-0065: LLM工具调用系统收敛 - 单一权威源头

**决策类型**: 架构决策
**状态**: 已接受
**创建时间**: 2026-03-28
**基于**: `docs/audit/llm_tool_calling/MASTER_AUDIT_REPORT_20260328.md`

---

## 背景

Polaris的LLM工具调用系统存在严重的架构碎片化问题：

1. **Tool定义三处独立**: `definitions.py`(16), `contracts.py`(30+), `registry.py`(10)
2. **Parser五层并存**: native/xml/json/prompt/tool_chain，只有2层实际使用
3. **Provider双注册表**: kernelone和infrastructure各有一个ProviderManager
4. **角色策略双路径**: RoleToolGateway和PolicyLayer各自实现权限检查
5. **规范化双系统**: tool_normalization.py和contracts.py各有一套归一化逻辑

这导致：
- 维护成本高（改一处要同步三处）
- 不一致风险（别名/参数定义可能不同步）
- 安全漏洞（HMAC验证不对称）
- 测试覆盖困难

---

## 决策

### 2.1 建立单一Tool Spec权威源头

**位置**: `polaris/kernelone/tools/tool_spec_registry.py`

```python
class ToolSpecRegistry:
    """单一Source of Truth for所有Tool定义"""
    _specs: dict[str, ToolSpec] = {}

    @classmethod
    def register(cls, spec: ToolSpec) -> None:
        """注册工具，自动处理别名映射"""

    @classmethod
    def get(cls, name: str) -> ToolSpec | None:
        """获取工具规格（支持别名）"""

    @classmethod
    def generate_llm_schemas(cls) -> list[dict]:
        """生成LLM-facing schemas"""

    @classmethod
    def generate_handler_registry(cls) -> dict[str, tuple[str, str]]:
        """生成handler映射"""
```

**生效时间**: Phase 1完成 (Week 2)

---

### 2.2 Parser收敛为2层

**架构**:
- `CanonicalToolCallParser`: 统一入口，format_hint优先
- FormatAdapters: OpenAI/Anthropic/Gemini/Ollama/JSONText

**删除**:
- `prompt_based.py`: deprecated但未删除
- `tool_chain.py`: 几乎不用
- `domain/services/parsing.py`: 冗余实现

**生效时间**: Phase 2完成 (Week 4)

---

### 2.3 ProviderRegistry合并

**架构**:
- 单一ProviderRegistry在 `infrastructure/llm/providers/`
- kernelone通过注入使用
- 消除 `kernelone/llm/providers/registry.py`

**生效时间**: Phase 3完成 (Week 6)

---

### 2.4 角色策略统一为RolePolicyEngine

**架构**:
- `polaris/kernelone/policy/role_engine.py`: 唯一策略执行点
- `builtin_profiles.py`: 唯一角色配置源
- 删除 `core_roles.yaml`

**生效时间**: Phase 4完成 (Week 8)

---

### 2.5 执行器单依赖ToolSpecRegistry

**架构**:
- `ToolHandlerRegistry`: 显式Handler注册
- executor只依赖ToolSpecRegistry
- 消除definitions.py和contracts.py的直接依赖

**生效时间**: Phase 5完成 (Week 10)

---

## 后果

### 3.1 正面

- ✅ 单一权威源头，消除不一致风险
- ✅ 维护成本降低（改一处即可）
- ✅ 测试覆盖更容易
- ✅ 安全漏洞可修复（HMAC验证统一）
- ✅ 新增Tool只需注册一次

### 3.2 负面

- ❌ 迁移期间有过渡代码
- ❌ 需要CI门禁防止回退
- ❌ 可能有API微小变化需要适配

### 3.3 中性

- 🔄 Phase 1-5期间功能正常
- 🔄 向后兼容保持

---

## 迁移策略

见 `docs/blueprints/llm_tool_calling/LLM_TOOL_CALLING_EXECUTION_PLAN_20260328.md`

### 关键里程碑

| 里程碑 | 目标日期 |
|--------|---------|
| Phase 1: ToolSpecRegistry上线 | 2026-04-14 |
| Phase 2: Parser收敛完成 | 2026-04-28 |
| Phase 3: Provider收敛完成 | 2026-05-14 |
| Phase 4: 角色策略统一 | 2026-05-28 |
| Phase 5: 执行器收敛 | 2026-06-10 |
| 全系统验证 | 2026-06-20 |

---

## 参考

- `docs/audit/llm_tool_calling/MASTER_AUDIT_REPORT_20260328.md`
- `docs/blueprints/llm_tool_calling/LLM_TOOL_CALLING_CONVERGENCE_BLUEPRINT_20260328.md`
- `polaris/kernelone/tools/tool_spec_registry.py` (实现)

---

*决策者*: Dains
*接受时间*: 2026-03-28

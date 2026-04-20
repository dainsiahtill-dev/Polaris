# Polaris 上下文工程优化计划（Sniper Mode v2.0）

基于 Polaris 三层成本模型，实施 **Structure-Aware 上下文工程优化**，重点解决上下文窗口管理与 **METERED** 模式成本控制问题。

---

## 1. 重新定位：从 Token 节省到上下文工程优化

**核心目标**：上下文工程优化，而非单纯 Token 节省。

- **稳定性优先**：避免上下文溢出导致的失败。
- **精准性提升**：Structure-Aware 替代暴力搜索。
- **成本控制**：在 METERED 场景显著节省费用。
- **效率优化**：减少无效上下文传输。

---

## 2. Polaris 成本架构分析

| 成本通道 | 主要场景 | 核心痛点 | Sniper Mode 价值 |
| --- | --- | --- | --- |
| 🖥️ LOCAL | Ollama 等本地模型 | 上下文窗口限制、推理效率 | 高：窗口管理 + 效率提升 |
| 📦 FIXED | Codex CLI 等订阅 | 配额利用率、单次请求限制 | 中：配额优化 |
| 💳 METERED | OpenAI API 等 | 直接成本控制 | 极高：成本显著降低 |

---

## 3. 技术方案：Sniper Mode v2.0（四大核心架构）

### 3.1 骨架图谱技术（Repository Map）- 🎯 核心价值

- **解决痛点**：上下文窗口管理  
- **适用场景**：所有成本通道，特别是 LOCAL 模式的长文档处理  
- **技术实现**：基于 Tree-sitter 生成项目骨架  
- **效果**：10,000 行文件压缩至 200 行，避免上下文溢出

### 3.2 AST 上下文切片 - 🎯 核心价值

- **解决痛点**：精准性提升  
- **适用场景**：所有需要精准修改的场景  
- **技术实现**：最小完备上下文（Minimal Complete Context）  
- **效果**：60%~80% 上下文减少，精准性显著提升

### 3.3 两阶段检索架构 - 🎯 支撑价值

- **解决痛点**：搜索效率优化  
- **适用场景**：大型项目快速定位  
- **技术实现**：Keyword Search → Selection → Reading  
- **效果**：搜索精度大幅提升，减少试错成本

### 3.4 Git 聚焦感知 - 🎯 增强价值

- **解决痛点**：任务相关性提升  
- **适用场景**：基于现有代码的修改任务  
- **技术实现**：Git Diff + Co-change 分析  
- **效果**：定位准确率 > 95%

---

## 4. 实施方案

### Phase 1：核心工具开发（1-2 周）

#### 4.1 Repository Map 生成器

```python
# tools/repo_map.py
def generate_repo_map(workspace: str, languages: List[str]) -> Dict:
    """生成项目骨架图谱，专注上下文窗口优化"""
    return {
        "skeleton": extract_skeletons(workspace),  # 仅类名、函数签名
        "metadata": {
            "total_files": count,
            "compressed_ratio": ratio,
            "context_safe": True  # 保证在上下文窗口内
        }
    }
```

#### 4.2 Context Window Manager

```python
# tools/context_manager.py
def build_context_window(request: ContextRequest) -> ContextPack:
    """智能上下文窗口管理，防止溢出"""
    # 1. 估算当前上下文大小
    # 2. 应用预算梯度策略
    # 3. 生成窗口安全的上下文包
    return safe_context_pack
```

#### 4.3 Cost-Aware Router

```python
# tools/cost_router.py
def route_by_cost_model(task: Task, cost_model: str) -> Strategy:
    """根据成本模型选择最优策略"""
    if cost_model == "LOCAL":
        return ContextWindowStrategy()  # 专注窗口管理
    elif cost_model == "FIXED":
        return QuotaOptimizationStrategy()  # 配额优化
    elif cost_model == "METERED":
        return TokenSavingStrategy()  # Token 节省优先
```

### Phase 2：工作流集成（1 周）

#### 4.4 Director Prompt 优化

```json
{
  "system_prompt": "你使用 Polaris Sniper Mode v2.0 进行上下文工程优化。\\n\\n成本感知策略：\\n- LOCAL 模式：优先上下文窗口管理，避免溢出\\n- FIXED 模式：优化配额利用率\\n- METERED 模式：严格控制 Token 消耗\\n\\n工作流程：\\n1. 检测成本模型\\n2. 选择对应策略\\n3. 执行精准定位和修改"
}
```

#### 4.5 成本监控集成

```python
# metrics/cost_tracker.py
class CostTracker:
    def track_context_efficiency(self, mode: str, metrics: Dict):
        """跟踪上下文工程效率"""
        self.emit_event("context.engineering", {
            "cost_model": mode,
            "context_window_usage": metrics["window_usage"],
            "precision": metrics["precision"],
            "cost_savings": metrics["savings"]
        })
```

### Phase 3：场景化优化（1-2 周）

#### 4.6 LOCAL 模式优化

- 上下文窗口监控：实时检测窗口使用率  
- 智能压缩：超限时自动摘要与指针化  
- 推理效率优化：减少无关上下文，提升速度

#### 4.7 FIXED 模式优化

- 配额预测：基于历史数据预测配额使用  
- 批量优化：合并小任务，减少请求次数  
- 优先级调度：重要任务优先使用配额

#### 4.8 METERED 模式优化

- 强门禁策略：严格 Token 预算控制  
- 成本预警：接近限额时自动降级  
- 紧急通道：关键任务快速审批

---

## 5. 预期效果重新评估

### 5.1 上下文窗口管理效果

| 指标 | 当前状态 | Sniper Mode v2.0 | 改善幅度 |
| --- | --- | --- | --- |
| 上下文溢出率 | 15%~20% | < 2% | 90%+ 减少 |
| 长文档处理成功率 | 70% | 95%+ | 25%+ 提升 |
| 推理效率（LOCAL） | 基准 | +30%~50% | 显著提升 |

### 5.2 成本控制效果（METERED）

| 场景 | 传统方案 | Sniper Mode v2.0 | 节省率 |
| --- | --- | --- | --- |
| 代码定位 | 5k-10k tokens | 0.5k tokens | 95% |
| 上下文获取 | 10k-30k tokens | 1k-2k tokens | 90% |
| 总体消耗 | 17k-45k tokens | 2.5k-4.5k tokens | 85% |

### 5.3 配额优化效果（FIXED）

- 配额利用率：60% → 85%+  
- 单次请求效率：提升 40%+  
- 任务完成量：相同配额下多完成 30%+ 任务

---

## 6. 风险评估与缓解

### 技术风险

- **复杂度增加**：新增上下文管理逻辑  
  - 缓解：渐进式迁移，保留传统路径  
- **兼容性问题**：不同模型的上下文限制差异  
  - 缓解：可配置的窗口大小与策略

### 业务风险

- **稳定性影响**：新功能可能影响现有流程  
  - 缓解：充分测试与灰度发布  
- **学习成本**：团队需要理解新的工作流  
  - 缓解：详细文档与培训

---

## 7. 实施优先级

**P0（立即实施）**
- Repository Map 生成器（解决窗口溢出）
- 上下文窗口管理器
- 成本感知路由器

**P1（2 周内）**
- Director Prompt 优化
- 成本监控集成
- LOCAL 模式优化

**P2（1 个月内）**
- FIXED/METERED 模式场景化优化
- Dashboard 可视化支持
- 完整性能监控

---

## 8. 成功指标

### 技术指标
- 上下文溢出率：< 2%  
- 定位准确率：> 95%  
- 推理效率提升：30%+（LOCAL）

### 业务指标
- METERED 模式成本节省：> 85%  
- FIXED 模式配额利用率：> 85%  
- 整体任务成功率：> 90%

---

## 9. 总结

Polaris Sniper Mode v2.0 的核心价值是**上下文工程优化**。通过 Structure-Aware 技术解决不同成本模型的核心痛点：

- **LOCAL 模式**：上下文窗口管理与推理效率  
- **FIXED 模式**：配额优化与利用率提升  
- **METERED 模式**：成本控制与费用节省

该方案符合 Polaris“Cloud/FIXED 主模型 + LOCAL(SLM) 协同、固定成本优先”的设计理念，是实现“单人低成本长跑”愿景的关键技术升级。

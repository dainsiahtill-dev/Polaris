# values.alignment

**Status**: Declared in cells.yaml
**Directory**: polaris/cells/values/

## 职责

实现四维价值观评估矩阵，从用户长期利益、系统完整性、他人影响、未来放大效应四个维度评估行动的对齐程度。

### 四维评估模型

1. **用户长期利益** (权重 0.35): 行动是否对用户的长期利益有利？
2. **系统完整性** (权重 0.30): 是否维护系统健康与安全？
3. **他人影响** (权重 0.20): 对非参与方的影响如何？
4. **未来放大效应** (权重 0.15): 1000x 放大后是否仍然合理？

### Stranger Test

"我是否愿意向一个陌生人解释为什么这样做？" — 整体得分 ≥ 0.6 通过。

## 公开契约

模块: `polaris.cells.values`

### Queries
- **`EvaluateValueAlignmentQueryV1`** — 评估行动的四维价值观对齐

### Results
- **`ValueAlignmentResultV1`** — 四维评估结果，包含 overall_score、stranger_test_passed、final_verdict、conflicts

### Errors
- **`ValueAlignmentErrorV1`** — 评估失败时抛出

### 公开 API

```python
from polaris.cells.values import (
    ValueAlignmentService,   # 四维评估服务
    ValueDimension,          # 枚举: USER_LONG_TERM, SYSTEM_INTEGRITY, OTHERS_IMPACT, FUTURE_AMPLIFICATION
    ValueEvaluation,         # 单维度评估结果
    ValueAlignmentResult,    # 完整四维评估结果
)
```

### 裁定逻辑

- 任一维度 REJECTED → 最终 REJECTED
- 存在 CONDITIONAL 或 overall < 0.7 → CONDITIONAL
- 否则 → APPROVED
- 任一维度 ESCALATE 或 overall < 0.5 → escalation_required=True

## 依赖

- `kernelone.security` — 调用 `is_dangerous_command` 进行危险命令检测

## 效果

- `fs.read:workspace/**`

## 验证

- 测试: 无
- Gaps:
  - Cell newly created from alignment_service.py
  - Full test coverage pending

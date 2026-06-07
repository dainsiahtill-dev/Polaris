# Software Engineering AGI 文档

本目录记录 Polaris 面向外部的 `Software Engineering AGI` 方案。

约定：

- 产品/文档/UI 叙事统一使用 `AGI`
- 当前代码实现层仍使用 `resident` 作为内核包名
- `resident` 是运行内核，不是产品名

## 文档索引

- `resident-engineering-rfc.md`
  - AGI 运行内核、治理边界、数据模型、运行时挂载点
- `resident-api.md`
  - `/v2/resident/*` 控制面 API、状态字段、错误语义
- `resident-rollout.md`
  - 启用顺序、治理门禁、运维巡检、验证命令
- `agi-workspace.md`
  - AGI 工作台 UI、操作流、前端入口与联动方式
- `agi-value-proposition.md`
  - AGI 当前的实际作用、能力边界，以及对未来项目与平台的长期价值

## 当前代码入口

- `src/backend/polaris/cells/resident/autonomy/public/service.py`
- `src/backend/polaris/cells/resident/autonomy/internal/decision_trace.py`
- `src/backend/polaris/cells/resident/autonomy/internal/meta_cognition.py`
- `src/backend/polaris/cells/resident/autonomy/internal/goal_governor.py`
- `src/backend/polaris/cells/resident/autonomy/internal/pm_bridge.py`
- `src/backend/polaris/cells/resident/autonomy/internal/counterfactual_lab.py`
- `src/backend/polaris/cells/resident/autonomy/internal/skill_foundry.py`
- `src/backend/polaris/cells/resident/autonomy/internal/self_improvement_lab.py`
- `src/backend/polaris/delivery/http/v2/resident.py`
- `src/frontend/src/app/components/resident/ResidentWorkspace.tsx`

## 最小验证命令

```bash
python -m pytest -q \
  src/backend/polaris/tests/test_resident_service.py \
  src/backend/polaris/tests/test_resident_api.py \
  src/backend/polaris/tests/test_runtime_projection_resident.py \
  src/backend/polaris/tests/test_resident_pm_bridge.py

npm run typecheck
npm run test -- src/frontend/src/app/components/resident/ResidentWorkspace.test.tsx
```

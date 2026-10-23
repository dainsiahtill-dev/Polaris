# Director 与独立 QA 交接实施计划

> 使用当前主仓 main 实施；用户已授权自主实验、最小修复与集成，不创建分支。

**目标：** 在保留同候选独立 QA 的前提下，实验证明能否删去重复交接门禁。
**方案：** [蓝图](../../blueprints/POLARIS_DIRECTOR_QA_HANDOFF_20261023.md)。唯一 owner、权限和存储不变。

- [x] 封存 L1-05 第一失败与三组真实请求/内容对比，核对取消和资源关闭。
- [x] ROOT 审阅并执行七场景模块控制；不生成 approved 或 Bench 成功。
- [x] 新回归覆盖必需 QA 路由、无 QA 视觉、失败/缺失检查与 QA 自身拒绝；先执行并保存 RED。
- [x] 在 `src/backend/polaris/cells/roles/runtime/public/engineering_service.py` 的 `_verify_director` 与 `_fresh_verification` 同步最小条件；`engineering_loop.py` 从持久 require_qa 传入 `engineering_roles.py` 提示，默认旧值；不放宽 QA 或候选校验。
- [x] 执行 GREEN 与相邻服务/视觉/检查选择/QA/恢复回归；Ruff、format、mypy 与项目必要检查；独立审查源码和证据。
- [ ] 更新 Cell 文档及索引、实验结果和 VC；main 集成后冻结新批次，正式入口串行继续 Bench，逐项目个人复查质量与资源。

反例：没有必需 QA 仍需 Director 图像；缺少或失败检查不能进 QA；QA 没有源码或当前图像必须拒绝。单次控制到达 QA 不证明优越性，最终依实际项目交付、人工干预、耗时和全部成本判断。

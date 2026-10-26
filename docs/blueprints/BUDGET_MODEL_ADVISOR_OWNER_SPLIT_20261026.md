# 预算与模型配置核心／角色顾问收敛

状态：CFO确定性核心／RoleAgent顾问已实施并经Root复验109项通过；公共路径显式迁移，生成投影与全仓验收未完成。HR配置核心／顾问分离仍在只读可行性审计。当前真实反向依赖为Budget public.reserve_budget创建RoleAgent派生CFO，以及LLMControlPlane普通service在同一配置store模块中加载RoleAgent派生HR。

目标：预算record/usage/KFS Store及确定性预算操作仍归finops；LLMConfig/Store及校验、脱敏、CRUD/cache仍归llm.control_plane；RoleAgent生命周期、message queue/toolbox/thread/context适配器归现有roles.runtime。默认交付PM/CE/Director/QA不加入CFO/HR。长期记忆不扩展。

低层reserve_budget直接消费同owner预算核心，不构造RoleAgent。LLM普通服务只导入配置核心，不加载运行时顾问。CFO/HR仍以原RoleAgent基类提供原工具/lifecycle/message功能，由高层运行时顾问模块消费低层公开服务。禁止用不兼容RoleBase替换、把RoleAgent搬进K1，或通过低层lazy re-export恢复反向边。

公共切换原则：能力保留、所有tracked消费者和capability metadata迁到正确owner。保留低层domain命令/store/config数据的现有公开位置或同对象别名；高层顾问的旧跨owner位置若无法在不重建循环情况下兼容，必须先列全部实际消费者/模块加载路径，提供明确迁移入口与公开变更说明，不能默默把CFOAgent改成缺少生命周期的轻量对象。具体入口在实施前用证据确认。

先红后绿：全新解释器阻止roles.runtime时低层预算/配置正常命令及冷读取可用；高层顾问仍RoleAgent、工具注册、TASK/RESULT/correlation、队列、initialize/start/stop/status及lazy上下文行为保持。真实KFS旧budget/config bytes不迁写，预算单位/阈值/错误、配置provider-kind/激活/日期/API脱敏、cache TTL/preload/invalidate完全对照。模型/provider和真实配置均不调用/修改。

共享public facade、metadata、graph由Root串行整合，具体file buckets由当前实现/消费者追踪确定，不能抢写当前权限/治理/quality worker。达成上述局部闭合后剩余大SCC继续，不宣称DAG全部收敛。数据/调用源证据见big-scc-first-cuts/report.json CUT-C。

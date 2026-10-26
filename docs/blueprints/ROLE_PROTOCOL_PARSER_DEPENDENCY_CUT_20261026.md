# RoleKernel 协议解析依赖收敛

状态：全仓收敛计划下的最小重构设计，未实现验证。

实际路径为RoleKernel output_parser/quality_checker -> DirectorExecution public parse_all_operations -> DirectorTasking wrapper -> 既有KernelOne toolkit.parse_protocol_output。低层通用协议解析绕过业务执行器，形成roles.kernel->director.execution真实反向边；公开RoleKernel parser/quality仍有消费者，不能删除功能。

只改两个RoleKernel内部文件，直接复用现有公开K1 toolkit.parse_protocol_output/EditType。输出解析器继续只选SEARCH_REPLACE；quality checker继续接受原全部操作并保持路径投影、安全拒绝、native/文本fallback顺序。DirectorTasking原公共业务wrapper为其余真实调用者保留。不新增parser、DTO、controller、角色协议或平台写入。

先保存旧实现固定语料及结果（PATCH_FILE、SEARCH/REPLACE、CREATE/FULL_FILE、DELETE/mixed、Unicode/换行、缺路径、绝对/Windows/traversal/NUL、malformed/torn/native）。旧代码在拒绝DirectorExecution导入的新进程中解析应红，改后相同语料结果和拒绝语义保持。通用K1解析不承接Director业务策略。

Scope测试见`.polaris/audits/technical-debt-clearance-20261025/big-scc-first-cuts/report.json` CUT-A。先红后绿、消费者回归、真实import图去掉边、独立审查通过即结束本主题。全25Cell SCC仍另行OPEN，不能以本两文件重构宣称全仓完成。共享graph/catalog由Root同步，不改allowlist。

# 原120项 v16：L1-10 原生接受，但亲检发现色轮缺陷

日期：2026-10-24。原目录项目：L1-10「情绪涂鸦色轮」。源码冻结：`cf8b95427b778714c5d2fce8f14f557e4ee04903`。

本项原生结果为 `completed / passed / QA approved / ProjectOutcome accepted`，资源自动闭合；ROOT 对真实网页和实际下载 SVG 亲检后，产物质量判为 **FAIL**。两种结论分别保留，不回写原生历史。

当前本批累计：**12次尝试、10个不同项目、12次终态质量判断；110项未启动，严格交付成功仍1项**。12次产物判断为8次FAIL、3次限定所测场景通过、1次INCOMPLETE。不是120项完成，也不代表整个平台完成。下一原目录项目为 L2-11「星际失物招领站」，尚未启动。

## 原生结果与版本

| 事实 | 当前证据 |
| --- | --- |
| run | `f6c080014ec34b5ab2951f744b5df133` |
| 独立实例 | `engineering-original120-v16-l1-10` |
| workspace | `/home/dains/Documents/polaris-bench-projects/original120-20261024-v16/L1-10` |
| 正式链路 | collaborative：PM6次、CE7次、Director86次、QA49次逻辑请求 |
| 当前候选 | revision85；正式验证、QA起止和当前19份源码hash一致，QA额外绑定当前可执行文件 |
| 正式验证 | 首轮1017失败；同一Director准备合法输出目录后1054通过7项；1369通过10项 |
| QA | 1953提交批准，1964完成；源码及可执行文件与当前候选一致 |
| 当前结果 | seq1966：completed/passed，release_allowed=true；权威投影 accepted |
| 原生耗时 | run约3121秒；外部Driver启动到结算3214.007秒，约53分34秒 |
| 资源 | Supervisor正常停止；Driver与归档均exit0，后端/前端/Driver/归档原PID消失；主实例保留 |
| 版本稳定 | ROOT复核9521份源码文件、5份冻结输入，零差异；运行期间没有热改 |

必需原目录检查保留：`go_compile`、`min_files:3`、`content_any:mood|color|wheel|doodle`、`source_target_coverage:**/*.go`。实际Go构建、vet、54个测试及Go HTTP入口存在执行证据；测试数不替代质量结论。新运行仍由 EngineeringRunService 与单份 engineering SQLite 持有事实，没有重启旧合同链。

Verifier新策略 `bash_pipefail_v1` 已在真实生产链消费并持久绑定全部命令，包括含管道的npm检查。普通Director/QA命令仍使用POSIX语义。当前真实运行证明了该执行策略的集成与合法失败后的推进；直接的失败管道反例仍来自此前模块实验，不能将本项质量FAIL改写成整个平台成功。

## ROOT真实操作与产物质量

ROOT未编辑目标源码、未代装依赖、未重建目标、未代跑其构建/测试、未补造原生收据。只复用平台已生成的 `dist/mooddoodle` 与原始web资源，在只读输入、私有输出、私有网络的K1环境中启动真实Go服务，由真实浏览器进行公开操作。

实际观察包括：首页与真实画布；空画布导出；未启动节拍的两笔开心；四类情绪和未知词木星；三笔开心一笔忧郁；实际指针多点拖画、页面计数与下载SVG。空报告显示「暂无笔触」且SVG没有伪造百分比；木星出现可理解提示与灰色线；75%/25%及五类各20%的页面和报告相符。

ROOT另用此前已验证的只读SVG字节渲染器，查看四份**原始下载字节**，并亲自查看首页、75/25实际绘画页面和四张SVG图。该渲染器只展示已经下载的报告，不替代Go业务服务、图像生成或原生验收。临时HTTP字节服务与浏览器均关闭，原字节hash不变。

**实际缺陷：单一情绪占100%时，色轮彩色环消失。**

最小复现：新页面不启动节拍，选择开心，实际拖画两笔，点击导出。页面和SVG正确显示2笔、开心100%，两条线也存在，但色轮只有外框与「2笔」，没有应有的彩色环。五类20%和75%/25%对照图能正常显示环，排除了整张报告未加载或截图空白的解释。

原始SVG外弧：

```text
M 750.00 322.00 A 78.00 78.00 0 1 1 750.00 322.00
```

内弧也具有相同起终点。第一处偏差属于目标产物几何：`internal/color/color.go:154` 的 WheelPath 始终为边界生成单个弧；`internal/doodle/doodle.go:441` 在某类别占100%时传入完整360度。按照[SVG 2路径规范](https://www.w3.org/TR/SVG2/paths.html)，弧段起终点相同会被省略。这与实际图像一致。目标文件未修，缺陷保持REPRODUCED/UNFIXED。

可推翻解释的结果是：同一未改SVG在实际浏览器中显示完整彩色环，或源输出实际上使用了不同端点/完整圆构造。本次证据均未出现。

## 验证边界与审计遗漏

原生QA确有独立源码、构建、测试和真实HTTP检查，批准绑定当前候选；不是角色自述替代所有执行。但它没有完成真实浏览器拖画到报告的循环，也没有实际看到导出SVG像素。其报告明示无法把私有临时SVG交给只读浏览器入口。API/几何检查遗漏了本次100%圆环缺陷。

保留14次QA工具失败，以及2次shell返回0但内部Python报错的事件。一次停服命令exit143，pkill模式与外层shell命令文本重合，自匹配终止是强支持推断，未捕获受害进程轨迹；另一次所谓停服检查实际仍HTTP200，随后检查不同关闭端口只证明拒绝连接，不能替代真实页面停服导出。不得将这些尝试补记为验收通过。

ROOT自己的P05在合法启动60BPM后，首次可见拍号等待超时；未采集到足够DOM/调度证据，静态诊断不能归因为产物问题。P06按下锁定、P07非法BPM保画、P08清空重置、P09实际停服导出UI及声音效果未完整亲检，保持NOT_RUN/UNKNOWN。已有明确色轮缺陷足以判FAIL，不宣称未执行项通过，也不继续把这些研究变成当前失败结论的前置条件。

测量系统错误也保留：脚本文件名遮蔽stdlib需使用Python `-P`；KFS导入在私有环境中牵入8个解析器/类型库依赖；JS禁用的SVG截图超时；独立渲染A/B两次因回调序列化、原子写入staging路径错误而没有形成有效对照。**没有A/B因果结论。** 原有失败文件与UNKNOWN未改写，Root后验进程证据另存。审阅首次Ruff还更新了现有主仓 `.ruff_cache`，后续均 `--no-cache`，没有删除历史缓存。这些是外部测量过程问题，未作为Polaris原生缺陷数或Bench尝试数。

## 成本与上下文

148次逻辑请求对应296份prepared/terminal快照；ROOT独立复核了所有CAS与完整wire hash、身份、工具和run/workspace绑定。不能将296份快照当作296次实际HTTP调用。实际物理POST次数及费用保持UNKNOWN。

148份终态provider-reported usage合计input296761、output90273；这不是账单。34次含图逻辑请求的真实视觉及最终上下文token总量未知；序列化proxy不冒充实际视觉token。外部观察与开发工作的总费用也未核定。

## 已关闭与下一步

本项关闭的是：冻结真实运行、当前候选验证/独立QA/权威结果/自动资源结算的事实核对，以及足以判FAIL的真实质量反例。没有外部代修，没有降低原目录要求，没有回写历史。

下一条最有证据的机制改进是**让独立QA能够获取、渲染真实工具产物并观测必要动态入口**，复用现有KFS、Browser和KernelOne进程能力，仍由EngineeringRunService持有身份/权限/生命周期。先用失败报告与正常多类别报告做有限反例/正控，验证相邻生产、消费、持久化和释放路径，再在新冻结批次继续原目录L2-11及后续项目。不能靠新增「开心100%」业务特例或重写角色合同绕过视觉能力缺口。

公共KernelOne进程句柄的三项有限模块实验已证明私有服务可保持并显式释放；尚未接入工程工具，也未证明跨沙箱浏览器连通。PM阶段焦点等实验准备不计为模型效果结论，未验证的旧特性不因存在设计文档就默认恢复。

## 本机证据

- Case与终态报告：`/home/dains/Documents/polaris/.polaris/bench/original120-20261024-v16/cases/010-L1-10/reports/010-L1-10/`。
- 终态物理请求审计与ROOT复核：同case的 `terminal-evidence-audit/`；296CAS及当前源码/可执行文件hash已核对。
- 亲检质量与缺陷：目标 `.polaris/audits/root-personal-quality.json`、`root-quality-defects.json`。
- 实际浏览器公开操作：目标 `.polaris/audits/ROOT-quality-review/browser/observation-2afde735dab641daa2355cc65976048c/`。
- 四份原始SVG渲染：目标 `.polaris/audits/ROOT-quality-review/svg/observation-1792816751207640690/`。
- 完整目标树pin：`8244e54e164de8014d73aaf82aac23ccc4cf647ad0af21d537acfcc0d1784d34`；可执行文件SHA256：`ecc4785463b0340ae33ea101882bde08499b45db2ce1769364ccc347bd9c1a03`。
- 源码/输入冻结复核：同case的 `ROOT-postnative-freeze-readback.json`。
- 进程模块实验：主仓 `.polaris/audits/process-handle-continuity-20261024/ROOT-review.json`。

以上报告是外部研发与质量判断，工程权威事实仍来自原workspace的engineering SQLite和正式投影。

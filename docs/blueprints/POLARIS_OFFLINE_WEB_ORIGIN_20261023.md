# 离线构建页面的真实浏览器入口

状态：组件实现、独立复审与最终源码资格已通过；主线集成／新冻结原生交付另行记录，未宣称新项目成功。基线源码 f71eb109a5070b21c84667ccf81c0be761a3797f。

## 用户问题与证据

原目录 L1-01 v7 的实际 run 为 `5ff76e3713924bb9974dbcd7b662a208`。PM/CE 交接与实际代码生成成立，核心28测试和若干当前候选验证通过；后续反复构建预览使验证失效，QA始终没有开始。ROOT于原5400秒预算尚未耗尽时正式取消：总wrapper2173.05秒，项目driver2157.02秒，最终 cancelled / verification unknown / QA not_run / ProjectOutcome unknown。不能称预算失败或交付成功。

- 原生578/580：HTTP entry被现有本地文件权限语义正确拒绝。
- 原生611/613：相对资源已经定位到项目dist/assets，ES module/CSS仍被file://的null origin CORS阻断；工具观察成功不等于应用运行。
- 原生633/635：Director生成inline preview后出现真实应用DOM；其后产物绘制错误也经实际浏览器反馈自行修复。
- 1294验证后→1519观察前：完整2062条输入中只有preview的mtime/ctime变化，字节、inode、mode、大小不变；1507/1508真实命令与NEXT请求说明重写、失效、复验循环。不能由此推导应放宽所有输入元数据，因为任意命令可能读取stat。
- 独立合成实验两次经过真实PermissionScope、现有capture adapter、K1 sandbox与Chromium：`/assets`加载失败，`./assets`执行JS标记。该实验隔离了classic-script根路径语义；ES modules只由上述原生CORS证据覆盖。

原件：`.polaris/bench/original120-20261023-v7/`，目标只读证据位于`/home/dains/Documents/polaris-bench-projects/original120-20261023-v7/L1-01/.polaris/audits/`；合成原件在`.polaris/audits/native-tool-context-integration-20261023/browser-entry-baseline/`。ROOT已核9480冻结文件和输入不变、native资源闭合，亲检94张原图中11张，质量FAIL（暂停月相立即生效、画面发光未消费闪光时间）。无外部目标修复。

## 假设与选择

假设：让普通构建页面在隔离的静态Web origin中读取已授权本地资源，可以避免为了file://观察而重新拼接目标产物。它可能消除本次循环的诱因，仍须新原生运行检验；不保证模型不会再重复写入。

选择复用现有Playwright路由，在原浏览器子进程内提供由KFS支持的静态响应。相较另启HTTP server，它不增加socket、端口或服务生命周期；相较放宽文件CORS或全局失效规则，它把变化限定在已授权的本地浏览器读能力。该选择由ROOT作出，属于用户已授权的可逆平台工程。

## 边界与数据流

```text
现有browser_observe(entry, 可选web_root)
  ToolSpec归一化 -> 原PermissionScope/BrowserPolicy -> 本地路径验证
  -> 原K1进程/网络隔离 -> Chromium受控静态origin
  -> KFS读取web_root内资源并fulfill -> 原始图像/DOM/console/拒绝结果
  -> 原工程journal -> 最终provider request -> 当前候选QA
```

owner仍为roles.runtime / EngineeringRunService；唯一耐久事实仍为现有工程SQLite。ToolSpec、KFS、sandbox/process、ContextOS与视觉重访均复用，不新增Cell、controller、ledger、数据库或realtime通道。

输入：原entry字段保持workspace相对HTML/SVG；新增可选`web_root`，目录必须存在、在workspace内、包含entry，并完全落在既有读权限。未提供时保留原file模式。普通绝对路径、空值、越界根、symlink、路径穿越及隐藏运行/密钥目录明确拒绝；模型参数不能扩权。字段自动进入已有JSON工具调用持久化，不新增schema表。

运行：虚拟origin使用保留的固定origin标识；所有请求在Playwright路由中截获。只对该origin的GET/HEAD读取指定根内常规文件；不启动HTTP服务器、不发真实网络请求。新静态模式对其他origin、file等协议及非读方法明确拒绝；旧file模式保持原执行行为，默认网络隔离不变。所有原read_roots只校验授权，不成为Web可寻址根；每次KFS读取仍校验web_root containment。禁用service workers以保持请求可观察。读操作通过KFS/guarded snapshot，保留真实MIME、字节、资源错误和路径拒绝；观察本身成功不自动判定应用通过。

预算：沿用现有30秒进程、总wait、图像/frame/wire、取消和drain边界；静态资源单件16MiB、单次累计64MiB，超限留下明确资源错误，不读取无限文件或静默截断。所有浏览器profile/cache/截图仍在项目`.polaris`下。

输出：原entry与图像/DOM/console保持，capture结果记录实际web_root与静态origin模式。重访返回原始图像及原绑定，不能伪装为当前新capture。静态响应不写目标源码或构建产物，所以观察不会制造新的候选。当前检查、权限、QA与UNKNOWN语义均不改。

## 隔离解释器的KFS交接

实测RED阶段确认：原浏览器子进程为`/usr/bin/python3 -I`，没有Polaris包可import。不能为此把整个backend挂入读权限。现有`kernelone/fs/guarded_regular_file_snapshot.py`仅依赖stdlib，但此前无公开runtime-source接口。

最小接口：K1公开`get_guarded_snapshot_runtime_source() -> str`，无模型/项目路径参数，使用同一guarded snapshot读取固定包内模块（256KiB上限、严格UTF-8）；fs facade导出。模块源码是唯一现有实现的可信分发，包含 dormant write helpers，本身不授予只读或写权限。

Cell只调用公开API；在父进程纯内存编码/压缩，child校验完整性/边界并先注册私有ModuleType到sys.modules，再compile/exec该可信实现，仅绑定read_guarded_regular_file_snapshot。target读取仍走同一KFS guard，原OS sandbox仍是实际效果边界；不读private实现文件、不把LLM/项目源码当可信runtime、不挂backend或扩大scope，不维护第二套guard。getter的包内静态源码读取和受控源码加载属于技术runtime装配，绝非项目验收证据。

此窄接口新增两个K1生产文件与一个测试；浏览器两个生产文件消费它。ROOT已核原RED为2个public API missing，并完成2个公开/真实isolated child GREEN与相邻guard、三文件style/type；尚不替代整组资格或原生交付。

## 反例、接受与停止

先使真实浏览器的绝对asset+ES module+CSS夹具失败，再通过新字段让同一公共DOM标记和computed style实际成立。负例覆盖越权根、entry不属于根、隐藏路径、URL穿越、symlink、外部网络和非读方法；原file模式、取消、重访必须继续成立。真实ToolSpec序列化/重建、普通归一化、service/cold读取与图像进入实际native body需相邻验证；替身只证明所替换的模型/transport接缝，不能冒充真实模型交付。

组件接受标准：上述反例/控制、必要相邻测试、Ruff/format/mypy、架构/K1 ALL/catalog增量与独立审查满足后结束模块研究、集成冻结。随后仅一次全新原L1-01（PM→CE→Director→QA，5400/6000预算，原验收）检验交付；ROOT亲检并闭合资源后再推进原120。测试数、旧通过、虚拟HTTP标识或模块DOM标记不替代真实交付。

推翻假设的结果：新origin仍不执行模块；读权限被扩大；真实资源不能绑定；需要松开外网/sandbox；或新原生仍反复重造预览而未更快进入有效QA。后者保留失败，转查实际当前反馈/执行策略，不追加项目专用规则。

## 相邻恢复消费者反例

真实service/SQLite/两种native协议夹具在15PASS/2FAIL处发现：初次`path`别名可合法capture，durable dispatch保留raw arguments；冷重访却直接读取`entry`并使用默认index，合法的原capture被错拒。修复仅在现`EngineeringRunService._observe_dispatch`通过同一ToolSpec/EngineeringTools先归一化saved args，再以当前host scope检查原entry/root。原raw provenance、历史像素、原绑定与错误事实保留；不补造新capture、权限或状态表。这属于本接口的持久化/恢复消费者迁移，不能用canonical-only测试绕过。

## 组件结果与审查边界

K1公开接口原RED2例、修后GREEN2例；ROOT相邻guard79项通过，原guard AST去除新增getter后与基线完全相同。浏览器组件原file模式不执行模块；新静态模式在真实K1/Chromium中执行绝对ES module与CSS。19项新组件与原重访8项通过，模型/transport在该组为明确替身，不证明真实模型交付。首轮失败、工具注册错误和冷别名反例均留存，不合并成独立交付缺陷计数。

独立审查真实复现OWO-R1：DOM控件value getter在截图后发出超限HEAD，旧检查只在截图前，最终出现资源错误却返回可用图像。超限字节实际被guard阻断，没有证明越权读取；结果时序仍必须修复。修复应在所有相关浏览器事件处理和关闭之后、成功输出之前检查sticky预算失败，并用真实延迟回调做RED/GREEN，不能只覆盖初次加载。证据在`.polaris/audits/offline-web-origin-20261023/{implementation,review,kfs-producer}/`，最终资格另由ROOT签发。

资源`bytes_read`仅为成功快照字节。失败且读取效果不明时保留预留预算，可能早于64MiB成功载荷耗尽；未知读取不能退成零或宣称精确物理读取总账。公开runtime源码包括dormant写helper；实际authority仍由既有OS sandbox和内部只读调用保证，不把源码束当只读权限。当前压缩载荷足够小；未来源码大小上限本身不证明任意压缩率都适合argv。

## 最终组件资格

ROOT最终同一8文件指纹：346项相关回归、40项架构检查、8文件Ruff/format/mypy通过；KernelOne ALL12阶段通过（368 passed、1 skipped）。Catalog为159条历史诊断／115个唯一指纹，与原只读历史baseline完全一致，新增0、manifest mismatch0；未清旧债。OWO-R1原复现独立复跑一次，失败明确、0图片、0pending；原RED保留。23项新浏览器组件包含4项late回调控制，源码／fixture与测试替换边界见原件。ROOT亲看合成ESM/CSS标记原图，不能当作项目美术质量。

资格原件：`.polaris/audits/offline-web-origin-20261023/root-acceptance.json`与`root-source-manifest.json`；完整日志在同根`root/`，独立复审在`review/rereview/`。该资格允许集成和新隔离实测，绝不替代PM/CE/Director/QA、当前ProjectOutcome、ROOT质量或资源闭合。

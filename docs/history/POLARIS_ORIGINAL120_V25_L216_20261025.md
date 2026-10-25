# L2-16：真实桌面、声音与交付后的独立质量复核

累计20次全新原目录尝试、16个不同项目、2次限定范围历史严格成功，104项未启动；另记1次同run人工恢复和2次单独预算的ROOT反馈任务。版本不同，不计算冻结版成功率。L2-16本次严格质量失败，原生accepted事实保留。

## 原生结果与边界

原目录第16项「会唱歌的植物图鉴」，冻结主线 `f6a7428eaa66d8dc2c879096c8d7e13ee9b9ea40`。全新workspace：`/home/dains/Documents/polaris-bench-projects/original120-20261025-v25/L2-16`；run `43b08b7fe4814095a20c98d24ff90398`。原始需求、任务重点、目录检查、模型、主机权限、collaborative与独立QA均保留，正式预算5400秒／外层6000秒。

PM、CE、Director、QA实际调用；Director自行准备JDK、写代码、编译、执行、吸收反馈修复。候选140，Verifier1591／attempt1584通过，QA1899批准，1901结束且release_allowed成立。正式驱动1795.257秒，自动CLOSED／exit0；原进程12569／12996出生身份消失、49978／5174释放、Registry stopped，main／NATS／主前端原进程保留。停止后的acceptance UNKNOWN只描述清理投影，不能倒写清理前accepted。

33个作者文件包括20个主Java文件、9个测试文件和4份文档／构建材料；SDK的215个普通文件、205个符号链接另计。三个真实正式配方退出0，包括49项自身检查、无桌面样例和Java11兼容编译。检查数与文件数不是质量证明。

9551项平台冻结文件、28个输入针及当前Verifier／QA输入与结果完整匹配。221个tool started均有真实observation，其中215个模型调用与6个嵌套验证命令；18个历史失败观察保留，不计为18个独立缺陷。154次逻辑LLM调用对应308份原始CAS，身份、wire、工具和usage核对无差异。物理POST次数、账单保持UNKNOWN。

## ROOT实际个人检查

ROOT只消费平台已经生成的Java／classes，没有外部编译、安装、修改目标代码、代跑原生验收或补写原生事实。所有应用数据、截图、日志与声音写入隔离的输出能力，再经KFS归档到该项目`.polaris`。

五个成功桌面观察会话共有85条有序观察步骤，其中55条鼠标、键盘或重开动作，其余为快照／等待。另实际执行4次公开CLI入口。每次完成均核对319个当前候选普通文件未变、进程已等待关闭、正常捕获目录删除。四次较早的外部观察器错误另记，涉及Unicode窗口名、未映射窗口聚焦；不能计作产品缺陷。一次已结束的失败捕获作为明确的非活动取证材料保留，不能冒充仍运行的进程。

亲检确认：

- 薄荷区间3..6，初始4；推进得到3／成长1，再推进2／成长1且偏干；浇水到5但不生长，再推进4／成长2。上下边界均有实际序列。
- 成长达到5后继续到第13天仍为5；成熟植物仍可浇水、推进，湿度0和10夹紧成立。
- 两株同名twin具有不同ID，状态与日志隔离；保存、重开后继续推进，日志连续。
- 非法湿度11出现真实界面提示且未创建错误植物；规则表可见。
- 同一P1、同一天5、同成长3，仅从偏干湿度2浇水到适宜5，分别真实导出。两份WAV均为8秒、44100Hz、16位单声道非静音；事件表分别为MIDI48/54/60/66与60/64/67/72。ROOT在八个音符内部窗口测得的主频与声明MIDI一致，PCM哈希不同。未据此宣称进行了音乐审美评价或逐样本等价证明。

这已是可操作、有真实数据与声音的工程产物；界面采用朴素Swing样式，不能把样式偏好当作原目录新增门禁。

## 两项实际桌面缺陷

**浇水后的列表状态过期。** P1浇水后详情与消息显示湿度5，选中列表仍显示2；P2后来详情10、列表仍4。`water()`只刷新详情，没有重建列表字符串。同一根因只计一次。真实截图与数据已封存，不是静态猜测。

**损坏存档启动无界面反馈。** 预置一个正常公开存档与一个坏文件，程序安全隔离坏文件并保留正常植物，但桌面的消息区域为空，也无警告。实际stdout同样为空。`CodexFrame.launch()`调用load后，没有把store.messages传入初始界面。CLI能够提示，不代表桌面入口已经提示。

独立离线审阅确认上述两项Important。README／内置帮助误写`target/classes`，实际`dist/classes`，另记Minor；可用构建入口保留。第一失败产物、classes、图像与坏档反例都在任何修复前封存。ROOT未代修目标。

## 平台缓存边界仍未收敛

Director191／202通过合法workspace及网络权限下载、解包JDK，352529290普通文件字节落在workspace `.toolchain`，未进入用户要求的项目`.polaris`。这是存储位置和能力接口缺口，不是权限逃逸。

实际工具说明写明临时缓存按调用丢弃、持久文件放workspace授权根，同时整个`.polaris`控制目录不可写。不能只要求模型把目录换进`.polaris`，也不能在外部移动目标文件伪造收敛。Verifier当前忽略整棵`.polaris`，未来受控持久缓存还必须进入现有候选指纹、验证时只读、变动使旧证据失效；不能把依赖藏起来却继续宣称验收绑定完整。下一模块只关闭这条边界，复用现有Cell、KernelOne、KFS和唯一工程owner。

## 图形环境实验

原生桌面探测失败首先混用了不同命令的Xvfb／临时目录。工具描述已说明隔离，不能据此证明必须增加持久进程功能。后续探测也没有完整保留Xvfb readiness／stderr，具体历史因果保持UNKNOWN。

ROOT另外用与目标业务无关的最小Java程序，经真实EngineeringTools／KernelOne执行固定三调用：编译成功，network true／false的Xvfb均在Java启动前signal11崩溃，日志含NVIDIA EGL／GBM及swrast栈。另开单假设实验，只添加`-extension GLX`后，readiness、Toolkit、窗口绘制／释放和PNG均成功；源码与JDK针未变。该证据支持GLX敏感的启动问题，不能扩大为已证明精确驱动根因。

ROOT之后在明确的软件显示环境中亲检真实桌面。该环境不同于失败的原生默认Xvfb探测，没有向原生运行注入反馈或修改模型、代码、配置。最小实验不是正式项目验收，Root亲检不是原生QA收据。

## 主动审计中的反证

实际浏览器验证用户的`59977/launcher`通过49977正确显示L2-16。实例页106.25秒内收到连续132条新工程事件249..380，DOM从248更新到380，没有跨workspace、全局bench订阅或定时HTTP实时替代。

PM／CE／Director真实最终请求包含正确身份、原目标和工具，模型输出上限524288。最初对Anthropic system token漏算的猜测，被现有producer与9个纯夹具、三份实际CAS复现推翻：system已经计入message_token_estimate，故未添加补丁。普通Context快照与final-request接口均200；后者与实际原生body匹配，不要求普通消息快照也嵌入完整body。

## 证据与下一步

- 原生完整性与当前候选：`.polaris/bench/original120-20261025-next-l216-preparation/ROOT-terminal-integrity/`。
- 最终结论：同目录`ROOT-final-assessment.json`及`ROOT-personal-adjudication/report.json`。
- 真实GUI、CLI、完整输出与数据：目标workspace `.polaris/audits/ROOT-personal-quality/`。
- 首失败封存：目标workspace `.polaris/audits/ROOT-first-quality-failure-c140/`。
- 缓存首次偏差：准备目录`storage-boundary-audit/`；图形实验：`gui-synthetic-runtime-experiment/`。
- 最后9551针及冻结释放：准备目录`ROOT-source-freeze-release.json`。

本次原生交付事实与资源闭合成立，但严格质量FAIL。下一步关闭受控持久缓存与验证依赖绑定，再以真实视觉证据改善验收；不靠继续堆叠合同或提示规则。原120项、最终鹈鹕骑单车网页动画、长期稳定性、ContextOS和简单Agent对照均未完成，长期记忆不在本轮范围。

# v13 L1-07：产物亲检可用，原生验收缺少必需GUI证据

本次结论分列：**原生completed／passed／QA approved／accepted，资源闭合；ROOT产物在所测场景通过；严格自主交付未通过**。原合同明确“桌面操作须真实产生一次结果”，Director与QA均承认没有实际点击/检查窗口，却批准了当前项目。ROOT事后的GUI观察不能补造成原生验收收据。

冻结main `9bf5063e3d9d676a8d3257aa70c04d606f81011e`；9514份源码/操作器、181777463字节与冻结输入全程未变。正式collaborative入口，全新隔离run `6f0badbbb44e424d80cab55e224e7092`，项目预算5400秒，driver实际约1600秒。原四输入保持原字节，另以独立host-java配置显式增加JDK21 bin/lib/conf三个只读根和bin搜索路径；原模型/输出上限、写根、网络、预算及browser.require_visual_review=false保持。该环境差异不与前一C++项目作因果胜负比较。

PM、CE、Director、QA各运行一次。真实Java业务、Swing入口、脚本、测试、示例与导出落盘；最终六条检查实际编译、285断言、三种脚本场景/导出等通过。最初正式检查因.build-root、build/test输出及out写入受限真实失败，Director在同一任务中调整路径到原授权dist后复验通过。新实现的declared/effective/absent信息已进入实际Director请求与真实Verifier attempt，当前均为dist/ dist/空；这是信息到达与权限一致性的产品证据，不证明它单独改善质量/成本。

工具错误同样如实保留。两次write_file因空文件名被拒，原供应商tool_use确实给了file=""，解码没有丢字段；平台没有从代码中猜文件名或补造写入。Xvfb/图形程序检查发生30秒超时，物理owner确认清理；不能把超时或启动日志当成实际GUI结果。普通失败均返回原Director，没有重启PM/CE或扩大授权。

QA进行了有价值的独立执行：在私有临时空间重编译并检查判定、音频/表格和多项变异；这些是当前工程内核的真实证据。问题是批准语义：其report_review approved=true，同时明确没有点击真实窗口或检查Swing画面，并以环境限制为不否决理由。原Goal的必需GUI条款在QA最终provider body中真实存在。代码审查可批准与完整交付接受在这里发生了错配；是否能仅靠澄清approved语义改善，已另作有限A/B预登记，尚无实验结论，不据此新增合同/角色/门禁。

ROOT在资源闭合后做了两类独立只读亲检，所有输出在该项目.polaris/audits/，不改变原生状态或目标代码：

- **16次公共CLI**：读取现成dist/classes，用绝对JDK21在无网、空write_roots的K1 namespace运行，输入/导出只在私有/sandbox-output。验证±100含边界、±101拒配、单拍去重、12/11/4/3/0命中性格边界、空输入、非法输入；14组实际WAV/CSV均2秒、PCM非零、声部位置与事件表一致，三种pattern确实不同，重跑字节一致。步进索引按实际NoteEvent/EventTable的0..15解释，没有凭编号习惯制造偏移缺陷。
- **真实Swing窗口**：同候选只读class，隔离软件Xvfb、实际Robot鼠标操作开始/击打/结果/导出/重开。实际得到16命中活跃、8命中好奇、零输入害羞；三种画面已亲看，同一窗口重开当前分数归零。各性格GUI导出的WAV/CSV与CLI同个性格字节匹配。89份原source/classes/scripts/原产物前后指纹不变。ROOT未重建项目，仅观察probe由JDK source-file launcher在内存/私有空间编译。

亲检过程也保留失败：原观察器在持续Swing刷新时用Robot.waitForIdle阻塞约10秒，返回时一局已经结束，故误以为开始后击打没启用；原生鼠标事件时间戳定位并推翻了单纯“等待状态太短”的解释。去掉全局idle等待后，另一次采样又先读到了上一局导出；没有把文件存在算新效果。最终用真实MouseReleased确认再过EDT屏障，实际新mtime、不同性格和独立字节比对闭合。旧失败/陈旧采样都保留，不改目标程序、不增加产品重试/超时。

这也反证“沙箱不能显示该程序”的笼统判断：同样窄JDK根、只读workspace、无网络的K1环境实际完成了窗口和点击；euid/xkb警告不是fatal。ROOT工具不等于Polaris能力，不能据此声称平台已有桌面视觉、普通PNG读取或私有命令截图返回能力。

指标：151个逻辑调用（PM15、CE3、Director99、QA34），302份prepared/terminal原始包全封存；ROOT重算全部key/wire hash、实际身份、tools计数、模型输出524288与窗口100万，未发现不一致。最大已知最终请求估计92466 tokens。214条工具观察均收尾、0pending。实际HTTP总数、账单、人工分钟保留UNKNOWN；断言、变异和快照数不等于交付成功。

原driver13138、backend13258、frontend13398均退出，49978/5174在kernel TCP/TCP6无监听，主实例/NATS/frontend出生标识保持。原accepted、历史FAILED/CANCELLED/UNKNOWN均不改写。本次产物在所测场景可用，完整无人值守交付仍因原生GUI验收缺口失败。

证据根：`/home/dains/Documents/polaris-bench-projects/original120-20261024-v13/L1-07/.polaris/audits/`，重点为root-personal-quality.json、root-quality-defects.json、root-all302-final-requests.json、root-real-desktop-evidence-gap.json、root-terminal-resource-recheck.json及ROOT-quality-review/。主仓.polaris/bench/original120-20261024-v13/cases/007-L1-07/保留原合同、冻结、正式报告、实时UI和预登记。研究材料不是第二产品事实源。

当前连续测评：9次尝试、7个不同项目，9次终态均亲检；严格自主交付0，113项未启动。产物6次FAIL、2次仅在所测场景通过、1次INCOMPLETE；不同版本不混成冻结版成功率。下一步有限核验QA批准的含义，继续原L1-08。普通图片读取与跨调用进程的原能力复用仍在独立证据队列，未实现的部分保持未实现。

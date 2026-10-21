# Director 与 QA 原生视觉闭环

状态：实施中。用户明确要求 Director、QA 真正支持视觉，最终仍以鹈鹕骑车动画网页验收。长期记忆不做。只在 main 实施，不热改既有运行。

## 事实、问题与有限假设

冻结 bef24 原生网页 a420e40bb7434b1e85bd50d2fbfadfcd completed/passed/text-QA approved，原资源关闭。主 Agent 与独立审阅实际查看多时相桌面/手机帧，发现腿脚在曲柄附近旋转但与鸟身不连接。源码与当前Verifier761/QA847/实际渲染hash一致；旧判决不改写，root质量FAIL已封存。原测试证明时钟/旋转/控制，不能推出完整骑行动作正确。

当前 ToolObservation.content:str 与工程循环把结果JSON当文本，图像未进入模型。真实MiniMax-M3原路由三张随机位置/颜色/形状图片探针均回答正确，最终物理body图像bytes匹配，未换模型或改全局配置。因此优先修平台信息链，不把缺陷归咎于模型必然不会看图。能力探针只证明简单图像理解，不证明项目质量。ModelCatalog的默认vision=false不阻止这次实际传输；它不是实际模型无视觉的证据。

已验证可复用：当前Anthropic canonical image块、K1结构预算与SQLite多模态消息冷读；K1实际只读命令sandbox能够启动既有Chromium并取4帧、暂停后帧相同，网络/写入被拒、源不变。OpenAI普通user list内容被str()字符串化是可复现适配缺口。

假设：真实帧进入Director的后续请求能支持视觉修复；QA独立重新取当前帧并看到它们，可减少这类错误批准。推翻：图像最终没有送达、冷恢复丢图/身份、QA复用旧候选/他人截图、已知坏案例看图仍误判，或新网页质量仍失败。失败原样报告，不能靠次数/规则增加自证优势。

## 最小结构与状态所有权

继续复用 EngineeringRunService + engineering/sessions.sqlite3 唯一工程owner。roles.runtime增加通用本地页面观察技术适配，复用KFS与K1 sandbox/process；不新建browser业务Cell、controller、图像总账或旧Factory/TransactionTurn准入。

`browser_observe` -> 当前输入binding -> 全新只读浏览器捕获 -> 原始图像附件与持久tool观察 -> 由持久观察构造模型图片消息 -> 真实provider body/ContextOS -> 模型回复/工具修复 -> 重新观察 -> 当前验证 -> QA独立取帧/判断。

## 固定程序接口

公开值对象放原 engineering_run.py：

- BrowserObservationConfig(executable:str, python_executable:str, python_paths:tuple[str,...], require_visual_review:bool=False)。全部来自host；不由模型选择驱动或扩大mount。PermissionScope追加browser:BrowserObservationConfig|None=None；旧配置默认关闭。
- ImageObservation(media_type:str, data_base64:str, width:int, height:int, sha256:str, label:str)。实际浏览器产出、有限尺寸/bytes、hash验证。ToolObservation追加images:tuple[ImageObservation,...]=()。
- tool_observation_payload(observation) 保留原非视觉序列化形状（空images省略），图像场景存完整附件到同SQLite；公共event/HTTP投影仅metadata，不向NATS/UI广播base64。不可更改原发送的图像bytes。

通用工具只收本地entry、有限viewport、时间帧及简单locator点击/按键；不接受远程URL、任意JS、任意shell、driver路径或持久浏览器。默认2帧，最多4帧；宽高有界，JPEG/PNG总bytes有界，超限显式失败不静默截断。使用相同host read_roots、强制write_roots=()和network=false，依赖必须已显式授权；私有临时profile、截图与进程一次性关闭。缺环境明确不可用，不安装依赖、不裸跑。支持Linux/WSL2已测边界。

browser适配导出 async capture_browser_observation(call_id, arguments, *, workspace, scope, run_process) -> ToolObservation。run_process(argv, timeout)复用现有真实物理owner；不得另起ProcessSupervisor。Core owner前后调用现有verifier.review_binding并在同SQLite追加视觉观察事实；前后输入不一致不得标成功。

## 消息与恢复

附件原始bytes及metadata在同份tool观察中持久化。构造AIRequest时从持久工具消息得到canonical图片视图：先完整输出该批所有tool_result，再追加独立user图片块（Anthropic image/source/base64）。不在并行tool结果中间插图，不破坏call/result关联。图片文本标注来源call、入口、当前候选；模型无需填写hash。

每次都可由持久观察重建视图，无需重放浏览器/写命令。旧记录无images继续按原语义读取；未知effects仍interrupted/UNKNOWN，不因图片绕过恢复。保存browser配置随scope冷恢复并核对当前host，不静默切驱动或扩大授权。

K1 Anthropic既有转换复用；OpenAI正确转换canonical图片为image_url数据URL，禁止str(list)伪视觉。Provider最终body必须包含相同图像bytes。原预算结构保持/超限明确拒绝；图片数/尺寸/bytes额外有界。视觉token公式未确定时，总上下文token/窗口占用为UNKNOWN，序列化估算单列，不能用base64长度或messages-only冒充真实视觉token。未知账单/HTTP次数仍未知。

此次明确迁移 Anthropic Messages 与 OpenAI Chat。独审复现 Responses 的 fallback 仍把 Chat image_url 当作该协议输入；当前对这个尚未迁移的图片路径明确报 unsupported，保留历史纯文本与显式原生 Responses input 路径。旧 Factory text-only semantic projection 不作为新 Engineering 视觉 authority，也不因本次工作重新启用。

## 当前候选与独立验收

host显式设置browser.require_visual_review=true时，Director在最终验证前须已看到当前候选的真实帧；QA批准前须在自己当前frame独立调用观察，并在批准所依据的真实provider request中包含匹配图像。缺少图、只保存文件名/hash、同一工具批次尚未进入后续请求、过期候选、Director提供的PNG、source drift均不满足。QA可以明确拒绝而不被“必须有成功图像”阻塞。

这只是可检查的证据条件，不替模型作视觉判决，不要求模型维护合同。默认其他任务关闭；能力可用与验收是否必需分别由host声明。普通失败回同一合法角色循环；写入后旧图失效。保留原source-read、独立QA当前binding和实际验证机制。

视觉候选比较仅忽略 `verification_seq`：新增只读验证收据不会改变画面输入。原始完整 binding 仍落原日志，采集前后完整相等；workspace_revision、实际输入、scope、profile、required_checks 任何变化仍使旧图失效。QA验收本身继续绑定完整当前验证结果，不弱化原有验收新鲜度。

## 实验与停止条件

1. 原生传输RED/GREEN：OpenAI多模态被字符串化反例；两个protocol实际body图像hash、工具结果顺序、输入未变、预算未知语义与快照可读。
2. 图像观察：真实K1只读捕获、时间帧/点击、网络/路径/symlink/control/空权限拒绝；同时合法推进与超时/取消drain。
3. 同SQLite冷恢复：原附件与模型视图等价、未知effects不重放、移除当前host/browser配置不得升级恢复。
4. QA接受反例：无图/旧图/同批未消费/不同角色/源改变不能批准；当前独立捕获+真实后续provider图像可批准。只证明机制，不证明模型判断正确。
5. 固定坏候选：对原封存网页进行不透露root诊断的原生QA视觉实验，与保留的文本批准事实比较；看图仍批准则记录失败，不能声称QA更好。图片条件和额外提示改变要分别说明，不伪称纯单变量。
6. 必要相邻回归、类型/lint、K1 ALL、目录新增问题比较和独立审阅后冻结新候选；全新隔离同一动画任务，Director/QA真实看图，原生检查/当前QA/root实际质量/资源各自闭合。

原反例失败、修后真实链路成立、相邻安全与恢复通过即结束模块研究并集成。不把长期记忆、通用远程浏览器、所有模型或120项目变成此次前置。一个成功网页不能证明最佳角色组合或优于Codex/Claude。

证据根：~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task17-native-vision/；原网页与root失败在task16-pelican-web-acceptance-r01/和baseline-quality/。

新内部数据变体为 `projects_pelican_web_v2.json` / `fixtures/pelican_web_visual/`。原目标、原必需 browser_smoke 命令及六项质量维度不变，只把过期的纯文本环境说明替换成真实 browser_observe 能力；host 显式要求 Director／QA 视觉。原 v1 和120目录不改写。新项目与固定坏候选实验均待最终集成资格与冻结后运行，不把模型三张基础图片探针扩大成网页判断能力证明。

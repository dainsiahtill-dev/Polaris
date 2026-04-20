# Polaris 前端 UI 架构文档

## 一、项目概述

Polaris 是一个 AI 驱动的软件开发自动化平台，其前端部分采用现代化的 React 技术栈构建。该项目是一个功能完备的 Web 应用，包含了项目管理、任务追踪、终端模拟、LLM 配置、AI 面试、系统测试等多种复杂功能模块。整个前端应用采用了组件化的设计思想，通过清晰的职责划分和模块化组织，实现了一个高度可维护和可扩展的用户界面。

项目的核心定位是一个“贞观法度·枢密中台”，这一定位在其 UI 设计中得到了充分体现——采用了类似中国古代朝堂文化的隐喻体系，如“廷议”（对话）、“备忘”（备忘录）、“案卷历史”等术语，营造出独特的文化氛围同时保持了现代化的交互体验。

### 1.1 技术栈详情

| 类别 | 技术选型 | 版本/备注 |
|------|----------|-----------|
| 核心框架 | React | 18.3.1 |
| 构建工具 | Vite | 最新版 |
| UI 组件库 | shadcn/ui | 基于 Radix UI Primitives |
| 样式解决方案 | Tailwind CSS | 4.1.12 |
| 状态管理 | React Hooks (useState/useReducer/useContext) | 内置 |
| 表单处理 | react-hook-form | 最新版 |
| 动画库 | framer-motion / motion | motion 是新版本 |
| 终端模拟 | @xterm/xterm | 最新版 |
| 图表库 | recharts | 最新版 |
| 可视化流程编辑器 | @xyflow/react | 最新版（原 React Flow） |
| 3D 渲染 | @react-three/fiber + three | 用于 CodeMap3D 组件 |
| 图标库 | lucide-react | 最新版 |
| 通知组件 | sonner | 最新版 |
| 可复现面板布局 | react-resizable-panels | 最新版 |

---

## 二、整体布局结构

### 2.1 页面层级架构

Polaris 的主界面采用了经典的“三栏式”可嵌套布局结构，充分利用了 `react-resizable-panels` 库提供的可调整面板功能。整个应用由上至下分为以下几个主要层级：

**第一层：顶部控制栏（ControlPanel）**
这是整个应用的“神经中枢”，位于页面最顶部，高度固定。控制栏从左到右依次包含以下功能区域：窗口控制按钮（最小化、最大化、关闭）、项目监控开关、Logo 和应用名称、当前工作区路径显示、PM（项目管理者）控制按钮组、Director（导演）控制按钮组、Agent 审核按钮、设置按钮以及终端开关。这一层级的高度约为 56px，采用了半透明背景和模糊效果（backdrop-blur），在视觉上营造出悬浮感。

**第二层：主工作区域（可调整的三栏布局）**
在控制栏下方，应用主体采用了水平方向的面板组（PanelGroup direction="horizontal"），默认保存布局状态到 localStorage（autoSaveId="polaris-main-layout-v2"）。这一层从左到右包含三个主要面板：

**左侧面板：ProcessMonitorSidebar（进程监控侧边栏）**
这是三个面板中宽度最小的区域，默认宽度为视口的 20%，可调整范围为 15% 到 30%。当用户不需要此面板时，可以通过控制栏上的监控开关将其完全收起。该面板采用延迟加载（React.lazy）策略，以优化首屏加载性能。面板内部采用垂直布局，顶部为工作区选择和操作按钮区域，中部为文件浏览器（支持树形结构和文件选择），底部为历史记录入口。面板背景为半透明的深色（bg-bg-panel/30），带有模糊效果，与主背景形成层次感。

**中间面板：主工作区（ProjectProgressPanel + TerminalPanel）**
这是应用的核心区域，采用垂直方向的面板组（PanelGroup direction="vertical"），包含两个可调整大小的子面板。上方的 ProjectProgressPanel 是整个应用最重要的功能区域，默认占据 70% 的垂直空间，最小不低于 30%。该区域显示当前批次的任务进度、目标列表、当前执行的任务卡片以及计划预览。下方的 TerminalPanel 是嵌入式终端模拟器，默认占据 30% 的垂直空间，可在 10% 到 80% 之间调整。用户可以通过控制栏的终端开关完全收起终端面板，此时主工作区会扩展至全屏。

**右侧面板：ContextSidebar（上下文侧边栏）**
这是三个面板中宽度最大的区域，默认宽度为视口的 30%，可调整范围为 20% 到 50%。该面板采用了垂直标签页（Tab）的设计，四个主要标签分别为：廷议（对话）、备忘（备忘录）、记忆（Memory，可通过设置开关）、快拍（Snapshot）。每个标签对应一个独立的功能模块，通过左侧的图标导航栏进行切换。面板内部同样采用了延迟加载策略，根据用户切换的标签动态加载相应的组件。

**第三层：底部状态栏（RealTimeStatusBar）**
位于页面最底部，高度固定约 32px。该组件实时显示系统运行状态，包括 PM 运行状态和运行时长、Director 运行状态和运行时长、当前迭代次数、LanceDB 连接状态等关键信息。状态栏采用紧凑的信息密度设计，通过颜色编码（绿色表示正常运行，红色表示异常）帮助用户快速感知系统状态。

### 2.2 模态层和抽屉层

除了上述三层固定布局外，应用还包含多个覆盖层组件，用于展示详情、进行设置或提供交互：

**模态对话框（Modal/Dialog）**
包括 SettingsModal（设置对话框，2350 行代码，是最大的单个组件）、LogsModal（日志查看对话框）、DocsInitDialog（文档初始化对话框）、AgentsReviewDialog（Agent 审核对话框）、RuntimeErrorDialog（运行时错误对话框）。这些模态框都基于 Radix UI 的 Dialog 原语构建，支持 ESC 键关闭、点击遮罩层关闭等标准行为。

**抽屉组件（Drawer）**
包括 HistoryDrawer（历史记录抽屉，从右侧滑入）和 PtyDrawer（终端抽屉，可拖拽调整大小）。Drawer 组件基于 Radix UI 的 Drawer 原语，提供更自然的滑动交互体验。

**通知系统**
采用 sonner 库实现的轻量级 toast 通知，显示在页面右下角。EnhancedNotificationManager 组件管理通知队列，支持最多同时显示 5 条通知，支持手动关闭和自动消失。

---

## 三、核心业务组件详细描述

### 3.1 ControlPanel（控制面板）

**文件路径**：`src/app/components/ControlPanel`（398 行）

**功能描述**：ControlPanel 是整个应用的顶部导航和控制中心，承担着用户与系统核心功能交互的主要职责。该组件将大量的操作入口进行了合理的分组和布局，使得用户可以在一个界面中完成大部分日常操作。

**主要功能区域**：

窗口控制区域：包含自定义的窗口控制按钮（最小化、最大化、关闭），这些按钮调用 Electron 或 Tauri 的原生 API 实现窗口操作。按钮采用了自定义的图标和样式，与整体 UI 风格保持一致。

项目监控开关：左侧第一个交互元素是一个 Activity 图标的按钮，用于切换左侧 ProcessMonitorSidebar 的显示状态。按钮在开启状态下会有高亮效果（bg-accent/10），关闭状态下则呈现默认的 muted 样式。

Logo 和标题区：应用名称“Polaris”采用自定义字体（font-heading）显示，标题下方还有副标题“贞观法度·枢密中台”，采用了等宽字体和字母间隔设计，呼应中国传统文化主题。

工作区显示区：这是控制栏最宽大的交互区域，显示当前选定的工作区路径。点击路径区域可以打开系统文件选择器（通过 Electron/Tauri 的原生对话框）来更换工作区。工作区路径采用了省略显示策略，过长的路径会进行智能截断，完整路径通过 tooltip 显示。该区域还支持拖拽工作区文件夹到此处来更换路径（需要实现拖拽处理逻辑）。

PM 控制按钮组：包含三个功能按钮——播放/暂停按钮（启动或停止 PM）、单次运行按钮（Run Once）、日志按钮（查看 PM 运行日志）。按钮根据 PM 的当前运行状态动态切换图标（Play/Square）和样式，禁用状态下呈现灰色且不可点击。当系统检测到 LanceDB 未就绪或缺少文档时，PM 的启动按钮会被禁用。

Director 控制按钮组：与 PM 控制类似，包含启动/停止按钮和日志按钮。Director 的启动还额外检查 Agent 审核状态，如果需要审核但尚未通过，Director 会被阻塞并显示相应原因。

Agent 审核按钮：当系统检测到需要审核 Agent 时，会显示额外的审核按钮。按钮根据审核状态（待审核、草稿就绪、草稿生成失败）呈现不同的样式和交互逻辑。

设置按钮：位于控制栏最右侧，打开 SettingsModal 模态框。

**样式特点**：控制栏采用了固定定位（position: relative, z-50），确保始终位于内容上方。背景使用了半透明效果和模糊滤镜（bg-bg backdrop-blur-sm），与下方内容形成层次感。所有的交互元素都支持键盘焦点样式和悬停态，使用了统一的过渡动画（transition-colors）。

### 3.2 ProjectProgressPanel（项目进度面板）

**文件路径**：`src/app/components/ProjectProgressPanel`（335 行）

**功能描述**：这是 Polaris 核心的任务管理和进度展示面板，用户可以在这里查看当前项目的所有任务、目标、笔记以及执行状态。该面板是从 WebSocket 接收实时数据更新的主要展示区，承载了最多、最复杂的业务逻辑展示。

**主要功能模块**：

**进度统计区**：面板顶部显示当前批次的整体进度，包括总任务数、已完成数、进行中数。进度通过 ProgressBar 组件可视化展示，同时显示具体的数字统计（如"3/12 任务已完成"）。

**目标列表（Goals List）**：可折叠的目标展示区域，显示项目当前的目标项。每个目标项前有复选框图标，已完成的目标会有划线效果。展开/折叠通过 chevron 图标按钮控制。

**当前任务卡片（CurrentTaskCard）**：这是面板中最醒目的区域，专门突出显示当前正在执行的任务。卡片显示任务 ID、任务标题、执行状态（如"RUNNING"、"BLOCKED"）、失败详情（如有）。状态通过颜色编码——绿色表示正常、红色表示失败、黄色表示阻塞。

**任务列表（Task List）**：按执行顺序排列的所有任务列表。每个任务项显示任务 ID、任务摘要、状态图标。已完成的任务有复选标记，进行中的任务有动态指示器（脉冲动画），失败或阻塞的任务有警告图标。列表支持滚动查看大量任务。

**计划预览（Plan Preview）**：当存在计划文本时，显示一个可展开的预览区域。用户可以查看当前批次的完整计划内容，包括 PM 对任务的分解和安排。

**引擎状态显示**：如果 Director 引擎正在运行，面板还会实时显示引擎的详细状态，包括当前阶段（phase）、角色状态（Director、QA 等的详细状态）、错误信息（如有）。

**样式特点**：面板内部使用了卡片式布局，不同的信息区域通过 subtle 的边框和背景色区分。当前任务的卡片使用了更大的视觉权重（边框发光效果、较大的字号），使其在众多任务中脱颖而出。整个面板使用了滚动区域（ScrollArea）组件，确保大量内容时的流畅滚动体验。

### 3.3 ContextSidebar（上下文侧边栏）

**文件路径**：`src/app/components/ContextSidebar`（261 行）

**功能描述**：ContextSidebar 是 Polaris 的“辅助大脑”，集中管理了与 AI 对话、用户笔记、长期记忆、快照相关的所有功能。该组件采用了经典的标签页式导航，四个功能模块共享同一个容器和导航结构。

**标签页导航设计**：

左侧垂直导航栏（宽度 56px）：包含四个图标按钮，分别对应四个功能标签。图标使用了 lucide-react 库，悬停时会有颜色变化选中态会有背景高亮。每个图标下方还有简短的中文标签（如“廷议”、“备忘”），帮助用户快速识别功能。

**廷议（对话面板 DialoguePanel）**：

这是侧边栏的默认打开标签，显示 AI 对话历史。消息以气泡形式展示，用户消息和 AI 消息通过不同的对齐方式（右对齐/左对齐）和背景色区分。AI 消息支持 Markdown 渲染，包括代码块、链接、列表等格式。每条消息显示发送者和时间戳。面板底部有一个输入框，支持发送新消息（需后端支持）。对话数据通过 WebSocket 实时推送有新消息时自动滚动到底部。

**备忘（备忘录面板 MemoPanel）**：

备忘录功能允许用户为当前工作区创建和保存多条笔记。左侧是备忘录列表（ MemoItems），每条显示标题、创建时间和摘要。点击列表项可在右侧查看和编辑详细内容。面板顶部有“新建备忘录”按钮，支持创建新的空白备忘录。备忘录数据会实时保存到后端（通过 useMemos hook）。

**记忆（Memory 面板 MemoryPanel）**：

这是一个可选功能，需要在设置中开启（show_memory）。开启后，标签栏会出现“记忆”标签。MemoryPanel 显示 AI 的长期记忆状态，包括反思次数、错误次数、记忆总数等统计信息。底部展示记忆内容的详细文本。该功能与 LanceDB 向量数据库集成，用于持久化存储和检索记忆。

**快拍（Snapshot 面板 SnapshotPanel）**：

显示当前快照的元数据信息，包括快照时间戳、文件状态、文件路径列表、Director 状态等。这是一个只读的信息展示面板，用于调试和问题排查。

**样式特点**：侧边栏整体使用了玻璃拟态效果（glass-bubble 类名），背景为半透明黑色并带有模糊效果。标签页切换使用 framer-motion 实现平滑的过渡动画。面板内容区域使用了可滚动区域组件，支持大量内容的展示。

### 3.4 ProcessMonitorSidebar（进程监控侧边栏）

**文件路径**：`src/app/components/ProcessMonitorSidebar`（延迟加载）

**功能描述**：这是 Polaris 的“观察者”面板，主要用于监控项目文件变化和进程状态。用户可以浏览工作区的文件结构，选择文件进行查看或编辑，并访问历史运行记录。

**主要功能**：

工作区操作区：显示当前工作区路径，有“打开文件夹”按钮调用系统文件管理器打开该路径。

文件浏览器：树形结构展示工作区中的文件和文件夹。支持展开/折叠文件夹、点击文件查看详情。文件按类型分类显示（通过文件扩展名判断）。当前选中的文件会有高亮效果。

历史记录入口：点击“案卷历史”按钮打开 HistoryDrawer，可以查看历次运行的快照和状态。

**样式特点**：与 ContextSidebar 风格一致，使用半透明背景和模糊效果。文件树使用缩进表示层级关系，每级缩进 16px。文件图标根据类型显示不同的 lucide-react 图标。

### 3.5 TerminalPanel（终端面板）

**文件路径**：`src/app/components/TerminalPanel`

**功能描述**：这是 Polaris 的嵌入式终端模拟器，基于 xterm.js 库实现。用户可以直接在应用内执行命令行操作，无需切换到外部终端。

**功能特性**：

完整的终端仿真：支持 xterm.js 的全部功能，包括 ANSI 转义序列渲染、256 色支持、鼠标事件等。

响应式布局：终端面板可以调整大小，最大化时占据整个中间区域。

主题适配：终端使用了与应用整体风格一致的颜色方案（通过 xterm.js 的 theme 配置）。

工作区上下文：终端初始化时会设置正确的工作目录，用户可以直接在项目根目录下执行命令。

**技术实现**：TerminalPanel 使用 @xterm/xterm 库，通过 ref 获取 DOM 元素并在 useEffect 中初始化。与后端的连接可能通过 WebSocket 或 PTY（伪终端）实现。

### 3.6 RealTimeStatusBar（实时状态栏）

**文件路径**：`src/app/components/RealTimeStatusBar`

**功能描述**：位于页面最底部的紧凑状态栏，提供系统运行状态的快速概览。

**显示信息**：

PM 状态：运行中/已停止、启动时间、运行时长

Director 状态：运行中/已停止、启动时间、运行时长

迭代次数：当前运行的迭代编号

LanceDB 状态：已连接/未连接（通过颜色区分）

**样式特点**：采用了极高信息密度的紧凑设计，字号较小（text-xs），图标简化。整体背景与主内容区分开（可能是深色背景），使用单宽字体显示数值以保证对齐。

### 3.7 SettingsModal（设置模态框）

**文件路径**：`src/app/components/SettingsModal`（2350 行）

**功能描述**：这是 Polaris 最复杂、代码量最大的组件，包含了应用的全部配置选项。由于功能丰富，该组件被拆分为多个子组件模块组织。

**主要设置分类**：

通用设置（General）：工作区路径、主题偏好、显示选项等

LLM 设置（LLM Tab）：这是最复杂的部分，包含多个子页面——LLM 提供商管理、模型选择、角色配置、连接测试、可视化编辑器等

Agent 设置：Agent 相关配置

**LLM 提供商配置**：SettingsModal 包含了 12 种 LLM 提供商的配置界面，包括：

- BaseProviderSettings（基础配置）
- AnthropicCompatProviderSettings（Anthropic 兼容）
- OpenAICompatProviderSettings（OpenAI 兼容）
- GeminiAPIProviderSettings（Google Gemini API）
- GeminiCLIProviderSettings（Google Gemini CLI）
- CodexCLIProviderSettings（OpenAI Codex CLI）
- CodexSDKProviderSettings（OpenAI Codex SDK）
- KimiProviderSettings（月之暗面 Kimi）
- MiniMaxProviderSettings（MiniMax）
- OllamaProviderSettings（本地 Ollama）
- DefaultProviderSettings（默认配置）

每个提供商配置界面都包含：API 密钥输入、端点 URL 配置、模型选择、连接测试按钮等。

**LLM 可视化编辑器（LLMVisualEditor）**：

这是一个基于 @xyflow/react 的节点编辑器，允许用户通过可视化的方式配置 LLM 提供商、模型和角色的连接关系。用户可以拖拽节点、连接边、配置参数，生成配置后可以导出或应用到系统。

**LLM 测试面板（TestPanel）**：

提供了对配置好的 LLM 进行连接测试的功能，包括测试面板头部、进度条、进度模态框、结果展示、日志查看器等子组件。

**LLM 面试功能（InterviewHall）**：

一个交互式的 AI 面试模块，允许用户与多个 AI 角色进行多轮对话，模拟面试场景。支持实时思考显示、流式标签显示、面试报告生成等功能。

**样式特点**：SettingsModal 使用了 Tabs 组件进行分类导航，每个 Tab 对应一个功能区域。表单元素使用了 shadcn/ui 的 Input、Select、Switch、Button 等基础组件。配置项按提供商分组，每组使用 Card 组件进行视觉区分。

---

## 四、shadcn/ui 组件使用详情

Polaris 前端大量使用了 shadcn/ui 组件库，这是基于 Radix UI 原语和 Tailwind CSS 构建的可定制组件集合。以下是各组件的使用详情：

### 4.1 布局类组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Resizable | `ui/resizable` | 基于 react-resizable-panels 的可调整面板容器，用于主布局的三栏结构 |
| ScrollArea | `ui/scroll-area` | 自定义滚动条样式的滚动容器，几乎用于所有可滚动区域 |
| AspectRatio | `ui/aspect-ratio` | 保持特定宽高比的容器，用于图片或视频预览 |

### 4.2 覆盖层组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Dialog | `ui/dialog` | 基础模态框组件，用于 SettingsModal、LogsModal、RuntimeErrorDialog 等 |
| Sheet | `ui/sheet` | 侧边抽屉组件，用于较小的侧边面板 |
| Drawer | `ui/drawer` | 全屏/大幅抽屉组件，用于 HistoryDrawer 和 PtyDrawer |
| Popover | `ui/popover` | 弹出气泡，用于下拉菜单、工具提示等 |
| Tooltip | `ui/tooltip` | 鼠标悬停提示，显示完整路径或按钮说明 |
| AlertDialog | `ui/alert-dialog` | 确认对话框，用于危险操作的二次确认 |

### 4.3 导航类组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Tabs | `ui/tabs` | 标签页切换，用于 SettingsModal 的分类导航、ContextSidebar 的功能切换 |
| NavigationMenu | `ui/navigation-menu` | 复杂导航菜单，应用中可能用于主导航 |
| Menubar | `ui/menubar` | 菜单栏组件 |
| Breadcrumb | `ui/breadcrumb` | 面包屑导航，用于文件路径展示 |

### 4.4 表单类组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Input | `ui/input` | 文本输入框，用于所有需要用户输入文本的场景 |
| Textarea | `ui/textarea` | 多行文本输入，用于长文本、备注等 |
| Select | `ui/select` | 下拉选择，用于模型选择、选项选择等 |
| Checkbox | `ui/checkbox` | 复选框，用于多选项、任务状态等 |
| RadioGroup | `ui/radio-group` | 单选按钮组，用于互斥选项 |
| Switch | `ui/switch` | 开关切换，用于布尔选项 |
| Toggle | `ui/toggle` | 切换按钮 |
| ToggleGroup | `ui/toggle-group` | 按钮组 |
| Slider | `ui/slider` | 滑块，用于数值范围选择 |
| Form | `ui/form` | 表单容器，结合 react-hook-form 使用 |
| Label | `ui/label` | 表单标签 |
| InputOTP | `ui/input-otp` | OTP 一次性密码输入 |

### 4.5 数据展示类组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Table | `ui/table` | 表格，用于结构化数据展示 |
| Card | `ui/card` | 卡片容器，用于分组展示相关内容 |
| Badge | `ui/badge` | 标签/徽章，用于状态显示、计数等 |
| Avatar | `ui/avatar` | 头像，用于用户或 AI 标识 |
| Skeleton | `ui/skeleton` | 加载占位骨架屏 |
| Progress | `ui/progress` | 进度条，用于任务进度展示 |
| Calendar | `ui/calendar` | 日历组件 |

### 4.6 反馈类组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Alert | `ui/alert` | 警告提示，用于重要信息展示 |
| Sonner | `ui/sonner` | Toast 通知，基于 sonner 库 |
| HoverCard | `ui/hover-card` | 悬停卡片，鼠标悬停时显示的详情卡片 |
| Collapsible | `ui/collapsible` | 可折叠区域，用于展开/收起内容 |

### 4.7 操作类组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Button | `ui/button` | 按钮，UI 中最常用的交互元素 |
| Separator | `ui/separator` | 分隔线，用于视觉分组 |
| Accordion | `ui/accordion` | 手风琴组件，用于分组内容展示 |
| ContextMenu | `ui/context-menu` | 右键菜单 |
| DropdownMenu | `ui/dropdown-menu` | 下拉菜单 |

### 4.8 媒体类组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Carousel | `ui/carousel` | 轮播图/幻灯片 |
| Chart | `ui/chart` | 图表，基于 recharts 库 |

### 4.9 其他组件

| 组件 | 文件路径 | 用途描述 |
|------|----------|----------|
| Command | `ui/command` | 命令面板，基于 cmdk 库，用于命令搜索 |
| Pagination | `ui/pagination` | 分页组件 |
| Sidebar | `ui/sidebar` | 侧边栏组件 |
| InputCyber | `ui/input-cyber` | 自定义赛博风格输入框 |

---

## 五、自定义样式系统

### 5.1 Tailwind CSS 配置

项目使用了 Tailwind CSS 4.1.12 版本，配置文件位于 `tailwind.config.ts`。以下是自定义的颜色系统：

**颜色变量**（在 theme.css 中定义）：

```
--color-bg: 背景主色（深色主题）
--color-bg-panel: 面板背景
--color-text-main: 主文本色
--color-text-muted: 次要文本色
--color-text-dim: 暗淡文本色
--color-accent: 强调色/品牌色
--color-status-error: 错误状态色
--color-status-success: 成功状态色
--color-status-warning: 警告状态色
```

### 5.2 自定义工具类

项目定义了一些自定义的 Tailwind 工具类：

```
glass-bubble: 玻璃拟态效果（半透明背景 + 模糊）
panel-header: 面板头部样式
glow: 发光效果
border-white/10: 细微边框
bg-bg-panel/30: 半透明面板背景
```

### 5.3 字体系统

项目定义了以下字体类：

```
font-sans: 默认无衬线字体
font-heading: 标题字体（更粗的字重）
font-mono: 等宽字体（用于代码和数值）
```

---

## 六、组件目录结构总览

```
src/app/
├── components/
│   ├── ui/                      # shadcn/ui 基础组件（48个）
│   │   ├── button
│   │   ├── dialog
│   │   ├── input
│   │   ├── select
│   │   ├── tabs
│   │   ├── card
│   │   ├── table
│   │   └── ... (更多组件)
│   │
│   ├── App                  # 主应用入口
│   ├── ControlPanel         # 顶部控制栏
│   ├── RealTimeStatusBar    # 底部状态栏
│   ├── ProjectProgressPanel # 项目进度面板
│   ├── ContextSidebar       # 右侧上下文面板
│   ├── ProcessMonitorSidebar # 左侧进程监控面板
│   ├── TerminalPanel        # 终端面板
│   ├── DialoguePanel        # 对话面板
│   ├── MemoPanel            # 备忘录面板
│   ├── MemoryPanel          # 记忆面板
│   ├── SnapshotPanel        # 快照面板
│   ├── SettingsModal        # 设置模态框（2350行）
│   ├── LogsModal            # 日志模态框
│   ├── DocsInitDialog       # 文档初始化对话框
│   ├── AgentsReviewDialog   # Agent审核对话框
│   ├── RuntimeErrorDialog   # 运行时错误对话框
│   ├── HistoryDrawer        # 历史抽屉
│   ├── InterventionCenter  # 人工干预中心
│   ├── CognitionPanel       # 认知面板
│   ├── LivingBackground     # 动态背景
│   ├── UsageHUD             # 使用量HUD
│   ├── FileViewer            # 文件查看器
│   ├── WindowControls       # 窗口控制按钮
│   ├── EnhancedNotificationManager # 通知管理器
│   ├── ErrorBoundary        # 错误边界
│   ├── PlanBoard             # 计划面板
│   │
│   └── llm/                     # LLM相关组件
│       ├── providers/           # LLM提供商配置（12个）
│       ├── test/                # LLM测试组件
│       ├── interview/           # AI面试组件
│       ├── visual/              # 可视化编辑器
│       ├── adapters/            # 视图适配器
│       └── model-browser/       # 模型浏览器
│
├── hooks/                       # 自定义React Hooks
├── services/                    # API服务
├── types/                       # TypeScript类型定义
└── utils/                       # 工具函数
```

---

## 七、迁移建议

### 7.1 组件迁移优先级

| 优先级 | 组件类型 | 数量 | 迁移策略 |
|--------|----------|------|----------|
| P0（最高） | 业务核心组件 | 15 | 完全重写新框架对应组件 |
| P1 | shadcn/ui 基础组件 | 48 | 替换为新框架的等效组件 |
| P2 | LLM 配置组件 | 30+ | 保留逻辑，重写 UI |
| P3 | 辅助组件 | 20 | 按需迁移 |

### 7.2 需要保留的技术

以下技术和库可以继续使用，无需替换：

- React 18.3.1 + Vite
- react-resizable-panels（布局系统）
- @xterm/xterm（终端模拟）
- @xyflow/react（可视化编辑器）
- @react-three/fiber + three（3D 渲染）
- recharts（图表）
- lucide-react（图标）
- react-hook-form（表单处理）
- sonner 或新框架的通知组件

### 7.3 关键文件位置

| 用途 | 文件路径 |
|------|----------|
| 主入口 | `src/frontend/src/app/App` |
| 布局配置 | `src/frontend/src/app/App`（第 489-592 行） |
| 主题配置 | `src/frontend/src/styles/theme.css` |
| Tailwind 配置 | `src/frontend/tailwind.config.ts` |
| shadcn/ui 组件 | `src/frontend/src/app/components/ui/` |
| 业务组件 | `src/frontend/src/app/components/` |
| LLM 组件 | `src/frontend/src/app/components/llm/` |
| 包管理配置 | `src/frontend/package.json` |

---

## 八、总结

Polaris 前端是一个功能完备、结构清晰的 React 应用。其 UI 架构有以下特点：

1. **模块化设计**：组件职责明确，层次清晰，便于维护和扩展

2. **组件复用**：通过 shadcn/ui 基础组件的组合和定制，实现了统一的设计语言

3. **响应式布局**：使用 react-resizable-panels 提供了灵活的面板调整能力

4. **实时交互**：大量使用 WebSocket 实现实时状态更新

5. **复杂功能集成**：终端模拟、可视化编辑器、3D 渲染等多种技术有机结合

6. **文化特色**：UI 中融入了中国古代文化元素，形成了独特的视觉风格

迁移到新 UI 框架时，建议首先梳理各业务组件的功能逻辑，然后替换基础 UI 组件，最后调整样式系统以保持整体视觉一致性。

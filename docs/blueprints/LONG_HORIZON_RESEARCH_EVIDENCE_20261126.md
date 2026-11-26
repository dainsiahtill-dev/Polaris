# 长期还原研究的证据与续接

## 要解决的问题

研究可能持续数周。一个实验可以只覆盖某组输入、比较精度和软件版本。下一位工程师需要知道哪些观察仍能复查，哪些结论需要重验，哪项产品义务仍然开放。登记了多少函数、完成了多少工作包，都缺少推导整体还原比例所需的分母。

## 职责与路径

```text
原始材料（只读）
  └─ 版本化的实验报告，声明输入域、比较器、观察与反例
      └─ 报告 SHA-256 + 精确 JSON Pointer + 产物 SHA-256
          ├─ KernelOne 受约束的文件读取，复核引用的当前字节
          └─ 已有 EngineeringRunService journal 保存实际验证观察
              ├─ ContextOS 为当前决策投影摘要和可取回的证据引用
              └─ 独立 QA 在同一候选上检查原始产品义务与正常入口
```

`roles.runtime` 继续拥有工程运行、恢复和裁决。主开发代理可把多个外部审计员用于发现线索。项目验收取决于版本绑定、真实验证与独立判断。实验名称和顺序号适合展示；稳定身份要包含完整来源路径和版本。并行分支即使同号，也须各自保留。

## 首次迁移与反证条件

第一步在内部审计工具中复核**既有报告的引用**。审计者显式选择工作区，以及冻结报告中的路径字段和 SHA 字段，两个字段均以 JSON Pointer 定位。报告自身先校验 SHA，随后使用既有 KernelOne 快照读取访问原始字节。只有一次审计中逐条比对过的文件才能计入匹配。缺件、不可读、引用失效、超出读取上限和内容变化各自保留。工具输出一次性的复核结果。给旧报告提供相同文件名但不同内容，就应能观察到失效；同时存在两个旧版摘要时要分别裁决。

## 后续接入顺序与验收

### 多实验室研究模式

模式在项目研究维度显式选择，可与单次运行的 direct 或 collaborative 组合。宿主提供实验计划与本次选中的实验室。计划列出 project、稳定的 lab/id、结论适用范围、下一项问题、报告版本及其精确证据指针。消费边声明生产方 lab/id 和报告 SHA。两个实验室可以使用相同的局部编号。只有明确发布并获得读权限的材料才能进入接收方视图。

准入时将选定实验室与计划固定在工程运行的 SQLite 记录中；相同请求键的不同计划冲突。恢复使用原运行固定的计划并重新检查当前资料。读取当前实验室及其传递依赖，按依赖顺序计算每条版本绑定的结果；上游换版、文件变化或证据缺失会使相应消费者需要复核，独立分支保留自身结果。循环依赖、重复身份和悬空引用须给出明确诊断。模型只接收有界状态、定位身份与未决问题，每份证据带读取时点。证据完整性与科学结论的验收分别记录。

单个工作区沿用宿主的一名源码写入者。需要真正并行写入时，分别使用已有 Launcher/Instance Registry 管理的独立工作区，通过显式授权的版本化产物交换成果。跨机器发布、长期排程与整项目独立验收分别需要真实运行证据。

### 工作区间交接

宿主在生产方选择已验证的运行、实验身份及一组工件。生产方依据工程 SQLite 中的实际验证回执、候选版本与源码清单复核每个工件，采用 KernelOne 受约束读取，将发布的字节以内容地址保存在本工作区运行目录，并在原运行记录发布事件。发布摘要包含运行、实验、验证事件、版本、原路径和逐文件 SHA。发布的度量范围由生产方报告声明。消费者依然需要运行自己的验证。

导入时，宿主显式指定源实例、源运行和发布 SHA，在消费端正常认证，并单独提供源实例读取凭据。消费端仅通过实例注册表的本机回环地址读取该版本，重新核验源身份及所有摘要，再在宿主授予写权限的本工作区中建立新版本目录。写入与已有工程运行共用 workspace 锁，所有对象齐备后最后写入清单。重复请求复核已提交内容。部分传输可只补齐缺件，已发布目录的内容冲突保留原状。

宿主可用下列命令把已核验的工件从 A 交给 C。示例的变量由操作者按已注册实例设置，令牌由本机私有文件提供。

```bash
rtk proxy env PYTHONPATH=src/backend python -m polaris.delivery.cli.research_exchange transfer \
  --source-instance "$LAB_A" --source-run "$ACCEPTED_RUN" --source-token-file "$LAB_A_TOKEN_FILE" \
  --experiment sample --file outputs/measurement.json \
  --consumer-instance "$LAB_C" --consumer-token-file "$LAB_C_TOKEN_FILE"
rtk proxy env PYTHONPATH=src/backend python -m polaris.delivery.cli.research_exchange adopt \
  --workspace "$LAB_C_WORKSPACE" --plan original-plan.json --publication "$PUBLICATION_SHA" \
  --for-experiment integration/result > next-plan.json
```

`transfer` 返回消费工作区的报告地址与 SHA。重复传送既有发布可用 `--publication` 指定已签发的版本。单独发行为 `publish`。HTTP 入口分别为 `POST /v2/engineering/runs/{id}/research-publications`、`GET /v2/engineering/runs/{id}/research-publications/{sha}`、其下的 `/files/{index}` 以及接收实例上的 `POST /v2/engineering/research/imports`。

`adopt` 复核已导入的每个工件，在标准输出给出新计划。只点选生产方会让仍绑定旧输入的消费者显示待复核。用 `--for-experiment` 为指定消费实验选择新输入时，旧结论的 SHA 保存在 `recheck`，原运行仍可取回旧计划。尚无首份报告的依赖可先登记 lab/id，首次采用时再固定 SHA。浏览器的“多实验室研究”选项可载入这份计划，选择实验室并开始新的工程运行。

### 可调用的接口

创建运行沿用 `POST /v2/engineering/runs`。普通请求省略 `research`。启用研究模式时，发送 `research.lab` 和 `research.plan`。计划结构如下，尚未发表的实验也可纳入。

```json
{
  "schema": "engineering.research-plan.v1",
  "project": "calibration",
  "experiments": [
    {
      "lab": "measurement",
      "id": "boundary",
      "scope": "第二种采样条件",
      "next_question": "在相同输入下能否复现首差？",
      "depends_on": []
    }
  ]
}
```

已发表实验的 `report` 含工作区相对路径 `manifest`、报告的 `sha256` 和 `references`。每条引用的 `path_pointer`、`digest_pointer` 定位同一报告中的工件路径和预期 SHA。消费端在 `depends_on` 中指定生产方的 `lab`、`id`、`sha256`。宿主为整个计划计算规范化 SHA，原样绑定到运行身份。相同请求键重复提交不同实验室或不同计划会冲突。

Director CLI 的 `--engineering-action execute --research-plan PLAN.json --laboratory LAB` 使用相同 owner。用 `--engineering-action research-status --research-project PROJECT` 或 `GET /v2/engineering/research/{project}/laboratories` 分页发现各实验室最近一次运行；发现结果不参与版本选择。SQLite 事件 `research_evidence_observed` 与原生请求中的摘要来自有界的逐文件检查，事件保留角色、截止 seq、工作区 revision 和计划身份。初次进入角色、恢复角色和新的验证完成后会重新取证。其他决策复用上次带时点的观察。

引用单独复核的正常入口是 `rtk proxy env PYTHONPATH=src/backend python -m infrastructure.scripts.verify_research_references`。输入查询使用 `research.reference-query.v1` 和 `reports` 列表，与上述 report 对象同形。命令只输出 JSON；全部匹配返回 0，有变化或未证返回 1，无效输入返回 2。跨系统的路径仅在审计者显式指定 `--recorded-windows-root` 时映射为当前授权根下的相对路径。

在实验室发现接口指定 `?lab=LAB` 可逐页查询该实验室的历史运行和各自的计划 SHA；同一参数在 CLI 中用 `--laboratory LAB`。使用 `GET /v2/engineering/runs/{session_id}/research-plan` 读取特定运行当时保存的完整计划。已发布的新版本保留独立身份。

| 工程环节 | 现有归属及变化 | 可证伪的验收 |
| --- | --- | --- |
| 记录实验 | 使用现有验证命令与其 observation，关联研究问题、输入域、候选、比较器、负对照和保留的失败。显式区分预先制定的条件和事后整理。 | 同一输入下，已知错误的比较对象必须被区分；没有原始对照的测试保留未证。 |
| 继续同一任务 | 按 workspace、原 run、证据版本和 journal 截止位置恢复。为当前角色投影最后可复查结论、适用域、反例及未决问题。 | 在一个分支更新公共依赖后恢复旧分支，只影响依赖该版本的结论。不可达引用保持未证。 |
| 长期检索 | 复用 FileCard、制图师、蒸馏器和历史段检索。向模型交付有界摘要与完整地址，保留多个来源变体及反例的出处。 | 跨越多次折叠后仍能取回对应版本；增加无关历史不会覆盖最后的反例。 |
| 传递局部结论 | 运行事实、验证结果、产品入口与发布分别引用已有事件；审核员报告保留为线索。 | 子实验全部通过而端到端仍缺关键环节时，整体保持未证。 |
| 正常入口与收尾 | 独立 QA 对同一候选执行真实的加载、编辑、保存、重开等用户声明操作；记录允许误差及负例。 | 只验证展示页、诊断夹具或打包字节，不能关闭行为义务。 |

首先以冻结的长期研究案例回放引用完整性。随后用多日、跨重启和跨候选的真实工程运行检验续接；分别记录错用旧结论、重复取证、成功取回、验证开销和独立验收。来源目录清单、研究域映射与外部审计报告留在审计归档。

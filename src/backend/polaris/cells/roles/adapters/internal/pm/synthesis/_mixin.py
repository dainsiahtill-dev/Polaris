"""PMContractSynthesisMixin: deterministic PM task-contract synthesis."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .._protocol import _PMAdapterMixinBase
from ..pm_text_utils import (
    _pm_extract_requirement_subject,
    _pm_path_token_from_subject,
    _pm_root_workspace_contract_targets_from_directive,
)
from ._checks import (
    _extract_content_any_keywords_from_directive,
    _extract_deterministic_checks_from_directive,
)
from ._delivery import (
    _append_delivery_depth_to_contracts,
    _contract_keyword_tokens,
    _delivery_depth_contract,
    _delivery_plan_document,
    _domain_module_name,
    _extract_typescript_semantic_keywords,
    _javascript_model_targets_from_keywords,
    _pascal_case_token,
    _typescript_model_targets_from_keywords,
)
from ._language import (
    _directive_requires_cpp_package_contract,
    _directive_requires_go_workspace_contract,
    _directive_requires_java_package_contract,
    _directive_requires_javascript_package_contract,
    _directive_requires_language_root_delivery_contract,
    _directive_requires_python_workspace_contract,
    _directive_requires_rust_package_contract,
    _directive_requires_typescript_package_contract,
)
from ._verification import _attach_synthesized_verification_commands


class PMContractSynthesisMixin(_PMAdapterMixinBase):
    """PM 合同确定性合成 mixin：在 LLM 输出不可用时，无 LLM 地基于需求指令生成可执行任务合同。"""

    def _build_projection_hint_contracts(
        self,
        *,
        directive: str,
        projection_hint: dict[str, Any],
    ) -> list[dict[str, Any]]:
        _raw_proj = projection_hint.get("projection") if isinstance(projection_hint, dict) else None
        projection: dict[str, Any] = dict(_raw_proj) if isinstance(_raw_proj, dict) else {}
        scenario_id = str(projection.get("scenario_id") or "").strip() or "registry.scenario"
        project_slug = self._normalize_projection_project_slug(projection.get("project_slug"))
        requirement = str(projection.get("requirement") or directive or "").strip()
        project_root = f"experiments/{project_slug}"
        scenario_token = re.sub(r"[^A-Za-z0-9_.-]+", "_", scenario_id).strip("._-") or "registry_scenario"
        projection_manifest = f"{project_root}/projection_manifest.json"
        projection_evidence = f"workspace/factory/projection_lab/{scenario_token}.projection.json"
        projection_readme = f"{project_root}/README.md"
        projection_quality_report = f"{project_root}/tests/projection_quality.json"
        projection_delivery_note = f"{project_root}/DELIVERY.md"

        return [
            {
                "id": "TASK-1",
                "title": "通过 Projection 生成受控基线子项目",
                "goal": "使用显式 projection_generate 后端生成传统代码基线并产出审计资产",
                "description": "基于上游给定的 projection 契约生成基线项目，不在 Polaris 主仓内内置任何业务模板名称。",
                "scope": [project_root, "workspace/factory/projection_lab"],
                "target_files": [projection_manifest, projection_evidence],
                "steps": [
                    "校验 projection 契约参数并归一化需求",
                    "执行 projection_generate 生成传统项目与隐藏 IR 资产",
                    "记录 experiment_id / project_root / artifact 路径并运行基础验证",
                ],
                "acceptance": [
                    "生成结果包含 experiment_id、project_root 与 artifact_paths",
                    "投影后的传统项目可运行基础验证命令且无空壳产物",
                ],
                "phase": "implementation",
                "depends_on": [],
                "assigned_to": "Director",
                "execution_backend": "projection_generate",
                "projection": {
                    "scenario_id": scenario_id,
                    "project_slug": project_slug,
                    "requirement": requirement,
                    "use_pm_llm": bool(projection.get("use_pm_llm", True)),
                    "run_verification": bool(projection.get("run_verification", True)),
                    "overwrite": bool(projection.get("overwrite", False)),
                },
            },
            {
                "id": "TASK-2",
                "title": "收敛生成结果与工程约束",
                "goal": "检查投影结果是否满足当前工作区工程约束并补齐缺口",
                "description": "在已生成基线之上做必要的传统代码收敛，避免生成结果与仓库约束脱节。",
                "scope": [project_root, "tests/"],
                "target_files": [projection_readme, projection_quality_report],
                "steps": [
                    "检查生成目录、配置与测试布局是否符合当前工程约束",
                    "对生成结果进行必要的代码编辑或补强",
                    "保留审计证据并记录需要 QA 关注的风险点",
                ],
                "acceptance": [
                    "关键目录结构、配置文件与测试入口满足工程约束",
                    "新增修改具有明确验证路径且无 TODO/FIXME/stub 残留",
                ],
                "phase": "implementation",
                "depends_on": ["TASK-1"],
                "assigned_to": "Director",
                "execution_backend": "code_edit",
            },
            {
                "id": "TASK-3",
                "title": "固化验证与交付说明",
                "goal": "为投影结果固化回归验证、交付说明与后续操作边界",
                "description": "补齐最终验证步骤、交付说明和已知风险记录，确保 QA 可以基于证据做最终裁决。",
                "scope": [project_root, "tui_runtime.md", "tests/"],
                "target_files": [
                    projection_readme,
                    projection_delivery_note,
                    projection_quality_report,
                    "tui_runtime.md",
                ],
                "steps": [
                    "整理可复现的验证命令与预期结果",
                    "补充必要测试或交付说明",
                    "记录当前投影结果的边界、风险与后续扩展点",
                ],
                "acceptance": [
                    "验证步骤可被 QA 独立复现且结果明确",
                    "交付说明包含运行方式、验证命令与当前已知限制",
                ],
                "phase": "verification",
                "depends_on": ["TASK-2"],
                "assigned_to": "Director",
                "execution_backend": "code_edit",
            },
        ]

    def _synthesize_task_contracts_from_directive(
        self,
        *,
        directive: str,
        projection_hint: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """在 PM 输出不可解析时，基于需求指令合成最小可执行任务合同。"""
        if projection_hint:
            contracts = self._build_projection_hint_contracts(
                directive=directive,
                projection_hint=projection_hint,
            )
            contracts = _attach_synthesized_verification_commands(contracts, directive=directive)
            normalized = [self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(contracts)]
            return [item for item in normalized if isinstance(item, dict)]

        requirement_subject = _pm_extract_requirement_subject(directive)
        keywords = self._extract_domain_keywords(directive, limit=4)
        domain = (
            _pm_path_token_from_subject(requirement_subject)
            if requirement_subject
            else (keywords[0] if keywords else self._derive_domain_token(directive))
        )
        domain_label = requirement_subject or domain
        secondary = (
            f"{domain}_integration"
            if requirement_subject
            else (keywords[1] if len(keywords) > 1 else f"{domain}_feature")
        )
        secondary_label = f"{domain_label} 集成能力" if requirement_subject else secondary
        source_metadata = {
            "source_context_redacted": True,
            "source_directive_length": len(str(directive or "")),
        }
        if _directive_requires_javascript_package_contract(directive):
            javascript_contracts = self._synthesize_javascript_workspace_contracts(
                directive=directive,
                domain_label=str(domain_label),
                domain_token=str(domain or "app"),
                source_metadata=source_metadata,
            )
            javascript_contracts = _attach_synthesized_verification_commands(
                javascript_contracts,
                directive=directive,
            )
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(javascript_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        if _directive_requires_java_package_contract(directive):
            java_contracts = self._synthesize_java_workspace_contracts(
                directive=directive,
                domain_label=str(domain_label),
                source_metadata=source_metadata,
            )
            java_contracts = _attach_synthesized_verification_commands(java_contracts, directive=directive)
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(java_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        if _directive_requires_python_workspace_contract(directive):
            python_contracts = self._synthesize_python_workspace_contracts(
                directive=directive,
                domain_label=str(domain_label),
                source_metadata=source_metadata,
            )
            python_contracts = _attach_synthesized_verification_commands(
                python_contracts,
                directive=directive,
            )
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(python_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        if _directive_requires_go_workspace_contract(directive):
            go_contracts = self._synthesize_go_workspace_contracts(
                directive=directive,
                domain_label=str(domain_label),
                source_metadata=source_metadata,
            )
            go_contracts = _attach_synthesized_verification_commands(go_contracts, directive=directive)
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(go_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        placeholder_repair_contracts = self._synthesize_placeholder_repair_contracts(
            directive=directive,
            source_metadata=source_metadata,
        )
        if placeholder_repair_contracts:
            placeholder_repair_contracts = _attach_synthesized_verification_commands(
                placeholder_repair_contracts,
                directive=directive,
            )
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive)
                for idx, item in enumerate(placeholder_repair_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        frontend_repair_contracts = self._synthesize_frontend_test_repair_contracts(
            directive=directive,
            source_metadata=source_metadata,
        )
        if frontend_repair_contracts:
            frontend_repair_contracts = _attach_synthesized_verification_commands(
                frontend_repair_contracts,
                directive=directive,
            )
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive)
                for idx, item in enumerate(frontend_repair_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        frontend_contracts = self._synthesize_frontend_workbench_contracts(
            directive=directive,
            domain=domain,
            source_metadata=source_metadata,
        )
        if frontend_contracts:
            frontend_contracts = _attach_synthesized_verification_commands(
                frontend_contracts,
                directive=directive,
            )
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(frontend_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        if _directive_requires_rust_package_contract(directive):
            rust_contracts = self._synthesize_rust_workspace_contracts(
                directive=directive,
                domain_label=str(domain_label),
                source_metadata=source_metadata,
            )
            rust_contracts = _attach_synthesized_verification_commands(rust_contracts, directive=directive)
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(rust_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        if _directive_requires_cpp_package_contract(directive):
            cpp_contracts = self._synthesize_cpp_workspace_contracts(
                directive=directive,
                domain_label=str(domain_label),
                source_metadata=source_metadata,
            )
            cpp_contracts = _attach_synthesized_verification_commands(cpp_contracts, directive=directive)
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(cpp_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        root_workspace_targets = _pm_root_workspace_contract_targets_from_directive(directive)
        if root_workspace_targets:
            source_file, test_file, readme_file = root_workspace_targets
            if source_file == "index.html" and _directive_requires_typescript_package_contract(directive):
                domain_token = _pm_path_token_from_subject(str(domain_label or domain)) or str(domain or "app")
                deterministic_checks = _extract_deterministic_checks_from_directive(directive)
                content_keywords = _extract_content_any_keywords_from_directive(directive)
                if not content_keywords:
                    content_keywords = _extract_typescript_semantic_keywords(directive)
                if not content_keywords:
                    content_keywords = self._extract_domain_keywords(directive, limit=6)
                keyword_summary = ", ".join(content_keywords[:6]) if content_keywords else str(domain_label)
                check_summary = (
                    "; ".join(deterministic_checks[:6])
                    if deterministic_checks
                    else ("html; ts_syntax; package_scripts")
                )
                model_file_targets = _typescript_model_targets_from_keywords(
                    content_keywords,
                    domain_token=domain_token,
                )
                model_targets = [
                    "package.json",
                    "tsconfig.json",
                    "src/index.ts",
                    "src/main.ts",
                    "src/models/types.ts",
                    *model_file_targets,
                    "src/models/index.ts",
                ]
                visual_targets = [
                    "index.html",
                    "src/engine/simulation.ts",
                    "src/engine/renderer.ts",
                    "src/web.ts",
                ]
                validation_targets = [
                    "package.json",
                    "src/verify.ts",
                    "tests/verify.test.ts",
                    "README.md",
                ]
                root_contracts = [
                    {
                        "id": "TASK-1",
                        "title": f"实现 {domain_label} TypeScript 项目骨架与核心模块",
                        "goal": (
                            f"在工作区根交付 {domain_label} 的 TypeScript/npm 项目骨架、"
                            "非占位 package 脚本和需求驱动的核心模块。"
                        ),
                        "description": (
                            "创建 package.json、tsconfig.json、src/index.ts、src/main.ts 与需求派生的领域模块，"
                            f"覆盖需求关键词和确定性检查：{keyword_summary}。"
                        ),
                        "scope": model_targets,
                        "target_files": model_targets,
                        "steps": [
                            "创建 package.json，声明真实 build/test/start 脚本，禁止 echo-only 或 manifest-only 脚本",
                            "创建 tsconfig.json，启用 strict、DOM/ES2020 lib、outDir=dist、rootDir=src，并保持 package.json type 与 compilerOptions.module 一致",
                            "实现 src/models/types.ts，集中定义跨模型共享类型、状态接口和领域枚举，禁止让多个文件重复定义同名类型",
                            "实现 src/index.ts、src/main.ts 与 src/models/ 需求派生领域模块，暴露可运行入口和核心需求状态",
                            "实现 src/models/index.ts 作为唯一模型聚合导出面，后续 engine/web/test 任务只能通过该公开面或具体模型文件导入",
                            "`npm start` 必须先 build 或引用当前存在的源码入口，不能指向未生成的 dist 文件",
                            "`npm start` 必须运行 Node-safe 入口（如 src/main.ts 或 dist/main.js），不得在 Node 中直接执行 DOM/browser 入口（src/web.ts 或 dist/web.js）",
                            "若 package.json 使用 type=module，则 TypeScript 必须输出可被 Node/浏览器加载的 ESM；否则不要声明 type=module",
                        ],
                        "acceptance": [
                            "`package.json`、`tsconfig.json`、`src/index.ts`、`src/main.ts`、`src/models/types.ts`、`src/models/index.ts` 与 `src/models/` 需求派生领域模块存在且非空",
                            "`npm run build`、`npm run test` 与 `npm start` 对真实入口执行检查",
                            "package.json type 与 tsconfig module 不得出现 ESM/CommonJS 错配",
                            "`src/models/types.ts` 是共享类型唯一来源，`src/models/index.ts` 导出模型公共 API，避免下游任务产生 unresolved local import",
                            "`npm start` 不得在 Node 中直接运行 `dist/web.js`、`src/web.ts` 或其他依赖 document/window 的浏览器入口",
                            f"源码或测试覆盖需求关键词：{keyword_summary}",
                        ],
                        "phase": "requirements",
                        "depends_on": [],
                        "assigned_to": "Director",
                        "metadata": dict(source_metadata),
                    },
                    {
                        "id": "TASK-2",
                        "title": f"实现 {domain_label} 模拟流程、Web 入口与验证资产",
                        "goal": f"实现 {domain_label} 的需求流程和浏览器入口。",
                        "description": (
                            "补齐 src/engine/simulation、src/engine/renderer、index.html、"
                            f"src/web.ts，让页面和源码共同体现需求关键词：{keyword_summary}。"
                        ),
                        "scope": visual_targets,
                        "target_files": visual_targets,
                        "steps": [
                            "实现 src/engine/simulation.ts 的状态更新或计算流程",
                            "实现 src/engine/renderer.ts，将核心状态渲染为浏览器可见内容",
                            "实现 src/web.ts 或等价浏览器 bootstrap，在 DOM 可用后初始化引擎并绘制首帧",
                            "创建 index.html，包含有效 <html>、HTML5 canvas 与可视化容器",
                            'index.html 不得把 Node-only CLI 入口直接作为 <script type="module"> 引入；必须引用浏览器入口或内联浏览器 bootstrap',
                            f"在页面或源码中保留验收关键词：{keyword_summary}",
                        ],
                        "acceptance": [
                            "`index.html` 存在并包含有效 `<html>` 标签、`<canvas>` 与模拟容器",
                            "`src/engine/` 存在并包含可渲染场景或引擎核心文件",
                            "浏览器入口在首屏自动绘制非空 canvas，无需用户先点击",
                            "HTML 入口引用的脚本/资源在 build 后真实存在并能被浏览器加载",
                            f"源码或页面包含需求关键词：{keyword_summary}",
                        ],
                        "phase": "implementation",
                        "depends_on": ["TASK-1"],
                        "assigned_to": "Director",
                        "metadata": dict(source_metadata),
                    },
                    {
                        "id": "TASK-3",
                        "title": f"固化 {domain_label} 验证脚本与交付说明",
                        "goal": f"为 {domain_label} 固化自动验证、运行说明和 QA 交付证据。",
                        "description": (
                            f"实现 src/verify.ts、tests/verify.test.ts 与 README，覆盖确定性检查：{check_summary}。"
                        ),
                        "scope": validation_targets,
                        "target_files": validation_targets,
                        "steps": [
                            f"实现 src/verify.ts 与 tests/verify.test.ts，覆盖确定性检查：{check_summary}",
                            "更新 package.json 的 test/verify 脚本，使其运行 Node-compatible verifier；不得依赖浏览器 document/window，也不得在 ESM 模式使用 require.main",
                            "编写 README，说明 npm install/build/test/start 与浏览器运行方式",
                        ],
                        "acceptance": [
                            "`npm run build` 通过且浏览器入口引用真实构建产物",
                            "`npm run test` 执行真实验证并返回 PASS",
                            "`npm test` 的验证入口可在当前 package module/tsconfig module 组合下由 Node 执行",
                            f"验证脚本覆盖确定性检查：{check_summary}",
                            "`README.md` 包含安装、构建、测试、启动和浏览器查看步骤",
                            "交付物包含 TypeScript 源码、package.json、index.html、测试与 README",
                        ],
                        "phase": "verification",
                        "depends_on": ["TASK-2"],
                        "assigned_to": "Director",
                        "metadata": dict(source_metadata),
                    },
                ]
                root_contracts = _attach_synthesized_verification_commands(
                    root_contracts,
                    directive=directive,
                )
                contracts = [
                    self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(root_contracts)
                ]
                return [item for item in contracts if isinstance(item, dict)]
            if source_file == "index.html":
                static_targets = [source_file, "styles.css"]
                verification_targets = [target for target in (readme_file, test_file) if target]
                root_contracts = [
                    {
                        "id": "TASK-1",
                        "title": f"实现 {domain_label} 静态页面结构与样式",
                        "goal": f"在工作区根交付 {domain_label} 的 HTML/CSS 真实可运行页面。",
                        "description": "创建语义化 HTML 页面与响应式样式文件，禁止只写说明文档或空壳占位。",
                        "scope": static_targets,
                        "target_files": static_targets,
                        "steps": [
                            f"创建或更新 `{source_file}`，实现简历页面语义化结构",
                            "创建或更新 `styles.css`，实现 Flexbox/Grid 布局、视觉样式与移动端媒体查询",
                            "浏览器打开页面确认桌面与移动宽度布局正常",
                        ],
                        "acceptance": [
                            "`index.html` 与 `styles.css` 存在于工作区根且非空",
                            "浏览器打开 `index.html` 正常渲染，375px 宽度布局无错乱",
                        ],
                        "phase": "requirements",
                        "depends_on": [],
                        "assigned_to": "Director",
                        "metadata": dict(source_metadata),
                    },
                    {
                        "id": "TASK-2",
                        "title": f"实现 {domain_label} 响应式验收测试",
                        "goal": f"用自动化检查覆盖 {domain_label} 的文件存在、语义化标签与移动端样式要求。",
                        "description": "补齐测试文件，验证 HTML/CSS 产物、关键内容、媒体查询与运行说明。",
                        "scope": [*static_targets, test_file],
                        "target_files": [*static_targets, test_file],
                        "steps": [
                            f"创建或更新 `{test_file}`，检查 HTML/CSS/README 交付文件",
                            "测试必须使用 Python 标准库 `unittest.TestCase`，禁止依赖未声明的 pytest 风格裸函数",
                            "验证页面包含语义化结构、简历内容区域、CSS Grid/Flexbox 与媒体查询",
                            "执行 `python -m unittest discover -s tests -p 'test_*.py' -v` 并确保测试通过",
                        ],
                        "acceptance": [
                            f"`{test_file}` 存在且包含静态页面验收用例",
                            "`python -m unittest discover -s tests -p 'test_*.py' -v` 返回 PASS，并覆盖产品验收样例",
                        ],
                        "phase": "implementation",
                        "depends_on": ["TASK-1"],
                        "assigned_to": "Director",
                        "metadata": dict(source_metadata),
                    },
                    {
                        "id": "TASK-3",
                        "title": f"完善 {domain_label} README 与交付证据",
                        "goal": f"交付 {domain_label} 的本地运行说明和可复现验收路径。",
                        "description": "补齐 README 运行方式、文件说明、浏览器验证步骤，并确认测试证据可复现。",
                        "scope": verification_targets or [readme_file or test_file],
                        "target_files": verification_targets or [readme_file or test_file],
                        "steps": [
                            "补充 README 本地打开方式和可选简易 HTTP 服务器方式",
                            "记录桌面与移动端验证步骤",
                            "记录最终验证命令和结果",
                        ],
                        "acceptance": [
                            "`README.md` 说明如何运行并包含验收步骤",
                            "`python -m unittest discover -s tests -p 'test_*.py' -v` 返回 PASS，交付物包含 HTML、CSS、测试与文档",
                        ],
                        "phase": "verification",
                        "depends_on": ["TASK-2"],
                        "assigned_to": "Director",
                        "metadata": dict(source_metadata),
                    },
                ]
                root_contracts = _attach_synthesized_verification_commands(
                    root_contracts,
                    directive=directive,
                )
                contracts = [
                    self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(root_contracts)
                ]
                return [item for item in contracts if isinstance(item, dict)]
            verification_targets = [target for target in (readme_file, test_file) if target]
            root_contracts = [
                {
                    "id": "TASK-1",
                    "title": f"实现 {domain_label} 可运行入口与核心解析模块",
                    "goal": f"在工作区根交付 {domain_label} 的真实可运行代码文件。",
                    "description": "建立命令行入口、核心解析/计算逻辑与错误处理骨架，禁止只写说明文档。",
                    "scope": [source_file],
                    "target_files": [source_file],
                    "steps": [
                        f"创建或更新 `{source_file}`，实现可运行 CLI 入口",
                        "实现核心数据流、输入校验与错误提示",
                        "运行语法检查确认入口文件可加载",
                    ],
                    "acceptance": [
                        f"`{source_file}` 存在于工作区根且可执行",
                        "运行核心命令可得到产品需求中的正常输出与错误输出",
                    ],
                    "phase": "requirements",
                    "depends_on": [],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-2",
                    "title": f"实现 {domain_label} 行为测试与边界验证",
                    "goal": f"用自动化测试覆盖 {domain_label} 的核心行为与错误路径。",
                    "description": "补齐测试文件，覆盖正常计算、优先级、括号、非法输入、除零等边界。",
                    "scope": [source_file, test_file],
                    "target_files": [source_file, test_file],
                    "steps": [
                        f"创建或更新 `{test_file}`，覆盖核心成功路径",
                        "测试必须使用 Python 标准库 `unittest.TestCase`，禁止依赖未声明的 pytest 风格裸函数",
                        "补充除零、非法字符、括号不匹配、退出命令等失败路径测试",
                        "执行 `python -m unittest discover -s tests -p 'test_*.py' -v` 并确保测试通过",
                    ],
                    "acceptance": [
                        f"`{test_file}` 存在且包含可执行测试用例",
                        "`python -m unittest discover -s tests -p 'test_*.py' -v` 返回 PASS，并覆盖产品验收样例",
                    ],
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-3",
                    "title": f"完善 {domain_label} README 与交付验收证据",
                    "goal": f"交付 {domain_label} 的运行说明和可复现验收路径。",
                    "description": "补齐 README 运行命令、示例输入输出、错误示例，并确认测试证据可复现。",
                    "scope": verification_targets or [test_file],
                    "target_files": verification_targets or [test_file],
                    "steps": [
                        "补充 README 运行命令、退出方式、正常示例与错误示例",
                        "确认 README 示例与测试用例一致",
                        "记录最终验证命令和结果",
                    ],
                    "acceptance": [
                        "`README.md` 说明如何运行并包含示例输入输出",
                        "`python -m unittest discover -s tests -p 'test_*.py' -v` 返回 PASS，交付物包含源码、测试与文档",
                    ],
                    "phase": "verification",
                    "depends_on": ["TASK-2"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
            ]
            root_contracts = _attach_synthesized_verification_commands(
                root_contracts,
                directive=directive,
            )
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(root_contracts)
            ]
            return [item for item in contracts if isinstance(item, dict)]

        raw_contracts: list[dict[str, Any]] = [
            {
                "id": "TASK-1",
                "title": f"实现 {domain_label} 核心业务模块",
                "goal": f"完成 {domain_label} 领域核心功能落地，形成可执行主流程",
                "description": "建立核心数据结构、领域服务与入口调用链，确保关键场景可运行。",
                "scope": [f"src/{domain}", f"src/{domain}_core", "tests/"],
                "target_files": [
                    f"src/{domain}/core.py",
                    f"src/{domain}_core/service.py",
                    f"tests/test_{domain}.py",
                ],
                "steps": [
                    f"梳理并实现 {domain_label} 核心数据模型与服务接口",
                    "补齐主流程入口与基础错误处理",
                    "为核心流程增加最小可运行验证用例",
                ],
                "acceptance": [
                    f"执行 `pytest -q` 或 `npm test` 时，{domain_label} 核心模块测试通过",
                    f"运行主流程后可看到 {domain_label} 关键业务输出，并覆盖错误路径处理",
                ],
                "phase": "requirements",
                "depends_on": [],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
            {
                "id": "TASK-2",
                "title": f"实现 {secondary_label} 增强能力与集成链路",
                "goal": f"补齐 {secondary_label} 相关增强特性并接入主流程",
                "description": "实现增强功能、状态同步与异常回退路径，确保与核心模块联动。",
                "scope": [f"src/{domain}_feature", f"src/{secondary}", "tests/integration/"],
                "target_files": [
                    f"src/{domain}_feature/integration.py",
                    f"src/{secondary}/service.py",
                    f"tests/integration/test_{secondary}.py",
                ],
                "steps": [
                    f"实现 {secondary_label} 增强逻辑并与核心模块集成",
                    "补齐失败重试、异常处理与边界校验",
                    "增加集成测试覆盖主流程与异常分支",
                ],
                "acceptance": [
                    "执行 `pytest -q` 或 `npm test` 时，集成测试覆盖核心链路并通过",
                    "异常输入触发回退逻辑后，系统返回可预期错误结果并记录日志",
                ],
                "phase": "implementation",
                "depends_on": ["TASK-1"],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
            {
                "id": "TASK-3",
                "title": f"编写 {domain_label} 验收测试与交付校验脚本",
                "goal": f"固化 {domain_label} 交付基线，确保回归可复现",
                "description": "补齐单元/集成验证与质量检查脚本，形成可重复验收证据。",
                "scope": [f"tests/{domain}", "scripts/", "tui_runtime.md"],
                "target_files": [
                    f"tests/{domain}/test_acceptance.py",
                    "scripts/verify_delivery.py",
                    "tui_runtime.md",
                ],
                "steps": [
                    "补充关键路径单元测试与回归测试",
                    "编写或更新质量检查脚本与执行说明",
                    "运行验证命令并记录结果到项目文档",
                ],
                "acceptance": [
                    "执行 `pytest -q`、`npm test` 或等价测试命令后返回 PASS",
                    "交付物包含可复现的验证步骤与命令输出说明",
                ],
                "phase": "verification",
                "depends_on": ["TASK-2"],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
        ]

        raw_contracts = _attach_synthesized_verification_commands(raw_contracts, directive=directive)
        contracts = [self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(raw_contracts)]
        return [item for item in contracts if isinstance(item, dict)]

    def _synthesize_javascript_workspace_contracts(
        self,
        *,
        directive: str,
        domain_label: str,
        domain_token: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        deterministic_checks = _extract_deterministic_checks_from_directive(directive)
        content_keywords = _extract_content_any_keywords_from_directive(directive)
        if not content_keywords:
            content_keywords = self._extract_domain_keywords(directive, limit=6)
        keyword_summary = ", ".join(content_keywords[:6]) if content_keywords else str(domain_label)
        check_summary = "; ".join(deterministic_checks[:8]) if deterministic_checks else "js_syntax; package_scripts"
        model_targets = _javascript_model_targets_from_keywords(content_keywords, domain_token=domain_token)
        source_targets = [
            "package.json",
            "src/index.js",
            "src/engine/rules.js",
            "src/engine/runner.js",
            *model_targets,
        ]
        verification_targets = [
            "tests/product.test.js",
            "tests/test_product.py",
            "README.md",
        ]
        delivery_depth_contract = _delivery_depth_contract(
            domain_label=domain_label,
            language="javascript",
            project_type="npm_workspace",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        delivery_plan_document = _delivery_plan_document(
            domain_label=domain_label,
            language="javascript",
            project_type="npm_workspace",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        return _append_delivery_depth_to_contracts(
            [
                {
                    "id": "TASK-1",
                    "title": f"实现 {domain_label} JavaScript/npm 项目骨架与核心模块",
                    "goal": (
                        f"在工作区根交付 {domain_label} 的 JavaScript/npm 项目骨架、真实 package 脚本和"
                        "需求驱动的核心业务源码。"
                    ),
                    "description": (
                        "创建 package.json、src/index.js、src/engine/ 与需求派生的 src/*.js 领域模块，"
                        f"覆盖需求关键词和确定性检查：{keyword_summary}。"
                    ),
                    "scope": source_targets,
                    "target_files": source_targets,
                    "steps": [
                        "创建 package.json，声明真实 build/test/start 脚本，禁止 echo-only 或 manifest-only 脚本",
                        "实现 src/index.js 作为 Node-safe CLI 或模块入口，可通过 npm start 执行",
                        "实现 src/engine/rules.js 与 src/engine/runner.js，封装核心匹配、计算或流程规则",
                        "实现 src/ 下的需求派生领域模块，源码必须真实使用需求关键词",
                        "执行 `node --check` 或等价语法检查覆盖 src/**/*.js",
                    ],
                    "acceptance": [
                        "`package.json`、`src/index.js`、`src/engine/` 与需求派生 `src/*.js` JavaScript 源码存在且非空",
                        "`npm run build`、`npm test` 与 `npm start` 都执行真实入口或验证逻辑",
                        f"源码或测试覆盖需求关键词：{keyword_summary}",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "requirements",
                    "depends_on": [],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-2",
                    "title": f"实现 {domain_label} JavaScript 验证脚本与交付说明",
                    "goal": f"固化 {domain_label} 的 npm 验证、Python 外部验收和 README 运行路径。",
                    "description": (
                        "补齐 tests/product.test.js、tests/test_product.py 与 README，"
                        f"确保 package_scripts、js_syntax 和 source_target_coverage 可被自动验证：{check_summary}。"
                    ),
                    "scope": ["package.json", *verification_targets],
                    "target_files": ["package.json", *verification_targets],
                    "context_files": source_targets,
                    "steps": [
                        "实现 tests/product.test.js，使用 Node 标准库 assert 或内置 test runner 验证核心规则",
                        "实现 tests/test_product.py，使用 Python unittest 检查 package.json 脚本、src/**/*.js 覆盖和 node 入口",
                        "更新 package.json 的 test/build/start 脚本，使其运行当前存在的 JavaScript 文件",
                        "编写 README，说明 npm install、npm run build、npm test、npm start 和验证脚本",
                        f"验证脚本覆盖确定性检查：{check_summary}",
                    ],
                    "acceptance": [
                        "`tests/product.test.js` 与 `tests/test_product.py` 存在且可执行",
                        "`python -m unittest discover -s tests -p 'test_*.py' -v` 返回 PASS",
                        "`npm test` 返回 PASS，且不是空脚本或 echo-only 脚本",
                        "`README.md` 包含安装、构建、测试和启动步骤",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
            ],
            delivery_plan_document=delivery_plan_document,
            delivery_depth_contract=delivery_depth_contract,
        )

    def _synthesize_python_workspace_contracts(
        self,
        *,
        directive: str,
        domain_label: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        deterministic_checks = _extract_deterministic_checks_from_directive(directive)
        content_keywords = _extract_content_any_keywords_from_directive(directive)
        if not content_keywords:
            content_keywords = self._extract_domain_keywords(directive, limit=6)
        keyword_summary = ", ".join(content_keywords[:6]) if content_keywords else str(domain_label)
        check_summary = "; ".join(deterministic_checks[:8]) if deterministic_checks else "py_compile; min_files"
        model_targets = [
            "src/__init__.py",
            "src/models/__init__.py",
            "src/models/mood.py",
            "src/models/weather.py",
        ]
        engine_targets = [
            "src/engine/__init__.py",
            "src/engine/forecast.py",
            "src/radio.py",
            "src/main.py",
        ]
        verification_targets = [
            "requirements.txt",
            "tests/test_product.py",
            "README.md",
        ]
        delivery_depth_contract = _delivery_depth_contract(
            domain_label=domain_label,
            language="python",
            project_type="python_workspace",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        delivery_plan_document = _delivery_plan_document(
            domain_label=domain_label,
            language="python",
            project_type="python_workspace",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        return _append_delivery_depth_to_contracts(
            [
                {
                    "id": "TASK-1",
                    "title": f"实现 {domain_label} Python 包结构与领域模型",
                    "goal": f"在工作区根交付 {domain_label} 的 Python src/ 包、领域模型和可导入核心源码。",
                    "description": (
                        "创建 requirements.txt、src/__init__.py、src/models/ 与需求派生模型文件，"
                        f"确保 src/**/*.py 覆盖需求关键词和确定性检查：{keyword_summary}。"
                    ),
                    "scope": ["requirements.txt", *model_targets],
                    "target_files": ["requirements.txt", *model_targets],
                    "steps": [
                        "创建 requirements.txt；如无第三方依赖也必须保留可执行的空依赖文件或注释说明",
                        "实现 src/__init__.py 与 src/models/__init__.py，公开核心模型入口",
                        "实现 src/models/mood.py 与 src/models/weather.py，表达 mood、weather、radio、forecast 等需求概念",
                        "执行 `python -m compileall -q src` 验证 src/**/*.py 可编译",
                    ],
                    "acceptance": [
                        "`requirements.txt`、`src/__init__.py` 与 `src/models/*.py` 存在且非空",
                        f"src/**/*.py 源码包含需求关键词：{keyword_summary}",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "requirements",
                    "depends_on": [],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-2",
                    "title": f"实现 {domain_label} Python 引擎与 CLI 入口",
                    "goal": f"实现 {domain_label} 的核心规则引擎、广播输出和可执行 Python 入口。",
                    "description": (
                        "补齐 src/engine/forecast.py、src/radio.py 与 src/main.py，"
                        f"让 CLI 入口真实运行并输出覆盖需求关键词的结果：{keyword_summary}。"
                    ),
                    "scope": engine_targets,
                    "target_files": engine_targets,
                    "steps": [
                        "实现 src/engine/forecast.py，将 mood 输入映射为 weather/forecast 结果",
                        "实现 src/radio.py，将 forecast 组织成私人 radio 播报文本或数据结构",
                        "实现 src/main.py，必须同时支持 `python src/main.py` 与 `python -m src.main` 两种入口",
                        "入口必须运行真实核心规则，不能只打印静态占位文本",
                    ],
                    "acceptance": [
                        "`src/engine/forecast.py`、`src/radio.py` 与 `src/main.py` 存在且非空",
                        "`python src/main.py` 与 `python -m src.main` 都可执行并返回成功",
                        f"源码或入口输出覆盖需求关键词：{keyword_summary}",
                    ],
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-3",
                    "title": f"实现 {domain_label} Python 验证脚本与 README",
                    "goal": f"固化 {domain_label} 的编译、单元测试、入口 smoke 和交付说明。",
                    "description": (
                        "创建 tests/test_product.py 与 README，验证 src/**/*.py 覆盖、py_compile、"
                        f"真实入口和核心领域规则：{check_summary}。"
                    ),
                    "scope": [*model_targets, *engine_targets, *verification_targets],
                    "target_files": [*model_targets, *engine_targets, *verification_targets],
                    "steps": [
                        "实现 tests/test_product.py，使用 Python unittest 覆盖模型、forecast 引擎、radio 输出和 CLI 入口",
                        "测试必须检查 src/**/*.py 至少存在多个源文件，避免只生成 tests/ 或 HTML/CSS 脚手架",
                        "README 记录依赖安装、compileall、unittest、`python src/main.py` 和 `python -m src.main`",
                        f"验证脚本覆盖确定性检查：{check_summary}",
                    ],
                    "acceptance": [
                        "`tests/test_product.py` 存在且可执行",
                        "`python -m compileall -q src tests` 返回成功",
                        "`python -m unittest discover -s tests -p 'test_*.py' -v` 返回 PASS",
                        "`python src/main.py` 与 `python -m src.main` 都返回 PASS",
                        "`README.md` 包含依赖安装、编译、测试和启动步骤",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "verification",
                    "depends_on": ["TASK-2"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
            ],
            delivery_plan_document=delivery_plan_document,
            delivery_depth_contract=delivery_depth_contract,
        )

    def _synthesize_go_workspace_contracts(
        self,
        *,
        directive: str,
        domain_label: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        deterministic_checks = _extract_deterministic_checks_from_directive(directive)
        content_keywords = _extract_content_any_keywords_from_directive(directive)
        if not content_keywords:
            content_keywords = self._extract_domain_keywords(directive, limit=6)
        keyword_summary = ", ".join(content_keywords[:6]) if content_keywords else str(domain_label)
        check_summary = "; ".join(deterministic_checks[:8]) if deterministic_checks else "go_compile; go test ./..."
        model_targets = [
            "go.mod",
            "models/entity.go",
            "models/state.go",
        ]
        engine_targets = [
            "engine/rules.go",
            "engine/service.go",
            "main.go",
        ]
        verification_targets = [
            "main_test.go",
            "README.md",
        ]
        delivery_depth_contract = _delivery_depth_contract(
            domain_label=domain_label,
            language="go",
            project_type="go_module",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        delivery_plan_document = _delivery_plan_document(
            domain_label=domain_label,
            language="go",
            project_type="go_module",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        return _append_delivery_depth_to_contracts(
            [
                {
                    "id": "TASK-1",
                    "title": f"实现 {domain_label} Go 模块与领域模型",
                    "goal": f"在工作区根交付 {domain_label} 的 go.mod 与项目级 Go 领域模型源码。",
                    "description": (
                        f"创建 go.mod 与 models/ 下的 Go 源文件，定义 {domain_label} 的领域实体、状态和校验契约，"
                        f"确保 .go 源码覆盖需求关键词：{keyword_summary}。"
                    ),
                    "scope": model_targets,
                    "target_files": model_targets,
                    "steps": [
                        "创建 go.mod，声明可被 `go test ./...` 加载的本地 Go module",
                        "实现 models/entity.go 与 models/state.go，封装当前需求的领域数据、状态和校验规则",
                        f"Go 源码中必须真实使用或保留验收关键词：{keyword_summary}",
                        "执行 `go test ./...` 或等价命令验证模块可编译",
                    ],
                    "acceptance": [
                        "`go.mod` 与 `models/*.go` 存在且非空",
                        "`go test ./...` 返回成功并编译当前 Go module",
                        f"Go 源码包含需求关键词：{keyword_summary}",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "requirements",
                    "depends_on": [],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-2",
                    "title": f"实现 {domain_label} Go 规则引擎与 CLI 入口",
                    "goal": f"实现 {domain_label} 的核心规则、状态转换和可执行 Go 入口。",
                    "description": (
                        "补齐 engine/ 下的核心规则与 main.go，"
                        f"让 `go run .` 执行真实领域流程并输出覆盖需求关键词的结果：{keyword_summary}。"
                    ),
                    "scope": [*model_targets, *engine_targets],
                    "target_files": [*model_targets, *engine_targets],
                    "steps": [
                        "实现 engine/rules.go，表达当前需求的正常、边界和无效输入规则",
                        "实现 engine/service.go，组织领域模型、状态转换和可复用业务流程",
                        "实现 main.go，调用 models/ 与 engine/ 的公开 API，提供可执行 CLI 或文本入口",
                        "入口必须运行真实核心规则，不能只打印静态占位文本",
                    ],
                    "acceptance": [
                        "`engine/*.go` 与 `main.go` 存在且非空",
                        "`go run .` 返回成功并执行真实核心规则",
                        "`go test ./...` 返回成功",
                        f"源码或入口输出覆盖需求关键词：{keyword_summary}",
                    ],
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-3",
                    "title": f"实现 {domain_label} Go 测试与 README",
                    "goal": f"固化 {domain_label} 的 Go 编译、单元测试、入口 smoke 和交付说明。",
                    "description": (
                        "创建 main_test.go 与 README，使用 Go 原生工具链验证 **/*.go 覆盖、go_compile、"
                        f"真实入口和核心领域规则：{check_summary}。"
                    ),
                    "scope": verification_targets,
                    "target_files": verification_targets,
                    "context_files": [*model_targets, *engine_targets],
                    "steps": [
                        "实现 main_test.go，使用 Go testing 包覆盖领域模型、规则引擎、正常路径、边界路径和错误路径",
                        "README 记录依赖要求、`go test ./...`、`go run .` 和原生验收步骤",
                        f"验证脚本覆盖确定性检查：{check_summary}",
                    ],
                    "acceptance": [
                        "`main_test.go` 存在且由 Go testing 包执行真实领域用例",
                        "`go test ./...` 返回成功",
                        "`go run .` 返回成功",
                        "`README.md` 包含编译、测试和启动步骤",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "verification",
                    "depends_on": ["TASK-2"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
            ],
            delivery_plan_document=delivery_plan_document,
            delivery_depth_contract=delivery_depth_contract,
        )

    def _synthesize_rust_workspace_contracts(
        self,
        *,
        directive: str,
        domain_label: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        deterministic_checks = _extract_deterministic_checks_from_directive(directive)
        content_keywords = _extract_content_any_keywords_from_directive(directive)
        if not content_keywords:
            content_keywords = self._extract_domain_keywords(directive, limit=6)
        keyword_tokens = _contract_keyword_tokens(content_keywords)
        keyword_summary = ", ".join(content_keywords[:6]) if content_keywords else str(domain_label)
        check_summary = "; ".join(deterministic_checks[:8]) if deterministic_checks else "rust_compile; cargo build"
        model_files = [f"src/models/{token}.rs" for token in keyword_tokens]
        model_targets = [
            "Cargo.toml",
            "src/lib.rs",
            "src/models/mod.rs",
            *model_files,
        ]
        rules_module = _domain_module_name(keyword_tokens, suffix="rules")
        runner_module = _domain_module_name(keyword_tokens, suffix="runner")
        engine_targets = [
            "src/engine/mod.rs",
            f"src/engine/{rules_module}.rs",
            f"src/engine/{runner_module}.rs",
            "src/main.rs",
        ]
        verification_targets = [
            "tests/product.rs",
            "README.md",
        ]
        delivery_depth_contract = _delivery_depth_contract(
            domain_label=domain_label,
            language="rust",
            project_type="cargo_workspace",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        delivery_plan_document = _delivery_plan_document(
            domain_label=domain_label,
            language="rust",
            project_type="cargo_workspace",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        return _append_delivery_depth_to_contracts(
            [
                {
                    "id": "TASK-1",
                    "title": f"实现 {domain_label} Rust crate 与领域模型",
                    "goal": f"在工作区根交付 {domain_label} 的 Cargo/Rust 项目骨架和领域模型源码。",
                    "description": (
                        "创建 Cargo.toml、src/lib.rs 与 src/models/ 下的 Rust 源文件，"
                        f"确保源码覆盖需求关键词：{keyword_summary}。"
                    ),
                    "scope": model_targets,
                    "target_files": model_targets,
                    "steps": [
                        "创建 Cargo.toml，声明 package、edition 和可构建的 lib/bin 目标",
                        "创建 src/lib.rs，公开 models 与 engine 模块入口",
                        f"实现 src/models/ 下与 {keyword_summary} 对齐的领域数据结构",
                        f"在 Rust 源码中保留验收关键词：{keyword_summary}",
                    ],
                    "acceptance": [
                        "`Cargo.toml`、`src/lib.rs` 与 `src/models/` Rust 源文件存在且非空",
                        f"源码包含需求关键词：{keyword_summary}",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "requirements",
                    "depends_on": [],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-2",
                    "title": f"实现 {domain_label} Rust 映射引擎与 CLI 入口",
                    "goal": f"实现 {domain_label} 的核心规则引擎和可执行入口。",
                    "description": (
                        "补齐 src/engine/ 下的领域规则和运行器，并创建 src/main.rs 调用公开 API 输出可验证结果。"
                    ),
                    "scope": engine_targets,
                    "target_files": engine_targets,
                    "steps": [
                        f"实现 src/engine/{rules_module}.rs，将 {keyword_summary} 输入映射为可解释规则结果",
                        f"实现 src/engine/{runner_module}.rs，组织样例数据、边界情况和错误路径",
                        "实现 src/engine/mod.rs，导出 run_domain_rules 或等价公开 API",
                        f"实现 src/main.rs，构造 {keyword_summary} 代表性样例并打印规则输出",
                        "执行 `cargo build` 或 `cargo check` 验证 Rust 编译通过",
                    ],
                    "acceptance": [
                        "`src/main.rs` 可通过 `cargo run` 执行",
                        f"`src/engine/` 源码实现与 {keyword_summary} 对齐的领域规则",
                        "`cargo build` 或 `cargo check` 返回成功",
                    ],
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
                {
                    "id": "TASK-3",
                    "title": f"实现 {domain_label} Rust 验收测试与 README",
                    "goal": f"固化 {domain_label} 的原生 Rust 验收测试、运行说明和交付证据。",
                    "description": "创建 tests/product.rs 与 README.md，通过 cargo test 验证核心领域规则和公开 API。",
                    "scope": verification_targets,
                    "target_files": verification_targets,
                    "context_files": [*model_targets, *engine_targets],
                    "steps": [
                        "创建 tests/product.rs，使用 Rust #[test] 集成测试调用 crate 的公开 API",
                        "原生测试覆盖正常路径、边界情况、错误路径和核心领域规则",
                        "执行 `cargo test --quiet`，证明测试由 Rust 工具链真实发现并运行",
                        "编写 README，说明 cargo build、cargo test 和 cargo run 命令",
                        f"原生测试与 Cargo 门禁覆盖确定性检查：{check_summary}",
                    ],
                    "acceptance": [
                        "`tests/product.rs` 存在、非空且包含至少一个 `#[test]`",
                        "`cargo test --quiet` 发现并运行原生 Rust 测试且返回 PASS",
                        "`README.md` 包含 Cargo 构建、原生测试、运行和验证步骤",
                    ],
                    "phase": "verification",
                    "depends_on": ["TASK-2"],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
            ],
            delivery_plan_document=delivery_plan_document,
            delivery_depth_contract=delivery_depth_contract,
        )

    def _synthesize_cpp_workspace_contracts(
        self,
        *,
        directive: str,
        domain_label: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        deterministic_checks = _extract_deterministic_checks_from_directive(directive)
        content_keywords = _extract_content_any_keywords_from_directive(directive)
        if not content_keywords:
            content_keywords = self._extract_domain_keywords(directive, limit=6)
        keyword_tokens = _contract_keyword_tokens(content_keywords)
        keyword_summary = ", ".join(content_keywords[:6]) if content_keywords else str(domain_label)
        check_summary = "; ".join(deterministic_checks[:8]) if deterministic_checks else "cpp_compile; C++17"
        model_targets = [
            item for token in keyword_tokens for item in (f"src/models/{token}.hpp", f"src/models/{token}.cpp")
        ]
        delivery_targets = [
            "CMakeLists.txt",
            *model_targets,
            "src/engine/generator.hpp",
            "src/engine/generator.cpp",
            "src/main.cpp",
            "tests/test_product.py",
            "README.md",
        ]
        delivery_depth_contract = _delivery_depth_contract(
            domain_label=domain_label,
            language="cpp",
            project_type="cmake_cli",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        delivery_plan_document = _delivery_plan_document(
            domain_label=domain_label,
            language="cpp",
            project_type="cmake_cli",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        return _append_delivery_depth_to_contracts(
            [
                {
                    "id": "TASK-1",
                    "title": f"实现 {domain_label} C++17 CLI、领域模型与验收",
                    "goal": f"在工作区根交付 {domain_label} 的完整 CMake/C++17 CLI、领域模型、验证脚本和 README。",
                    "description": (
                        "创建 CMakeLists.txt、src/models/、src/engine/、src/main.cpp、tests/test_product.py 与 README.md，"
                        f"确保源码覆盖需求关键词和确定性检查：{keyword_summary}。"
                    ),
                    "scope": delivery_targets,
                    "target_files": delivery_targets,
                    "steps": [
                        "创建 CMakeLists.txt，声明 C++17 标准、名为 polaris_app 的可执行目标和所有 src/**/*.cpp 源文件",
                        f"实现 src/models/ 下与 {keyword_summary} 对齐的领域对象头文件和实现文件",
                        "实现 src/engine/generator.hpp，声明领域规则生成公开 API",
                        f"实现 src/engine/generator.cpp，将 {keyword_summary} 等需求元素组合为可解释输出",
                        f"实现 src/main.cpp，构造示例输入并打印 {keyword_summary} 相关结果",
                        "创建 tests/test_product.py，使用 Python unittest 调用 CMake/g++ 或检查 C++ 产物结构",
                        "编写 README，说明 cmake build、直接 g++ 编译、运行和测试命令",
                        f"验证脚本覆盖确定性检查：{check_summary}",
                    ],
                    "acceptance": [
                        "`CMakeLists.txt` 声明 polaris_app 可执行目标；`src/models/`、`src/engine/`、`src/main.cpp`、`tests/test_product.py` 与 `README.md` 存在且非空",
                        "`src/main.cpp` 存在并可作为 C++17 CLI 入口编译运行",
                        f"`src/engine/` 源码实现与 {keyword_summary} 对齐的领域规则",
                        f"源码包含需求关键词：{keyword_summary}",
                        "`cmake --build build` 或 `g++ -std=c++17` 返回成功",
                        "`python -m unittest discover -s tests -p 'test_*.py' -v` 返回 PASS",
                        "`README.md` 包含 C++17 构建、运行和验证步骤",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "implementation",
                    "depends_on": [],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
            ],
            delivery_plan_document=delivery_plan_document,
            delivery_depth_contract=delivery_depth_contract,
        )

    def _synthesize_java_workspace_contracts(
        self,
        *,
        directive: str,
        domain_label: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        deterministic_checks = _extract_deterministic_checks_from_directive(directive)
        content_keywords = _extract_content_any_keywords_from_directive(directive)
        if not content_keywords:
            content_keywords = self._extract_domain_keywords(directive, limit=6)
        keyword_tokens = _contract_keyword_tokens(content_keywords)
        keyword_summary = ", ".join(content_keywords[:6]) if content_keywords else str(domain_label)
        check_summary = "; ".join(deterministic_checks[:8]) if deterministic_checks else "java_compile; javac"
        engine_class = f"{_pascal_case_token(keyword_tokens[0] if keyword_tokens else '', fallback='Domain')}Engine"
        test_class = f"{engine_class}Test"
        domain_targets = [
            f"src/main/java/polaris/factory/domain/{_pascal_case_token(token, fallback=f'Domain{idx + 1}')}Model.java"
            for idx, token in enumerate(keyword_tokens[:3])
        ]
        delivery_targets = [
            "pom.xml",
            "src/main/java/polaris/factory/Main.java",
            *domain_targets,
            f"src/main/java/polaris/factory/engine/{engine_class}.java",
            f"src/test/java/polaris/factory/{test_class}.java",
            "tests/test_product.py",
            "README.md",
        ]
        delivery_depth_contract = _delivery_depth_contract(
            domain_label=domain_label,
            language="java",
            project_type="java_cli",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        delivery_plan_document = _delivery_plan_document(
            domain_label=domain_label,
            language="java",
            project_type="java_cli",
            keywords=content_keywords,
            checks=deterministic_checks,
        )
        return _append_delivery_depth_to_contracts(
            [
                {
                    "id": "TASK-1",
                    "title": f"实现 {domain_label} Java CLI、领域模型与验收",
                    "goal": f"在工作区根交付 {domain_label} 的完整 Java CLI、领域模型、自包含验证和 README。",
                    "description": (
                        "创建 src/main/java/、src/test/java/、tests/test_product.py 与 README.md，"
                        f"确保 Java 源码覆盖需求关键词和确定性检查：{keyword_summary}。"
                    ),
                    "scope": delivery_targets,
                    "target_files": delivery_targets,
                    "steps": [
                        "创建 pom.xml 或等价 Java 项目元数据，但 java_compile 必须不依赖 Maven/Gradle 才能通过 javac",
                        "实现 src/main/java/polaris/factory/Main.java，作为可直接 java 运行的 CLI 入口",
                        f"实现 src/main/java/polaris/factory/domain/ 下的领域模型，表达 {keyword_summary} 规则",
                        f"实现 src/main/java/polaris/factory/engine/{engine_class}.java，计算 {keyword_summary} 相关状态、评分或匹配结果",
                        f"实现 src/test/java/polaris/factory/{test_class}.java，使用 main/assert 或标准库自包含验证，禁止依赖未声明 JUnit",
                        "创建 tests/test_product.py，使用 Python unittest 调用 javac/java 或检查 Java 产物结构",
                        "编写 README，说明 javac 编译、java 运行和测试命令",
                        f"验证脚本覆盖确定性检查：{check_summary}",
                    ],
                    "acceptance": [
                        "`src/main/java/`、`src/test/java/`、`tests/test_product.py` 与 `README.md` 存在且非空",
                        "`src/main/java/polaris/factory/Main.java` 存在并可作为 Java CLI 入口编译运行",
                        f"`src/main/java/` 源码实现与 {keyword_summary} 对齐的领域规则",
                        f"源码包含需求关键词：{keyword_summary}",
                        "`javac -encoding UTF-8` 对所有 `.java` 文件返回成功",
                        "`python -m unittest discover -s tests -p 'test_*.py' -v` 返回 PASS",
                        "`README.md` 包含 Java 编译、运行和验证步骤",
                        f"确定性检查进入任务验收：{check_summary}",
                    ],
                    "phase": "implementation",
                    "depends_on": [],
                    "assigned_to": "Director",
                    "metadata": dict(source_metadata),
                },
            ],
            delivery_plan_document=delivery_plan_document,
            delivery_depth_contract=delivery_depth_contract,
        )

    def _synthesize_placeholder_repair_contracts(
        self,
        *,
        directive: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        directive_text = str(directive or "")
        lowered = directive_text.lower()
        has_placeholder_signal = "placeholder_content_detected" in lowered or bool(
            re.search(r"\bplaceholder\b", lowered)
        )
        if not has_placeholder_signal:
            return []
        has_explicit_qa_placeholder_signal = "placeholder_content_detected" in lowered or "qa_rework_reason" in lowered
        if not has_explicit_qa_placeholder_signal and _directive_requires_language_root_delivery_contract(
            directive_text
        ):
            return []

        scope_paths = self._extract_directive_file_paths(directive_text, limit=12)
        source_scope_paths = [
            path
            for path in scope_paths
            if path.startswith("src/") and not any(part in path for part in ("/__tests__/", ".test."))
        ]
        if not source_scope_paths:
            return []

        scope_text = ", ".join(source_scope_paths)
        failure_context = self._sanitize_plan_artifact_text(
            self._compact_text_for_prompt(directive_text, max_chars=900)
        )
        cleanup_paths = []
        if re.search(r"(?i)(?:^|\s)PATCH_FILE\s+src[\\/]", directive_text):
            cleanup_paths.append("PATCH_FILE src/")
        repair_metadata = dict(source_metadata)
        repair_metadata["qa_rework_reason"] = "placeholder_content_detected"
        repair_metadata["qa_rework_evidence"] = [
            line.strip()
            for line in directive_text.splitlines()
            if "placeholder" in line.lower() and any(path in line for path in source_scope_paths)
        ][:12]
        if cleanup_paths:
            repair_metadata["cleanup_paths"] = cleanup_paths
        verification_metadata = dict(source_metadata)
        verification_metadata["qa_rework_verification_only"] = True

        cleanup_steps = [
            f"Remove malformed Director protocol artifact directory `{path}` if it exists" for path in cleanup_paths
        ]
        cleanup_acceptance = [
            f"Malformed Director protocol artifact `{path}` no longer exists" for path in cleanup_paths
        ]

        return [
            {
                "id": "TASK-1",
                "title": "QA Placeholder Evidence Repair",
                "goal": "Remove unfinished placeholder markers from the concrete QA evidence files without changing public behavior.",
                "description": f"QA evidence repair context: {failure_context}",
                "scope": source_scope_paths,
                "steps": [
                    "Inspect each QA evidence file and locate unfinished TODO/FIXME/placeholder/stub markers",
                    "Replace unfinished markers with concrete production-safe logic or neutral non-placeholder wording",
                    "Restore any source file that was accidentally overwritten by protocol diff text before applying the wording repair",
                    *cleanup_steps,
                    "Preserve existing provider behavior, public API shapes, and tests",
                ],
                "acceptance": [
                    f"No unfinished placeholder/stub marker remains in {scope_text}",
                    "Evidence source files remain valid source code, not flattened text or unified diff fragments",
                    *cleanup_acceptance,
                    "Existing provider behavior and public API shapes are preserved",
                    "No TODO, FIXME, NotImplemented, placeholder, or stub markers are introduced",
                ],
                "phase": "implementation",
                "depends_on": [],
                "assigned_to": "Director",
                "metadata": repair_metadata,
            },
            {
                "id": "TASK-2",
                "title": "QA Placeholder Repair Verification",
                "goal": "Verify the placeholder repair through project tests, build, and QA evidence scan.",
                "description": f"Verify repaired files: {scope_text}.",
                "scope": [*source_scope_paths, "package.json", "tests/"],
                "steps": [
                    "Run npm test in the target workspace",
                    "Run npm run build in the target workspace",
                    "Confirm QA no longer reports placeholder_content_detected for the evidence files",
                ],
                "acceptance": [
                    "npm test returns PASS",
                    "npm run build returns PASS",
                    "placeholder_content_detected no longer references the evidence files",
                ],
                "phase": "verification",
                "depends_on": ["TASK-1"],
                "assigned_to": "Director",
                "metadata": verification_metadata,
            },
        ]

    def _synthesize_frontend_test_repair_contracts(
        self,
        *,
        directive: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build focused contracts for frontend test/type failures.

        This is intentionally generic: the PM does not encode business-domain
        knowledge, it turns an explicit failing test directive into a bounded
        reproduce/fix/verify handoff for Director.
        """
        directive_text = str(directive or "")
        lowered = directive_text.lower()
        has_explicit_test_failure = bool(
            re.search(
                r"npm\s+test.{0,240}(?:fail|failed|failure|failing|typeerror|cannot read|报错|失败)",
                lowered,
                flags=re.DOTALL,
            )
            or re.search(
                r"(?:fail|failed|failure|failing|typeerror|cannot read|报错|失败).{0,240}npm\s+test",
                lowered,
                flags=re.DOTALL,
            )
        )
        is_frontend_test_failure = (
            has_explicit_test_failure
            and any(token in lowered for token in ("vitest", ".test.", "test failure", "failing test", "failed test"))
            and any(token in lowered for token in ("typescript", ".ts", ".tsx", "type", "import", "export"))
        )
        if not is_frontend_test_failure:
            return []

        scope_paths = self._extract_directive_file_paths(directive_text, limit=8)
        scope_paths = self._infer_directive_related_module_paths(directive_text, scope_paths, limit=8)
        if not scope_paths:
            scope_paths = ["src/", "tests/", "package.json"]
        scope_text = ", ".join(scope_paths)
        failure_context = self._sanitize_plan_artifact_text(
            self._compact_text_for_prompt(directive_text, max_chars=900)
        )

        return [
            {
                "id": "TASK-1",
                "title": "Frontend Test Failure Reproduction",
                "goal": "Reproduce the reported frontend test failure and identify the smallest type or import contract mismatch.",
                "description": (
                    "Collect exact failing test evidence before modifying target project files. "
                    f"Failure context: {failure_context}"
                ),
                "scope": scope_paths,
                "steps": [
                    "Run npm test in the target workspace and capture the failing test name and stack frame",
                    "Inspect the failing test file and directly referenced TypeScript modules",
                    "Identify the minimal export, import, or shape mismatch causing the failure",
                ],
                "acceptance": [
                    "The failing Vitest case and exact TypeScript symbol mismatch are identified",
                    f"Repair scope is limited to {scope_text}",
                ],
                "phase": "requirements",
                "depends_on": [],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
            {
                "id": "TASK-2",
                "title": "Minimal Frontend Type Contract Repair",
                "goal": "Apply the smallest target-project TypeScript change that makes the failing test align with the current contract.",
                "description": (
                    "Prefer a narrow export/import or test contract fix over broad rewrites. "
                    f"Failure context: {failure_context}"
                ),
                "scope": scope_paths,
                "steps": [
                    "Update only the directly implicated target project TypeScript files",
                    "Preserve existing public domain types unless the failing test proves they are incomplete",
                    "Run npm test and npm run build after the repair",
                ],
                "acceptance": [
                    "npm test returns PASS",
                    "npm run build returns PASS",
                    "No Polaris repository business code is changed for the target repair",
                ],
                "phase": "implementation",
                "depends_on": ["TASK-1"],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
        ]

    @staticmethod
    def _extract_directive_file_paths(directive: str, *, limit: int) -> list[str]:
        rows: list[str] = []
        for match in re.finditer(
            r"(?:src|tests|app|lib|packages)/[A-Za-z0-9_./*{}@-]+\.(?:py|ts|tsx|js|jsx|json|ya?ml|md)",
            directive,
        ):
            token = match.group(0).strip().replace("\\", "/")
            if token not in rows:
                rows.append(token)
            if len(rows) >= limit:
                break
        return rows

    @staticmethod
    def _infer_directive_related_module_paths(directive: str, scope_paths: list[str], *, limit: int) -> list[str]:
        rows = list(scope_paths)
        imports = [
            match.group("module").strip()
            for match in re.finditer(r"from\s+['\"](?P<module>\.{1,2}/[^'\"]+)['\"]", str(directive or ""))
        ]
        imports.extend(
            match.group("module").strip().rstrip(".,;:")
            for match in re.finditer(r"\bfrom\s+(?P<module>\.{1,2}/[A-Za-z0-9_./-]+)", str(directive or ""))
        )
        if not imports:
            return rows[:limit]
        source_paths = [
            path for path in rows if re.search(r"\.(?:test|spec)\.(?:ts|tsx|js|jsx)$", path) or "/__tests__/" in path
        ]
        for source in source_paths:
            source_dir = source.replace("\\", "/").rsplit("/", 1)[0]
            for module_ref in imports:
                normalized = str(Path(source_dir, module_ref).as_posix()).replace("\\", "/")
                while "/./" in normalized:
                    normalized = normalized.replace("/./", "/")
                parts: list[str] = []
                for part in normalized.split("/"):
                    if part == ".." and parts:
                        parts.pop()
                    elif part not in {"", "."}:
                        parts.append(part)
                candidate = "/".join(parts)
                if candidate and not Path(candidate).suffix:
                    candidate = f"{candidate}.ts"
                if candidate and candidate not in rows:
                    rows.append(candidate)
                if len(rows) >= limit:
                    return rows[:limit]
        return rows[:limit]

    def _synthesize_frontend_workbench_contracts(
        self,
        *,
        directive: str,
        domain: str,
        source_metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build execution-ready contracts for desktop/frontend workbench directives."""
        text = str(directive or "").lower()
        frontend_hits = sum(
            1
            for token in (
                "react",
                "electron",
                "vite",
                "tailwind",
                "typescript",
                "zustand",
                "workbench",
                "desktop",
            )
            if token in text
        )
        route_hits = re.findall(r"/(?:workbench|library|history|settings)/[a-z0-9-]+", str(directive or ""))
        if frontend_hits < 3 and len(route_hits) < 3:
            return []

        route_summary = ", ".join(sorted(set(route_hits))[:12])
        if not route_summary:
            route_summary = "/workbench/*, /library/*, /history/jobs, /settings/models"

        return [
            {
                "id": "TASK-1",
                "title": "Project Foundation & Toolchain Setup",
                "goal": "Create a runnable desktop frontend project scaffold with strict TypeScript, Electron, Vite, TailwindCSS, and test tooling.",
                "description": "Establish buildable project foundations before domain UI work begins.",
                "scope": [
                    "package.json",
                    "tsconfig.json",
                    "vite.config.ts",
                    "tailwind.config.js",
                    "postcss.config.js",
                    "electron/main.ts",
                    "electron/preload.ts",
                    "src/main.tsx",
                    "src/index.css",
                    "README.md",
                ],
                "steps": [
                    "Create package scripts for dev, test, build, and Electron build flows",
                    "Configure strict TypeScript, Vite React, TailwindCSS, and Electron preload/main entries",
                    "Create renderer entrypoint and base CSS tokens for the workbench UI",
                    "Document UTF-8 run, test, and build commands in README",
                ],
                "acceptance": [
                    "npm install completes without dependency resolution errors",
                    "npm test runs at least one passing Vitest test",
                    "npm run build succeeds and emits production renderer assets",
                    "Project contains non-empty Electron, Vite, Tailwind, TypeScript, and README files",
                ],
                "phase": "requirements",
                "depends_on": [],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
            {
                "id": "TASK-2",
                "title": "Asset Model & Generation Spec Layer",
                "goal": "Implement typed asset entities, provider-agnostic generation specs, local edit tasks, and a mock generation service.",
                "description": "Represent the requested creative workflow as data and service contracts before UI screens consume it.",
                "scope": ["src/types", "src/spec", "src/services", "src/store", "tests/spec"],
                "target_files": [
                    "src/types/asset.ts",
                    "src/spec/GenerationSpec.ts",
                    "src/services/mockGenerationService.ts",
                    "src/services/generationService.ts",
                    "src/store/workbench.ts",
                    "tests/spec/GenerationSpec.test.ts",
                ],
                "steps": [
                    "Define asset, reference-purpose, face identity, template, result, and local edit task types",
                    "Implement GenerationSpec builder and validator with task-type specific required inputs",
                    "Implement deterministic mock generation provider with queue, progress, and mock result images",
                    "Add Zustand store slices for projects, assets, identities, templates, jobs, and active workbench state",
                ],
                "acceptance": [
                    "GenerationSpec builder creates immutable specs for model, headless, face-lab, scene, and batch tasks",
                    "Validation reports explicit errors for missing garments, duplicate reference-purpose assignments, and invalid dimensions",
                    "Mock generation service exposes queued, processing, completed, and failed job states",
                    "Unit tests cover spec validation and store/service state transitions",
                ],
                "phase": "implementation",
                "depends_on": ["TASK-1"],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
            {
                "id": "TASK-3",
                "title": "Workbench Screens & Layered UI Implementation",
                "goal": "Build the route-driven creative workbench UI with layered asset rail, canvas, parameter panel, and queue/history strip.",
                "description": f"Implement task-type-driven routes and controls: {route_summary}.",
                "scope": ["src/App.tsx", "src/layouts", "src/workbench", "src/components", "src/library"],
                "steps": [
                    "Implement app routing, navigation rail, workbench shell, responsive panels, and route fallback",
                    "Build model, headless, face-lab, scene/reference, and batch workbench screens with functional controls",
                    "Build library/history/settings screens for assets, model identities, templates, jobs, and model settings",
                    "Wire submit actions to GenerationSpec builder, store state, and mock generation queue",
                ],
                "acceptance": [
                    "All configured workbench, library, history, and settings routes render without runtime errors",
                    "Main model workbench can create a spec with a garment input and submit a mock job",
                    "Face Lab saves a reusable identity card with multi-angle preview data",
                    "Scene workbench prevents duplicate garment-fact reference-purpose assignments",
                    "No emoji icons are used; action icons come from the configured icon library",
                ],
                "phase": "implementation",
                "depends_on": ["TASK-2"],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
            {
                "id": "TASK-4",
                "title": "Delivery Tests, Build Verification & Documentation",
                "goal": "Validate the desktop workbench end to end and record reproducible delivery evidence.",
                "description": "Add tests and verification scripts for the generated project baseline.",
                "scope": ["tests", "src/**/*.test.ts", "src/**/*.test.tsx", "README.md", "vitest.config.ts"],
                "steps": [
                    "Add unit tests for GenerationSpec, validation rules, store actions, and batch CSV parsing",
                    "Add route smoke tests for the main workbench and library/settings screens",
                    "Run npm test and npm run build and capture expected commands in README",
                    "Ensure the project meets file count, line count, module, config, and test acceptance targets",
                ],
                "acceptance": [
                    "npm test returns PASS",
                    "npm run build returns PASS",
                    "At least 10 source/config/test files and 500+ lines exist",
                    "README explains project purpose, install, dev, test, and build commands in UTF-8 text",
                ],
                "phase": "verification",
                "depends_on": ["TASK-3"],
                "assigned_to": "Director",
                "metadata": dict(source_metadata),
            },
        ]

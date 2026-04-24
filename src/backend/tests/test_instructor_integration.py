"""Tests for Instructor integration.

验证结构化输出功能的正确性。
"""

import pytest
from polaris.cells.roles.adapters.internal.schemas import (
    ROLE_OUTPUT_SCHEMAS,
    BlueprintOutput,
    ConstructionPlan,
    QAReportOutput,
    Task,
    TaskListOutput,
    get_schema_for_role,
)
from pydantic import ValidationError


class TestPMSchema:
    """Test PM task list schema."""

    def test_valid_task_creation(self):
        """Test creating a valid task."""
        task = Task(
            id="TASK-001",
            title="实现登录功能",
            description="使用JWT实现用户登录功能，包含前端表单和后端验证",
            target_files=["src/auth.py", "src/login.html"],
            acceptance_criteria=["用户可正常登录", "密码错误提示清晰"],
            priority="high",
            phase="core",
            estimated_effort=5,
            dependencies=[],
        )
        assert task.id == "TASK-001"
        assert task.priority == "high"

    def test_task_id_pattern_validation(self):
        """Test task ID format validation."""
        with pytest.raises(ValidationError):
            Task(
                id="invalid-id",  # Should be TASK-XXX
                title="测试",
                description="这是一个测试任务的描述内容",
                acceptance_criteria=["标准1"],
                priority="medium",
                phase="core",
                estimated_effort=3,
            )

    def test_vague_word_validation(self):
        """Test rejection of vague words in criteria."""
        with pytest.raises(ValidationError) as exc_info:
            Task(
                id="TASK-002",
                title="测试任务",
                description="这是一个测试任务的描述内容，足够长的描述",
                acceptance_criteria=["适当的响应时间"],  # "适当" is vague
                priority="medium",
                phase="core",
                estimated_effort=3,
            )
        assert "适当" in str(exc_info.value)

    def test_unsafe_path_validation(self):
        """Test rejection of unsafe file paths."""
        with pytest.raises(ValidationError):
            Task(
                id="TASK-003",
                title="测试任务",
                description="这是一个测试任务的描述内容，需要足够长的描述",
                target_files=["../etc/passwd"],  # Unsafe path
                acceptance_criteria=["标准1"],
                priority="medium",
                phase="core",
                estimated_effort=3,
            )

    def test_task_list_output(self):
        """Test full task list output."""
        output = TaskListOutput(
            tasks=[
                Task(
                    id="TASK-001",
                    title="实现用户认证模块",
                    description="使用JWT实现完整的用户认证模块，包含登录、登出和Token刷新功能",
                    acceptance_criteria=["标准1", "标准2"],
                    priority="high",
                    phase="bootstrap",
                    estimated_effort=3,
                ),
                Task(
                    id="TASK-002",
                    title="添加权限控制功能",
                    description="基于RBAC模型实现细粒度的权限控制系统，支持角色和资源的动态配置",
                    acceptance_criteria=["标准3"],
                    priority="medium",
                    phase="core",
                    estimated_effort=5,
                    dependencies=["TASK-001"],
                ),
            ],
            analysis={
                "total_tasks": 2,
                "risk_level": "medium",
                "key_risks": ["依赖复杂"],
                "recommended_sequence": ["TASK-001", "TASK-002"],
            },
        )
        assert len(output.tasks) == 2
        assert output.analysis.total_tasks == 2

    def test_duplicate_task_ids_rejected(self):
        """Test that duplicate task IDs are rejected."""
        with pytest.raises(ValidationError):
            TaskListOutput(
                tasks=[
                    Task(
                        id="TASK-001",
                        title="任务1",
                        description="这是任务1的详细描述，描述需要足够长",
                        acceptance_criteria=["标准1"],
                        priority="high",
                        phase="core",
                        estimated_effort=3,
                    ),
                    Task(
                        id="TASK-001",  # Duplicate
                        title="任务2",
                        description="这是任务2的详细描述，描述需要足够长",
                        acceptance_criteria=["标准2"],
                        priority="medium",
                        phase="core",
                        estimated_effort=3,
                    ),
                ],
                analysis={"total_tasks": 2, "risk_level": "low", "recommended_sequence": []},
            )

    def test_dependency_validation(self):
        """Test that dependencies must reference existing tasks."""
        with pytest.raises(ValidationError):
            TaskListOutput(
                tasks=[
                    Task(
                        id="TASK-001",
                        title="任务1",
                        description="这是任务1的详细描述，描述需要足够长",
                        acceptance_criteria=["标准1"],
                        priority="high",
                        phase="core",
                        estimated_effort=3,
                        dependencies=["TASK-999"],  # Non-existent
                    ),
                ],
                analysis={"total_tasks": 1, "risk_level": "low", "recommended_sequence": ["TASK-001"]},
            )


class TestChiefEngineerSchema:
    """Test Chief Engineer blueprint schema."""

    def test_blueprint_creation(self):
        """Test creating a valid blueprint."""
        blueprint = BlueprintOutput(
            blueprint_version="1.0",
            blueprint_id="bp-001",
            task_id="TASK-001",
            doc_id="doc-001",
            analysis={
                "level": "medium",
                "estimated_files": 5,
                "estimated_lines": 500,
                "technical_approach": "使用FastAPI框架实现RESTful API服务，采用SQLAlchemy作为ORM进行数据库操作，Pydantic进行数据验证",
            },
            construction_plan=ConstructionPlan(
                preparation=["安装依赖", "创建目录"],
                implementation=["实现模型", "添加路由"],
                verification=["运行单元测试", "验证API响应"],
            ),
            scope_for_apply=["src/runtime_models.py", "src/routes.py"],
            dependencies={
                "required": [],
                "concurrent_safe": True,
                "external_libs": ["fastapi", "sqlalchemy"],
            },
        )
        assert blueprint.blueprint_version == "1.0"
        assert blueprint.blueprint_id == "bp-001"
        assert blueprint.doc_id == "doc-001"
        assert len(blueprint.scope_for_apply) == 2

    def test_blueprint_defaults(self):
        """Test blueprint fields have sensible defaults."""
        blueprint = BlueprintOutput(
            blueprint_version="1.0",
            task_id="TASK-001",
            analysis={
                "level": "low",
                "estimated_files": 1,
                "estimated_lines": 50,
                "technical_approach": "A simple technical approach description that is long enough to pass the minimum length validation requirement",
            },
            construction_plan=ConstructionPlan(
                implementation=["步骤1"],
                verification=["验证1"],
            ),
            scope_for_apply=["src/a.py"],
        )
        assert blueprint.blueprint_id is None
        assert blueprint.doc_id is None

    def test_risk_flag_requires_mitigation(self):
        """Test that critical risks require mitigation."""
        with pytest.raises(ValidationError):
            BlueprintOutput(
                blueprint_version="1.0",
                analysis={
                    "level": "high",
                    "estimated_files": 10,
                    "estimated_lines": 1000,
                    "technical_approach": "复杂的重构任务",
                },
                construction_plan=ConstructionPlan(
                    implementation=["步骤1"],
                    verification=["验证1"],
                ),
                scope_for_apply=["src/fastapi_entrypoint.py"],
                risk_flags=[
                    {
                        "level": "error",
                        "description": "高风险操作，可能导致系统不稳定",
                        # Missing mitigation
                    }
                ],
            )


class TestQASchema:
    """Test QA report schema."""

    def test_qa_report_pass(self):
        """Test PASS verdict."""
        report = QAReportOutput(
            verdict="PASS",
            summary="经过全面代码审查，所有模块均符合质量标准，测试覆盖率达标",
            findings=[],
            metrics={"code_coverage": 85.5},
        )
        assert report.verdict == "PASS"

    def test_qa_report_fail_requires_findings(self):
        """Test that FAIL verdict requires findings."""
        with pytest.raises(ValidationError):
            QAReportOutput(
                verdict="FAIL",
                summary="审查失败",
                findings=[],  # Empty but verdict is FAIL
            )

    def test_pass_with_critical_finding_rejected(self):
        """Test that PASS with critical findings is rejected."""
        with pytest.raises(ValidationError):
            QAReportOutput(
                verdict="PASS",
                summary="通过",
                findings=[
                    {
                        "severity": "critical",
                        "category": "security",
                        "description": "SQL注入漏洞",
                        "recommendation": "使用参数化查询",
                    }
                ],
            )

    def test_blocked_requires_blockers(self):
        """Test that BLOCKED verdict requires blockers list."""
        with pytest.raises(ValidationError):
            QAReportOutput(
                verdict="BLOCKED",
                summary="被阻塞",
                findings=[],
                # Missing blockers
            )


class TestSchemaRegistry:
    """Test schema registry functions."""

    def test_get_schema_for_pm(self):
        """Test getting schema for PM role."""
        schema = get_schema_for_role("pm")
        assert schema is TaskListOutput

    def test_get_schema_for_chief_engineer(self):
        """Test getting schema for Chief Engineer role."""
        schema = get_schema_for_role("chief_engineer")
        assert schema is BlueprintOutput

    def test_get_schema_for_unknown_role(self):
        """Test getting schema for unknown role."""
        schema = get_schema_for_role("unknown_role")
        assert schema is None

    def test_role_output_schemas_complete(self):
        """Test that all expected roles have schemas."""
        expected_roles = ["pm", "chief_engineer", "architect", "qa", "director"]
        for role in expected_roles:
            assert role in ROLE_OUTPUT_SCHEMAS, f"Missing schema for {role}"


class TestInstructorClientFallback:
    """Test Instructor client fallback behavior."""

    def test_import_without_instructor(self):
        """Test that the module works even without instructor installed."""
        # This tests the try/except import pattern
        from polaris.infrastructure.llm import instructor_client

        assert hasattr(instructor_client, "StructuredLLMClient")
        assert hasattr(instructor_client, "INSTRUCTOR_AVAILABLE")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

from polaris.bootstrap.config import EngineeringPermissionProfile, ServerConfig


def test_engineering_defaults_include_recovery_deadline_and_qa() -> None:
    config = ServerConfig()
    assert config.engineering_run_timeout_seconds == 3600
    assert config.engineering_require_qa is True
    assert "read_command_result" in EngineeringPermissionProfile().enabled_tools
    assert "report_review" in EngineeringPermissionProfile().enabled_tools

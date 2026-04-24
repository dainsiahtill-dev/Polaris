"""Tests for polaris.domain.exceptions."""

from __future__ import annotations

from polaris.domain.exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConfigurationError,
    ConflictError,
    DomainException,
    ExternalServiceError,
    InfrastructureError,
    LLMError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    ProcessAlreadyRunningError,
    ProcessError,
    ProcessNotRunningError,
    RateLimitError,
    ServiceUnavailableError,
    StateError,
    StorageError,
    TimeoutError,
    ValidationError,
)


class TestDomainException:
    def test_basic_attributes(self) -> None:
        exc = DomainException("msg", code="CODE")
        assert exc.message == "msg"
        assert exc.code == "CODE"
        assert exc.details == {}

    def test_to_dict(self) -> None:
        exc = DomainException("msg", code="CODE", details={"k": "v"})
        d = exc.to_dict()
        assert d["code"] == "CODE"
        assert d["message"] == "msg"
        assert d["details"] == {"k": "v"}

    def test_str_with_details(self) -> None:
        exc = DomainException("msg", details={"k": "v"})
        assert "msg" in str(exc)
        assert "k" in str(exc)

    def test_str_without_details(self) -> None:
        exc = DomainException("msg")
        assert str(exc) == "[DOMAIN_ERROR] msg"


class TestValidationError:
    def test_field_and_value(self) -> None:
        exc = ValidationError("bad", field="name", value="x")
        assert exc.details["field"] == "name"
        assert exc.details["value"] == "x"
        assert exc.status_code == 422


class TestNotFoundError:
    def test_default_message(self) -> None:
        exc = NotFoundError("User", "123")
        assert "User '123' not found" in str(exc)
        assert exc.status_code == 404


class TestConflictError:
    def test_resource_type(self) -> None:
        exc = ConflictError("oops", resource_type="User")
        assert exc.details["resource_type"] == "User"


class TestPermissionDeniedError:
    def test_action_and_resource(self) -> None:
        exc = PermissionDeniedError("nope", action="delete", resource="User")
        assert exc.details["action"] == "delete"
        assert exc.details["resource"] == "User"


class TestRateLimitError:
    def test_retry_after(self) -> None:
        exc = RateLimitError("slow down", retry_after=60)
        assert exc.details["retry_after"] == 60


class TestStateError:
    def test_states(self) -> None:
        exc = StateError("bad", current_state="idle", required_state="running")
        assert exc.details["current_state"] == "idle"
        assert exc.details["required_state"] == "running"


class TestProcessError:
    def test_process_and_exit_code(self) -> None:
        exc = ProcessError("fail", process_name="worker", exit_code=1)
        assert exc.details["process"] == "worker"
        assert exc.details["exit_code"] == 1


class TestProcessAlreadyRunningError:
    def test_pid(self) -> None:
        exc = ProcessAlreadyRunningError("worker", pid=1234)
        assert exc.details["pid"] == 1234


class TestProcessNotRunningError:
    def test_message(self) -> None:
        exc = ProcessNotRunningError("worker")
        assert "not running" in str(exc)


class TestStorageError:
    def test_path_and_operation(self) -> None:
        exc = StorageError("fail", path="/tmp", operation="read")
        assert exc.details["path"] == "/tmp"
        assert exc.details["operation"] == "read"


class TestNetworkError:
    def test_url(self) -> None:
        exc = NetworkError("fail", url="http://example.com")
        assert exc.details["url"] == "http://example.com"


class TestExternalServiceError:
    def test_service_and_status(self) -> None:
        exc = ExternalServiceError("svc", "fail", status_code=500)
        assert exc.details["service"] == "svc"
        assert exc.details["status_code"] == 500


class TestServiceUnavailableError:
    def test_service(self) -> None:
        exc = ServiceUnavailableError("db")
        assert "db" in str(exc)


class TestConfigurationError:
    def test_setting(self) -> None:
        exc = ConfigurationError("bad", setting="timeout")
        assert exc.details["setting"] == "timeout"


class TestTimeoutError:
    def test_timeout_and_operation(self) -> None:
        exc = TimeoutError("took too long", timeout_seconds=30.0, operation="query")
        assert exc.details["timeout_seconds"] == 30.0
        assert exc.details["operation"] == "query"


class TestLLMError:
    def test_provider_and_model(self) -> None:
        exc = LLMError("fail", provider="openai", model="gpt-4")
        assert exc.details["provider"] == "openai"
        assert exc.details["model"] == "gpt-4"


class TestBusinessRuleError:
    def test_status_code(self) -> None:
        exc = BusinessRuleError("rule broken")
        assert exc.status_code == 400


class TestAuthenticationError:
    def test_status_code(self) -> None:
        exc = AuthenticationError("fail")
        assert exc.status_code == 401


class TestInfrastructureError:
    def test_status_code(self) -> None:
        exc = InfrastructureError("fail")
        assert exc.status_code == 500

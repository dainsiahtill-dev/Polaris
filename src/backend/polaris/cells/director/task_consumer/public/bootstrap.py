"""Bootstrap binding for Director project verification."""

from .project_verification import (
    ProjectVerificationClientPortV1,
    _bind_project_verification_client,
)


def bind_project_verification_client(port: ProjectVerificationClientPortV1) -> None:
    _bind_project_verification_client(port)


__all__ = ["bind_project_verification_client"]

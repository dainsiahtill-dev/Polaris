"""Public boundary for `architect.design`."""

from .contracts import (
    ArchitectDesignError,
    ArchitectDesignErrorV1,
    ArchitectureDesignGeneratedEventV1,
    ArchitectureDesignResultV1,
    GenerateArchitectureDesignCommandV1,
    QueryArchitectureDesignStatusV1,
)
from .service import ArchitectConfig, ArchitectService, ArchitectureDoc

__all__ = [
    "ArchitectConfig",
    "ArchitectDesignError",
    "ArchitectDesignErrorV1",
    "ArchitectService",
    "ArchitectureDesignGeneratedEventV1",
    "ArchitectureDesignResultV1",
    "ArchitectureDoc",
    "GenerateArchitectureDesignCommandV1",
    "QueryArchitectureDesignStatusV1",
]

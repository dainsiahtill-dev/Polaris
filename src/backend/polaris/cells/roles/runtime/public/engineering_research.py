"""Read-only engineering research evidence and laboratory dependency projections."""

from polaris.cells.roles.runtime.internal.engineering_research_exchange import (
    DESCRIPTOR_LIMIT as DESCRIPTOR_LIMIT,
    FILE_LIMIT as FILE_LIMIT,
    PUBLICATION_LIMIT as PUBLICATION_LIMIT,
    adopt_research_publications as adopt_research_publications,
    valid_digest as valid_digest,
    validate_descriptor as validate_descriptor,
)
from polaris.cells.roles.runtime.internal.engineering_research_labs import (
    PLAN_LIMIT as PLAN_LIMIT,
    project_lab as project_lab,
)
from polaris.cells.roles.runtime.internal.engineering_research_references import (
    decode_research_json as decode_research_json,
    verify_references as verify_references,
)
from polaris.cells.roles.runtime.internal.engineering_session import (
    query_research_laboratories as query_research_laboratories,
)

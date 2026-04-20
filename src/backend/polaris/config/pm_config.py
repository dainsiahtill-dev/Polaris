from pydantic import BaseModel, Field

class PMConfig(BaseModel):
    """Project Manager configuration."""

    model: str | None = Field(default=None, description="Model override for PM")
    backend: str = Field(default="auto", description="PM backend type")
    show_output: bool = Field(default=True, description="Show PM output")
    runs_director: bool = Field(default=True, description="PM runs director automatically")
    director_show_output: bool = Field(default=False, description="Show director output via PM")
    director_timeout: int = Field(default=600, description="Director timeout in seconds")
    director_iterations: int = Field(default=1, description="Director iterations per PM cycle")
    director_match_mode: str = Field(default="run_id", description="Match mode for director")
    agents_approval_mode: str = Field(default="auto_accept", description="Agents approval mode")
    agents_approval_timeout: int = Field(default=90, description="Approval timeout in seconds")
    max_failures: int = Field(default=5, description="Maximum consecutive failures")
    max_blocked: int = Field(default=5, description="Maximum blocked iterations")
    max_same: int = Field(default=3, description="Maximum same-state iterations")
    blocked_strategy: str = Field(
        default="auto", description="Blocked task handling strategy: skip, manual, degrade_retry, auto"
    )
    blocked_degrade_max_retries: int = Field(
        default=1, description="Maximum degrade retries for blocked tasks (degrade_retry/auto strategies)"
    )

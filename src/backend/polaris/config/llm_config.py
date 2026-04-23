from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    model: str = Field(
        default="modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest",
        description="Default LLM model identifier",
    )
    provider: str = Field(default="ollama", description="LLM provider name")
    base_url: str = Field(default="", description="Base URL for API")
    api_key: str = Field(default="", description="API key")
    api_path: str = Field(default="/v1/chat/completions", description="API endpoint path")
    timeout: int = Field(default=300, description="Request timeout in seconds")

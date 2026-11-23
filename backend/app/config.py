from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR.parent / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-flash"
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 60
    llm_max_retries: int = 2
    llm_tool_choice: str = "required"
    # Official DeepSeek defaults to thinking, which rejects tool_choice=required.
    llm_thinking: str = "disabled"

    agent_max_iterations: int = 15
    agent_history_messages: int = Field(default=8, ge=0)
    agent_context_input_tokens: int = Field(default=12_000, gt=0)

    sim_max_steps: int = 100

    app_env: str = "dev"
    log_level: str = "INFO"
    log_dir: str = "logs"
    cors_origins: str = "http://localhost:5173"

    @property
    def log_path(self) -> Path:
        p = Path(self.log_dir)
        return p if p.is_absolute() else BACKEND_DIR / p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

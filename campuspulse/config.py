"""Environment-backed settings, with secrets excluded from repr."""
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="")
    gemini_api_key: SecretStr | None = Field(default=None, repr=False)
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    google_genai_use_vertexai: bool = False
    gemini_model: str = "gemini-2.5-flash"
    github_token: SecretStr | None = Field(default=None, repr=False)
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"
    database_path: str = "data/campuspulse.sqlite3"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_tool_calls: int = 8
    request_timeout_seconds: float = 60
    allow_github_writes: bool = False

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key or (self.google_genai_use_vertexai and self.google_cloud_project))

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token)

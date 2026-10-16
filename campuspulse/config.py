"""Environment-backed settings, with secrets excluded from repr."""
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="")
    gemini_api_key: SecretStr | None = Field(default=None, repr=False)
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    gemini_location: str = "global"
    google_genai_use_vertexai: bool = False
    gemini_model: str = "gemini-3.8-flash"
    github_token: SecretStr | None = Field(default=None, repr=False)
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"
    database_path: str = "data/campuspulse.sqlite3"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_tool_calls: int = 8
    request_timeout_seconds: float = 60
    allow_github_writes: bool = False
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"])

    @property
    def gemini_configured(self) -> bool:
        return bool((self.gemini_api_key and self.gemini_api_key.get_secret_value())
                    or (self.google_genai_use_vertexai and self.google_cloud_project))

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token and self.github_token.get_secret_value())

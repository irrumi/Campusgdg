"""Read-only Agent Engine entrypoint; import never loads settings or credentials."""
from __future__ import annotations

import os
from typing import Any, Callable

from campuspulse.config import Settings
from campuspulse.models import AnalysisResult, Document


class CloudConfigurationError(RuntimeError):
    """A safe, actionable runtime configuration error."""


def runtime_settings() -> Settings:
    """Use runtime identity, never a local dotenv file or a Gemini API key."""
    settings = Settings(
        _env_file=None,
        google_cloud_project=os.environ.get("CAMPUSPULSE_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT"),
        google_genai_use_vertexai=True,
        gemini_api_key=None,
        allow_github_writes=False,
        github_mcp_url="https://api.githubcopilot.com/mcp/",
    )
    if not settings.google_cloud_project or not settings.github_configured:
        raise CloudConfigurationError("Runtime needs a Google Cloud project and GITHUB_TOKEN secret reference.")
    return settings


class GuardianCloudAgent:
    """JSON-compatible transport around the actual Gemini GuardianAgent.

    Dependencies may be injected explicitly in tests. No clients or secrets are
    captured in the source-deployed entrypoint object.
    """

    def __init__(self, *, settings_factory: Callable | None = None,
                 agent_factory: Callable | None = None, repository_validator: Callable | None = None):
        self._settings_factory = settings_factory
        self._agent_factory = agent_factory
        self._repository_validator = repository_validator

    async def async_query(self, document: dict[str, Any], repository_url: str) -> dict[str, Any]:
        """Analyze an already parsed assignment; no Issue/write operation exists."""
        try:
            parsed = Document.model_validate(document)
            if not parsed.filename or not parsed.sha256 or not any(page.text.strip() for page in parsed.pages):
                raise ValueError
            if len({page.number for page in parsed.pages}) != len(parsed.pages):
                raise ValueError
            # Bound the remote input independently of the upload/API surface.
            if sum(len(page.text.encode("utf-8")) for page in parsed.pages) > 1_000_000:
                raise ValueError
            validator = self._repository_validator
            if validator is None:
                from campuspulse.security import validate_repository_url
                validator = validate_repository_url
            repository = validator(repository_url)
        except (TypeError, ValueError):
            raise ValueError("Invalid document or GitHub repository URL.") from None

        settings = (self._settings_factory or runtime_settings)()
        # Even an injected/custom settings factory cannot enable cloud writes.
        settings = settings.model_copy(update={"allow_github_writes": False, "gemini_api_key": None,
                                               "google_genai_use_vertexai": True})
        factory = self._agent_factory
        if factory is None:
            from campuspulse.agent import GuardianAgent
            factory = GuardianAgent
        try:
            result = await factory(settings).analyze(parsed, repository)
            return AnalysisResult.model_validate(result).model_dump(mode="json")
        except Exception:
            # Provider errors can contain headers and document/repository data.
            raise RuntimeError("Cloud analysis failed. Check runtime configuration and restricted logs.") from None


def operation_schemas() -> list[dict[str, Any]]:
    """The entire remote API allowlist passed to the deployment service."""
    return [{
        "name": "async_query",
        "api_mode": "async",
        "description": "Read-only grounded assignment analysis using Gemini and GitHub MCP.",
        "parameters": {
            "type": "object",
            "properties": {"document": {"type": "object", "description": "Parsed CampusPulse Document JSON."},
                           "repository_url": {"type": "string"}},
            "required": ["document", "repository_url"],
            "additionalProperties": False,
        },
    }]


root_agent = GuardianCloudAgent()

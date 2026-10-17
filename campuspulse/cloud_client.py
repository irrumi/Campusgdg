"""Server-side Agent Engine adapter. No SDK, ADC or network access at import time."""
from __future__ import annotations

from typing import Any, Callable

from starlette.concurrency import run_in_threadpool

from campuspulse.config import Settings
from campuspulse.models import AnalysisResult, Document, RepositoryRef


class VertexConfigurationError(RuntimeError):
    """Cloud analysis needs a valid resource and the optional deployment SDK."""


class VertexInvocationError(RuntimeError):
    """A remote call failed or returned an invalid/offline response."""


def make_client(project: str, location: str) -> Any:
    try:
        import agentplatform
    except ImportError:
        raise VertexConfigurationError("Install the cloud extra to use Vertex AI analysis.") from None
    return agentplatform.Client(project=project, location=location)


class VertexGuardianClient:
    """Implement the same analyze contract as GuardianAgent, with no local fallback."""

    def __init__(self, settings: Settings, *, client_factory: Callable | None = None):
        self.settings = settings
        self.client_factory = client_factory or make_client

    async def analyze(self, document: Document, repository: RepositoryRef) -> AnalysisResult:
        if not self.settings.vertex_configured:
            raise VertexConfigurationError("Configure VERTEX_AGENT_RESOURCE with a reasoningEngines resource name.")
        name = self.settings.vertex_agent_resource
        project, location = name.split("/")[1], name.split("/")[3]

        def connect():
            client = self.client_factory(project, location)
            remote = client.runtimes.get(name=name)
            schemas = remote.operation_schemas()
            if (len(schemas) != 1 or schemas[0].get("name") != "async_query"
                    or schemas[0].get("api_mode") != "async"):
                raise VertexInvocationError("The cloud agent exposes an unexpected operation schema.")
            return remote

        try:
            # ADC discovery and synchronous SDK readback must not block the web event loop.
            remote = await run_in_threadpool(connect)
            response = await remote.async_query(
                document=document.model_dump(mode="json"), repository_url=repository.url,
            )
            result = AnalysisResult.model_validate(response)
            if not result.live:
                raise VertexInvocationError("The cloud agent returned an offline result.")
            return result
        except VertexConfigurationError:
            raise
        except Exception:
            # SDK/provider errors may contain headers and private input. Never expose them.
            raise VertexInvocationError(
                "Vertex AI analysis failed. Check ADC, resource access, runtime configuration and quotas."
            ) from None

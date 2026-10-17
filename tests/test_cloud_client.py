"""Offline Vertex integration: explicit fake remote transport; real HTTP and storage."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from campuspulse.api import create_app
from campuspulse.cloud import operation_schemas
from campuspulse.cloud_client import VertexConfigurationError, VertexGuardianClient, VertexInvocationError
from campuspulse.config import Settings
from campuspulse.models import AnalysisResult, AgentStep
from campuspulse.storage import Store

RESOURCE = "projects/123/locations/us-central1/reasoningEngines/456"


def settings(**kwargs):
    return Settings(_env_file=None, analysis_backend="vertex", vertex_agent_resource=RESOURCE,
                    gemini_api_key=None, github_token=None, **kwargs)


def remote_client(*, response=None, error=None, schemas=None):
    calls = []

    async def query(**payload):
        calls.append(payload)
        if error:
            raise error
        return response

    remote = SimpleNamespace(async_query=query, operation_schemas=lambda: operation_schemas() if schemas is None else schemas)

    def get(*, name):
        assert name == RESOURCE
        return remote

    def factory(project, location):
        assert (project, location) == ("123", "us-central1")
        return SimpleNamespace(runtimes=SimpleNamespace(get=get))

    return factory, calls


def test_http_upload_analyze_and_reload_use_remote_without_local_keys(monkeypatch):
    result = AnalysisResult(requirements=[], findings=[], evidence=[],
                            steps=[AgentStep(kind="tool", tool="repository", message="Read repository via MCP.")],
                            model="explicit-fake-remote", live=True)
    factory, calls = remote_client(response=result.model_dump(mode="json"))
    monkeypatch.setattr("campuspulse.cloud_client.make_client", factory)
    with _store_context() as store:
        with TestClient(create_app(settings=settings(), store=store), base_url="http://127.0.0.1") as client:
            health = client.get("/api/health").json()
            assert health["analysis_configured"] and health["vertex_configured"]
            assert not health["gemini_configured"] and not health["github_configured"]
            uploaded = client.post("/api/assignments", data={"repository_url": "https://github.com/acme/course"},
                                   files={"file": ("assignment.txt", b"Provide automated tests.")})
            assert uploaded.status_code == 200
            assert calls == []  # Upload/health do not contact Vertex.
            payload = uploaded.json()
            route = f"/api/assignments/{payload['assignment']['id']}"
            headers = {"X-Assignment-Token": payload["token"]}
            assert client.post(route + "/analyze").status_code == 404
            analyzed = client.post(route + "/analyze", headers=headers)
            assert analyzed.status_code == 200, analyzed.text
            assert analyzed.json()["analysis"] == result.model_dump(mode="json")
            assert len(calls) == 1
            assert calls[0]["document"] == payload["assignment"]["document"]
            assert calls[0]["repository_url"] == "https://github.com/acme/course"
            assert client.get(route, headers=headers).json() == analyzed.json()
            events = client.get(route + "/events", headers=headers).json()
            assert events[-1]["kind"] == "analysis_completed"


@contextmanager
def _store_context():
    store = Store(":memory:")
    try:
        yield store
    finally:
        store.close()


@pytest.mark.parametrize("resource", [None, "", "https://evil.example", RESOURCE + "/extra"])
def test_missing_or_invalid_resource_fails_without_local_fallback(resource):
    config = settings()
    config.vertex_agent_resource = resource
    assert not config.analysis_configured
    with _store_context() as store, TestClient(create_app(settings=config, store=store),
                                             base_url="http://127.0.0.1") as client:
        payload = client.post("/api/assignments", data={"repository_url": "https://github.com/acme/course"},
                              files={"file": ("a.txt", b"Provide tests.")}).json()
        response = client.post(f"/api/assignments/{payload['assignment']['id']}/analyze",
                               headers={"X-Assignment-Token": payload["token"]})
        assert response.status_code == 503
        assert "VERTEX_AGENT_RESOURCE" in response.json()["detail"]


@pytest.mark.parametrize("response,error,schemas", [
    ({"invalid": "response"}, None, None),
    (AnalysisResult(requirements=[], findings=[], evidence=[], steps=[], model="fake", live=False).model_dump(), None, None),
    (None, RuntimeError("Authorization: Bearer PRIVATE"), None),
    (None, None, []),
    (None, None, [{"name": "create_issue", "api_mode": "async"}]),
])
async def test_rejects_invalid_offline_and_failed_remote_responses(response, error, schemas):
    from campuspulse.models import Document, DocumentPage, RepositoryRef
    factory, _ = remote_client(response=response, error=error, schemas=schemas)
    adapter = VertexGuardianClient(settings(), client_factory=factory)
    with pytest.raises(VertexInvocationError) as caught:
        await adapter.analyze(Document(filename="a.txt", sha256="a" * 64,
                                       pages=[DocumentPage(number=1, text="Provide tests.")]),
                              RepositoryRef(owner="acme", name="course", url="https://github.com/acme/course"))
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("configuration_error,status", [(False, 502), (True, 503)])
def test_http_remote_failures_preserve_upload_and_redact_errors(monkeypatch, configuration_error, status):
    def fail_client(*_):
        error = VertexConfigurationError if configuration_error else RuntimeError
        raise error("Authorization: Bearer PRIVATE")

    monkeypatch.setattr("campuspulse.cloud_client.make_client", fail_client)
    with _store_context() as store, TestClient(create_app(settings=settings(), store=store),
                                             base_url="http://127.0.0.1") as client:
        payload = client.post("/api/assignments", data={"repository_url": "https://github.com/acme/course"},
                              files={"file": ("a.txt", b"Provide tests.")}).json()
        route = f"/api/assignments/{payload['assignment']['id']}"
        headers = {"X-Assignment-Token": payload["token"]}
        response = client.post(route + "/analyze", headers=headers)
        assert response.status_code == status
        assert "PRIVATE" not in response.text
        assert client.get(route, headers=headers).json()["analysis"] is None
        events = client.get(route + "/events", headers=headers)
        assert "PRIVATE" not in events.text
        assert events.json()[-1]["kind"] == "analysis_failed"


def test_http_cloud_timeout_preserves_assignment(monkeypatch):
    import asyncio

    async def slow_query(**_):
        await asyncio.sleep(1)

    remote = SimpleNamespace(async_query=slow_query, operation_schemas=operation_schemas)
    monkeypatch.setattr("campuspulse.cloud_client.make_client", lambda *_: SimpleNamespace(
        runtimes=SimpleNamespace(get=lambda **_: remote)))
    with _store_context() as store, TestClient(
        create_app(settings=settings(request_timeout_seconds=0.02), store=store),
        base_url="http://127.0.0.1",
    ) as client:
        payload = client.post("/api/assignments", data={"repository_url": "https://github.com/acme/course"},
                              files={"file": ("a.txt", b"Provide tests.")}).json()
        route = f"/api/assignments/{payload['assignment']['id']}"
        headers = {"X-Assignment-Token": payload["token"]}
        assert client.post(route + "/analyze", headers=headers).status_code == 502
        assert client.get(route, headers=headers).json()["analysis"] is None

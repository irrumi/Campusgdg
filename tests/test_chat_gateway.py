"""Offline regressions for the hosted CampusAgent gateway."""
import hashlib
import importlib
from collections import defaultdict, deque
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("CAMPUS_API_KEY_SHA256", hashlib.sha256(b"test-only-api-key").hexdigest())
    monkeypatch.setenv("AGENT_ENGINE_RESOURCE", "projects/test/locations/us-central1/reasoningEngines/123")
    server = importlib.import_module("agent.server")
    monkeypatch.setattr(server, "KEY_HASH", hashlib.sha256(b"test-only-api-key").hexdigest())
    monkeypatch.setattr(server, "_hits", defaultdict(deque))
    with TestClient(server.app) as client:
        yield server, client


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer incorrect"}])
def test_unauthorized_request_never_reaches_vertex(gateway, monkeypatch, headers):
    server, client = gateway

    def unexpected_call(resource):
        pytest.fail("Unauthenticated request reached Vertex")

    monkeypatch.setattr(server, "get_remote_agent", unexpected_call)
    assert client.post("/api/v1/chat", headers=headers, json={"message": "hello"}).status_code == 401


def test_health_reports_cloud_failure_without_provider_details(gateway, monkeypatch):
    server, client = gateway

    def unavailable(resource):
        raise RuntimeError("private provider error")

    monkeypatch.setattr(server, "get_remote_agent", unavailable)
    response = client.get("/api/v1/chat")
    assert response.status_code == 503
    assert "private provider error" not in response.text


def test_authorized_request_uses_remote_agent_and_bounds_history(gateway, monkeypatch):
    server, client = gateway
    received = []

    def query(**payload):
        received.append(payload)
        return {"output": "test response", "steps": []}

    monkeypatch.setattr(server, "get_remote_agent", lambda resource: SimpleNamespace(query=query))
    history = [{"role": "user", "content": str(index)} for index in range(25)]
    response = client.post("/api/v1/chat", headers={"Authorization": "Bearer test-only-api-key"},
                           json={"message": "hello", "history": history, "student_context": {"tasks": []}})
    assert response.status_code == 200
    assert response.json() == {"output": "test response", "steps": []}
    assert received == [{"message": "hello", "history": history[-20:], "student_context": {"tasks": []}}]


def test_health_rechecks_provider_after_success(gateway, monkeypatch):
    import sys

    server, client = gateway
    available = True
    calls = []

    def get(*, name):
        calls.append(name)
        if not available:
            raise RuntimeError("private revoked access details")
        return SimpleNamespace()

    def make_client(*, project, location):
        assert project == "test" and location == "us-central1"
        return SimpleNamespace(runtimes=SimpleNamespace(get=get))

    monkeypatch.setitem(sys.modules, "agentplatform", SimpleNamespace(Client=make_client))
    assert client.get("/api/v1/chat").status_code == 200
    available = False
    response = client.get("/api/v1/chat")
    assert response.status_code == 503
    assert "private revoked" not in response.text
    assert len(calls) == 2


def test_gateway_forwards_full_files_and_deadline_mode(gateway, monkeypatch):
    import base64

    server, client = gateway
    received = []
    expected = {"output": "Explicit test result", "steps": [{"type": "tool_call", "tool": "file"}],
                "deadline": {"action": "analyze"}}

    def query(**payload):
        received.append(payload)
        return expected

    monkeypatch.setattr(server, "get_remote_agent", lambda resource: SimpleNamespace(query=query))
    files = [{"name": "assignment.txt", "base64": base64.b64encode(b"Actual contents").decode()}]
    result = client.post("/api/v1/chat", headers={"Authorization": "Bearer test-only-api-key"},
        json={"message": "review", "files": files, "mode": "deadline",
              "deadline_request": {"action": "analyze", "repository_url": "https://github.com/example/project"}})
    assert result.status_code == 200
    assert result.json() == expected
    assert received[0]["files"] == files
    assert received[0]["mode"] == "deadline"
    assert received[0]["deadline_request"]["action"] == "analyze"


def test_old_runtime_cannot_silently_pretend_to_support_deadline(gateway, monkeypatch):
    server, client = gateway
    monkeypatch.setattr(server, "get_remote_agent", lambda resource: SimpleNamespace(
        query=lambda **kwargs: {"output": "Old generic answer", "steps": []}))
    result = client.post("/api/v1/chat", headers={"Authorization": "Bearer test-only-api-key"},
                         json={"message": "review", "mode": "deadline", "deadline_request": {"action": "analyze"}})
    assert result.status_code == 502
    assert "Update the Agent Engine" in result.json()["detail"]


def test_upstream_error_never_exposes_provider_headers(gateway, monkeypatch):
    server, client = gateway

    def query(**kwargs):
        raise RuntimeError("Authorization: Bearer private-provider-token")

    monkeypatch.setattr(server, "get_remote_agent", lambda resource: SimpleNamespace(query=query))
    result = client.post("/api/v1/chat", headers={"Authorization": "Bearer test-only-api-key"}, json={"message": "review"})
    assert result.status_code == 502
    assert "private-provider" not in result.text


def test_unconfigured_gateway_rejects_post_before_cloud_access(gateway, monkeypatch):
    server, client = gateway
    monkeypatch.setattr(server, "KEY_HASH", "")

    def unexpected(resource):
        pytest.fail("Unconfigured gateway reached Vertex")

    monkeypatch.setattr(server, "get_remote_agent", unexpected)
    response = client.post("/api/v1/chat", json={"message": "hello"})
    assert response.status_code == 503


def test_actual_sdk_binds_query_and_keeps_deadline_payload(gateway, monkeypatch):
    """Real SDK runtime binding with explicit fake get/query RPCs, never ADC."""
    import agentplatform
    from agentplatform import types
    from agentplatform._genai.runtimes import Runtimes

    server, client = gateway
    resource = "projects/test/locations/us-central1/reasoningEngines/123"
    runtime_api = Runtimes(api_client_=SimpleNamespace(project="test", location="us-central1"))
    monkeypatch.setattr(runtime_api, "_get", lambda **_: types.ReasoningEngine(
        name=resource, spec={"class_methods": [{"name": "query", "api_mode": ""}]}))
    captured = []

    def fake_query(*, name, config):
        assert name == resource
        captured.append(config)
        return SimpleNamespace(output={"output": "Explicit test result", "steps": [], "deadline": {"action": "analyze"}})

    monkeypatch.setattr(runtime_api, "_query", fake_query)
    monkeypatch.setattr(agentplatform, "Client", lambda **_: SimpleNamespace(runtimes=runtime_api))
    response = client.post("/api/v1/chat", headers={"Authorization": "Bearer test-only-api-key"},
        json={"message": "review", "mode": "deadline", "deadline_request": {"action": "analyze"}})
    assert response.status_code == 200
    assert captured[0]["class_method"] == "query"
    assert captured[0]["input"]["mode"] == "deadline"
    assert captured[0]["input"]["deadline_request"] == {"action": "analyze"}

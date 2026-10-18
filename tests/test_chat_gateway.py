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

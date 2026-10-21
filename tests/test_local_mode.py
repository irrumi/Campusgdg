"""Explicit offline checks for local execution; no provider calls or credentials."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google import genai
from google.genai import types

from agent.agent import CampusAgent
from scripts.run_local import child_environments, gateway_environment


def test_gateway_launch_uses_existing_application_key_without_provider_keys():
    source = {"AGENT_API_KEY": "existing-test-application-key", "CAMPUS_LOCAL_ONLY": "1",
              "GEMINI_API_KEY": "test-provider-key", "GOOGLE_API_KEY": "test-other-provider",
              "GITHUB_TOKEN": "test-github-token", "NO_PROXY": "internal.example"}
    environment = gateway_environment(source, "https://gateway.example/api/v1/chat", direct=True)
    assert environment["AGENT_API_KEY"] == source["AGENT_API_KEY"]
    assert environment["AGENT_API_URL"] == "https://gateway.example/api/v1/chat"
    assert environment["CAMPUS_SKIP_LOCAL_CONFIG"] == "1"
    assert "gateway.example" in environment["NO_PROXY"] and "internal.example" in environment["NO_PROXY"]
    assert all(name not in environment for name in ("CAMPUS_LOCAL_ONLY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GITHUB_TOKEN"))
    assert source["CAMPUS_LOCAL_ONLY"] == "1"


@pytest.mark.parametrize("endpoint", ["http://gateway.example", "https://key:secret@gateway.example", "invalid"])
def test_gateway_launch_rejects_insecure_or_credential_bearing_urls(endpoint):
    with pytest.raises(ValueError):
        gateway_environment({}, endpoint)


def test_default_launcher_never_prompts_for_provider_credentials(monkeypatch, tmp_path):
    import scripts.run_local as launcher

    (tmp_path / "agent.connection.json").write_text('{"api_url":"https://gateway.example/api/v1/chat"}', encoding="utf-8")
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.delenv("AGENT_API_URL", raising=False)
    monkeypatch.setattr(launcher, "check_port", lambda port: None)
    monkeypatch.setattr(launcher.getpass, "getpass", lambda *args: pytest.fail("Default launcher requested provider credentials"))
    child = Mock()
    child.wait.return_value = 0
    child.poll.return_value = 0
    start = Mock(return_value=child)
    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    assert launcher.main(["--ui-port", "8502", "--direct"]) == 0
    assert start.call_count == 1
    assert "streamlit" in start.call_args.args[0]
    environment = start.call_args.kwargs["env"]
    assert environment["AGENT_API_URL"] == "https://gateway.example/api/v1/chat"
    assert "CAMPUS_LOCAL_ONLY" not in environment


def test_launcher_disconnects_foreign_cloud_and_isolates_provider_credentials():
    source = {"AGENT_ENGINE_RESOURCE": "foreign-resource", "VERTEX_AGENT_RESOURCE": "foreign-guardian",
              "CAMPUS_API_KEY_SHA256": "old-hash", "CAMPUS_API_KEY": "old-key", "AGENT_API_KEY": "old-key",
              "GEMINI_API_KEY": "old-gemini", "GOOGLE_API_KEY": "old-google", "GITHUB_TOKEN": "old-github"}
    gateway, interface = child_environments(source, "test-gemini-only", "test-github-only", "test-local-key-at-least-16",
                                           gateway_port=8013, model="explicit-test-model")
    assert source["AGENT_ENGINE_RESOURCE"] == "foreign-resource"
    for env in (gateway, interface):
        assert "AGENT_ENGINE_RESOURCE" not in env and "VERTEX_AGENT_RESOURCE" not in env
        assert env["CAMPUS_LLM_BACKEND"] == "gemini"
        assert env["CAMPUS_LOCAL_ONLY"] == "1"
        assert "CAMPUS_API_KEY" not in env
    assert gateway["GEMINI_API_KEY"] == "test-gemini-only"
    assert gateway["GITHUB_TOKEN"] == "test-github-only"
    assert "GEMINI_API_KEY" not in interface and "GITHUB_TOKEN" not in interface and "GOOGLE_API_KEY" not in interface
    assert interface["AGENT_API_URL"] == "http://127.0.0.1:8013/api/v1/chat"
    assert interface["AGENT_API_KEY"] == "test-local-key-at-least-16"


def test_gemini_backend_constructs_api_key_client_without_vertex_or_adc(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "explicit-test-gemini-key")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GEMINI_MODEL", "explicit-test-model")
    client = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(genai, "Client", client)
    monkeypatch.setattr(types.FunctionDeclaration, "from_callable", lambda **kwargs:
                        types.FunctionDeclaration(name=kwargs["callable"].__name__))
    agent = CampusAgent(backend="gemini")
    agent.set_up()
    client.assert_called_once_with(vertexai=False, api_key="explicit-test-gemini-key")
    assert agent.model == "explicit-test-model"


def test_deadline_mode_receives_real_gemini_api_settings(monkeypatch):
    import agent.deadline

    monkeypatch.setenv("GEMINI_API_KEY", "explicit-test-gemini-key")
    monkeypatch.setenv("GITHUB_TOKEN", "explicit-test-github-token")
    received = []

    def explicit_fake_boundary(payload, files, settings):
        received.append(settings)
        return {"output": "Explicit offline test boundary", "steps": []}

    monkeypatch.setattr(agent.deadline, "run_deadline", explicit_fake_boundary)
    CampusAgent(backend="gemini", model="explicit-test-model").query("review", mode="deadline", deadline_request={})
    settings = received[0]
    assert settings.gemini_configured and settings.github_configured
    assert not settings.google_genai_use_vertexai and not settings.allow_github_writes
    assert settings.gemini_model == "explicit-test-model"
    assert settings.gemini_api_key.get_secret_value() == "explicit-test-gemini-key"


def test_local_mode_missing_key_never_attempts_adc(monkeypatch):
    from campuspulse.agent import AgentConfigurationError

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    client = Mock()
    monkeypatch.setattr(genai, "Client", client)
    with pytest.raises(AgentConfigurationError):
        CampusAgent(backend="gemini").set_up()
    client.assert_not_called()


def test_explicit_vertex_backend_remains_vertex(monkeypatch):
    client = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(genai, "Client", client)
    monkeypatch.setattr(types.FunctionDeclaration, "from_callable", lambda **kwargs:
                        types.FunctionDeclaration(name=kwargs["callable"].__name__))
    CampusAgent(project="test-project", location="global", backend="vertex").set_up()
    client.assert_called_once_with(vertexai=True, project="test-project", location="global")


def test_invalid_backend_is_rejected():
    with pytest.raises(ValueError):
        CampusAgent(backend="unknown")

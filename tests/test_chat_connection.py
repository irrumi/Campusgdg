"""Offline checks of the Streamlit configuration without starting the UI."""
import ast
import json
import os
from pathlib import Path

import pytest


def load_configuration(tmp_path, monkeypatch, portable=None, local=None, endpoint=None):
    monkeypatch.delenv("CAMPUS_SKIP_LOCAL_CONFIG", raising=False)
    monkeypatch.delenv("CAMPUS_LOCAL_ONLY", raising=False)
    for name, data in (("agent.connection.json", portable), ("vertex.config.json", local)):
        if data is not None:
            (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.delenv("AGENT_API_URL", raising=False)
    if endpoint is not None:
        monkeypatch.setenv("AGENT_API_URL", endpoint)
    source = Path(__file__).resolve().parents[1] / "campus_chat_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"VERTEX_CONNECTION", "VERTEX_RESOURCE", "DEFAULT_AGENT_URL"}
    nodes = [node for node in tree.body if
             isinstance(node, ast.FunctionDef) and node.name == "load_vertex_connection" or
             isinstance(node, ast.Assign) and any(
                 isinstance(target, ast.Name) and target.id in names for target in node.targets)]
    namespace = {"Path": Path, "json": json, "os": os, "__file__": str(tmp_path / "campus_chat_app.py")}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


def test_portable_connection_used_by_default(tmp_path, monkeypatch):
    portable = {"api_url": "https://portable.example/api/v1/chat", "project": "portable-project"}
    config = load_configuration(tmp_path, monkeypatch, portable=portable)
    assert config["DEFAULT_AGENT_URL"] == portable["api_url"]
    assert config["VERTEX_CONNECTION"] == portable


def test_local_connection_overrides_portable_defaults(tmp_path, monkeypatch):
    portable = {"api_url": "https://portable.example/api/v1/chat", "project": "portable-project"}
    local = {"api_url": "https://local.example/api/v1/chat", "project": "local-project",
             "agent_engine_resource": "projects/local/locations/test/reasoningEngines/123"}
    config = load_configuration(tmp_path, monkeypatch, portable=portable, local=local)
    assert config["DEFAULT_AGENT_URL"] == local["api_url"]
    assert config["VERTEX_CONNECTION"] == local


@pytest.mark.parametrize("local", [None, {"api_url": "https://local.example/api/v1/chat"}])
def test_explicit_environment_endpoint_has_priority(tmp_path, monkeypatch, local):
    endpoint = "https://override.example/api/v1/chat"
    config = load_configuration(tmp_path, monkeypatch,
                                portable={"api_url": "https://portable.example/api/v1/chat"},
                                local=local, endpoint=endpoint)
    assert config["DEFAULT_AGENT_URL"] == endpoint


def test_unconfigured_connection_stays_empty(tmp_path, monkeypatch):
    assert load_configuration(tmp_path, monkeypatch)["DEFAULT_AGENT_URL"] == ""


def test_different_local_agent_does_not_inherit_portable_gateway(tmp_path, monkeypatch):
    config = load_configuration(tmp_path, monkeypatch,
        portable={"api_url": "https://portable.example/api/v1/chat", "agent_engine_resource": "portable-agent"},
        local={"agent_engine_resource": "my-agent", "project": "my-project"})
    assert config["VERTEX_RESOURCE"] == "my-agent"
    assert config["DEFAULT_AGENT_URL"] == "http://127.0.0.1:8000/api/v1/chat"


def test_local_only_never_reads_cloud_connection_files(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "campus_chat_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "load_vertex_connection")
    monkeypatch.setenv("CAMPUS_LOCAL_ONLY", "1")
    namespace = {"Path": Path, "json": json, "os": os, "__file__": str(tmp_path / "campus_chat_app.py")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)

    def reject_file_read(*args, **kwargs):
        pytest.fail("Local-only startup read a cloud connection file")

    monkeypatch.setattr(Path, "read_text", reject_file_read)
    assert namespace["load_vertex_connection"]() == {}

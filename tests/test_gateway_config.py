"""Offline regression checks for gateway configuration before agent construction."""

import hashlib
import json
import os
from pathlib import Path
import runpy

import pytest


@pytest.mark.parametrize("existing_environment", [False, True])
def test_gateway_loads_configuration_before_agent_construction(tmp_path, monkeypatch, existing_environment):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    # Copy only the gateway into an isolated root, never reading real credentials.
    source = Path(__file__).resolve().parents[1] / "agent" / "server.py"
    gateway = tmp_path / "agent" / "server.py"
    gateway.parent.mkdir()
    gateway.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    key = "offline-test-key-only"
    (tmp_path / ".env").write_text(
        f"CAMPUS_API_KEY={key}\nGOOGLE_CLOUD_LOCATION=test-region\nGOOGLE_CLOUD_MODEL=test-model\n",
        encoding="utf-8",
    )
    (tmp_path / "vertex.config.json").write_text(
        json.dumps({"project": "file-project", "agent_engine_resource": "file-resource"}),
        encoding="utf-8",
    )
    for name in (
        "CAMPUS_API_KEY", "CAMPUS_API_KEY_SHA256", "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_MODEL", "AGENT_ENGINE_RESOURCE", "RATE_LIMIT_PER_MIN",
    ):
        monkeypatch.delenv(name, raising=False)
    if existing_environment:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "existing-project")
        monkeypatch.setenv("AGENT_ENGINE_RESOURCE", "existing-resource")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "existing-region")
        monkeypatch.setenv("GOOGLE_CLOUD_MODEL", "existing-model")

    namespace = runpy.run_path(str(gateway))
    agent = namespace["CampusAgent"]()

    prefix = "existing" if existing_environment else "file"
    assert agent.project == f"{prefix}-project"
    assert namespace["os"].environ["AGENT_ENGINE_RESOURCE"] == f"{prefix}-resource"
    assert agent.location == ("existing-region" if existing_environment else "test-region")
    assert agent.model == ("existing-model" if existing_environment else "test-model")
    assert namespace["KEY_HASH"] == hashlib.sha256(key.encode()).hexdigest()

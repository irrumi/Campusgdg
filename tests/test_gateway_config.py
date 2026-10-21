"""Offline regression checks for gateway configuration before agent construction."""

import hashlib
import json
import os
from pathlib import Path
import runpy

import pytest


@pytest.mark.parametrize("existing_environment", [False, True])
def test_gateway_uses_only_process_configuration_without_reading_local_files(tmp_path, monkeypatch, existing_environment):
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
        monkeypatch.setenv("CAMPUS_API_KEY", key)

    read_text = Path.read_text

    def guarded_read(path, *args, **kwargs):
        if path.name in {".env", "vertex.config.json"}:
            pytest.fail("Importing the gateway must not read credential/configuration files")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)

    namespace = runpy.run_path(str(gateway))
    agent = namespace["CampusAgent"]()

    assert agent.project == ("existing-project" if existing_environment else None)
    assert namespace["os"].environ.get("AGENT_ENGINE_RESOURCE") == ("existing-resource" if existing_environment else None)
    assert agent.location == ("existing-region" if existing_environment else "us-central1")
    assert agent.model == ("existing-model" if existing_environment else "gemini-3.1-flash-lite")
    assert namespace["KEY_HASH"] == (hashlib.sha256(key.encode()).hexdigest() if existing_environment else "")

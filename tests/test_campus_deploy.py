"""Packaging/SDK regressions; all cloud RPCs are explicit fakes."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import deploy


def test_deployment_config_accepts_actual_sdk_schema_and_only_secret_references():
    from agentplatform import types

    config = deploy.deployment_config("gs://explicit-test-bucket", "github-readonly", "2")
    types.AgentRuntimeConfig.model_validate(config)
    assert config["env_vars"] == {"GITHUB_TOKEN": {"secret": "github-readonly", "version": "2"}}
    assert set(config["extra_packages"]) == {"agent", "campuspulse"}


@pytest.mark.parametrize("secret,version", [("secret/value", "1"), ("token", "latest"), ("token", "0")])
def test_invalid_secret_reference_rejected_before_client(secret, version):
    with pytest.raises(ValueError):
        deploy.deployment_config("gs://explicit-test-bucket", secret, version)


def test_dry_run_never_constructs_cloud_client_or_reads_connection_files(monkeypatch, capsys):
    import agentplatform

    monkeypatch.setattr(agentplatform, "Client", lambda **_: pytest.fail("Dry run contacted cloud"))
    original = Path.read_text

    def guarded_read(path, *args, **kwargs):
        if path.name in {"vertex.config.json", ".env"}:
            pytest.fail("Dry run read local credentials/configuration")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert deploy.main(["--project", "explicit-test-project", "--bucket", "gs://explicit-test-bucket", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["cloud_contacted"] is False


def test_deploy_uses_sdk_runtime_api_and_reads_returned_resource(monkeypatch, tmp_path):
    import agentplatform

    captured = {}
    resource = "projects/test/locations/us-central1/reasoningEngines/123"

    class FakeRuntimes:
        def create(self, *, agent, config):
            captured.update(agent=agent, config=config)
            return SimpleNamespace(api_resource=SimpleNamespace(name=resource))

    monkeypatch.setattr(agentplatform, "Client", lambda **_: SimpleNamespace(runtimes=FakeRuntimes()))
    monkeypatch.setattr(deploy, "__file__", str(tmp_path / "deploy.py"))
    assert deploy.main(["--project", "explicit-test-project", "--bucket", "gs://explicit-test-bucket"]) == 0
    state = json.loads((tmp_path / "vertex.config.json").read_text())
    assert state["agent_engine_resource"] == resource
    assert captured["agent"].project == "explicit-test-project"
    assert "google-cloud-agentplatform[agent_engines]==2.5.0" in captured["config"]["requirements"]

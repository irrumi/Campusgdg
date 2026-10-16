"""Offline tests: injected agents/SDK only; no cloud credentials or writes."""
from __future__ import annotations

import base64
from contextlib import chdir
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from campuspulse.cloud import CloudConfigurationError, GuardianCloudAgent, operation_schemas, runtime_settings
from campuspulse.config import Settings
from campuspulse.models import AnalysisResult, Document, DocumentPage, RepositoryRef
from scripts import deploy, invoke


@pytest.fixture
def document():
    return Document(filename="assignment.txt", pages=[DocumentPage(number=1, text="Include a README.")],
                    sha256="a" * 64)


@pytest.fixture
def config():
    return deploy.build_config(project="campusgdg-511817", location="us-central1",
                               service_account="campuspulse-runtime@campusgdg-511817.iam.gserviceaccount.com",
                               secret_id="campuspulse-github", secret_version="1")


@pytest.fixture
def source_root(tmp_path):
    # Explicit test package, never a replacement for real production source.
    root = tmp_path / "source"
    for name in deploy.SOURCE_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# explicit test source\n", encoding="utf-8")
    (root / ".env").write_text("GITHUB_TOKEN=test-secret-never-package", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "private.db").write_bytes(b"private")
    return root


def settings():
    return Settings(_env_file=None, google_cloud_project="campusgdg-511817", github_token=SecretStr("test-only"),
                    gemini_api_key=SecretStr("test-api-key"), allow_github_writes=True)


def repository_validator(url):
    assert url == "https://github.com/example/project"
    return RepositoryRef(owner="example", name="project", url=url)


def result(*, live=True):
    return AnalysisResult(requirements=[], findings=[], evidence=[], steps=[], model="gemini-3.8-flash", live=live)


@pytest.mark.asyncio
async def test_wrapper_calls_agent_with_readonly_identity_settings(document):
    calls = []

    class FakeAgent:
        def __init__(self, runtime):
            assert runtime.allow_github_writes is False
            assert runtime.google_genai_use_vertexai is True
            assert runtime.gemini_api_key is None

        async def analyze(self, parsed, repository):
            calls.append((parsed, repository))
            return result(live=False)

    wrapper = GuardianCloudAgent(settings_factory=settings, agent_factory=FakeAgent,
                                 repository_validator=repository_validator)
    response = await wrapper.async_query(document.model_dump(), "https://github.com/example/project")
    assert calls[0][0] == document
    assert calls[0][1].owner == "example"
    assert response["live"] is False  # The wrapper must not relabel injected offline results as live.
    assert not hasattr(wrapper, "create_issue")
    assert [item["name"] for item in operation_schemas()] == ["async_query"]


@pytest.mark.asyncio
async def test_wrapper_does_not_leak_provider_errors(document):
    class FailingAgent:
        def __init__(self, _settings):
            pass

        async def analyze(self, *_):
            raise RuntimeError("Authorization: Bearer test-secret")

    wrapper = GuardianCloudAgent(settings_factory=settings, agent_factory=FailingAgent,
                                 repository_validator=repository_validator)
    with pytest.raises(RuntimeError, match="Cloud analysis failed") as error:
        await wrapper.async_query(document.model_dump(), "https://github.com/example/project")
    assert "test-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_invalid_document_rejected_before_agent(document):
    wrapper = GuardianCloudAgent(settings_factory=lambda: pytest.fail("must not load credentials"))
    payload = document.model_dump()
    payload["pages"].append(payload["pages"][0])
    with pytest.raises(ValueError, match="Invalid document"):
        await wrapper.async_query(payload, "https://github.com/example/project")


@pytest.mark.asyncio
async def test_wrapper_preserves_blank_pdf_page_numbers(document):
    class FakeAgent:
        def __init__(self, _settings):
            pass

        async def analyze(self, parsed, _repository):
            assert parsed.pages[0].text == ""
            assert parsed.pages[1].number == 2
            return result(live=False)

    document.pages = [DocumentPage(number=1, text=""), DocumentPage(number=2, text="Include a README.")]
    wrapper = GuardianCloudAgent(settings_factory=settings, agent_factory=FakeAgent,
                                 repository_validator=repository_validator)
    response = await wrapper.async_query(document.model_dump(), "https://github.com/example/project")
    assert response["live"] is False


def test_runtime_settings_ignores_dotenv_and_api_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GITHUB_TOKEN=must-not-be-loaded\n", encoding="utf-8")
    monkeypatch.setenv("CAMPUSPULSE_PROJECT", "campusgdg-511817")
    monkeypatch.setenv("GEMINI_API_KEY", "must-ignore")
    monkeypatch.setenv("ALLOW_GITHUB_WRITES", "true")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(CloudConfigurationError):
        runtime_settings()
    monkeypatch.setenv("GITHUB_TOKEN", "explicit-test-token")
    runtime = runtime_settings()
    assert runtime.gemini_api_key is None
    assert runtime.google_genai_use_vertexai
    assert not runtime.allow_github_writes
    assert runtime.gemini_location == "global"


def test_config_contains_secret_reference_and_no_reserved_environment(config):
    assert config["env_vars"]["GITHUB_TOKEN"] == {"secret": "campuspulse-github", "version": "1"}
    assert not ({"GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS",
                 "GEMINI_API_KEY"} & config["env_vars"].keys())
    assert config["entrypoint_module"] == "campuspulse.cloud"
    assert config["requirements_file"] in config["source_packages"]
    assert "requirements" not in config and "staging_bucket" not in config


def test_actual_sdk_schema_without_credentials(config):
    pytest.importorskip("agentplatform")
    deploy.check_sdk(config)


def test_actual_sdk_create_path_with_fake_transport(config, source_root, monkeypatch):
    """Exercise real create preprocessing/method binding; all RPCs are explicit fakes."""
    pytest.importorskip("agentplatform")
    from agentplatform import types
    from agentplatform._genai.runtimes import Runtimes

    resource_name = "projects/123/locations/us-central1/reasoningEngines/456"
    operation = types.RuntimeOperation(
        name=resource_name + "/operations/789", done=True,
        response=types.ReasoningEngine(name=resource_name, spec={"class_methods": operation_schemas()}))
    captured = {}
    runtime_api = Runtimes(api_client_=SimpleNamespace(project="campusgdg-511817", location="us-central1"))

    def fake_create(*, config):
        captured["config"] = config
        return operation

    monkeypatch.setattr(runtime_api, "_create", fake_create)
    monkeypatch.setattr(runtime_api, "_get_agent_operation", lambda **_: operation)
    with chdir(source_root):
        remote = runtime_api.create(config=config)
    assert remote.api_resource.name == resource_name
    assert callable(remote.async_query)
    assert deploy.verify_operations(remote)[0]["api_mode"] == "async"
    encoded = json.dumps(captured["config"])
    assert "campuspulse.cloud" in encoded
    assert "campuspulse-github" in encoded
    assert "secret_ref" in encoded
    assert "pickle_object_gcs_uri" not in encoded


def test_source_manifest_ignores_secrets_and_fails_missing_source(source_root):
    manifest = deploy.source_manifest(source_root)
    assert {item["path"] for item in manifest} == set(deploy.SOURCE_FILES)
    (source_root / "campuspulse" / "agent.py").unlink()
    with pytest.raises(ValueError, match="agent.py"):
        deploy.source_manifest(source_root)


def test_deployment_packages_relative_source_with_actual_sdk(config, source_root, tmp_path):
    pytest.importorskip("agentplatform")
    from agentplatform._genai import _runtimes_utils
    from agentplatform import types

    name = "projects/123456/locations/us-central1/reasoningEngines/789"
    remote = types.Runtime(api_resource=types.ReasoningEngine(
        name=name, spec={"class_methods": operation_schemas()}))
    captured = {}

    class FakeRuntimes:
        def create(self, *, config):
            types.AgentRuntimeConfig.model_validate(config)
            archive = _runtimes_utils._create_base64_encoded_tarball(source_packages=config["source_packages"])
            with tarfile.open(fileobj=io.BytesIO(base64.b64decode(archive)), mode="r:gz") as package:
                captured["files"] = package.getnames()
                captured["requirements"] = package.extractfile("requirements-cloud.txt").read()
            assert config["env_vars"]["GITHUB_TOKEN"]["secret"] == "campuspulse-github"
            return remote

        def get(self, *, name):
            assert name == remote.api_resource.name
            return remote

    cwd = Path.cwd()
    state_path = tmp_path / "deployment.json"
    state = deploy.deploy(config, project="campusgdg-511817", location="us-central1", state_path=state_path,
                          root=source_root, client_factory=lambda *_: SimpleNamespace(runtimes=FakeRuntimes()))
    assert Path.cwd() == cwd
    assert set(captured["files"]) == set(deploy.SOURCE_FILES)
    assert state["resource_name"] == name and state["invocation_verified"] is False
    assert json.loads(state_path.read_text())["status"] == "deployed"
    with pytest.raises(FileExistsError):
        deploy.deploy(config, project="campusgdg-511817", location="us-central1", state_path=state_path,
                      root=source_root, client_factory=lambda *_: pytest.fail("no retry"))


def test_failed_deploy_is_recorded_without_provider_secret(config, source_root, tmp_path):
    def failure(*_):
        raise RuntimeError("test-secret")

    state_path = tmp_path / "failed.json"
    with pytest.raises(RuntimeError, match="needs inspection"):
        deploy.deploy(config, project="campusgdg-511817", location="us-central1", state_path=state_path,
                      root=source_root, client_factory=failure)
    text = state_path.read_text()
    assert "needs_inspection" in text and "test-secret" not in text


def test_dry_run_never_constructs_client(config, source_root, monkeypatch, capsys):
    monkeypatch.setattr(deploy, "source_manifest", lambda: [])
    monkeypatch.setattr(deploy, "make_client", lambda *_: pytest.fail("no cloud in dry-run"))
    assert deploy.main(["--project", "campusgdg-511817", "--service-account", config["service_account"],
                        "--github-secret", "campuspulse-github", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["cloud_contacted"] is False


@pytest.mark.asyncio
async def test_remote_invocation_records_actual_return_and_rejects_offline(document):
    calls = []

    class FakeRemote:
        live = True

        def operation_schemas(self):
            return operation_schemas()

        async def async_query(self, **kwargs):
            calls.append(kwargs)
            return result(live=self.live).model_dump(mode="json")

    remote = FakeRemote()
    client = SimpleNamespace(runtimes=SimpleNamespace(get=lambda **_: remote))
    arguments = dict(resource_name="projects/123/locations/us-central1/reasoningEngines/456",
                     document=document, repository_url="https://github.com/example/project",
                     client_factory=lambda *_: client)
    evidence = await invoke.invoke(**arguments)
    assert evidence["analysis"]["model"] == "gemini-3.8-flash"
    assert evidence["status"] == "remote_response_received"
    assert calls[0]["document"]["sha256"] == document.sha256
    remote.live = False
    with pytest.raises(ValueError, match="offline"):
        await invoke.invoke(**arguments)


@pytest.mark.asyncio
async def test_remote_write_operation_refused_before_invocation(document):
    remote = SimpleNamespace(operation_schemas=lambda: operation_schemas() + [{"name": "create_issue"}])
    client = SimpleNamespace(runtimes=SimpleNamespace(get=lambda **_: remote))
    with pytest.raises(ValueError, match="read-only"):
        await invoke.invoke(resource_name="projects/123/locations/us-central1/reasoningEngines/456",
                            document=document, repository_url="https://github.com/example/project",
                            client_factory=lambda *_: client)

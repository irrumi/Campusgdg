"""HTTP trust-boundary tests with explicit offline service fakes and real SQLite/parsers."""
from __future__ import annotations

import asyncio
import json
import mimetypes
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from campuspulse.api import RequestGuard, create_app
from campuspulse.config import Settings
from campuspulse.github_mcp import GitHubConfigurationError
from campuspulse.models import AnalysisResult, Evidence, Finding, IssueReceipt, Requirement, SourceReference
from campuspulse.security import proposal_hash
from campuspulse.storage import Store


def analysis_result():
    return AnalysisResult(
        requirements=[Requirement(id="req-1", title="Add tests", description="Provide automated tests.",
                                  source=SourceReference(page=1, quote="Provide automated tests."))],
        findings=[Finding(requirement_id="req-1", status="not_satisfied", explanation="No test job in CI.",
                          evidence_ids=["ev-1"], recommendation="Add a test job and document its command.")],
        evidence=[Evidence(id="ev-1", tool="ci", source_url="https://github.com/acme/course/actions",
                           summary="The configured CI workflow does not have a test job.")],
        steps=[], model="explicit-offline-test-double", live=False,
    )


class FakeAgent:
    def __init__(self):
        self.result = analysis_result()
        self.error = None
        self.calls = 0

    async def analyze(self, document, repository):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class FakeGateway:
    def __init__(self):
        self.settings = None
        self.calls = []
        self.enter_error = None
        self.create_error = None
        self.verify_error = None
        self.mismatch = {}
        self.delay = 0
        self.receipt = None

    def factory(self, settings):
        self.settings = settings
        return self

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, *_):
        return None

    async def create_issue(self, repository, title, body):
        self.calls.append(("create", repository.url, title, body))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.create_error:
            raise self.create_error
        self.receipt = IssueReceipt(number=17, url=f"{repository.url}/issues/17", title=title, body=body)
        return self.receipt

    async def verify_issue(self, repository, number):
        self.calls.append(("verify", repository.url, number))
        if self.verify_error:
            raise self.verify_error
        return self.receipt.model_copy(update={"verified": True, **self.mismatch})


@contextmanager
def harness(**overrides):
    settings = Settings(_env_file=None, database_path=":memory:", allow_github_writes=True,
                        **overrides)
    store, agent, gateway = Store(":memory:"), FakeAgent(), FakeGateway()
    app = create_app(settings=settings, store=store, agent=agent, gateway_factory=gateway.factory)
    with TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, settings=settings, store=store, agent=agent,
                              gateway=gateway, app=app)
    store.close()


@pytest.fixture
def system():
    with harness() as test_system:
        yield test_system


def upload(system, *, content=b"Provide automated tests.", filename="assignment.txt", **kwargs):
    return system.client.post("/api/assignments", data={"repository_url": "https://github.com/acme/course"},
                              files={"file": (filename, content, "text/plain")}, **kwargs)


def prepared(system):
    response = upload(system)
    assert response.status_code == 200, response.text
    payload = response.json()
    assignment_id, token = payload["assignment"]["id"], payload["token"]
    headers = {"X-Assignment-Token": token}
    response = system.client.post(f"/api/assignments/{assignment_id}/analyze", headers=headers)
    assert response.status_code == 200, response.text
    response = system.client.post(f"/api/assignments/{assignment_id}/actions", headers=headers,
                                  json={"requirement_id": "req-1"})
    assert response.status_code == 200, response.text
    return assignment_id, token, headers, response.json()


def approve(system, assignment_id, headers, action, **changes):
    return system.client.post(f"/api/assignments/{assignment_id}/actions/{action['id']}/approve",
                              headers=headers,
                              json={"approved": True, "proposal_hash": action["proposal_hash"], **changes})


def test_upload_is_local_and_token_is_returned_once(system):
    response = upload(system)
    assert response.status_code == 200
    assignment, token = response.json()["assignment"], response.json()["token"]
    assert assignment["analysis"] is None
    assert assignment["document"]["pages"][0]["text"] == "Provide automated tests."
    assert system.agent.calls == 0 and system.gateway.calls == []
    read = system.client.get(f"/api/assignments/{assignment['id']}", headers={"X-Assignment-Token": token})
    assert read.json() == assignment
    assert token not in read.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("route,method", [("", "get"), ("/events", "get"), ("/analyze", "post"),
                                         ("/actions", "post"), ("/actions/missing/approve", "post")])
def test_all_assignment_routes_reject_other_session(system, route, method):
    first = upload(system).json()
    second = upload(system).json()
    path = f"/api/assignments/{first['assignment']['id']}{route}"
    body = {"requirement_id": "req-1"} if route == "/actions" else {
        "approved": True, "proposal_hash": "x"}
    kwargs = {"json": body} if method == "post" and route != "/analyze" else {}
    for headers in ({}, {"X-Assignment-Token": second["token"]}):
        response = getattr(system.client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "Resource not found."}
    assert system.gateway.calls == [] and system.agent.calls == 0


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:8000",
                                   "http://127.0.0.1:8001", "http://user@127.0.0.1:8000",
                                   "http://127.0.0.1:8000/path"])
def test_cross_origin_upload_is_rejected(system, origin):
    response = upload(system, headers={"Origin": origin})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_same_origin_browser_upload_and_non_browser_clients_work(system):
    assert upload(system, headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    assert upload(system).status_code == 200
    assert upload(system, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 400


def test_health_contains_only_configuration_booleans_not_secrets(system):
    settings = Settings(_env_file=None, gemini_api_key="test-gemini-secret", github_token="test-github-secret")
    with TestClient(create_app(settings=settings, store=system.store), base_url="http://127.0.0.1") as client:
        response = client.get("/api/health")
    assert response.json() == {"status": "ok", "mode": "live", "gemini_configured": True,
                               "github_configured": True, "allow_github_writes": False,
                               "analysis_backend": "local", "analysis_configured": True,
                               "vertex_configured": False}
    assert "test-gemini-secret" not in response.text and "test-github-secret" not in response.text


def test_file_limit_is_enforced_before_document_parser():
    with harness(max_upload_bytes=16) as system:
        response = upload(system, content=b"x" * 17)
        assert response.status_code == 413
        assert system.agent.calls == 0


@pytest.mark.parametrize("filename,content", [("bad.exe", b"binary"), ("empty.txt", b""),
                                            ("invalid.txt", b"\xff\xff"), ("bad.pdf", b"not a pdf")])
def test_invalid_documents_return_safe_400(system, filename, content):
    response = upload(system, filename=filename, content=content)
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


def test_invalid_repository_and_extra_multipart_fields(system):
    response = system.client.post("/api/assignments", data={"repository_url": "https://evil.example/repo"},
                                  files={"file": ("test.txt", b"Example")})
    assert response.status_code == 400
    response = system.client.post("/api/assignments", data={"repository_url": "https://github.com/acme/course",
                                                            "extra": "ignored?"},
                                  files={"file": ("test.txt", b"Example")})
    assert response.status_code in {400, 422}


async def test_request_guard_counts_streamed_bytes_without_content_length():
    invoked = False
    responses = []

    async def downstream(*_):
        nonlocal invoked
        invoked = True

    parts = iter([{"type": "http.request", "body": b"a" * 10_000, "more_body": True},
                  {"type": "http.request", "body": b"b" * 10_000, "more_body": False}])

    async def receive():
        return next(parts)

    async def send(message):
        responses.append(message)

    middleware = RequestGuard(downstream, lambda: Settings(_env_file=None))
    await middleware({"type": "http", "method": "POST", "path": "/api/assignments/x/actions",
                      "scheme": "http", "headers": [(b"host", b"localhost")]}, receive, send)
    assert not invoked
    assert responses[0]["status"] == 413


def test_analysis_missing_configuration_keeps_local_upload_available(system):
    with TestClient(create_app(Settings(_env_file=None), store=system.store), base_url="http://127.0.0.1") as client:
        original = upload(system).json()
        response = client.post(f"/api/assignments/{original['assignment']['id']}/analyze",
                               headers={"X-Assignment-Token": original["token"]})
    assert response.status_code == 503


@pytest.mark.parametrize("stage", ["extraction", "agent"])
def test_provider_quota_has_actionable_safe_response(system, caplog, stage):
    from campuspulse.agent import AgentRateLimitError
    from campuspulse.extraction import ExtractionRateLimitError

    error = ExtractionRateLimitError if stage == "extraction" else AgentRateLimitError
    system.agent.error = error("secret=private-provider-detail")
    original = upload(system).json()
    response = system.client.post(f"/api/assignments/{original['assignment']['id']}/analyze",
                                  headers={"X-Assignment-Token": original["token"]})
    assert response.status_code == 429
    assert "AI Studio limits" in response.json()["detail"]
    assert "private-provider-detail" not in response.text + caplog.text
    assert system.agent.calls == 1


def test_analysis_failure_is_safe_and_audited(system, caplog):
    system.agent.error = RuntimeError("secret=super-private-provider-error")
    original = upload(system).json()
    assignment_id, token = original["assignment"]["id"], original["token"]
    headers = {"X-Assignment-Token": token}
    response = system.client.post(f"/api/assignments/{assignment_id}/analyze", headers=headers)
    assert response.status_code == 502
    assert "RuntimeError" in caplog.text
    assert "super-private" not in caplog.text
    assert "super-private" not in response.text
    events = system.client.get(f"/api/assignments/{assignment_id}/events", headers=headers)
    assert "super-private" not in events.text
    assert [item["kind"] for item in events.json()] == ["upload", "analysis_started", "analysis_failed"]


def test_proposal_comes_from_stored_requirement_and_requires_recommendation(system):
    original = upload(system).json()
    assignment_id, token = original["assignment"]["id"], original["token"]
    path, headers = f"/api/assignments/{assignment_id}/actions", {"X-Assignment-Token": token}
    assert system.client.post(path, headers=headers, json={"requirement_id": "req-1"}).status_code == 409
    result = analysis_result()
    result.findings[0].recommendation = None
    system.store.save_analysis(assignment_id, token, result)
    assert system.client.post(path, headers=headers, json={"requirement_id": "req-1"}).status_code == 409
    assert system.client.post(path, headers=headers, json={"requirement_id": "missing"}).status_code == 409
    result.findings[0].recommendation = "Implement this requirement."
    system.store.save_analysis(assignment_id, token, result)
    response = system.client.post(path, headers=headers, json={"requirement_id": "req-1", "title": "override"})
    assert response.status_code == 422
    assert system.gateway.calls == []


def test_successful_approval_uses_exact_saved_proposal_and_readback(system):
    assignment_id, token, headers, action = prepared(system)
    assert system.gateway.calls == []
    assert "Provide automated tests." in action["body"]
    assert "page/paragraph 1" in action["body"]
    assert "https://github.com/acme/course/actions" in action["body"]
    saved = system.store.get_action(assignment_id, token, action["id"])
    assert action["proposal_hash"] == proposal_hash(saved.repository, saved.title, saved.body)
    response = approve(system, assignment_id, headers, action)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded"
    assert response.json()["receipt"]["verified"] is True
    assert system.gateway.calls == [("create", "https://github.com/acme/course", action["title"], action["body"]),
                                     ("verify", "https://github.com/acme/course", 17)]
    assert approve(system, assignment_id, headers, action).status_code == 409
    assert len(system.gateway.calls) == 2


@pytest.mark.parametrize("approved", [False, "true", "yes", 1, 0, None, {}, []])
def test_approval_requires_literal_json_true(system, approved):
    assignment_id, token, headers, action = prepared(system)
    response = approve(system, assignment_id, headers, action, approved=approved)
    assert response.status_code == 422
    assert system.store.get_action(assignment_id, token, action["id"]).status == "pending"
    assert system.gateway.calls == []


def test_hash_and_write_setting_checked_before_claim(system):
    assignment_id, token, headers, action = prepared(system)
    assert approve(system, assignment_id, headers, action, proposal_hash="wrong").status_code == 409
    system.settings.allow_github_writes = False
    assert approve(system, assignment_id, headers, action).status_code == 409
    assert system.store.get_action(assignment_id, token, action["id"]).status == "pending"
    assert system.gateway.calls == []


def test_approval_cross_origin_never_claims(system):
    assignment_id, token, headers, action = prepared(system)
    response = approve(system, assignment_id, {**headers, "Origin": "http://evil.example"}, action)
    assert response.status_code == 400
    assert system.store.get_action(assignment_id, token, action["id"]).status == "pending"
    assert system.gateway.calls == []


@pytest.mark.parametrize("stage", ["create", "verify"])
def test_uncertain_remote_outcome_is_terminal_and_not_retried(system, stage):
    assignment_id, token, headers, action = prepared(system)
    setattr(system.gateway, f"{stage}_error", RuntimeError("provider-token=do-not-disclose"))
    response = approve(system, assignment_id, headers, action)
    assert response.status_code == 502
    assert "do-not-disclose" not in response.text
    saved = system.store.get_action(assignment_id, token, action["id"])
    assert saved.status == "unknown" and saved.receipt is None
    assert approve(system, assignment_id, headers, action).status_code == 409
    assert len([call for call in system.gateway.calls if call[0] == "create"]) == 1
    assert "action_unknown" in [event.kind for event in system.store.list_events(assignment_id, token)]


@pytest.mark.parametrize("mismatch", [{"verified": False}, {"title": "Different"}, {"body": "Different"},
                                     {"number": 18}, {"url": "https://github.com/other/repo/issues/17"},
                                     {"url": "https://github.com/acme/course/issues/17?leak=1"}])
def test_readback_must_match_all_approved_fields(system, mismatch):
    assignment_id, token, headers, action = prepared(system)
    system.gateway.mismatch = mismatch
    response = approve(system, assignment_id, headers, action)
    assert response.status_code == 502
    assert system.store.get_action(assignment_id, token, action["id"]).status == "unknown"


def test_gateway_configuration_failure_before_write_is_terminal_failed(system):
    assignment_id, token, headers, action = prepared(system)
    system.gateway.enter_error = GitHubConfigurationError("unsafe provider detail")
    response = approve(system, assignment_id, headers, action)
    assert response.status_code == 503
    assert "unsafe provider detail" not in response.text
    assert system.store.get_action(assignment_id, token, action["id"]).status == "failed"
    assert system.gateway.calls == []
    assert approve(system, assignment_id, headers, action).status_code == 409


def test_write_timeout_remains_unknown_and_single_use():
    with harness(request_timeout_seconds=0.02) as system:
        assignment_id, token, headers, action = prepared(system)
        system.gateway.delay = 0.2
        response = approve(system, assignment_id, headers, action)
        assert response.status_code == 502
        assert system.store.get_action(assignment_id, token, action["id"]).status == "unknown"
        assert approve(system, assignment_id, headers, action).status_code == 409


async def test_concurrent_approvals_have_one_writer(system):
    assignment_id, token, headers, action = prepared(system)
    system.gateway.delay = 0.05
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=system.app),
                                 base_url="http://127.0.0.1:8000") as client:
        path = f"/api/assignments/{assignment_id}/actions/{action['id']}/approve"
        first, second = await asyncio.gather(*[
            client.post(path, headers=headers, json={"approved": True, "proposal_hash": action["proposal_hash"]})
            for _ in range(2)
        ])
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert system.store.get_action(assignment_id, token, action["id"]).status == "succeeded"
    assert len([call for call in system.gateway.calls if call[0] == "create"]) == 1


def test_validation_error_does_not_echo_attacker_input(system):
    assignment_id, _, headers, action = prepared(system)
    response = approve(system, assignment_id, headers, action, approved="my-sensitive-value")
    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed."}
    assert "my-sensitive-value" not in json.dumps(response.json())


def test_same_origin_dns_rebinding_host_is_rejected(system):
    response = upload(system, headers={"Host": "attacker.example", "Origin": "http://attacker.example"})
    assert response.status_code == 400
    assert system.client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400
    assert system.agent.calls == 0


def test_provider_http_exception_is_sanitized(system):
    original = upload(system).json()
    system.agent.error = HTTPException(400, "secret=provider-specific-detail")
    response = system.client.post(f"/api/assignments/{original['assignment']['id']}/analyze",
                                  headers={"X-Assignment-Token": original["token"]})
    assert response.status_code == 502
    assert "provider-specific-detail" not in response.text


def test_missing_approval_and_additional_fields_do_not_claim(system):
    assignment_id, token, headers, action = prepared(system)
    path = f"/api/assignments/{assignment_id}/actions/{action['id']}/approve"
    for body in ({"proposal_hash": action["proposal_hash"]},
                 {"approved": True, "proposal_hash": action["proposal_hash"], "body": "tampered"}):
        assert system.client.post(path, headers=headers, json=body).status_code == 422
    assert system.store.get_action(assignment_id, token, action["id"]).status == "pending"
    assert system.gateway.calls == []


def test_missing_github_configuration_does_not_consume_approval(system):
    assignment_id, token, headers, action = prepared(system)
    app = create_app(settings=system.settings, store=system.store, agent=system.agent)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(f"/api/assignments/{assignment_id}/actions/{action['id']}/approve",
                               headers=headers, json={"approved": True, "proposal_hash": action["proposal_hash"]})
    assert response.status_code == 503
    assert system.store.get_action(assignment_id, token, action["id"]).status == "pending"


def test_analysis_timeout_keeps_previous_result_and_audits_failure(system):
    original = upload(system).json()
    assignment_id, token = original["assignment"]["id"], original["token"]
    system.settings.request_timeout_seconds = 0.01

    async def slow_analysis(*_):
        await asyncio.sleep(0.2)
        return analysis_result()

    system.agent.analyze = slow_analysis
    response = system.client.post(f"/api/assignments/{assignment_id}/analyze",
                                  headers={"X-Assignment-Token": token})
    assert response.status_code == 502
    assert system.store.get_assignment(assignment_id, token).analysis is None
    assert system.store.list_events(assignment_id, token)[-1].kind == "analysis_failed"


def test_explicit_host_configuration_supports_deployed_ui(system):
    system.settings.allowed_hosts = ["guardian.example"]
    response = upload(system, headers={"Host": "guardian.example", "Origin": "http://guardian.example"})
    assert response.status_code == 200
    response = upload(system, headers={"Host": "guardian.example", "Origin": "http://evil.example"})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["user@localhost", "localhost:bad", "localhost/path", " localhost"])
def test_malformed_host_cannot_bypass_allowlist(system, host):
    assert system.client.get("/api/health", headers={"Host": host}).status_code == 400


def test_ipv6_loopback_is_a_trusted_host(system):
    response = system.client.get("/api/health", headers={"Host": "[::1]:8000"})
    assert response.status_code == 200


def test_factory_and_module_app_do_not_initialize_settings_or_database(monkeypatch):
    import campuspulse.api as api

    def forbidden(*_, **__):
        pytest.fail("Creating an app must not read credentials or open a database")

    monkeypatch.setattr(api, "Settings", forbidden)
    monkeypatch.setattr(api, "Store", forbidden)
    assert api.create_app() is not None


@pytest.mark.parametrize("extension", [".mjs", ".js"])
def test_static_javascript_mime_ignores_windows_registry(monkeypatch, tmp_path, extension):
    import campuspulse.api as api

    static = tmp_path / "static"
    static.mkdir()
    source = "export const serviceStatus = 'ready';\n"
    (static / f"app{extension}").write_bytes(source.encode("utf-8"))
    monkeypatch.setattr(api, "__file__", str(tmp_path / "api.py"))
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, extension, "text/plain")
    assert mimetypes.guess_type(f"app{extension}")[0] == "text/plain"

    with TestClient(api.create_app(Settings(_env_file=None)), base_url="http://localhost") as client:
        response = client.get(f"/static/app{extension}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/javascript; charset=utf-8"
        assert response.text == source
        head = client.head(f"/static/app{extension}")
        assert head.headers["content-type"] == response.headers["content-type"]
        assert head.content == b""
        cached = client.get(f"/static/app{extension}", headers={"If-None-Match": response.headers["etag"]})
        assert cached.status_code == 304

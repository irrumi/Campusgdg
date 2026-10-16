"""Offline cross-module flows: real SDK/gateway/SQLite, explicitly fake services.

Gemini HTTP responses use httpx.MockTransport. MCP service sessions use real SDK
message types, but there is no MCP HTTP server or live GitHub/Gemini connection.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from google import genai
from google.genai import types
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from campuspulse.agent import GuardianAgent
from campuspulse.api import create_app
from campuspulse.config import Settings
from campuspulse.github_mcp import GitHubGateway
from campuspulse.models import ActionProposal, RepositoryRef
from campuspulse.security import proposal_hash
from campuspulse.storage import Store


REPOSITORY_URL = "https://github.com/offline-fixture/course-project"
ASSIGNMENT_PATH = Path(__file__).resolve().parents[1] / "examples" / "assignment.txt"
README_TEXT = "Local setup: python -m uvicorn campuspulse.api:app --host 127.0.0.1"


def _instruction_schema(body):
    """Read the actual SDK wire instruction without depending on its prose marker."""
    instruction = "\n".join(part["text"] for part in body["systemInstruction"]["parts"] if "text" in part)
    schemas = []
    decoder = json.JSONDecoder()
    for offset, character in enumerate(instruction):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(instruction[offset:])
        except ValueError:
            continue
        if (isinstance(candidate, dict) and candidate.get("type") == "object"
                and {"requirements", "findings"}.intersection(candidate.get("properties", {}))):
            schemas.append(candidate)
    assert len(schemas) == 1, "JSON-mode requests must supply one expected model schema in systemInstruction"
    assert schemas[0]["additionalProperties"] is False
    return schemas[0]


def _tools():
    """Minimal representative discovery schemas; the real gateway validates them."""
    string, integer = {"type": "string"}, {"type": "integer"}
    scoped = {"owner": string, "repo": string}
    definitions = {
        "get_file_contents": ({**scoped, "path": string}, ["owner", "repo", "path"]),
        "list_issues": ({**scoped, "perPage": integer}, ["owner", "repo"]),
        "actions_list": ({**scoped, "method": {"enum": ["list_workflow_runs"]},
                          "perPage": integer, "page": integer}, ["owner", "repo", "method"]),
        "issue_read": ({**scoped, "method": {"enum": ["get"]}, "issue_number": integer},
                       ["owner", "repo", "method", "issue_number"]),
        "issue_write": ({**scoped, "method": {"enum": ["create"]}, "title": string, "body": string},
                        ["owner", "repo", "method", "title", "body"]),
    }
    return [Tool(name=name, inputSchema={"type": "object", "properties": properties,
                                        "required": required, "additionalProperties": False},
                 annotations=ToolAnnotations(readOnlyHint=name != "issue_write"))
            for name, (properties, required) in definitions.items()]


class FakeMCPService:
    """State shared by explicitly injected read and write sessions."""

    def __init__(self):
        self.calls = []
        self.sessions = []
        self.initialized = []
        self.discovered = []
        self.issue = None
        self.mode = "success"
        self.fail_reads = False
        self.write_entered = asyncio.Event()
        self.release_write = asyncio.Event()

    @property
    def writes(self):
        return [call for call in self.calls if call[1] == "issue_write"]

    @asynccontextmanager
    async def session(self, *, read_only):
        self.sessions.append(read_only)
        yield _FakeMCPSession(self, read_only)

    async def call(self, read_only, name, arguments):
        self.calls.append((read_only, name, dict(arguments)))
        assert arguments["owner"] == "offline-fixture"
        assert arguments["repo"] == "course-project"
        if name == "issue_write":
            assert not read_only, "A model read session must never perform a write"
            self.issue = {"number": 41, "html_url": REPOSITORY_URL + "/issues/41",
                          "title": arguments["title"], "body": arguments["body"]}
            self.write_entered.set()
            # Simulate a remote commit before its reply is delayed or lost.
            if self.mode in {"timeout", "hold"}:
                await self.release_write.wait()
            payload = {"method": "create", "issue": self.issue}
        elif name == "issue_read":
            assert read_only and self.issue is not None
            payload = {"method": "get", "issue": dict(self.issue)}
            if self.mode == "mismatched_readback":
                payload["issue"]["body"] += "\nChanged after creation."
        elif self.fail_reads:
            return CallToolResult(isError=True, content=[TextContent(type="text", text="Unavailable")])
        elif name == "get_file_contents":
            payload = ([{"name": "README.md", "type": "file"}] if not arguments["path"] else {
                "name": "README.md", "encoding": "base64",
                "content": base64.b64encode(README_TEXT.encode()).decode(),
            })
        elif name == "list_issues":
            payload = {"issues": [], "total_count": 0}
        elif name == "actions_list":
            payload = {"workflow_runs": [{"id": 7, "status": "completed", "conclusion": "failure"}]}
        else:
            raise AssertionError(f"Unexpected MCP tool: {name}")
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


class _FakeMCPSession:
    def __init__(self, service, read_only):
        self.service, self.read_only = service, read_only

    async def initialize(self):
        self.service.initialized.append(self.read_only)

    async def list_tools(self, cursor=None):
        assert cursor is None
        self.service.discovered.append(self.read_only)
        return ListToolsResult(tools=_tools())

    async def call_tool(self, name, arguments):
        return await self.service.call(self.read_only, name, arguments)


class FakeGeminiHTTP:
    """Scripted service responses consumed by the unmodified google-genai SDK."""

    def __init__(self):
        self.requests = []
        self.forge_citations = False
        self.forge_source = False
        self.extra_model_field = False
        self.extra_assessment_field = False

    def __call__(self, request):
        assert request.method == "POST" and request.url.host == "offline-gemini.invalid"
        body = json.loads(request.content)
        self.requests.append(body)
        generation = body.get("generationConfig", {})
        # JSON mode succeeded in a live probe where constrained schema requests
        # did not. The model's shape is instructed; strict enforcement stays local.
        assert "responseJsonSchema" not in generation and "responseSchema" not in generation
        schema = {}
        if generation.get("responseMimeType") == "application/json":
            schema = _instruction_schema(body)
        properties = schema.get("properties", {})
        if "requirements" in properties:
            data = json.loads(body["contents"][0]["parts"][0]["text"])
            quotes = [source["text"] for source in data["sources"]]
            assert data["source_kind"] == "paragraph" and len(quotes) == 2
            payload = {"requirements": [
                {"id": "setup", "title": "Document local setup", "description": quotes[0],
                 "source": {"page": 1, "quote": quotes[0]}, "ambiguity": None},
                {"id": "ci", "title": "CI — successful run", "description": quotes[1],
                 "source": {"page": 2, "quote": "Invented quote" if self.forge_source else quotes[1]},
                 "ambiguity": None},
            ]}
            if self.extra_model_field:
                payload["requirements"][0]["undeclared_field"] = "Must fail local validation"
            parts = [{"text": json.dumps(payload)}]
        elif "findings" in properties:
            data = json.loads(body["contents"][0]["parts"][0]["text"])
            evidence = {item["tool"]: item for item in data["evidence"]}
            payload = {"findings": [
                {"requirement_id": "setup", "status": "satisfied", "explanation": "README documents setup.",
                 "evidence_ids": [evidence["file"]["id"]], "basis": "supports_requirement",
                 "citations": [{"evidence_id": evidence["file"]["id"], "quote": "python -m uvicorn"}],
                 "recommendation": None},
                {"requirement_id": "ci", "status": "not_satisfied", "explanation": "The observed CI run failed.",
                 "evidence_ids": [evidence["ci"]["id"]], "basis": "contradicts_requirement",
                 "citations": [{"evidence_id": evidence["ci"]["id"],
                                "quote": "Invented evidence" if self.forge_citations else "failure"}],
                 "recommendation": "Inspect the failing CI job and fix the failing check."},
            ]}
            if self.extra_assessment_field:
                payload["findings"][0]["undeclared_field"] = "Must fail local validation"
            parts = [{"text": json.dumps(payload)}]
        elif any("functionResponse" in part for content in body["contents"] for part in content["parts"]):
            parts = [{"text": "Evidence collection is complete."}]
        else:
            names = {item["name"] for tool in body["tools"] for item in tool["functionDeclarations"]}
            assert names == {"read_repository", "read_file", "read_issues", "read_ci"}
            parts = [{"functionCall": {"name": name, "args": args}} for name, args in [
                ("read_repository", {}), ("read_file", {"path": "README.md"}),
                ("read_issues", {}), ("read_ci", {}),
            ]]
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": parts},
                                                       "finishReason": "STOP"}]})


@pytest.fixture
async def harness(tmp_path):
    # Explicit values prevent dotenv, ADC, or real environment credentials being used.
    settings = Settings(_env_file=None, gemini_api_key=None, github_token=None,
                        google_genai_use_vertexai=False, google_cloud_project=None,
                        allow_github_writes=True, database_path=str(tmp_path / "integration.sqlite3"),
                        allowed_hosts=["localhost"], request_timeout_seconds=10, max_tool_calls=8)
    gemini, mcp = FakeGeminiHTTP(), FakeMCPService()
    sdk = genai.Client(vertexai=False, api_key="explicit-offline-test-key", http_options=types.HttpOptions(
        base_url="https://offline-gemini.invalid",
        async_client_args={"transport": httpx.MockTransport(gemini)},
        client_args={"transport": httpx.MockTransport(gemini)},
    ))
    def gateway_factory(config):
        return GitHubGateway(config, session_factory=mcp.session)

    store = Store(settings.database_path)
    agent = GuardianAgent(settings, client=sdk, gateway_factory=gateway_factory)
    app = create_app(settings=settings, store=store, agent=agent, gateway_factory=gateway_factory)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            yield SimpleNamespace(client=client, settings=settings, gemini=gemini, mcp=mcp, store=store,
                                  agent=agent, gateway_factory=gateway_factory)
    finally:
        await sdk.aio.aclose()
        sdk.close()
        store.close()


async def _upload(harness):
    content = ASSIGNMENT_PATH.read_bytes()
    response = await harness.client.post("/api/assignments", data={"repository_url": REPOSITORY_URL},
                                         files={"file": ("assignment.txt", content, "text/plain")})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["assignment"]["document"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert len(result["assignment"]["document"]["pages"]) == 2
    assert not harness.gemini.requests and not harness.mcp.sessions
    return result["assignment"]["id"], result["token"]


@pytest.fixture
async def prepared(harness):
    assignment_id, token = await _upload(harness)
    headers = {"X-Assignment-Token": token}
    base = f"/api/assignments/{assignment_id}"
    analyzed = await harness.client.post(base + "/analyze", headers=headers)
    assert analyzed.status_code == 200, analyzed.text
    proposal = await harness.client.post(base + "/actions", headers=headers, json={"requirement_id": "ci"})
    assert proposal.status_code == 200, proposal.text
    action = proposal.json()
    assert action["status"] == "pending"
    assert not harness.mcp.writes and False not in harness.mcp.sessions
    return SimpleNamespace(harness=harness, id=assignment_id, token=token, headers=headers, base=base,
                           analysis=analyzed.json()["analysis"], action=action,
                           approve=base + f"/actions/{action['id']}/approve",
                           approval={"approved": True, "proposal_hash": action["proposal_hash"]})


async def test_upload_real_sdk_gateway_analysis_approval_and_durable_readback(prepared):
    p, h = prepared, prepared.harness
    assert p.analysis["live"] is False
    assert any("Injected dependencies" in warning for warning in p.analysis["warnings"])
    assert [finding["status"] for finding in p.analysis["findings"]] == ["satisfied", "not_satisfied"]
    assert {item["tool"] for item in p.analysis["evidence"]} == {"repository", "file", "issues", "ci"}
    assert p.analysis["usage"]["tool_calls"] == 4
    assert len(h.gemini.requests) == 4  # extraction, tool calls, stop, assessment
    assert "tools" not in h.gemini.requests[0]
    for request, root_field in ((h.gemini.requests[0], "requirements"), (h.gemini.requests[-1], "findings")):
        generation = request["generationConfig"]
        assert generation["responseMimeType"] == "application/json"
        assert "responseJsonSchema" not in generation and "responseSchema" not in generation
        schema = _instruction_schema(request)
        assert root_field in schema["properties"] and root_field in schema["required"]
    assert h.mcp.initialized == h.mcp.discovered == [True]
    # The approval digest computed by security and SQLite must agree byte for byte.
    repository = RepositoryRef.model_validate(p.action["repository"])
    assert p.action["proposal_hash"] == proposal_hash(repository, p.action["title"], p.action["body"])
    assert "—" in p.action["title"]
    response = await h.client.post(p.approve, headers=p.headers, json=p.approval)
    assert response.status_code == 200, response.text
    completed = response.json()
    assert completed["status"] == "succeeded" and completed["receipt"]["verified"] is True
    assert completed["receipt"]["title"] == p.action["title"]
    assert completed["receipt"]["body"] == p.action["body"]
    assert completed["receipt"]["url"] == REPOSITORY_URL + "/issues/41"
    assert len(h.mcp.writes) == 1
    assert [name for _, name, _ in h.mcp.calls][-2:] == ["issue_write", "issue_read"]
    assert h.mcp.discovered == [True, True, False]
    retry = await h.client.post(p.approve, headers=p.headers, json=p.approval)
    assert retry.status_code == 409 and len(h.mcp.writes) == 1
    with Store(h.settings.database_path) as reopened:
        assert reopened.get_action(p.id, p.token, p.action["id"]).model_dump(mode="json") == completed
        assert reopened.get_assignment(p.id, p.token).analysis.live is False
    with sqlite3.connect(h.settings.database_path) as connection:
        stored_hash = connection.execute("SELECT token_hash FROM assignments WHERE id = ?", (p.id,)).fetchone()[0]
        assert stored_hash == hashlib.sha256(p.token.encode()).hexdigest()
        assert p.token not in "\n".join(connection.iterdump())
    events = (await h.client.get(p.base + "/events", headers=p.headers)).json()
    kinds = [event["kind"] for event in events]
    assert kinds[:4] == ["upload", "analysis_started", "analysis_completed", "action_proposed"]
    assert kinds.index("action_approved") < kinds.index("action_succeeded")
    assert p.token not in json.dumps(events)


async def test_literal_approval_hash_token_origin_and_write_setting_gate_same_proposal(prepared):
    p, h = prepared, prepared.harness
    for value in (False, 1, "true", None):
        response = await h.client.post(p.approve, headers=p.headers, json={**p.approval, "approved": value})
        assert response.status_code == 422
    missing_flag = await h.client.post(p.approve, headers=p.headers,
                                      json={"proposal_hash": p.action["proposal_hash"]})
    assert missing_flag.status_code == 422
    altered = await h.client.post(p.approve, headers=p.headers, json={**p.approval, "body": "New text"})
    assert altered.status_code == 422
    wrong_hash = await h.client.post(p.approve, headers=p.headers,
                                    json={**p.approval, "proposal_hash": "0" * 64})
    assert wrong_hash.status_code == 409
    missing = await h.client.post(p.approve, json=p.approval)
    wrong_token = await h.client.post(p.approve, headers={"X-Assignment-Token": "another-assignment"},
                                     json=p.approval)
    unknown = await h.client.post(p.approve.replace(p.id, "unknown"), headers=p.headers, json=p.approval)
    assert missing.status_code == wrong_token.status_code == unknown.status_code == 404
    assert missing.json() == wrong_token.json() == unknown.json()
    cross_origin = await h.client.post(p.approve, headers={**p.headers, "Origin": "https://untrusted.invalid"},
                                      json=p.approval)
    assert cross_origin.status_code == 400
    h.settings.allow_github_writes = False
    disabled = await h.client.post(p.approve, headers=p.headers, json=p.approval)
    assert disabled.status_code == 409
    assert h.store.get_action(p.id, p.token, p.action["id"]).status == "pending"
    assert not h.mcp.writes and False not in h.mcp.sessions
    h.settings.allow_github_writes = True
    assert (await h.client.post(p.approve, headers=p.headers, json=p.approval)).status_code == 200
    assert len(h.mcp.writes) == 1


async def test_concurrent_http_approvals_using_separate_sqlite_connections_have_one_winner(prepared):
    p, h = prepared, prepared.harness
    h.mcp.mode = "hold"
    with Store(h.settings.database_path) as second_store:
        second_app = create_app(settings=h.settings, store=second_store, gateway_factory=h.gateway_factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second_app),
                                     base_url="http://localhost") as second_client:
            first = asyncio.create_task(h.client.post(p.approve, headers=p.headers, json=p.approval))
            try:
                await asyncio.wait_for(h.mcp.write_entered.wait(), timeout=2)
                assert second_store.get_action(p.id, p.token, p.action["id"]).status == "executing"
                second = await second_client.post(p.approve, headers=p.headers, json=p.approval)
                assert second.status_code == 409
                assert len(h.mcp.writes) == 1
            finally:
                h.mcp.release_write.set()
                completed = await first
            assert completed.status_code == 200, completed.text
            assert second_store.get_action(p.id, p.token, p.action["id"]).status == "succeeded"


@pytest.mark.parametrize("mode", ["timeout", "mismatched_readback"])
async def test_unknown_remote_outcome_is_durable_and_cannot_be_reapproved(prepared, mode):
    p, h = prepared, prepared.harness
    h.mcp.mode = mode
    if mode == "timeout":
        h.settings.request_timeout_seconds = 0.05
    response = await h.client.post(p.approve, headers=p.headers, json=p.approval)
    assert response.status_code == 502 and "unknown" in response.json()["detail"].lower()
    assert h.mcp.issue is not None and len(h.mcp.writes) == 1
    with Store(h.settings.database_path) as reopened:
        failed = reopened.get_action(p.id, p.token, p.action["id"])
        assert failed.status == "unknown" and failed.receipt is None
        assert "action_unknown" in [event.kind for event in reopened.list_events(p.id, p.token)]
    # A later healthy service does not make the same approval retryable.
    h.mcp.mode = "success"
    retry = await h.client.post(p.approve, headers=p.headers, json=p.approval)
    assert retry.status_code == 409 and len(h.mcp.writes) == 1


@pytest.mark.parametrize("failure", [
    "unavailable_mcp", "fabricated_evidence_quote", "fabricated_source_quote", "extra_model_field",
    "extra_assessment_field",
])
async def test_service_failures_and_ungrounded_model_output_never_create_actionable_success(harness, failure):
    h = harness
    assignment_id, token = await _upload(h)
    headers = {"X-Assignment-Token": token}
    base = f"/api/assignments/{assignment_id}"
    h.mcp.fail_reads = failure == "unavailable_mcp"
    h.gemini.forge_citations = failure == "fabricated_evidence_quote"
    h.gemini.forge_source = failure == "fabricated_source_quote"
    h.gemini.extra_model_field = failure == "extra_model_field"
    h.gemini.extra_assessment_field = failure == "extra_assessment_field"
    response = await h.client.post(base + "/analyze", headers=headers)
    if h.gemini.forge_source or h.gemini.extra_model_field or h.gemini.extra_assessment_field:
        assert response.status_code == 502
        assert h.store.get_assignment(assignment_id, token).analysis is None
        if h.gemini.extra_assessment_field:
            assert len(h.mcp.calls) == 4
        else:
            assert not h.mcp.calls
    else:
        assert response.status_code == 200, response.text
        findings = response.json()["analysis"]["findings"]
        ci = next(finding for finding in findings if finding["requirement_id"] == "ci")
        assert ci["status"] == "unverified" and ci["recommendation"] is None
        if h.mcp.fail_reads:
            assert all(finding["status"] == "unverified" for finding in findings)
    proposal = await h.client.post(base + "/actions", headers=headers, json={"requirement_id": "ci"})
    assert proposal.status_code == 409
    assert not h.mcp.writes and False not in h.mcp.sessions


@pytest.mark.parametrize(("title", "body"), [
    ("CI — проверка", "Сохранить точный текст.\nSecond line.\n"),
    ("Leading and trailing spaces ", " body with trailing whitespace \n"),
    ("Unicode normalization", "é and e\u0301 are distinct byte sequences"),
])
async def test_security_and_sqlite_hash_the_exact_same_proposal_bytes(prepared, title, body):
    p, h = prepared, prepared.harness
    action = h.store.propose_action(p.id, p.token, ActionProposal(requirement_id="ci", title=title, body=body))
    canonical = json.dumps({"repository": action.repository.model_dump(mode="json"), "title": title,
                            "body": body}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert action.proposal_hash == proposal_hash(action.repository, title, body) == digest
    assert proposal_hash(action.repository, title, body + " ") != digest
    assert not h.mcp.writes

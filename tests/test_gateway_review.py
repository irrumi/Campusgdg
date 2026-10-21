"""Explicit offline fakes at HTTP/MCP boundaries; never live-service evidence."""
import base64
import json

import pytest
import requests

import agent.gateway_review as review
import agent.public_github_mcp as public
from campuspulse.models import Evidence
from campuspulse.parsing import parse_document
from campuspulse.security import validate_repository_url

REPO = "https://github.com/example/student"
SHA = "a" * 40
TEXT = "Provide README installation commands."
SETTINGS = {"api_url": "https://gateway.example/api/v1/chat", "api_key": "explicit-test-gateway-key"}


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    return result


@pytest.mark.parametrize("raw", ['hello', '{"x":1,"x":2}', '[1]', '{"x":NaN}', 'text {"x":1}', '```json\n{}\n``` suffix'])
def test_generic_or_ambiguous_answers_never_become_verified_reviews(raw):
    with pytest.raises(review.GatewayReviewError):
        review.structured_output(raw)


def test_unambiguous_json_fence_is_supported_without_relaxing_validation():
    assert review.structured_output('```json\n{"calls":[]}\n```') == {"calls": []}


def test_model_transport_uses_only_legacy_fields_and_never_puts_access_key_in_prompt(monkeypatch):
    calls = []

    def fake_post(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return response({"output": '{"calls":[]}', "steps": [{"type": "thought", "content": "private"}]})

    monkeypatch.setattr(review.requests, "post", fake_post)
    steps = []
    client = review.GatewayJSON(SETTINGS, steps)
    assert client.ask("test", "Instruction", {"sources": ["actual input"]}, {"type": "object"}) == {"calls": []}
    payload = calls[0][1]["json"]
    assert set(payload) == {"message", "history", "student_context"}
    assert SETTINGS["api_key"] not in json.dumps(payload)
    assert payload["student_context"]["campuspulse_review"]["untrusted_data"] == {"sources": ["actual input"]}
    assert len(payload["message"]) <= 4000
    assert "private" not in json.dumps([step.model_dump(mode="json") for step in steps])


def test_oversized_assignment_fails_before_network_and_never_silently_truncates(monkeypatch):
    monkeypatch.setattr(review.requests, "post", lambda *args, **kwargs: pytest.fail("Unexpected HTTP"))
    client = review.GatewayJSON(SETTINGS, [])
    with pytest.raises(review.GatewayReviewError, match="200 000"):
        client.ask("test", "Instruction", {"text": "x" * 200000}, {})


def test_legacy_4000_character_gateway_receives_complete_large_document_in_existing_context(monkeypatch):
    document = "Actual full document. " * 1000
    observed = []

    def legacy_gateway(endpoint, **kwargs):
        payload = kwargs["json"]
        assert len(payload["message"]) <= 4000
        observed.append(payload)
        return response({"output": '{"calls":[]}', "steps": []})

    monkeypatch.setattr(review.requests, "post", legacy_gateway)
    review.GatewayJSON(SETTINGS, []).ask("test", "Extract requirements", {"document": document}, {})
    assert observed[0]["student_context"]["campuspulse_review"]["untrusted_data"]["document"] == document


def test_all_review_phase_instructions_fit_published_gateway_limit(monkeypatch):
    from campuspulse.extraction import SYSTEM_INSTRUCTION as extraction
    from campuspulse.agent import SYSTEM_INSTRUCTION, FINAL_INSTRUCTION

    def legacy_gateway(endpoint, **kwargs):
        assert len(kwargs["json"]["message"]) <= 4000
        return response({"output": '{}', "steps": []})

    monkeypatch.setattr(review.requests, "post", legacy_gateway)
    client = review.GatewayJSON(SETTINGS, [])
    for instruction in (extraction, SYSTEM_INSTRUCTION + FINAL_INSTRUCTION,
                        SYSTEM_INSTRUCTION + "Choose only declared read operations. " * 10):
        client.ask("test", instruction, {}, {})


def test_quotes_are_checked_against_actual_uploaded_document():
    document = parse_document(TEXT.encode(), "assignment.txt")
    requirement = {"id": "r1", "title": "README", "description": "Install commands", "ambiguity": None,
                   "source": {"page": 1, "quote": "Invented requirement"}}
    with pytest.raises(review.GatewayReviewError, match="цитаты"):
        review._requirements(document, {"requirements": [requirement]})
    with pytest.raises(review.GatewayReviewError, match="расписание"):
        review._requirements(document, {"requirements": []})


@pytest.mark.parametrize("kind,args", [("write", {}), ("file", {"path": "../secret"}),
                                     ("file", {"path": "https://external.example/a"}),
                                     ("repository", {"external_url": "https://external.example"})])
def test_public_read_rejects_undeclared_operations_and_paths(monkeypatch, kind, args):
    monkeypatch.setattr(public, "_get", lambda *args, **kwargs: pytest.fail("Unexpected HTTP"))
    with pytest.raises(ValueError):
        public.read_public_github(REPO, kind, args, revision=SHA)


def test_public_file_uses_pinned_commit_and_labels_partial_evidence(monkeypatch):
    observed = []

    def fake_get(path, params=None):
        observed.append((path, params))
        return {"type": "file", "encoding": "base64", "content": base64.b64encode(b"x" * 1000).decode()}

    monkeypatch.setattr(public, "_get", fake_get)
    result = public.read_public_github(REPO, "file", {"path": "README.md"}, revision=SHA, max_chars=500)
    item = result["evidence"][0]
    assert observed == [("/repos/example/student/contents/README.md", {"ref": SHA})]
    assert item["source_url"] == REPO + "/blob/" + SHA + "/README.md"
    assert item["data"]["truncated"] is True and len(item["summary"]) == 500
    assert item["data"]["transport"] == "custom-public-github-mcp"


def test_commit_switch_is_rejected_before_http(monkeypatch):
    monkeypatch.setattr(public, "_get", lambda *args, **kwargs: pytest.fail("Unexpected HTTP"))
    with pytest.raises(ValueError, match="pinned"):
        public.read_public_github(REPO, "file", {"path": "README.md", "ref": "other"}, revision=SHA)


@pytest.mark.asyncio
async def test_mcp_response_cannot_switch_repository_or_snapshot():
    gateway = public.PublicGitHubGateway()
    gateway.repository_url = REPO
    with pytest.raises(public.GitHubMCPError, match="switch repositories"):
        await gateway.collect(validate_repository_url("https://github.com/other/repo"), "file", {})


@pytest.mark.parametrize("repair_quote,expected_status", [
    ("Invented supporting quote", "unverified"),
    ("pip install -r requirements.txt", "satisfied"),
])
def test_full_compatibility_review_checks_evidence_instead_of_accepting_model_assertions(monkeypatch, repair_quote, expected_status):
    evidence = Evidence(id="e1", tool="file", source_url=REPO + "/blob/" + SHA + "/README.md",
                        summary="Run pip install -r requirements.txt", data={"commit_sha": SHA})
    outputs = iter([
        {"requirements": [{"id": "r1", "title": "README", "description": "Install commands", "ambiguity": None,
                           "source": {"page": 1, "quote": TEXT}}]},
        {"calls": [{"name": "read_file", "args": {"path": "README.md"}}], "summary": "Read README"},
        {"calls": [], "summary": "Enough evidence"},
        {"findings": [{"requirement_id": "r1", "status": "satisfied", "explanation": "Installation is documented.",
                       "evidence_ids": ["e1"], "basis": "supports_requirement",
                       "citations": [{"evidence_id": "e1", "quote": "Invented supporting quote"}], "recommendation": None}]},
        {"findings": [{"requirement_id": "r1", "status": "satisfied", "explanation": "Installation is documented.",
                       "evidence_ids": ["e1"], "basis": "supports_requirement",
                       "citations": [{"evidence_id": "e1", "quote": repair_quote}], "recommendation": None}]}
    ])
    calls = []

    def explicit_fake_post(endpoint, **kwargs):
        calls.append(json.loads(json.dumps(kwargs["json"])))
        return response({"output": json.dumps(next(outputs)), "steps": []})

    class ExplicitFakeMCP:
        revision = SHA

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def collect(self, repository, kind, arguments, **kwargs):
            assert repository.url == REPO and kind == "file" and arguments == {"path": "README.md"}
            return [evidence]

    monkeypatch.setattr(review.requests, "post", explicit_fake_post)
    monkeypatch.setattr(review, "PublicGitHubGateway", ExplicitFakeMCP)
    result = review.run_gateway_review({"repository_url": REPO},
        [{"name": "assignment.txt", "base64": base64.b64encode(TEXT.encode()).decode()}], SETTINGS)
    analysis = result["deadline"]["analysis"]
    assert analysis["findings"][0]["status"] == expected_status
    assert len(calls) == 5 and analysis["usage"]["tool_calls"] == 1
    assert all(len(call["message"]) <= 4000 for call in calls)
    history = analysis["usage"]["verification"]
    assert len(history) == 2 and history[0]["checks"][0]["reasons"] == ["citation_mismatch"]
    assert history[1]["checks"][0]["effective_status"] == expected_status
    final_context = calls[-1]["student_context"]["campuspulse_review"]
    assert final_context["untrusted_data"]["verification_feedback"] == history[:1]
    assert final_context["untrusted_data"]["evidence"][0]["id"] == "e1"
    assert final_context["untrusted_data"]["evidence"][0]["content"] == evidence.summary
    assert set(final_context["output_schema"]["$defs"]["_Decision"]["required"]) >= {"evidence_ids", "citations"}
    assert result["deadline"]["document_sha256"] == parse_document(TEXT.encode(), "assignment.txt").sha256
    receipt = next(step for step in result["steps"] if step.get("tool") == "public_github_mcp.read_file")
    assert receipt["result"]["commit_sha"] == SHA
    assert "custom-public-github-mcp" in json.dumps(receipt)


@pytest.mark.asyncio
async def test_ambiguous_requirement_is_explained_without_repair_that_cannot_resolve_it():
    requirements = review._requirements(parse_document(TEXT.encode(), "assignment.txt"), {"requirements": [
        {"id": "r1", "title": "README", "description": TEXT, "source": {"page": 1, "quote": TEXT},
         "ambiguity": "Which installation method is required?"}]})
    calls, warnings, steps = [], [], []

    async def explicit_fake_ask(*args):
        calls.append(args)
        return {"findings": [{"requirement_id": "r1", "status": "satisfied", "explanation": "Unsupported claim.",
                              "basis": "supports_requirement", "evidence_ids": [], "citations": [], "recommendation": None}]}

    findings, history = await review._assess(explicit_fake_ask, requirements, [], {}, warnings, steps)
    assert len(calls) == 1
    assert findings[0].status == "unverified"
    assert "ambiguous_requirement" in history[0]["checks"][0]["reasons"]
    assert requirements[0].ambiguity == "Which installation method is required?"


@pytest.mark.asyncio
async def test_successful_ci_can_be_grounded_with_exact_json_fields_without_changing_ambiguity():
    text = "The latest continuous integration run must finish successfully."
    requirements = review._requirements(parse_document(text.encode(), "assignment.txt"), {"requirements": [
        {"id": "ci", "title": "CI", "description": text, "source": {"page": 1, "quote": text}, "ambiguity": None}]})
    content = json.dumps({"bounded_listing": True, "head_sha": SHA, "workflow_runs": [
        {"id": 123, "name": "Offline checks", "status": "completed", "conclusion": "success", "head_sha": SHA}]})
    evidence = [Evidence(id="e1", tool="ci", source_url=REPO + "/actions", summary=content, data={"content": content})]
    calls = []

    async def explicit_fake_ask(phase, instruction, data, schema):
        calls.append(data)
        options = data["evidence"][0]["quote_options"]
        assert '"status": "completed"' in options
        assert '"conclusion": "success"' in options
        assert all(quote in content for quote in options)
        return {"findings": [{"requirement_id": "ci", "status": "satisfied", "explanation": "CI завершён успешно.",
            "evidence_ids": ["e1"], "basis": "supports_requirement", "recommendation": None,
            "citations": [{"evidence_id": "e1", "quote": '"status": "completed"'},
                          {"evidence_id": "e1", "quote": '"conclusion": "success"'}]}]}

    findings, history = await review._assess(explicit_fake_ask, requirements, evidence, {}, [], [])
    assert len(calls) == 1 and len(history) == 1
    assert findings[0].status == "satisfied"
    assert requirements[0].ambiguity is None


def test_json_quote_options_preserve_escaping_and_never_invent_or_reformat_tool_content():
    content = r'{"name":"CI \\\"quoted\\\"","conclusion" : "success","bounded_listing":true}'
    source = Evidence(id="e1", tool="ci", source_url=REPO + "/actions", summary=content)
    rendered = review._model_evidence([source])[0]
    assert rendered["content"] == content
    assert '"conclusion" : "success"' in rendered["quote_options"]
    assert '"conclusion": "success"' not in rendered["quote_options"]
    assert all(quote in content for quote in rendered["quote_options"])
    source.summary = "partial invalid JSON {"
    assert review._model_evidence([source])[0]["quote_options"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("citation_id,quote,cleared", [
    ("e1", '"conclusion": "success"', True),
    ("unknown", '"conclusion": "success"', False),
    ("e1", '"conclusion":"success"', False),
])
async def test_contextual_ambiguity_needs_real_sources_and_never_replaces_author_criteria(monkeypatch, citation_id, quote, cleared):
    from campuspulse.models import Requirement, SourceReference

    requirements = [Requirement(id="ci", title="CI", description="CI must finish successfully.",
        source=SourceReference(page=1, quote="CI must finish successfully."), ambiguity="Which CI provider?"),
        Requirement(id="fast", title="Performance", description="Make it fast.",
        source=SourceReference(page=2, quote="Make it fast."), ambiguity="No latency threshold.")]
    evidence = [Evidence(id="e1", tool="ci", source_url=REPO + "/actions", summary='{"conclusion": "success"}')]
    response_body = {"resolutions": [
        {"requirement_id": "ci", "resolution": "resolved_by_observed_context", "explanation": "Provider outcome is available.",
         "evidence_ids": [citation_id], "citations": [{"evidence_id": citation_id, "quote": quote}]},
        {"requirement_id": "fast", "resolution": "needs_author_input", "explanation": "Author must specify latency.",
         "evidence_ids": [], "citations": []}]}
    observed = []

    def explicit_fake_post(endpoint, **kwargs):
        assert len(kwargs["json"]["message"]) <= 4000
        observed.append(kwargs["json"])
        return response({"output": json.dumps(response_body), "steps": []})

    monkeypatch.setattr(review.requests, "post", explicit_fake_post)
    client = review.GatewayJSON(SETTINGS, [])

    async def ask(phase, instruction, data, schema):
        return client.ask(phase, instruction, data, schema)

    steps, warnings = [], []
    result = await review._resolve_context_ambiguities(ask, requirements, evidence, {}, steps, warnings)
    assert len(observed) == 1
    assert (result[0].ambiguity is None) is cleared
    assert requirements[0].ambiguity == "Which CI provider?"  # Original source metadata is retained in the receipt.
    assert result[1].ambiguity == "No latency threshold."
    assert result[0].source == requirements[0].source
    if cleared:
        assert steps[0].result["previous_ambiguity"] == "Which CI provider?"
        assert steps[0].result["citations"][0]["quote"] in evidence[0].summary


@pytest.mark.asyncio
async def test_duplicate_context_resolutions_cannot_clear_any_ambiguity():
    from campuspulse.models import Requirement, SourceReference

    requirements = [Requirement(id="r1", title="Requirement", description="Text",
        source=SourceReference(page=1, quote="Text"), ambiguity="Missing author criterion.")]
    item = {"requirement_id": "r1", "resolution": "resolved_by_observed_context", "explanation": "Claim",
            "evidence_ids": ["e1"], "citations": [{"evidence_id": "e1", "quote": "Text"}]}

    async def ask(*args):
        return {"resolutions": [item, item]}

    warnings = []
    result = await review._resolve_context_ambiguities(ask, requirements,
        [Evidence(id="e1", tool="file", source_url=REPO + "/README.md", summary="Text")], {}, [], warnings)
    assert result == requirements
    assert warnings


@pytest.mark.parametrize("resolution_quote,expected_status", [
    ('"status": "completed"', "satisfied"), ('invented provider evidence', "unverified")])
def test_full_ci_review_revisits_context_ambiguity_and_still_requires_grounded_final_status(monkeypatch, resolution_quote, expected_status):
    text = "The latest continuous integration run must finish successfully."
    content = '{"status": "completed", "conclusion": "success"}'
    evidence = Evidence(id="e1", tool="ci", source_url=REPO + "/actions", summary=content)
    outputs = iter([
        {"requirements": [{"id": "ci", "title": "CI", "description": text, "source": {"page": 1, "quote": text},
                           "ambiguity": "CI provider is not named."}]},
        {"calls": [{"name": "read_ci", "args": {}}], "summary": "Read actual CI"},
        {"calls": [], "summary": "Enough evidence"},
        {"resolutions": [{"requirement_id": "ci", "resolution": "resolved_by_observed_context", "explanation": "CI outcome is recorded.",
                          "evidence_ids": ["e1"], "citations": [{"evidence_id": "e1", "quote": resolution_quote}]}]},
        {"findings": [{"requirement_id": "ci", "status": "satisfied", "explanation": "CI завершён успешно.",
            "evidence_ids": ["e1"], "basis": "supports_requirement", "recommendation": None,
            "citations": [{"evidence_id": "e1", "quote": '"conclusion": "success"'}]}]}])
    calls = []

    def explicit_fake_post(endpoint, **kwargs):
        calls.append(kwargs["json"])
        assert len(kwargs["json"]["message"]) <= 4000
        return response({"output": json.dumps(next(outputs)), "steps": []})

    class ExplicitFakeMCP:
        revision = SHA

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def collect(self, repository, kind, arguments, **kwargs):
            assert repository.url == REPO and kind == "ci"
            return [evidence]

    monkeypatch.setattr(review.requests, "post", explicit_fake_post)
    monkeypatch.setattr(review, "PublicGitHubGateway", ExplicitFakeMCP)
    result = review.run_gateway_review({"repository_url": REPO},
        [{"name": "assignment.txt", "base64": base64.b64encode(text.encode()).decode()}], SETTINGS)
    analysis = result["deadline"]["analysis"]
    assert len(calls) == 5 and analysis["usage"]["model_calls"] == 5
    assert analysis["findings"][0]["status"] == expected_status
    assert (analysis["requirements"][0]["ambiguity"] is None) is (expected_status == "satisfied")
    assert analysis["requirements"][0]["source"]["quote"] == text


def test_no_invalid_empty_requirement_can_unlock_planning():
    with pytest.raises(review.GatewayReviewError):
        review._requirements(parse_document(TEXT.encode(), "assignment.txt"), {"requirements": [{"id": "x"}]})

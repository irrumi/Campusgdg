"""Real parsing/planning with explicit offline fakes only at external-service boundaries."""
import base64
from copy import deepcopy
from datetime import datetime
import io
import json
from types import SimpleNamespace

import pytest
from google.genai import types
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from agent.agent import CampusAgent
from agent.attachments import attachment_document, chat_attachments, decode_attachments
from agent.deadline import build_plan, calendar_revision, review_work, run_deadline
from campuspulse.config import Settings
from campuspulse.models import AgentStep, AnalysisResult, DeadlineRequest, Evidence, Finding, Requirement, SourceReference
from campus_chat_ui.deadline_mode import _accept_response
from campus_chat_ui.deadline_state import apply_plan, plan_events, undo_plan
from campus_chat_ui.tool_activity import public_steps

REPO = "https://github.com/example/student"
SHA = "a" * 64


def upload(text=b"Provide README. Pass CI. Expose API.", name="assignment.txt"):
    return {"name": name, "base64": base64.b64encode(text).decode(), "text_preview": "WRONG PREVIEW"}


def pdf_bytes():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 200 Td (Actual PDF requirement) Tj ET")
    page[NameObject("/Contents")] = stream
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def analysis(statuses=("satisfied", "not_satisfied", "unverified")):
    return AnalysisResult(requirements=[Requirement(id=f"r{i}", title=title, description=title,
        source=SourceReference(page=1, quote=title)) for i, title in enumerate(["Provide README.", "Pass CI.", "Expose API."])],
        findings=[Finding(requirement_id=f"r{i}", status=status, explanation="Explicit offline test finding.",
                          evidence_ids=["e1"] if status != "unverified" else []) for i, status in enumerate(statuses)],
        evidence=[Evidence(id="e1", tool="ci", source_url=REPO + "/actions/runs/1", summary="Explicit test CI result.")],
        steps=[AgentStep(kind="tool", tool="ci", message="Read CI.", arguments={"per_page": 1},
                         result={"evidence": [{"source_url": REPO + "/actions/runs/1"}]})],
        model="explicit-test-model", live=False)


def planning(**updates):
    result = analysis()
    work = review_work(result, SHA)
    payload = {"action": "plan", "repository_url": REPO, "document_sha256": SHA, "analysis": result,
               "estimates": {work[1]["id"]: 180, work[2]["id"]: 60},
               "start_at": "2026-10-22T09:00:00+05:00", "due_at": "2026-10-24T18:00:00+05:00",
               "day_start": "09:00", "day_end": "18:00", "daily_minutes": 120,
               "calendar_events": [{"id": "lecture", "title": "Lecture", "type": "lecture",
                                    "date": "2026-10-22", "time": "09:00–10:00"}]}
    payload.update(updates)
    return DeadlineRequest.model_validate(payload)


def test_pdf_contents_reach_chat_as_text_with_page_receipt():
    parts, steps = chat_attachments([upload(pdf_bytes(), "assignment.pdf")])
    assert "Actual PDF requirement" in parts[0]["text"]
    assert "WRONG PREVIEW" not in parts[0]["text"]
    assert "page/paragraph 1" in parts[0]["text"]
    assert steps[0]["tool"] == "read_uploaded_file"


@pytest.mark.parametrize("file", [upload(name="../x.txt"), {"name": "a.txt", "base64": "not base64"}, upload(b""),
                                     upload(name="x.exe")])
def test_invalid_attachments_fail_before_any_model_call(file):
    with pytest.raises(ValueError):
        chat_attachments([file])


def test_complete_text_is_transferred_without_preview_truncation():
    raw = b"a" * 6000 + b" FINAL REQUIREMENT"
    parts, _ = chat_attachments([upload(raw)])
    assert "FINAL REQUIREMENT" in parts[0]["text"]


def test_agent_upload_tool_roundtrip_filters_private_reasoning():
    received = []
    responses = iter([
        types.Content(role="model", parts=[types.Part(text="PRIVATE INTERNAL TEXT", thought=True),
            types.Part(function_call=types.FunctionCall(name="test_tool", args={"value": 3}))]),
        types.Content(role="model", parts=[types.Part(text="PRIVATE FINAL THOUGHT", thought=True), types.Part(text="Public answer")])])

    def generate(**kwargs):
        received.append(deepcopy(kwargs["contents"]))
        return SimpleNamespace(candidates=[SimpleNamespace(content=next(responses))])

    agent = CampusAgent(project="explicit-test")
    agent._types = types
    agent._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    agent._config = None
    agent._tools = {"test_tool": lambda value: {"value": value * 2}}
    result = agent.query("Read the assignment", files=[upload(pdf_bytes(), "assignment.pdf")])
    assert result["output"] == "Public answer"
    assert "PRIVATE" not in json.dumps(result)
    assert "Actual PDF requirement" in received[0][0].parts[1].text
    assert result["steps"][-1]["result"] == {"value": 6}


def test_real_guardian_bridge_receives_document_and_normalized_repository():
    received = []

    class ExplicitFakeGuardian:
        def __init__(self, settings):
            assert not settings.allow_github_writes

        async def analyze(self, document, repository):
            received.append((document, repository))
            return analysis()

    settings = Settings(_env_file=None, gemini_api_key=None, github_token=None)
    response = run_deadline({"action": "analyze", "repository_url": REPO}, [upload()], settings,
                            agent_factory=ExplicitFakeGuardian)
    document = attachment_document(decode_attachments([upload()])[0])
    assert received[0][0] == document
    assert received[0][1].url == REPO
    assert response["deadline"]["document_sha256"] == document.sha256
    assert response["deadline"]["analysis"]["live"] is False
    receipt = next(s for s in response["steps"] if s.get("tool") == "github_mcp.ci")
    assert receipt["args"] == {"per_page": 1}
    assert receipt["result"]["evidence"][0]["source_url"].endswith("/actions/runs/1")


def test_no_overlap_with_calendar_and_all_blocks_before_deadline():
    request = planning()
    plan = build_plan(request)
    assert plan["required_minutes"] == 240
    assert plan["deficit_minutes"] == 0
    for block in plan["blocks"]:
        assert datetime.fromisoformat(block["end_at"]) <= datetime.fromisoformat(request.due_at)
        if block["start_at"][:10] == "2026-10-22":
            assert block["start_at"][11:16] >= "10:00"
    for a, b in zip(plan["blocks"], plan["blocks"][1:]):
        assert a["end_at"] <= b["start_at"]
    assert sum(b["minutes"] for b in plan["blocks"]) == 240


def test_overload_is_explicit_and_never_spills_past_deadline():
    plan = build_plan(planning(due_at="2026-10-22T10:30:00+05:00"))
    assert plan["available_minutes"] == 30
    assert plan["deficit_minutes"] == 210
    assert sum(w["minutes"] for w in plan["unscheduled"]) == 210
    assert all(b["end_at"] <= "2026-10-22T10:30:00+05:00" for b in plan["blocks"])


def test_untimed_event_blocks_the_day_with_an_explanation():
    plan = build_plan(planning(calendar_events=[{"date": "2026-10-22", "title": "Lecture", "time": "10:00"}]))
    assert all(b["start_at"][:10] != "2026-10-22" for b in plan["blocks"])
    assert "нет полного интервала" in plan["warnings"][0]


def test_replan_preserves_feasible_blocks_and_moves_only_conflicting_work():
    request = planning()
    before = build_plan(request)
    events, _ = apply_plan(request.calendar_events, before)
    after = build_plan(planning(calendar_events=events, previous_plan=before, day_limits={"2026-10-23": 60}))
    assert after["deficit_minutes"] == 0
    assert any(c["kind"] == "moved" for c in after["changes"])
    assert [b for b in after["blocks"] if b["start_at"][:10] == "2026-10-22"] == [
        b for b in before["blocks"] if b["start_at"][:10] == "2026-10-22"]
    assert sum(b["minutes"] for b in after["blocks"] if b["start_at"][:10] == "2026-10-23") <= 60
    final, _ = apply_plan(events, after)
    assert final[0] == request.calendar_events[0]
    assert len({e["id"] for e in final}) == len(final)
    assert len(final) == 1 + len(after["blocks"])


def test_resolved_requirement_is_removed_but_unverified_work_remains():
    before = build_plan(planning())
    updated = analysis(("satisfied", "satisfied", "unverified"))
    updated.requirements[1].id = "new-extraction-id"
    updated.findings[1].requirement_id = "new-extraction-id"
    after = build_plan(planning(analysis=updated, previous_plan=before))
    assert after["required_minutes"] == 60
    assert any(c["kind"] == "removed" for c in after["changes"])
    assert all(b["title"].startswith("Проверить:") for b in after["blocks"])


@pytest.mark.parametrize("update", [{"daily_minutes": -1}, {"daily_minutes": 0, "estimates": {}},
    {"due_at": "2026-10-21T10:00:00+05:00"}, {"due_at": "2027-01-01T10:00:00+05:00"},
    {"due_at": "2026-10-23T10:00:00"}, {"day_limits": {"2026-10-23": -1}}, {"day_end": "08:00"}])
def test_invalid_constraints_are_rejected(update):
    with pytest.raises(ValueError):
        build_plan(planning(**update))


def test_zero_availability_reports_all_work_unscheduled():
    plan = build_plan(planning(daily_minutes=0))
    assert plan["blocks"] == []
    assert plan["deficit_minutes"] == plan["required_minutes"] == 240


def test_timezone_conversion_and_past_time_rounding():
    plan = build_plan(planning(start_at="2026-10-22T05:01:00+00:00"))
    assert all(b["start_at"] >= "2026-10-22T10:15:00+05:00" for b in plan["blocks"])


def test_stale_calendar_cannot_be_overwritten_and_undo_is_scoped():
    request = planning()
    plan = build_plan(request)
    applied, undo = apply_plan(request.calendar_events, plan)
    assert undo_plan(applied, undo) == request.calendar_events
    with pytest.raises(ValueError, match="изменился"):
        apply_plan(request.calendar_events + [{"id": "new", "date": "2026-10-24"}], plan)
    with pytest.raises(ValueError, match="изменился"):
        undo_plan(applied + [{"id": "new"}], undo)
    assert calendar_revision(list(reversed(applied))) == calendar_revision(applied)


def test_malformed_second_block_is_rejected_atomically():
    request = planning()
    plan = build_plan(request)
    plan["blocks"][1]["end_at"] = "invalid"
    original = deepcopy(request.calendar_events)
    with pytest.raises(ValueError):
        apply_plan(request.calendar_events, plan)
    assert request.calendar_events == original


def test_ui_rejects_unbound_or_invented_plans():
    request = planning()
    response = {"deadline": {"action": "plan", "plan": build_plan(request)}}
    assert _accept_response(response, request.model_dump(mode="json"))["plan"]["id"]
    response["deadline"]["plan"]["available_minutes"] = 999999
    with pytest.raises(ValueError, match="не соответствует"):
        _accept_response(response, request.model_dump(mode="json"))


def test_tool_activity_excludes_private_reasoning_and_credentials():
    visible = public_steps([{"type": "thought", "content": "private"},
        {"type": "tool_call", "tool": "file", "args": {"base64": "hidden", "token": "hidden", "path": "README.md"},
         "result": "test-only-key"}], secrets=("test-only-key",))
    assert len(visible) == 1
    assert visible[0]["args"] == {"path": "README.md"}
    assert "test-only-key" not in json.dumps(visible)
    assert "private" not in json.dumps(visible)


def test_plan_events_preserves_explicit_local_ownership():
    plan = build_plan(planning())
    assert all(e["_deadline_plan"] and e["deadline_id"] == plan["deadline_id"] for e in plan_events(plan))


@pytest.mark.parametrize("daily", [30, 60, 180, 240])
def test_busy_intervals_daily_caps_and_replan_conserve_all_estimated_minutes(daily):
    events = [{"id": str(i), "type": "lecture", "title": "Explicit interval", "date": day, "time": span}
              for i, (day, span) in enumerate([
                  ("2026-10-22", "09:00–11:00"), ("2026-10-22", "10:00–12:00"),
                  ("2026-10-23", "11:00–15:00"), ("2026-10-24", "16:00–18:00")])]
    request = planning(calendar_events=events, daily_minutes=daily)
    first = build_plan(request)
    next_request = request.model_copy(update={"previous_plan": first, "day_limits": {"2026-10-23": 30}})
    for plan, limits in [(first, {}), (build_plan(next_request), {"2026-10-23": 30})]:
        by_day = {}
        assert plan["scheduled_minutes"] + plan["deficit_minutes"] == plan["required_minutes"] == 240
        for block in plan["blocks"]:
            a, b = datetime.fromisoformat(block["start_at"]), datetime.fromisoformat(block["end_at"])
            key = a.date().isoformat()
            by_day[key] = by_day.get(key, 0) + block["minutes"]
            assert 0 < block["minutes"] <= 90
            assert b <= datetime.fromisoformat(request.due_at)
            for event in events:
                if event["date"] == key:
                    left, right = event["time"].split("–")
                    assert b.strftime("%H:%M") <= left or a.strftime("%H:%M") >= right
        assert all(minutes <= min(daily, limits.get(day, daily)) for day, minutes in by_day.items())


@pytest.mark.parametrize("capacity", [0, -1, float("nan"), float("inf")])
def test_legacy_planner_cannot_hang_on_invalid_capacity(capacity):
    from agent.tools import plan_study_week

    with pytest.raises(ValueError):
        plan_study_week([{"title": "Explicit test task", "hours": 1}], hours_per_day=capacity)

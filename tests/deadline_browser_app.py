"""Explicit OFFLINE browser-test harness. Never use as a live-service demo.

Run with Streamlit solely to verify rendering and interactions without credentials.
All service responses are test fakes; parsing, planning and calendar application are real.
"""
from datetime import datetime, timedelta
from pathlib import Path
import runpy
import sys
from unittest.mock import Mock, patch

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agent.deadline import ZONE, build_plan, review_work  # noqa: E402
from campuspulse.models import DeadlineRequest  # noqa: E402
from campuspulse.parsing import parse_document  # noqa: E402
from campus_chat_ui.deadline_mode import request_signature  # noqa: E402
from test_deadline import analysis  # noqa: E402

st.warning("ТЕСТОВЫЙ СТЕНД: данные и ответы сервисов — явные фейки. Реальные GCP/GitHub не вызываются.")

if "deadline_browser_seeded" not in st.session_state:
    now = datetime.now(ZONE)
    doc = parse_document(b"Provide README. Pass CI. Expose API.", "assignment.txt")
    result = analysis()
    result.requirements[2].ambiguity = "Уточните, какие данные должен возвращать API."
    result.findings[2].explanation = "The assignment requirement needs clarification."
    result.usage["verification"] = [{"attempt": 1, "checks": [
        {"requirement_id": "r2", "reasons": ["ambiguous_requirement"], "effective_status": "unverified"}]}]
    work = review_work(result, doc.sha256)
    repo = "https://github.com/example/student"
    review = {"action": "analyze", "document_sha256": doc.sha256, "filename": doc.filename,
              "repository_url": repo, "analysis": result.model_dump(mode="json"), "work": work}
    events = [{"id": "test-lecture", "title": "Лекция · тестовые данные", "type": "lecture",
               "date": (now.date() + timedelta(days=1)).isoformat(), "time": "10:00–12:00",
               "course": "Тестовый курс", "location": "Тестовый кампус", "_example": True}]
    estimates = {w["id"]: 180 for w in work if w["status"] != "satisfied"}
    request = DeadlineRequest(action="plan", repository_url=repo, document_sha256=doc.sha256,
        analysis=result, estimates=estimates, start_at=now.isoformat(),
        due_at=f"{(now.date()+timedelta(days=3)).isoformat()}T23:59:00+05:00", calendar_events=events)
    payload = request.model_dump(mode="json")
    st.session_state.deadline_workspace = {"review": review, "plan": build_plan(request), "applied": {},
        "day_limits": {}, "activity": [{"type": "tool_call", "tool": "explicit_test_receipt",
            "status": "success", "args": {"fixture": "offline browser test"}, "result": {"live": False}}],
        "plan_signature": request_signature(payload), "estimates": estimates}
    st.session_state.calendar_events = events
    st.session_state.active_view = "deadline_rescue"
    st.session_state.agent_settings = {"api_url": "https://explicit-offline-test.invalid/api/v1/chat",
        "api_key": "", "agent_id": "explicit-test", "protocol": "Custom Agent REST (JSON)",
        "temperature": 0.3, "timeout": 60, "system_prompt": "", "sync_student_context": True}
    st.session_state.deadline_browser_seeded = True


def explicit_fake_post(url, **kwargs):
    payload = kwargs["json"]["deadline_request"]
    response = Mock(status_code=200)
    response.json.return_value = {"output": "Явный тестовый ответ", "steps": [{"type": "tool_call",
        "tool": "plan_before_deadline", "args": {"test_only": True}, "result": {"live": False}}],
        "deadline": {"action": "plan", "plan": build_plan(DeadlineRequest.model_validate(payload))}}
    return response


exists = Path.exists
with patch.object(Path, "exists", lambda path: False if path.name in {"vertex.config.json", "agent.connection.json"} else exists(path)):
    with patch("requests.post", side_effect=explicit_fake_post):
        runpy.run_path(str(ROOT / "campus_chat_app.py"), run_name="__main__")

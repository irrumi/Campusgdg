"""Explicit offline fixtures for the calendar reply protocol."""
import json

import pytest

from campus_chat_ui.planner_actions import decode_planner_response, validate_additions


@pytest.mark.parametrize("wrapped", [False, True])
def test_vertex_output_can_carry_calendar_actions(wrapped):
    payload = {"response": "Готово", "add_calendar_events": [
        {"title": "Математика", "date": "2026-11-05", "time": "19:00"}], "add_tasks": []}
    reply = json.dumps(payload, ensure_ascii=False)
    if wrapped:
        reply = "```json\n" + reply + "\n```"
    answer, actions = decode_planner_response({"output": reply, "steps": []}, reply)
    events, tasks = validate_additions(actions)
    assert answer == "Готово"
    assert events[0]["date"] == "2026-11-05"
    assert events[0]["time"] == "19:00"
    assert tasks == []


@pytest.mark.parametrize("bad", [None, "", "2026-02-30", "05.11.2026", 20261105])
def test_bad_dates_are_not_silently_replaced_with_today(bad):
    with pytest.raises(ValueError):
        validate_additions({"add_calendar_events": [{"title": "Урок", "date": bad}]})


@pytest.mark.parametrize("bad", ["25:00", "19:70", [], 19])
def test_invalid_time_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_additions({"add_calendar_events": [{"title": "Урок", "date": "2026-11-05", "time": bad}]})


def test_unspecified_time_is_not_invented():
    events, _ = validate_additions({"add_calendar_events": [{"title": "Урок", "date": "2026-11-05"}]})
    assert events[0]["time"] == ""


def test_conflicting_nested_actions_are_rejected():
    reply = json.dumps({"response": "Готово", "add_tasks": []})
    with pytest.raises(ValueError):
        decode_planner_response({"add_tasks": [{"title": "Other"}]}, reply)

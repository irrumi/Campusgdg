"""Atomic, scoped application of a reviewed plan to the session calendar."""
from copy import deepcopy
from datetime import datetime

from agent.deadline import ZONE, calendar_revision


def plan_events(plan: dict) -> list[dict]:
    owner = plan.get("deadline_id")
    blocks = plan.get("blocks")
    if not isinstance(owner, str) or not owner or not isinstance(blocks, list) or len(blocks) > 2000:
        raise ValueError("Некорректный план: календарь не изменён.")
    events, seen = [], set()
    for block in blocks:
        try:
            a, b = datetime.fromisoformat(block["start_at"]), datetime.fromisoformat(block["end_at"])
            due = datetime.fromisoformat(plan["due_at"])
            if a.tzinfo is None or b.tzinfo is None or due.tzinfo is None:
                raise ValueError
            a, b = a.astimezone(ZONE), b.astimezone(ZONE)
            if not a < b <= due or a.date() != b.date() or not isinstance(block["title"], str) or not block["title"].strip():
                raise ValueError
            identifier = "deadline_" + block["id"]
            if identifier in seen:
                raise ValueError
            seen.add(identifier)
            events.append({"id": identifier, "title": block["title"], "date": a.date().isoformat(),
                           "time": f"{a:%H:%M}–{b:%H:%M}", "course": "До сдачи", "location": "Самостоятельно",
                           "type": "study", "deadline_id": owner, "_deadline_plan": True,
                           "work_id": block["work_id"], "plan_id": plan["id"]})
        except (KeyError, TypeError, ValueError):
            raise ValueError("Некорректный блок плана: календарь не изменён.") from None
    return events


def owned(event, owner):
    return event.get("_deadline_plan") is True and event.get("deadline_id") == owner


def apply_plan(events: list[dict], plan: dict) -> tuple[list[dict], dict]:
    if calendar_revision(events) != plan.get("calendar_revision"):
        raise ValueError("Календарь изменился после расчёта. Пересчитайте план перед применением.")
    additions = plan_events(plan)  # Validate the entire batch before touching any collection.
    owner = plan["deadline_id"]
    result = [deepcopy(e) for e in events if not owned(e, owner)] + additions
    undo = {"owner": owner, "before": [deepcopy(e) for e in events if owned(e, owner)],
            "after_revision": calendar_revision(result)}
    return result, undo


def undo_plan(events: list[dict], undo: dict) -> list[dict]:
    if calendar_revision(events) != undo.get("after_revision"):
        raise ValueError("После применения календарь изменился. Пересчитайте план, чтобы сохранить новые события.")
    return [deepcopy(e) for e in events if not owned(e, undo["owner"])] + deepcopy(undo["before"])

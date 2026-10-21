"""Guardian-to-calendar bridge. Pure bounded planning; no calendar or GitHub writes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from agent.attachments import attachment_document, decode_attachments
from campuspulse.models import AnalysisResult, DeadlineRequest
from campuspulse.security import validate_repository_url

ZONE = ZoneInfo("Asia/Qyzylorda")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def calendar_revision(events: list[dict]) -> str:
    """Order-independent optimistic concurrency token for the local calendar."""
    return digest(sorted((json.dumps(e, sort_keys=True, ensure_ascii=False) for e in events)))


def review_work(analysis: AnalysisResult, sha: str) -> list[dict]:
    findings = {f.requirement_id: f for f in analysis.findings}
    work = []
    for requirement in analysis.requirements:
        finding = findings.get(requirement.id)
        status = finding.status.value if finding else "unverified"
        work.append({"id": digest([sha, requirement.title, requirement.source.model_dump()])[:24],
                     "requirement_id": requirement.id, "title": requirement.title, "status": status,
                     "source": requirement.source.model_dump(),
                     "explanation": finding.explanation if finding else "Результат проверки отсутствует.",
                     "recommendation": finding.recommendation if finding else None,
                     "evidence_ids": finding.evidence_ids if finding else []})
    if len({w["id"] for w in work}) != len(work):
        raise ValueError("Повторяющиеся требования: уточните задание и повторите проверку.")
    return work


def _clock(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Время должно быть в формате ЧЧ:ММ.")
    return int(value[:2]) * 60 + int(value[3:])


def _instant(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(ZONE)
    except (ValueError, TypeError):
        raise ValueError("Укажите дату, время и часовой пояс начала и дедлайна.") from None


def _subtract(windows: list[tuple[int, int]], busy: tuple[int, int]) -> list[tuple[int, int]]:
    output = []
    for start, end in windows:
        if busy[1] <= start or busy[0] >= end:
            output.append((start, end))
        else:
            if start < busy[0]:
                output.append((start, busy[0]))
            if busy[1] < end:
                output.append((busy[1], end))
    return output


def _slots(request: DeadlineRequest, deadline_id: str) -> tuple[list[dict], list[str]]:
    start, due = _instant(request.start_at), _instant(request.due_at)
    if not start < due or due - start > timedelta(days=31):
        raise ValueError("Дедлайн должен быть позже начала, в пределах 31 дня.")
    begin, finish = _clock(request.day_start), _clock(request.day_end)
    if begin >= finish:
        raise ValueError("Конец учебного окна должен быть позже начала.")
    for day, limit in request.day_limits.items():
        try:
            date.fromisoformat(day)
        except ValueError:
            raise ValueError("Некорректная дата ограничения.") from None
        if type(limit) is not int or not 0 <= limit <= 1440:
            raise ValueError("Дневное ограничение должно быть от 0 до 1440 минут.")
    busy_by_day, blocked_days, warnings = {}, set(), []
    for event in request.calendar_events:
        if (event.get("deadline_id") == deadline_id and event.get("_deadline_plan") is True
                or event.get("type") == "deadline"):
            continue
        day = event.get("date")
        try:
            event_day = date.fromisoformat(day)
        except (ValueError, TypeError):
            raise ValueError("В календаре есть событие без корректной даты. Исправьте его перед планированием.") from None
        if not start.date() <= event_day <= due.date():
            continue
        times = re.split(r"\s*[-–—]\s*", str(event.get("time", "")))
        if len(times) != 2:
            blocked_days.add(day)
            warnings.append(f"{day}: у события «{str(event.get('title', 'Без названия'))[:160]}» нет полного интервала. "
                            "Этот день исключён; задайте время начала и окончания.")
            continue
        left, right = _clock(times[0]), _clock(times[1])
        if left >= right:
            raise ValueError("В календаре есть событие с некорректным интервалом времени.")
        busy_by_day.setdefault(day, []).append((left, right))
    slots = []
    for offset in range((due.date() - start.date()).days + 1):
        day = start.date() + timedelta(days=offset)
        key = day.isoformat()
        if key in blocked_days:
            continue
        # Round the first start upwards to a quarter hour, never schedule in the past.
        left = max(begin, ((start.hour * 60 + start.minute + (1 if start.second or start.microsecond else 0) + 14) // 15) * 15
                   if day == start.date() else 0)
        right = min(finish, due.hour * 60 + due.minute if day == due.date() else 1440)
        windows = [(left, right)] if left < right else []
        for busy in sorted(busy_by_day.get(key, [])):
            windows = _subtract(windows, busy)
        cap = min(request.daily_minutes, request.day_limits.get(key, request.daily_minutes))
        # Capacity is attached to the day, not the earliest window: the scheduler
        # can therefore preserve previously applied blocks later in that day.
        for left, right in windows:
            slots.append({"date": key, "start": left, "end": right, "cap": cap})
    return slots, list(dict.fromkeys(warnings))


def _stamp(day: str, minute: int) -> str:
    return datetime.combine(date.fromisoformat(day), time(minute // 60, minute % 60), ZONE).isoformat()


def _changes(previous: dict | None, blocks: list[dict]) -> list[dict]:
    before, after = {}, {}
    for source, target in (((previous or {}).get("blocks", []), before), (blocks, after)):
        if not isinstance(source, list) or len(source) > 2000:
            raise ValueError("Некорректный предыдущий план.")
        for block in source:
            if not isinstance(block, dict) or not all(isinstance(block.get(k), str)
                                                    for k in ("work_id", "start_at", "end_at", "title")):
                raise ValueError("Некорректный блок предыдущего плана.")
            target.setdefault(block["work_id"], []).append(block)
    changes = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key, []), after.get(key, [])
        def intervals(records):
            return sorted((b["start_at"], b["end_at"]) for b in records)
        kind = "added" if not old else "removed" if not new else "unchanged" if intervals(old) == intervals(new) else "moved"
        changes.append({"work_id": key, "title": (new or old)[0]["title"], "kind": kind,
                        "before": old, "after": new})
    return changes


def build_plan(request: DeadlineRequest) -> dict:
    """Allocate estimated work before its deadline, preserving feasible old blocks."""
    if request.analysis is None or not re.fullmatch(r"[a-f0-9]{64}", request.document_sha256):
        raise ValueError("Сначала проверьте задание и репозиторий.")
    repository = validate_repository_url(request.repository_url)
    deadline_id = digest([request.document_sha256, repository.url])[:24]
    previous = request.previous_plan
    if previous and previous.get("deadline_id") != deadline_id:
        raise ValueError("Предыдущий план относится к другому заданию.")
    work = review_work(request.analysis, request.document_sha256)
    pending = [w for w in work if w["status"] != "satisfied"]
    estimates = request.estimates
    if any(type(v) is not int or not 0 <= v <= 10_080 for v in estimates.values()):
        raise ValueError("Оценки работы должны быть целым числом минут от 0 до 10080.")
    missing = [w["id"] for w in pending if not estimates.get(w["id"])]
    if missing:
        raise ValueError("Оцените время для каждого неподтверждённого требования; ноль означает, что оценка ещё не задана.")
    slots, warnings = _slots(request, deadline_id)
    # Merge all free windows with a per-day capacity for honest available-time totals.
    capacities = {s["date"]: s["cap"] for s in slots}
    free_by_day = {day: sum(s["end"] - s["start"] for s in slots if s["date"] == day) for day in capacities}
    available = sum(min(capacities[d], free_by_day[d]) for d in capacities)
    remaining = {w["id"]: estimates[w["id"]] for w in pending}
    titles = {w["id"]: ("Проверить: " if w["status"] == "unverified" else "") + w["title"] for w in pending}
    used, blocks = {}, []

    def reserve(key: str, day: str, left: int, right: int) -> bool:
        duration = right - left
        if (key not in remaining or duration <= 0 or remaining[key] < duration
                or used.get(day, 0) + duration > capacities.get(day, 0)):
            return False
        index = next((i for i, s in enumerate(slots) if s["date"] == day and s["start"] <= left < right <= s["end"]), None)
        if index is None:
            return False
        slot = slots.pop(index)
        for a, b in _subtract([(slot["start"], slot["end"])], (left, right)):
            slots.append({**slot, "start": a, "end": b})
        remaining[key] -= duration
        used[day] = used.get(day, 0) + duration
        start_at, end_at = _stamp(day, left), _stamp(day, right)
        blocks.append({"id": digest([deadline_id, key, start_at, end_at])[:24], "work_id": key,
                       "title": titles[key], "start_at": start_at, "end_at": end_at, "minutes": duration})
        return True

    if previous:
        old_blocks = previous.get("blocks", [])
        if not isinstance(old_blocks, list) or len(old_blocks) > 2000:
            raise ValueError("Некорректный предыдущий план.")
        for old in old_blocks:
            try:
                a, b = _instant(old["start_at"]), _instant(old["end_at"])
                if a.date() == b.date():
                    reserve(old["work_id"], a.date().isoformat(), a.hour * 60 + a.minute, b.hour * 60 + b.minute)
            except (KeyError, TypeError):
                raise ValueError("Некорректный предыдущий план.") from None
    # Stable order: proven gaps first, then verification tasks. Never drop a requirement.
    for item in sorted(pending, key=lambda w: (w["status"] == "unverified", w["id"])):
        key = item["id"]
        for slot in sorted(list(slots), key=lambda s: (s["date"], s["start"])):
            minutes = min(remaining[key], slot["end"] - slot["start"],
                          capacities[slot["date"]] - used.get(slot["date"], 0), 90)
            cursor = slot["start"]
            while minutes > 0:
                if not reserve(key, slot["date"], cursor, cursor + minutes):
                    break
                cursor += minutes
                minutes = min(remaining[key], slot["end"] - cursor,
                              capacities[slot["date"]] - used.get(slot["date"], 0), 90)
    blocks.sort(key=lambda b: (b["start_at"], b["work_id"]))
    required = sum(estimates[w["id"]] for w in pending)
    unscheduled = [{"work_id": w["id"], "title": titles[w["id"]], "minutes": remaining[w["id"]]}
                   for w in pending if remaining[w["id"]] > 0]
    plan = {"deadline_id": deadline_id, "due_at": request.due_at, "start_at": request.start_at,
            "calendar_revision": calendar_revision(request.calendar_events), "blocks": blocks,
            "required_minutes": required, "available_minutes": available,
            "scheduled_minutes": sum(b["minutes"] for b in blocks),
            "deficit_minutes": sum(w["minutes"] for w in unscheduled), "unscheduled": unscheduled,
            "slack_minutes": available - required, "warnings": warnings,
            "changes": _changes(previous, blocks), "estimate_source": "student"}
    plan["id"] = digest(plan)
    return plan


def run_deadline(payload: dict, files: list[dict] | None, settings, *, agent_factory=None) -> dict:
    request = DeadlineRequest.model_validate(payload)
    repository = validate_repository_url(request.repository_url)
    if request.action == "analyze":
        attachments = decode_attachments(files)
        if len(attachments) != 1 or attachments[0]["suffix"] not in {".pdf", ".docx", ".txt", ".md"}:
            raise ValueError("Выберите один файл задания: PDF, DOCX, TXT или MD.")
        document = attachment_document(attachments[0])
        if agent_factory is None:
            from campuspulse.agent import GuardianAgent
            agent_factory = GuardianAgent
        async def bounded_analysis():
            return await asyncio.wait_for(agent_factory(settings).analyze(document, repository), timeout=225)

        analysis = asyncio.run(bounded_analysis())
        analysis = AnalysisResult.model_validate(analysis)
        steps = [{"type": "tool_call", "tool": "read_uploaded_file", "status": "success",
                  "args": {"filename": document.filename},
                  "result": {"pages_or_paragraphs": len(document.pages), "sha256": document.sha256}}]
        for step in analysis.steps:
            steps.append({"type": "tool_call" if step.tool else "decision",
                          "tool": "github_mcp." + step.tool if step.tool else None,
                          "status": "error" if step.kind == "error" else "success",
                          "summary": step.message, "args": step.arguments, "result": step.result,
                          "timestamp": step.timestamp.isoformat()})
        return {"output": "Проверка завершена. Оцените оставшуюся работу и постройте план.", "steps": steps,
                "deadline": {"action": "analyze", "document_sha256": document.sha256,
                             "filename": document.filename, "repository_url": repository.url,
                             "analysis": analysis.model_dump(mode="json"),
                             "work": review_work(analysis, document.sha256)}}
    from agent.tools import plan_before_deadline

    plan = plan_before_deadline(request.model_dump(mode="json"))
    deficit = plan["deficit_minutes"]
    return {"output": f"Не помещается до дедлайна: {deficit} мин." if deficit else "Работа помещается до дедлайна по вашим оценкам.",
            "steps": [{"type": "tool_call", "tool": "plan_before_deadline", "status": "success",
                       "args": {"due_at": request.due_at, "daily_minutes": request.daily_minutes,
                                "day_limits": request.day_limits, "busy_events": len(request.calendar_events)},
                       "result": {k: plan[k] for k in ("required_minutes", "available_minutes", "deficit_minutes")}}],
            "deadline": {"action": "plan", "plan": plan}}

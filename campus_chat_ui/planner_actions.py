"""Planner response contract shared by requests and local calendar updates."""
import json
import re
from datetime import date


def requests_planner_change(message: str) -> bool:
    text = message.casefold()
    return bool(re.search(r"добав|постав|запланир|запиши|создай|назнач|\badd\b|\bschedule\b", text)
                and re.search(r"урок|занят|лекци|встреч|календар|расписан|событ|задач|дедлайн|lesson|event|task|meeting", text))


def planner_contract(today: str) -> str:
    return (
        "\n\n[CampusGDG response contract]\n"
        f"Current student date: {today}; timezone Asia/Qyzylorda (UTC+5). "
        "Return ONLY one JSON object with response (your answer in the user's language), "
        "add_calendar_events (array), and add_tasks (array). For an explicit request to add or schedule "
        "a lesson/event, include its title, date (YYYY-MM-DD), time (HH:MM or HH:MM - HH:MM), "
        "course, location and type='study' in add_calendar_events. Resolve relative dates using the "
        "student date above. If a date or the intended event is unclear, ask a question and return "
        "empty arrays. Never invent a time: use an empty time when it was not specified. "
        "Tasks use title, due_date (YYYY-MM-DD), course and priority. "
        "Only include additions explicitly requested by the user, never examples, suggestions or "
        "items already present in the student context. The application saves these arrays locally; "
        "plain text cannot change the calendar. Do not claim anything was saved unless its item "
        "is included. For ordinary questions return your answer in response and empty arrays."
    )


def decode_planner_response(data: dict, reply: str) -> tuple[str, dict]:
    """Support direct actions and JSON inside Vertex output/OpenAI message content."""
    text = reply.strip()
    if text.startswith("```json") or text.startswith("```\n"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    if text.startswith("{"):
        try:
            nested = json.loads(text)
        except ValueError:
            raise ValueError("Агент вернул повреждённые данные события. Календарь и задачи не изменены.") from None
        if not isinstance(nested, dict):
            raise ValueError("Некорректный ответ агента. Календарь и задачи не изменены.")
        answer = next((nested[key] for key in ("response", "output", "reply", "answer", "text")
                       if isinstance(nested.get(key), str) and nested[key].strip()), None)
        if answer is None:
            raise ValueError("В структурированном ответе нет текста. Календарь и задачи не изменены.")
        actions = dict(data)
        for key in ("add_calendar_events", "add_tasks"):
            if key in nested:
                if key in data and data[key] != nested[key]:
                    raise ValueError("Агент вернул противоречивые действия. Календарь и задачи не изменены.")
                actions[key] = nested[key]
        return answer, actions
    return reply, data


def validate_additions(data: dict) -> tuple[list[dict], list[dict]]:
    """Validate the complete batch before mutating either planner collection."""
    result = []
    for key, date_key in (("add_calendar_events", "date"), ("add_tasks", "due_date")):
        records = data.get(key, [])
        if not isinstance(records, list) or len(records) > 100:
            raise ValueError("Некорректный список действий. Календарь и задачи не изменены.")
        normalized = []
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("title"), str) or not record["title"].strip():
                raise ValueError("У события или задачи нет названия. Календарь и задачи не изменены.")
            value = record.get(date_key)
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("Агент не указал корректную дату YYYY-MM-DD. Календарь и задачи не изменены.") from None
            fields = {"title": record["title"].strip(), date_key: value}
            defaults = ({"course": "Общее", "time": "", "location": "Онлайн", "type": "study"}
                        if key == "add_calendar_events" else {"course": "Общее", "priority": "Средний"})
            for field, default in defaults.items():
                supplied = record.get(field, default)
                if not isinstance(supplied, str):
                    raise ValueError("Некорректные поля события. Календарь и задачи не изменены.")
                fields[field] = supplied.strip()
            if key == "add_calendar_events" and fields["time"]:
                if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?:\s*[-–—]\s*(?:[01]\d|2[0-3]):[0-5]\d)?", fields["time"]):
                    raise ValueError("Некорректное время занятия. Календарь и задачи не изменены.")
            normalized.append(fields)
        result.append(normalized)
    return result[0], result[1]

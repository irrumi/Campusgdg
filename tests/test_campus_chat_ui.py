"""Streamlit state and API regressions; all service responses here are explicit fakes."""

from copy import deepcopy
from pathlib import Path
import ast
import base64
import json
from types import SimpleNamespace
from datetime import date, datetime
import uuid
from unittest.mock import Mock, patch

import pytest
import requests
from streamlit.testing.v1 import AppTest
from streamlit.proto.TextInput_pb2 import TextInput as InputProto


@pytest.fixture(autouse=True)
def isolate_project_connection(monkeypatch):
    """UI tests must neither read local connection files nor inherit real keys."""
    exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: False if path.name in ("vertex.config.json", "agent.connection.json") else exists(path),
    )
    monkeypatch.delenv("AGENT_API_URL", raising=False)
    monkeypatch.delenv("AGENT_API_KEY", raising=False)


def app(settings_dialog=False):
    source_path = Path(__file__).resolve().parents[1] / "campus_chat_app.py"
    if settings_dialog:
        # AppTest reruns the full script rather than a browser dialog fragment.
        # Reopen the real dialog on each harness run to exercise its form state.
        source = source_path.read_text(encoding="utf-8")
        source = source.replace("Path(__file__)", f"Path({str(source_path)!r})")
        source = source.rsplit("render_shell()", 1)[0] + "open_settings_dialog()\n"
        instance = AppTest.from_string(source, default_timeout=30)
    else:
        instance = AppTest.from_file(str(source_path), default_timeout=30)
    instance.run()
    assert not instance.exception
    return instance


def button(instance, label):
    return next(item for item in instance.button if item.label == label)


def chat(instance):
    state = instance.session_state
    return state.chats[state.current_chat_id]


def configure(instance):
    settings = deepcopy(instance.session_state.agent_settings)
    settings.update(api_url="https://agent.example.test/chat", api_key="test-only-key")
    instance.session_state.agent_settings = settings


def queue(instance, text="Проверь расписание"):
    current = chat(instance)
    current["messages"].append({"role": "user", "content": text})
    current["pending_request"] = {"prompt": text, "status": "queued"}
    instance.session_state.active_view = "💬 Чат с Агентом"
    instance.session_state.next_view = "💬 Чат с Агентом"


def test_first_run_has_no_fabricated_data_or_responses():
    instance = app()
    assert chat(instance)["messages"] == []
    assert instance.session_state.student_tasks == []
    assert instance.session_state.calendar_events == []


def test_examples_are_explicit_and_never_insert_agent_messages():
    instance = app()
    button(instance, "Добавить учебные примеры").click().run()
    assert not instance.exception
    assert all(item["_example"] for item in instance.session_state.student_tasks)
    assert all(item["_example"] for item in instance.session_state.calendar_events)
    assert chat(instance)["messages"] == []
    assert any("Учебные примеры" in item.value for item in instance.info)


def test_navigation_preserves_settings_messages_and_user_data():
    instance = app()
    configure(instance)
    settings = deepcopy(instance.session_state.agent_settings)
    current_id = instance.session_state.current_chat_id
    chat(instance)["messages"].append({"role": "user", "content": "Мой сохранённый вопрос"})
    task = {
        "id": "owned",
        "title": "Личная задача",
        "course": "Курс",
        "due_date": "2026-10-25",
        "done": False,
        "priority": "Средний",
    }
    instance.session_state.student_tasks = [task]
    button(instance, "Все задачи").click().run()
    assert not instance.exception
    assert instance.session_state.planner_tab == "Задачи"
    instance.sidebar.radio[0].set_value("💬 Чат с Агентом").run()
    assert not instance.exception
    assert instance.session_state.agent_settings == settings
    assert instance.session_state.current_chat_id == current_id
    assert instance.session_state.student_tasks == [task]
    assert chat(instance)["messages"][0]["content"] == "Мой сохранённый вопрос"


def test_missing_url_never_replies_or_changes_calendar():
    instance = app()
    instance.session_state.agent_settings["api_url"] = ""
    queue(instance, "Добавь в календарь консультацию завтра")
    with patch("requests.post") as post:
        instance.run()
    assert not instance.exception
    post.assert_not_called()
    assert len(chat(instance)["messages"]) == 1
    assert instance.session_state.calendar_events == []
    assert chat(instance)["pending_request"]["status"] == "failed"


def test_success_uses_existing_payload_and_applies_explicit_effect_once():
    instance = app()
    configure(instance)
    queue(instance)
    response = Mock(status_code=200)
    response.json.return_value = {
        "response": "Ответ тестового агента",
        "add_calendar_events": [{"title": "Консультация", "date": "2026-10-25"}],
    }
    with patch("requests.post", return_value=response) as post:
        instance.run()
        instance.run()
    assert not instance.exception
    assert post.call_count == 1
    payload = post.call_args.kwargs["json"]
    assert set(payload) == {
        "chat_id",
        "agent_id",
        "message",
        "system_prompt",
        "temperature",
        "student_context",
        "history",
        "tools",
        "files",
    }
    assert payload["history"] == []
    assert chat(instance)["messages"][-1]["content"].endswith("Ответ тестового агента")
    assert len(instance.session_state.calendar_events) == 1
    assert instance.session_state.agent_connection["state"] == "verified"


def test_vertex_json_reply_saves_lesson_and_opens_requested_calendar_date():
    instance = app()
    configure(instance)
    queue(instance, "Поставь урок математики 5 ноября 2026 в 19:00")
    structured = {"response": "Урок подготовлен", "add_calendar_events": [
        {"title": "Математика", "date": "2026-11-05", "time": "19:00"}], "add_tasks": []}
    response = Mock(status_code=200)
    response.json.return_value = {"output": json.dumps(structured, ensure_ascii=False), "steps": []}
    with patch("requests.post", return_value=response) as post:
        instance.run()
        instance.run()
    assert not instance.exception
    assert post.call_count == 1
    assert "add_calendar_events" in post.call_args.kwargs["json"]["message"]
    assert instance.session_state.calendar_events[0]["date"] == "2026-11-05"
    instance.sidebar.radio[0].set_value("calendar_tasks").run()
    assert not instance.exception
    assert instance.session_state.selected_cal_date == date(2026, 11, 5)
    assert instance.text_input(key="calendar_date_text").value == "05.11.2026"
    assert len(instance.session_state.calendar_events) == 1


def test_repeated_response_does_not_duplicate_lesson():
    instance = app()
    configure(instance)
    response = Mock(status_code=200)
    response.json.return_value = {"output": json.dumps({"response": "Готово", "add_calendar_events": [
        {"title": "Урок", "date": "2026-11-05", "time": "19:00"}]})}
    with patch("requests.post", return_value=response):
        for _ in range(2):
            queue(instance, "Добавь урок 5 ноября в 19:00")
            instance.run()
    assert not instance.exception
    assert len(instance.session_state.calendar_events) == 1


def test_bad_second_action_does_not_leave_partially_saved_lesson():
    instance = app()
    configure(instance)
    queue(instance, "Добавь урок")
    response = Mock(status_code=200)
    response.json.return_value = {"output": json.dumps({"response": "Готово", "add_calendar_events": [
        {"title": "Первый", "date": "2026-11-05"}, {"title": "Второй", "date": "2026-02-30"}]})}
    with patch("requests.post", return_value=response):
        instance.run()
    assert not instance.exception
    assert instance.session_state.calendar_events == []
    assert chat(instance)["pending_request"]["status"] == "failed"


def test_text_only_confirmation_does_not_pretend_to_save_lesson():
    instance = app()
    configure(instance)
    queue(instance, "Поставь урок на 5 ноября")
    response = Mock(status_code=200)
    response.json.return_value = {"output": "Да, добавил"}
    with patch("requests.post", return_value=response):
        instance.run()
    assert not instance.exception
    assert instance.session_state.calendar_events == []
    assert "Календарь и задачи не изменены" in chat(instance)["messages"][-1]["content"]


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError()])
def test_uncertain_failure_requires_explicit_retry_and_preserves_prompt(failure):
    instance = app()
    configure(instance)
    queue(instance, "Длинный вопрос " * 200)
    with patch("requests.post", side_effect=failure) as post:
        instance.run()
        instance.run()
    assert not instance.exception
    assert post.call_count == 1
    assert len(chat(instance)["messages"]) == 1
    assert chat(instance)["pending_request"]["uncertain"]
    assert button(instance, "Повторить запрос").disabled


def test_interrupted_post_is_not_automatically_repeated():
    instance = app()
    configure(instance)
    queue(instance)
    chat(instance)["pending_request"]["status"] = "running"
    with patch("requests.post") as post:
        instance.run()
    post.assert_not_called()
    assert chat(instance)["pending_request"]["status"] == "failed"
    assert not instance.exception


def test_bad_response_does_not_apply_actions_or_expose_provider_data():
    instance = app()
    configure(instance)
    queue(instance)
    response = Mock(status_code=200)
    response.json.return_value = {
        "unexpected": "private-provider-data",
        "add_tasks": [{"title": "Не добавлять"}],
    }
    with patch("requests.post", return_value=response):
        instance.run()
    assert not instance.exception
    assert instance.session_state.student_tasks == []
    assert len(chat(instance)["messages"]) == 1
    assert all("private-provider-data" not in item.value for item in instance.error)


def test_calendar_and_task_creation_validation_and_duplicate_submit():
    instance = app()
    instance.sidebar.radio[0].set_value("calendar_tasks").run()
    assert not instance.exception
    button(instance, "Добавить в календарь").click().run()
    assert instance.error
    next(item for item in instance.text_input if item.label == "Название события").set_value(
        "Моя консультация"
    )
    button(instance, "Добавить в календарь").click().run()
    button(instance, "Добавить в календарь").click().run()
    assert not instance.exception
    assert len(instance.session_state.calendar_events) == 1
    button(instance, "Перейти к сегодня").click().run()
    assert not instance.exception
    instance.radio(key="planner_tab").set_value("Задачи").run()
    next(item for item in instance.text_input if item.label == "Что нужно сделать?").set_value("Мой отчёт")
    button(instance, "Добавить задачу").click().run()
    button(instance, "Добавить задачу").click().run()
    assert not instance.exception
    assert len(instance.session_state.student_tasks) == 1
    instance.checkbox[0].check().run()
    assert instance.session_state.student_tasks[0]["done"]


def test_chat_rename_remove_restore_and_search():
    instance = app()
    instance.sidebar.radio[0].set_value("💬 Чат с Агентом").run()
    current_id = instance.session_state.current_chat_id
    next(item for item in instance.text_input if item.label == "Название диалога").set_value("Подготовка")
    button(instance, "Переименовать").click().run()
    assert chat(instance)["title"] == "Подготовка"
    button(instance, "Убрать текущий диалог").click().run()
    assert current_id not in instance.session_state.chats
    button(instance, "Восстановить удалённый диалог").click().run()
    assert instance.session_state.current_chat_id == current_id
    assert chat(instance)["title"] == "Подготовка"
    assert not instance.exception


def test_retry_keeps_one_user_message_and_sends_once_after_confirmation():
    instance = app()
    configure(instance)
    queue(instance)
    with patch("requests.post", side_effect=requests.Timeout()):
        instance.run()
    instance.checkbox[0].check().run()
    response = Mock(status_code=200)
    response.json.return_value = {"reply": "Проверочный ответ после явного повтора"}
    with patch("requests.post", return_value=response) as post:
        button(instance, "Повторить запрос").click().run()
        instance.run()
    assert not instance.exception
    assert post.call_count == 1
    assert [m["role"] for m in chat(instance)["messages"]] == ["user", "assistant"]


def test_plain_response_never_infers_calendar_actions_from_prompt():
    instance = app()
    configure(instance)
    queue(instance, "Добавь в календарь консультацию завтра")
    response = Mock(status_code=200)
    response.json.return_value = {"reply": "Уточните время консультации"}
    with patch("requests.post", return_value=response):
        instance.run()
    assert instance.session_state.calendar_events == []


def test_openai_protocol_retains_message_shape_and_single_current_prompt():
    instance = app()
    configure(instance)
    instance.session_state.agent_settings["protocol"] = "OpenAI-Compatible (/v1/chat/completions)"
    queue(instance, "Проверь тему")
    response = Mock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": "Проверочный ответ"}}]}
    with patch("requests.post", return_value=response) as post:
        instance.run()
    payload = post.call_args.kwargs["json"]
    assert set(payload) == {"model", "messages", "temperature"}
    assert sum(message["role"] == "user" for message in payload["messages"]) == 1
    assert chat(instance)["messages"][-1]["content"] == "Проверочный ответ"


def test_settings_validation_mask_and_configuration_are_distinct():
    instance = app(settings_dialog=True)
    assert not instance.exception
    key_field = next(item for item in instance.text_input if item.label == "Ключ доступа")
    assert key_field.proto.type == InputProto.PASSWORD
    url_field = next(item for item in instance.text_input if item.label == "URL агента")
    url_field.set_value("не адрес")
    previous = deepcopy(instance.session_state.agent_settings)
    button(instance, "Сохранить настройки").click().run()
    assert instance.error
    assert instance.session_state.agent_settings == previous
    next(item for item in instance.text_input if item.label == "URL агента").set_value(
        "https://agent.example.test/chat"
    )
    button(instance, "Сохранить настройки").click().run()
    assert not instance.exception
    assert instance.session_state.agent_settings["api_url"] == "https://agent.example.test/chat"
    assert "agent_connection" not in instance.session_state


def test_filename_is_escaped_in_saved_message():
    instance = app()
    chat(instance)["messages"] = [
        {"role": "user", "content": "Проверочный файл", "attachments": ["<img src=x onerror=alert(1)>.txt"]}
    ]
    instance.sidebar.radio[0].set_value("💬 Чат с Агентом").run()
    assert not instance.exception
    assert any("&lt;img" in item.value for item in instance.markdown)


def test_upload_handler_keeps_bytes_and_deduplicates_within_one_selection():
    source = (Path(__file__).resolve().parents[1] / "campus_chat_app.py").read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "process_uploaded_files"
    )
    namespace = {"base64": base64}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "upload_handler", "exec"), namespace)
    name = "проверочный_конспект_" * 8 + ".txt"
    upload = SimpleNamespace(name=name, type="text/plain", getvalue=lambda: "Тестовый конспект".encode())
    current = {"uploaded_files": []}
    errors = namespace["process_uploaded_files"]([upload, upload], current)
    assert len(current["uploaded_files"]) == 1
    assert base64.b64decode(current["uploaded_files"][0]["base64"]) == upload.getvalue()
    assert current["uploaded_files"][0]["content_preview"] == "Тестовый конспект"
    assert len(errors) == 1 and "уже прикреплён" in errors[0]
    upload.name = "too-large.txt"
    upload.getvalue = lambda: b"x" * (10 * 1024 * 1024 + 1)
    assert "больше 10 МБ" in namespace["process_uploaded_files"]([upload], current)[0]


def test_sidebar_title_search_does_not_switch_or_erase_chats():
    instance = app()
    original_id = instance.session_state.current_chat_id
    second = deepcopy(chat(instance))
    second.update(id="exam-chat", title="Подготовка к экзамену")
    instance.session_state.chats[second["id"]] = second
    instance.run()
    instance.text_input(key="sidebar_chat_search").set_value("ЭКЗАМЕН").run()
    assert not instance.exception
    assert "select_chat_exam-chat" in [item.key for item in instance.sidebar.button]
    assert f"select_chat_{original_id}" not in [item.key for item in instance.sidebar.button]
    assert instance.session_state.current_chat_id == original_id
    instance.text_input(key="sidebar_chat_search").set_value("нет совпадений").run()
    assert not any(str(item.key).startswith("select_chat_") for item in instance.sidebar.button)
    button(instance, "Очистить поиск диалогов").click().run()
    assert len(instance.session_state.chats) == 2
    assert f"select_chat_{original_id}" in [item.key for item in instance.sidebar.button]
    assert not instance.exception
    instance.text_input(key="sidebar_chat_search").set_value("нет совпадений").run()
    instance.button(key="new_chat_button").click().run()
    assert not instance.exception
    assert instance.session_state.sidebar_chat_search == ""
    assert len(instance.session_state.chats) == 3
    assert instance.session_state.current_chat_id != original_id


def initialization_with_new_project_defaults(state):
    """Exercise real initialization with explicit test-only connection defaults."""
    source_path = Path(__file__).resolve().parents[1] / "campus_chat_app.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == "init_session_state"
    )
    namespace = {
        "st": SimpleNamespace(session_state=state),
        "today_local": lambda: date(2026, 10, 19),
        "datetime": datetime,
        "uuid": uuid,
        "os": SimpleNamespace(environ={"AGENT_API_KEY": "test-only-environment-key"}),
        "DEFAULT_AGENT_URL": "https://new-project.example.test/api/v1/chat",
        "VERTEX_RESOURCE": "projects/test/locations/test/reasoningEngines/new",
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source_path), "exec"), namespace)
    namespace["init_session_state"]()


class TestSessionState(dict):
    __test__ = False

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


@pytest.mark.parametrize(
    "url", ["", "http://127.0.0.1:8000/api/v1/chat", "https://personal.example.test/chat"]
)
@pytest.mark.parametrize("migration_marker", [None, True, "https://old-project.example.test/chat"])
def test_upstream_defaults_preserve_existing_settings_and_data(url, migration_marker):
    settings = {
        "api_url": url,
        "api_key": "test-only-saved-key",
        "agent_id": "personal-agent",
        "protocol": "OpenAI-Compatible (/v1/chat/completions)",
        "temperature": 0.65,
        "timeout": 90,
        "sync_student_context": False,
        "system_prompt": "Личная инструкция",
    }
    state = TestSessionState(
        agent_settings=deepcopy(settings),
        student_tasks=[{"id": "saved-task"}],
        calendar_events=[{"id": "saved-event"}],
        student_courses=[],
        chats={"saved-chat": {"messages": [{"role": "user", "content": "Личный вопрос"}]}},
        current_chat_id="saved-chat",
    )
    if migration_marker is not None:
        state["vertex_connection_applied"] = migration_marker
    expected = deepcopy(state)
    initialization_with_new_project_defaults(state)
    assert state.agent_settings == settings
    for field in ("student_tasks", "calendar_events", "student_courses", "chats", "current_chat_id"):
        assert state[field] == expected[field]


def test_new_session_uses_updated_project_defaults():
    state = TestSessionState()
    initialization_with_new_project_defaults(state)
    assert state.agent_settings["api_url"] == "https://new-project.example.test/api/v1/chat"
    assert state.agent_settings["agent_id"] == "projects/test/locations/test/reasoningEngines/new"
    assert state.agent_settings["api_key"] == "test-only-environment-key"


def test_chat_keeps_public_tool_receipts_and_transmits_original_file():
    instance = app()
    configure(instance)
    original = base64.b64encode(b"Actual assignment contents, not a preview.").decode()
    chat(instance)["uploaded_files"].append({"name": "assignment.txt", "type": "text/plain", "size_kb": 1,
        "content_preview": "Short preview", "base64": original})
    queue(instance, "Прочитай задание")
    response = Mock(status_code=200)
    response.json.return_value = {"output": "Explicit test answer", "steps": [
        {"type": "thought", "content": "private reasoning"},
        {"type": "tool_call", "tool": "read_uploaded_file", "args": {"filename": "assignment.txt"},
         "result": {"summary": "Read actual contents"}, "status": "success"}]}
    with patch("requests.post", return_value=response) as post:
        instance.run()
    assert not instance.exception
    assert post.call_args.kwargs["json"]["files"][0]["base64"] == original
    stored = chat(instance)["messages"][-1]["steps"]
    assert len(stored) == 1 and stored[0]["tool"] == "read_uploaded_file"
    assert "private reasoning" not in json.dumps(stored)
    assert any("Действия агента" in e.label for e in instance.expander)


def test_deadline_full_review_plan_replan_apply_undo_without_duplicate_events():
    from agent.deadline import build_plan, run_deadline
    from campuspulse.config import Settings
    from campuspulse.models import DeadlineRequest
    from test_deadline import analysis, upload

    instance = app()
    configure(instance)
    explicit_file = upload()
    chat(instance)["uploaded_files"] = [{**explicit_file, "type": "text/plain", "size_kb": 1,
                                         "content_preview": "Explicit offline test fixture"}]
    instance.sidebar.radio[0].set_value("deadline_rescue").run()
    assert not instance.exception
    assert any("Начните с файла" in i.value for i in instance.info)
    instance.selectbox[0].set_value("assignment.txt")
    instance.text_input[0].set_value("https://github.com/example/student")

    class ExplicitFakeGuardian:
        def __init__(self, settings):
            pass

        async def analyze(self, document, repository):
            return analysis()

    calls = []

    def explicit_service(url, **kwargs):
        payload = kwargs["json"]
        calls.append(payload)
        if payload["deadline_request"]["action"] == "analyze":
            data = run_deadline(payload["deadline_request"], payload["files"], Settings(_env_file=None),
                                agent_factory=ExplicitFakeGuardian)
        else:
            plan = build_plan(DeadlineRequest.model_validate(payload["deadline_request"]))
            data = {"output": "Explicit test plan", "steps": [{"type": "tool_call", "tool": "plan_before_deadline"}],
                    "deadline": {"action": "plan", "plan": plan}}
        response = Mock(status_code=200)
        response.json.return_value = data
        return response

    with patch("requests.post", side_effect=explicit_service):
        button(instance, "Проверить готовность").click().run()
        assert not instance.exception
        workspace = instance.session_state.deadline_workspace
        assert workspace["review"]["analysis"]["live"] is False
        assert calls[0]["files"][0]["base64"] == explicit_file["base64"]
        verified_connection = deepcopy(instance.session_state.agent_connection)
        instance.session_state.agent_settings = {**instance.session_state.agent_settings, "api_url": "", "api_key": ""}
        pending_work = [w for w in workspace["review"]["work"] if w["status"] != "satisfied"]
        for item in pending_work:
            instance.number_input(key="deadline_estimate_" + item["id"]).set_value(3.0)
        # An unrelated personal event outside the study window must survive every operation.
        today = instance.session_state.selected_cal_date.isoformat()
        unrelated = {"id": "personal", "title": "Morning class", "type": "lecture", "date": today,
                     "time": "07:00–08:00", "course": "Course", "location": "Campus"}
        instance.session_state.calendar_events = [unrelated]
        button(instance, "Рассчитать план").click().run()
        assert not instance.exception
        first = deepcopy(workspace["plan"])
        assert first and not first["deficit_minutes"]
        assert instance.session_state.agent_connection == verified_connection  # Local planning proves no cloud access.
        button(instance, "Применить к календарю").click().run()
        assert not instance.exception
        assert button(instance, "Применить к календарю").disabled
        assert unrelated in instance.session_state.calendar_events
        due_input = instance.text_input(key="deadline_due_date").value
        instance.sidebar.radio[0].set_value("calendar_tasks").run()
        assert not instance.exception
        instance.sidebar.radio[0].set_value("deadline_rescue").run()
        assert not instance.exception
        assert instance.text_input(key="deadline_due_date").value == due_input
        assert all(instance.number_input(key="deadline_estimate_" + w["id"]).value == 3.0 for w in pending_work)
        button(instance, "Завтра только час — пересчитать").click().run()
        assert not instance.exception
        second = workspace["plan"]
        assert second["id"] != first["id"]
        assert workspace["last_request"]["day_limits"]
        assert len(calls) == 1  # Only analysis needs the cloud; both plans ran locally.
        assert workspace["activity"][-1]["result"]["execution"] == "local"
        assert any(change["kind"] == "moved" for change in second["changes"])
        button(instance, "Применить к календарю").click().run()
        assert not instance.exception
        events = instance.session_state.calendar_events
        assert unrelated in events
        assert len({e["id"] for e in events}) == len(events)
        assert len(events) == 1 + len(second["blocks"])
        button(instance, "Отменить последнее применение").click().run()
        assert not instance.exception
        assert len(instance.session_state.calendar_events) == 1 + len(first["blocks"])
        assert workspace["applied"][first["deadline_id"]]["id"] == first["id"]


def test_legacy_response_retries_through_real_compatibility_boundary_without_losing_settings():
    from agent.deadline import run_deadline
    from campuspulse.config import Settings
    from test_deadline import analysis, upload

    instance = app()
    configure(instance)
    saved_settings = deepcopy(instance.session_state.agent_settings)
    file = upload()
    payload = {"action": "analyze", "repository_url": "https://github.com/example/student"}
    instance.session_state.deadline_workspace = {"activity": [], "day_limits": {}, "applied": {},
        "pending": {"status": "failed", "payload": payload, "files": [file],
                    "error": "Подключённый агент не вернул структурированную проверку «До сдачи». Обычный ответ чата не заменяет проверку требований."}}

    class ExplicitFakeGuardian:
        def __init__(self, settings):
            pass

        async def analyze(self, document, repository):
            return analysis()

    data = run_deadline(payload, [file], Settings(_env_file=None), agent_factory=ExplicitFakeGuardian)
    instance.sidebar.radio[0].set_value("deadline_rescue").run()
    assert button(instance, "Проверить через совместимый режим")
    with patch("campus_chat_ui.deadline_mode.run_gateway_review", return_value=data) as compatibility:
        with patch("requests.post") as post:
            button(instance, "Проверить через совместимый режим").click().run()
    assert not instance.exception
    post.assert_not_called()
    assert compatibility.call_count == 1
    assert compatibility.call_args.args[:2] == (payload, [file])
    assert instance.session_state.agent_settings == saved_settings
    assert instance.session_state.deadline_workspace["source_file"] == file
    assert instance.session_state.deadline_workspace["review"]["analysis"]["live"] is False


def test_compatibility_progress_stays_on_ui_thread_and_shows_timetable_error():
    from test_deadline import upload

    instance = app()
    configure(instance)
    instance.session_state.deadline_workspace = {"activity": [], "day_limits": {}, "applied": {},
        "pending": {"status": "queued", "transport": "gateway-text",
                    "payload": {"action": "analyze", "repository_url": "https://github.com/example/student"},
                    "files": [upload(b"Timetable: lecture tomorrow.")]}}
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"output": '{"requirements":[]}', "steps": []}).encode()
    with patch("requests.post", return_value=response) as post:
        instance.sidebar.radio[0].set_value("deadline_rescue").run()
    assert not instance.exception
    assert post.call_count == 1
    error = instance.session_state.deadline_workspace["pending"]["error"]
    assert "В файле нет проверяемых требований" in error
    assert "Не удалось обработать" not in error


def test_deadline_explains_rejected_sources_and_actual_ambiguity_and_keeps_all_read_sources():
    from agent.deadline import review_work
    from test_deadline import analysis

    instance = app()
    result = analysis(("unverified", "unverified", "unverified"))
    result.requirements[1].ambiguity = "Укажите workflow и ветку для проверки CI."
    result.findings[0].explanation = "The proposed conclusion could not be grounded in sufficient collected evidence."
    result.findings[0].evidence_ids = []
    result.findings[1].explanation = "The assignment requirement needs clarification."
    result.usage["verification"] = [{"attempt": 2, "checks": [
        {"requirement_id": "r0", "reasons": ["unknown_evidence_id", "citation_mismatch"]}]}]
    instance.session_state.deadline_workspace = {"activity": [], "day_limits": {}, "applied": {}, "review": {
        "filename": "explicit-test.txt", "repository_url": "https://github.com/example/student",
        "document_sha256": "a" * 64, "analysis": result.model_dump(mode="json"),
        "work": review_work(result, "a" * 64)}}
    with patch("requests.post") as post:
        instance.sidebar.radio[0].set_value("deadline_rescue").run()
    post.assert_not_called()
    assert not instance.exception
    rendered = " ".join(item.value for kind in (instance.markdown, instance.info, instance.caption) for item in kind)
    assert "Укажите workflow и ветку для проверки CI." in rendered
    assert "Цитата агента не совпала" in rendered
    assert "которого нет среди полученных данных" in rendered
    assert "The proposed conclusion" not in rendered
    assert any(item.label == "Полученные источники · 1" for item in instance.expander)
    assert any("Explicit test CI result." in item.value for item in instance.code)

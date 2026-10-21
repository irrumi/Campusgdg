"""Streamlit state and API regressions; all service responses here are explicit fakes."""

from copy import deepcopy
from pathlib import Path
import ast
import base64
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

import base64
import calendar
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import uuid
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import requests
import streamlit as st


def load_vertex_connection():
    connection = {}
    for name in ("agent.connection.json", "vertex.config.json"):
        config_path = Path(__file__).with_name(name)
        if config_path.exists():
            configured = json.loads(config_path.read_text(encoding="utf-8"))
            if (
                name == "vertex.config.json"
                and "api_url" not in configured
                and configured.get("agent_engine_resource")
                and (configured["agent_engine_resource"] != connection.get("agent_engine_resource"))
            ):
                connection.pop("api_url", None)
            connection.update(configured)
    return connection


VERTEX_CONNECTION = load_vertex_connection()
VERTEX_RESOURCE = VERTEX_CONNECTION.get("agent_engine_resource", "")
DEFAULT_AGENT_URL = (
    os.environ.get("AGENT_API_URL")
    or VERTEX_CONNECTION.get("api_url")
    or ("http://127.0.0.1:8000/api/v1/chat" if VERTEX_RESOURCE else "")
)

st.set_page_config(
    page_title="CampusGDG — учебное пространство",
    page_icon=":material/school:",
    layout="wide",
    initial_sidebar_state="auto",
    menu_items={},
)

# Streamlit owns controls, popups and keyboard behavior. Only documented test IDs
# and our own classes/keys are styled; no hashed classes or positional selectors.
CUSTOM_CSS = (
    "<style>"
    + (Path(__file__).parent / "campus_chat_ui" / "styles.css").read_text(encoding="utf-8")
    + "</style>"
)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

HOME = "📊 Dashboard студента"
CHAT = "💬 Чат с Агентом"
SPLIT = "⚡ Split: Чат + Календарь"
PLANNER = "calendar_tasks"
VIEW_NAMES = {HOME: "Главная", CHAT: "Чат", PLANNER: "Календарь и задачи", SPLIT: "Чат и календарь"}
VIEW_ICONS = {
    HOME: ":material/home:",
    CHAT: ":material/chat_bubble_outline:",
    PLANNER: ":material/calendar_month:",
    SPLIT: ":material/splitscreen:",
}
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
MONTH_TITLES = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
EVENT_TYPES = {"lecture": "Занятие", "study": "Подготовка", "deadline": "Дедлайн", "exam": "Экзамен"}


def local_now():
    return datetime.now(ZoneInfo("Asia/Qyzylorda"))


def today_local():
    return local_now().date()


def display_date(value, short=False):
    try:
        day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        return f"{day.day} {MONTHS[day.month - 1]}" + ("" if short else f" {day.year}")
    except (ValueError, TypeError):
        return "Дата не указана"


def due_label(value):
    try:
        days = (date.fromisoformat(value) - today_local()).days
    except (ValueError, TypeError):
        return "Без срока"
    if days < 0:
        return "Срок прошёл · " + display_date(value, short=True)
    return {0: "Сегодня", 1: "Завтра"}.get(days, display_date(value, short=True))


def date_field(label, value, key):
    # Typed dates keep Russian labels and DD.MM.YYYY independent of browser locale.
    text = st.text_input(
        label,
        value=value.strftime("%d.%m.%Y"),
        key=key,
        placeholder="ДД.ММ.ГГГГ",
        help="Например: 25.10.2026",
    )
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except ValueError:
        st.error(f"{label}: введите существующую дату в формате ДД.ММ.ГГГГ.")
        return None


def connection_signature(settings):
    # Kept server-side only; never displayed, exported or logged.
    return tuple(settings.get(k, "") for k in ("api_url", "api_key", "protocol", "agent_id"))


def connection_state():
    settings = st.session_state.agent_settings
    if not settings.get("api_url", "").strip():
        return "not_configured", "Агент не подключён"
    status = st.session_state.get("agent_connection", {})
    if status.get("signature") == connection_signature(settings):
        if status.get("state") == "verified":
            return "verified", f"Ответ агента подтверждён · {status['checked_at']}"
        if status.get("state") == "reachable":
            return "reachable", "Адрес доступен · ответ агента не проверен"
        if status.get("state") == "error":
            return "error", "Ошибка подключения · проверьте настройки"
    return "configured", "Настройки сохранены · подключение не проверено"


class AgentRequestError(Exception):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def init_session_state():
    today = today_local()

    # По умолчанию открываем чат или Dashboard
    if "active_view" not in st.session_state:
        st.session_state.active_view = "📊 Dashboard студента"

    if "agent_settings" not in st.session_state:
        st.session_state.agent_settings = {
            "api_url": DEFAULT_AGENT_URL,
            "api_key": os.environ.get("AGENT_API_KEY", ""),
            "protocol": "Custom Agent REST (JSON)",
            "agent_id": VERTEX_RESOURCE or "campusgdg-student-agent-v1",
            "system_prompt": (
                "Ты — CampusGDG Assistant, ИИ-помощник студента. У тебя есть"
                " доступ к расписанию, календарю, дедлайнам и загруженным"
                " конспектам студента. Помогай планировать учебу, разбирать сложные"
                " темы и управлять календарем."
            ),
            "temperature": 0.3,
            "timeout": 60,
            "sync_student_context": True,
        }

    # Existing session settings are user-owned, including an intentionally empty URL.
    # Project defaults seed only new sessions. The settings dialog provides an
    # explicit action to apply the project's connection to an existing session.

    for field in ("calendar_events", "student_tasks", "student_courses"):
        if field not in st.session_state:
            st.session_state[field] = []
    if "chats" not in st.session_state:
        chat_id = str(uuid.uuid4())
        st.session_state.chats = {
            chat_id: {
                "id": chat_id,
                "title": "Новый диалог",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "messages": [],
                "uploaded_files": [],
                "web_search_enabled": False,
                "drive_connected": False,
                "drive_source": "",
            }
        }
        st.session_state.current_chat_id = chat_id

    if "show_dropzone" not in st.session_state:
        st.session_state.show_dropzone = False

    if "dropzone_filter" not in st.session_state:
        st.session_state.dropzone_filter = "all"

    if "search_query" not in st.session_state:
        st.session_state.search_query = ""

    if "selected_cal_date" not in st.session_state:
        st.session_state.selected_cal_date = today


def example_student_data():
    today = today_local()
    tomorrow = today + timedelta(days=1)
    in_two_days = today + timedelta(days=2)
    in_four_days = today + timedelta(days=4)
    data = {
        "calendar_events": [
            {
                "id": str(uuid.uuid4()),
                "title": "Лекция: Алгоритмы и структуры данных",
                "course": "Computer Science",
                "date": today.strftime("%Y-%m-%d"),
                "time": "10:00 - 11:30",
                "location": "Ауд. 402 / Google Meet",
                "type": "lecture",
            },
            {
                "id": str(uuid.uuid4()),
                "title": "Семинар: Линейная алгебра и ML",
                "course": "Математика",
                "date": today.strftime("%Y-%m-%d"),
                "time": "13:00 - 14:30",
                "location": "Ауд. 215",
                "type": "lecture",
            },
            {
                "id": str(uuid.uuid4()),
                "title": "Сдача Лабораторной №3 (Графы и Дейкстра)",
                "course": "Computer Science",
                "date": tomorrow.strftime("%Y-%m-%d"),
                "time": "23:59",
                "location": "CampusGDG LMS",
                "type": "deadline",
            },
            {
                "id": str(uuid.uuid4()),
                "title": "Подготовка к коллоквиуму с ИИ-агентом",
                "course": "Математика",
                "date": in_two_days.strftime("%Y-%m-%d"),
                "time": "18:00 - 19:30",
                "location": "CampusGDG Study Hub",
                "type": "study",
            },
            {
                "id": str(uuid.uuid4()),
                "title": "Midterm Exam: Базы данных и SQL",
                "course": "Базы данных",
                "date": in_four_days.strftime("%Y-%m-%d"),
                "time": "11:00 - 12:30",
                "location": "Главный корпус, Ауд. 101",
                "type": "exam",
            },
        ],
        "student_tasks": [
            {
                "id": str(uuid.uuid4()),
                "title": "Реализовать алгоритм Дейкстры и оформить отчет",
                "course": "Computer Science",
                "due_date": tomorrow.strftime("%Y-%m-%d"),
                "priority": "Высокий",
                "done": False,
            },
            {
                "id": str(uuid.uuid4()),
                "title": "Решить 10 задач по собственным векторам (ДЗ №4)",
                "course": "Математика",
                "due_date": in_two_days.strftime("%Y-%m-%d"),
                "priority": "Высокий",
                "done": False,
            },
            {
                "id": str(uuid.uuid4()),
                "title": "Повторить нормализацию БД (1NF–BCNF) перед Midterm",
                "course": "Базы данных",
                "due_date": in_four_days.strftime("%Y-%m-%d"),
                "priority": "Средний",
                "done": False,
            },
            {
                "id": str(uuid.uuid4()),
                "title": "Прочитать статью по Attention Is All You Need",
                "course": "AI & Deep Learning",
                "due_date": today.strftime("%Y-%m-%d"),
                "priority": "Низкий",
                "done": True,
            },
        ],
        "student_courses": [
            {"name": "Computer Science", "grade": "94%", "gpa": 4.0, "credits": 5},
            {"name": "Математика и ML", "grade": "89%", "gpa": 3.7, "credits": 5},
            {"name": "Базы данных и SQL", "grade": "91%", "gpa": 3.9, "credits": 4},
            {"name": "AI & Deep Learning", "grade": "96%", "gpa": 4.0, "credits": 4},
        ],
    }
    for records in data.values():
        for record in records:
            record["_example"] = True
    return data


def create_new_chat(title="Новый диалог"):
    chat_id = str(uuid.uuid4())
    st.session_state.chats[chat_id] = {
        "id": chat_id,
        "title": title,
        "created_at": local_now().strftime("%Y-%m-%d %H:%M"),
        "messages": [],
        "uploaded_files": [],
        "web_search_enabled": False,
        "drive_connected": False,
        "drive_source": "",
    }
    st.session_state.current_chat_id = chat_id
    st.session_state.search_query = ""
    return chat_id


def get_current_chat():
    if st.session_state.get("current_chat_id") not in st.session_state.chats:
        create_new_chat()
    return st.session_state.chats[st.session_state.current_chat_id]


def delete_chat(chat_id):
    chat = st.session_state.chats.pop(chat_id, None)
    if chat:
        st.session_state.deleted_chat = chat
    if st.session_state.current_chat_id == chat_id:
        st.session_state.current_chat_id = next(iter(st.session_state.chats), None)
    get_current_chat()


def rename_chat(chat_id, new_title):
    if new_title.strip() and chat_id in st.session_state.chats:
        st.session_state.chats[chat_id]["title"] = new_title.strip()


def navigate(view):
    st.session_state.active_view = view
    st.session_state.next_view = view
    st.rerun()


def queue_prompt(prompt, chat):
    if chat.get("pending_request"):
        return False
    if not prompt.strip():
        return False
    if chat["title"] in ("New Chat", "Новый диалог") and not chat["messages"]:
        chat["title"] = prompt[:48] + ("…" if len(prompt) > 48 else "")
    chat["messages"].append(
        {"role": "user", "content": prompt, "attachments": [f["name"] for f in chat["uploaded_files"]]}
    )
    chat["pending_request"] = {"prompt": prompt, "status": "queued"}
    st.session_state.search_query = ""
    return True


def send_prompt_from_dashboard(prompt_text, chat_title=""):
    chat = get_current_chat()
    if chat.get("pending_request"):
        create_new_chat(chat_title or "Новый диалог")
        chat = get_current_chat()
    if not chat.get("pending_request"):
        if chat_title and not chat["messages"]:
            chat["title"] = chat_title
        queue_prompt(prompt_text, chat)
    st.session_state.active_view = CHAT
    st.session_state.next_view = CHAT


def process_uploaded_files(uploaded_files, chat):
    existing = {f["name"] for f in chat["uploaded_files"]}
    errors = []
    for file in uploaded_files or []:
        if file.name in existing:
            errors.append(f"Файл «{file.name}» уже прикреплён. Уберите старую версию, чтобы заменить его.")
            continue
        raw = file.getvalue()
        if len(raw) > 10 * 1024 * 1024:
            errors.append(f"Файл «{file.name}» больше 10 МБ.")
            continue
        if len(chat["uploaded_files"]) >= 10:
            errors.append("Можно прикрепить до 10 файлов к диалогу.")
            break
        preview = (
            raw.decode("utf-8", errors="replace")[:4000]
            if file.name.lower().endswith((".txt", ".csv", ".md", ".json"))
            else ""
        )
        chat["uploaded_files"].append(
            {
                "name": file.name,
                "type": file.type or "application/octet-stream",
                "size_kb": round(len(raw) / 1024, 1),
                "content_preview": preview,
                "base64": base64.b64encode(raw).decode("ascii"),
            }
        )
        existing.add(file.name)
    return errors


def render_examples_notice():
    if any(
        item.get("_example")
        for field in ("calendar_events", "student_tasks", "student_courses")
        for item in st.session_state[field]
    ):
        st.info(
            "Учебные примеры помечены в списках. Они добавлены для знакомства с интерфейсом и не синхронизированы с вузом.",
            icon=":material/info:",
        )


def render_first_run():
    st.markdown("Начните с одной задачи, добавьте событие или подключите агента в настройках.")
    with st.expander("Познакомиться с приложением на учебных примерах"):
        st.caption("Будут добавлены примерные занятия, задачи и оценки. Ответы агента не имитируются.")
        if st.button("Добавить учебные примеры", disabled=st.session_state.get("examples_loaded", False)):
            for field, records in example_student_data().items():
                st.session_state[field].extend(records)
            st.session_state.examples_loaded = True
            st.rerun()


def example_tag(item):
    return '<span class="tag">Учебный пример</span>' if item.get("_example") else ""


def render_event(event):
    kind = EVENT_TYPES.get(event.get("type"), "Событие")
    st.markdown(
        f'<article class="event-row"><div class="event-time">{escape(display_date(event.get("date"), True))} · {escape(str(event.get("time", "Время не указано")))} · {kind}</div>'
        f"<h3>{escape(str(event['title']))}{example_tag(event)}</h3>"
        f'<div class="muted">{escape(str(event.get("course", "")))} · {escape(str(event.get("location", "")))}</div></article>',
        unsafe_allow_html=True,
    )


def task_prompt(task):
    return f"Помоги выполнить учебную задачу «{task['title']}» по предмету «{task['course']}» (срок: {task['due_date']}). Разбей её на понятные шаги."


def render_task(task, prefix):
    with st.container(border=True):
        completed = st.checkbox(
            task["title"],
            value=task["done"],
            key=f"{prefix}_{task['id']}",
            help="Отметьте выполненную задачу. Отметку можно снять.",
        )
        if completed != task["done"]:
            task["done"] = completed
            st.rerun()
        status = "Выполнено" if task["done"] else due_label(task.get("due_date"))
        st.markdown(
            f'<div class="muted">{escape(str(task.get("course", "")))} · {escape(status)} · Приоритет: {escape(str(task.get("priority", "Средний")))}{example_tag(task)}</div>',
            unsafe_allow_html=True,
        )
        if not task["done"] and st.button("Разобрать с агентом", key=f"{prefix}_ask_{task['id']}"):
            send_prompt_from_dashboard(task_prompt(task), task["title"][:48])
            st.rerun()


def render_month_calendar_html(target_date, events):
    event_days = {e.get("date") for e in events}
    rows = []
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(target_date.year, target_date.month):
        cells = []
        for day in week:
            if not day:
                cells.append("<td></td>")
                continue
            current = date(target_date.year, target_date.month, day)
            classes = []
            labels = [display_date(current)]
            if current == today_local():
                classes.append("today")
                labels.append("сегодня")
            if current == target_date:
                classes.append("selected")
                labels.append("выбранная дата")
            if current.isoformat() in event_days:
                classes.append("has-event")
                labels.append("есть события")
            cells.append(
                f'<td class="{" ".join(classes)}" aria-label="{escape(", ".join(labels), quote=True)}">{day}</td>'
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<table class="cal-grid"><caption>{MONTH_TITLES[target_date.month - 1]} {target_date.year}</caption><thead><tr>'
        + "".join(f'<th scope="col">{day}</th>' for day in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"))
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_student_dashboard():
    tasks = sorted(
        (t for t in st.session_state.student_tasks if not t["done"]), key=lambda t: t.get("due_date", "9999")
    )
    today_events = sorted(
        (e for e in st.session_state.calendar_events if e.get("date") == today_local().isoformat()),
        key=lambda e: e.get("time", ""),
    )
    render_examples_notice()
    if not st.session_state.student_tasks and not st.session_state.calendar_events:
        render_first_run()
    # The next deadline is actionable content, not a decorative KPI.
    candidates = [(t.get("due_date", "9999"), t["title"], t) for t in tasks]
    candidates += [
        (e["date"], e["title"], e)
        for e in st.session_state.calendar_events
        if e.get("type") in ("deadline", "exam") and e.get("date", "") >= today_local().isoformat()
    ]
    closest = min(candidates, key=lambda item: item[0]) if candidates else None
    if closest:
        item = closest[2]
        st.markdown(
            f'<section class="focus-card"><p>Ближайший срок · {escape(due_label(closest[0]))}{example_tag(item)}</p><h2>{escape(str(closest[1]))}</h2><p>{escape(str(item.get("course", "")))} · {escape(display_date(closest[0]))}</p></section>',
            unsafe_allow_html=True,
        )
        if st.button("Составить план подготовки", type="primary"):
            send_prompt_from_dashboard(
                f"Помоги подготовиться к «{closest[1]}» к {closest[0]}. Составь план по шагам.",
                "План подготовки",
            )
            st.rerun()
    else:
        st.markdown(
            '<section class="focus-card"><h2>Учёба в вашем ритме</h2><p>Добавьте ближайший срок — он появится здесь.</p></section>',
            unsafe_allow_html=True,
        )
        if st.button("Добавить первую задачу", type="primary"):
            navigate(PLANNER)
    completed = sum(bool(t["done"]) for t in st.session_state.student_tasks)
    st.markdown(
        f'<div class="summary"><span><strong>{len(today_events)}</strong> событий сегодня</span><span><strong>{len(tasks)}</strong> задач в работе</span><span><strong>{completed}</strong> выполнено</span></div>',
        unsafe_allow_html=True,
    )
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.subheader("Сегодня")
        st.caption(display_date(today_local()) + " · время Астаны (UTC+5)")
        if not today_events:
            st.info("На сегодня событий нет. Добавьте занятие или время для подготовки.")
        for event in today_events[:4]:
            render_event(event)
        if st.button("Открыть календарь", use_container_width=True):
            st.session_state.planner_tab = "Календарь"
            navigate(PLANNER)
    with right:
        st.subheader("Требуют внимания")
        if not tasks:
            st.info("Нет незавершённых задач. Новую задачу можно добавить в планировщике.")
        for task in tasks[:3]:
            render_task(task, "home")
        if st.button("Все задачи", use_container_width=True):
            st.session_state.planner_tab = "Задачи"
            navigate(PLANNER)
    with st.expander("Курсы и успеваемость"):
        if not st.session_state.student_courses:
            st.caption(
                "Данных о курсах пока нет. Они появятся после передачи контекста от агента или добавления учебных примеров."
            )
        for course in st.session_state.student_courses:
            st.markdown(f"**{escape(str(course['name']))}**{example_tag(course)}", unsafe_allow_html=True)
            st.caption(
                f"Оценка: {course['grade']} · GPA: {course['gpa']} · Кредиты ECTS: {course['credits']}"
            )


def render_calendar():
    if st.session_state.pop("calendar_go_today", False):
        st.session_state.selected_cal_date = today_local()
        st.session_state.calendar_date_text = today_local().strftime("%d.%m.%Y")
    selected = st.session_state.selected_cal_date
    left, right = st.columns([0.85, 1.35], gap="large")
    with left:
        value = date_field("Дата просмотра", selected, "calendar_date_text")
        if value:
            st.session_state.selected_cal_date = value
            selected = value
        period = st.radio("Период", ["День", "Неделя", "Месяц"], horizontal=True, index=1)
        st.markdown(
            render_month_calendar_html(selected, st.session_state.calendar_events), unsafe_allow_html=True
        )
        st.caption("Точка — есть события. Обводка — выбранная дата. Синий фон — сегодня.")
        if st.button("Перейти к сегодня"):
            st.session_state.calendar_go_today = True
            st.rerun()
    with right:
        if period == "День":
            start = end = selected
        elif period == "Неделя":
            start = selected - timedelta(days=selected.weekday())
            end = start + timedelta(days=6)
        else:
            start = selected.replace(day=1)
            end = selected.replace(day=calendar.monthrange(selected.year, selected.month)[1])
        st.subheader("Расписание")
        st.caption(f"{display_date(start)} — {display_date(end)} · UTC+5")
        events = sorted(
            (
                e
                for e in st.session_state.calendar_events
                if start.isoformat() <= e.get("date", "") <= end.isoformat()
            ),
            key=lambda e: (e["date"], e.get("time", "")),
        )
        if not events:
            st.info("В этом периоде нет событий. Добавьте событие ниже или выберите другую дату.")
        for event in events:
            render_event(event)
            with st.popover("Действия с событием", key=f"event_actions_{event['id']}"):
                if st.button("Подготовиться с агентом", key=f"event_ask_{event['id']}"):
                    send_prompt_from_dashboard(
                        f"Помоги подготовиться к событию «{event['title']}» ({event['date']}, {event['time']}). Составь план и список тем.",
                        event["title"][:48],
                    )
                    st.rerun()
                if st.button("Убрать событие", key=f"event_remove_{event['id']}"):
                    st.session_state.removed_event = event.copy()
                    st.session_state.calendar_events = [
                        e for e in st.session_state.calendar_events if e["id"] != event["id"]
                    ]
                    st.rerun()
        removed = st.session_state.get("removed_event")
        if removed:
            st.caption(f"Убрано: {removed['title']}")
            if st.button("Восстановить событие"):
                st.session_state.calendar_events.append(st.session_state.pop("removed_event"))
                st.rerun()
    with st.expander("Добавить событие"):
        with st.form("add_cal_event_form", clear_on_submit=False):
            title = st.text_input("Название события", placeholder="Например: консультация по математике")
            a, b = st.columns(2)
            with a:
                course = st.text_input("Предмет", key="event_course")
                event_date = date_field("Дата события", selected, "new_event_date")
            with b:
                time = st.text_input(
                    "Время события", value="16:00–17:30", help="ЧЧ:ММ или ЧЧ:ММ–ЧЧ:ММ, время Астаны"
                )
                kind = st.selectbox("Тип события", list(EVENT_TYPES), format_func=EVENT_TYPES.get)
            location = st.text_input("Место или ссылка", value="Онлайн")
            if st.form_submit_button("Добавить в календарь", type="primary"):
                import re

                if not title.strip():
                    st.error("Укажите название события. Остальные поля сохранены.")
                elif not event_date:
                    st.error("Исправьте дату события.")
                elif not re.fullmatch(
                    r"(?:[01]\d|2[0-3]):[0-5]\d(?:\s*[-–]\s*(?:[01]\d|2[0-3]):[0-5]\d)?", time.strip()
                ):
                    st.error("Укажите время в формате 16:00 или 16:00–17:30.")
                else:
                    signature = (
                        title.strip(),
                        event_date.isoformat(),
                        time.strip(),
                        course.strip(),
                        location.strip(),
                        kind,
                    )
                    if st.session_state.get("last_event_submission") == signature:
                        st.info("Это событие уже добавлено. Измените поля для нового события.")
                    else:
                        st.session_state.calendar_events.append(
                            {
                                "id": str(uuid.uuid4()),
                                "title": title.strip(),
                                "course": course.strip() or "Общее",
                                "date": event_date.isoformat(),
                                "time": time.strip(),
                                "location": location.strip(),
                                "type": kind,
                            }
                        )
                        st.session_state.last_event_submission = signature
                        st.session_state.ui_notice = (
                            "Событие добавлено. Оно появится в выбранном периоде расписания."
                        )
                        st.rerun()


def render_tasks():
    with st.expander("Добавить задачу", expanded=not st.session_state.student_tasks):
        with st.form("add_student_task_form", clear_on_submit=False):
            title = st.text_input("Что нужно сделать?", placeholder="Например: сдать отчёт по практике")
            a, b = st.columns(2)
            with a:
                course = st.text_input("Предмет", key="task_course")
                due = date_field("Срок сдачи", today_local() + timedelta(days=2), "new_task_date")
            with b:
                priority = st.selectbox("Приоритет", ["Высокий", "Средний", "Низкий"], index=1)
            if st.form_submit_button("Добавить задачу", type="primary"):
                if not title.strip():
                    st.error("Напишите, что нужно сделать. Остальные поля сохранены.")
                elif not due:
                    st.error("Исправьте срок сдачи.")
                else:
                    signature = (title.strip(), course.strip(), due.isoformat(), priority)
                    if st.session_state.get("last_task_submission") == signature:
                        st.info("Эта задача уже добавлена. Измените поля для новой задачи.")
                    else:
                        st.session_state.student_tasks.insert(
                            0,
                            {
                                "id": str(uuid.uuid4()),
                                "title": title.strip(),
                                "course": course.strip() or "Общее",
                                "due_date": due.isoformat(),
                                "priority": priority,
                                "done": False,
                            },
                        )
                        st.session_state.last_task_submission = signature
                        st.session_state.ui_notice = "Задача добавлена. Список отсортирован по сроку сдачи."
                        st.rerun()
    status = st.radio("Статус задач", ["В работе", "Выполненные", "Все"], horizontal=True)
    tasks = sorted(
        (
            t
            for t in st.session_state.student_tasks
            if status == "Все" or bool(t["done"]) == (status == "Выполненные")
        ),
        key=lambda t: t.get("due_date", "9999"),
    )
    st.caption(f"Показано задач: {len(tasks)} · по сроку сдачи")
    if not tasks:
        st.info("В этом списке пока нет задач. Добавьте новую или выберите другой статус.")
    for task in tasks:
        render_task(task, "planner")


def render_planner():
    render_examples_notice()
    tab = st.radio("Раздел планировщика", ["Календарь", "Задачи"], horizontal=True, key="planner_tab")
    if tab == "Календарь":
        render_calendar()
    else:
        render_tasks()


def apply_agent_side_effects(user_message: str, agent_data: dict | None = None) -> list[str]:
    applied_logs = []

    if isinstance(agent_data, dict):
        for ev in agent_data.get("add_calendar_events", []):
            if isinstance(ev, dict) and ev.get("title"):
                st.session_state.calendar_events.append(
                    {
                        "id": str(uuid.uuid4()),
                        "title": ev.get("title"),
                        "course": ev.get("course", "Общее"),
                        "date": ev.get("date", today_local().strftime("%Y-%m-%d")),
                        "time": ev.get("time", "16:00 - 17:00"),
                        "location": ev.get("location", "Онлайн"),
                        "type": ev.get("type", "study"),
                    }
                )
                applied_logs.append(f"Добавлено в календарь: **{ev.get('title')}**")

        for tsk in agent_data.get("add_tasks", []):
            if isinstance(tsk, dict) and tsk.get("title"):
                st.session_state.student_tasks.insert(
                    0,
                    {
                        "id": str(uuid.uuid4()),
                        "title": tsk.get("title"),
                        "course": tsk.get("course", "Общее"),
                        "due_date": tsk.get("due_date", today_local().strftime("%Y-%m-%d")),
                        "priority": tsk.get("priority", "Средний"),
                        "done": False,
                    },
                )
                applied_logs.append(f"Добавлена задача: **{tsk.get('title')}**")

    return applied_logs


def call_agent_api(user_message: str, chat: dict) -> str:
    settings = st.session_state.agent_settings
    api_url = settings.get("api_url", "").strip()
    api_key = settings.get("api_key", "").strip()
    protocol = settings.get("protocol", "Custom Agent REST (JSON)")

    student_context = {
        "today": today_local().strftime("%Y-%m-%d"),
        "calendar_events": st.session_state.calendar_events,
        "tasks": st.session_state.student_tasks,
        "courses": st.session_state.student_courses,
    }

    if not api_url:
        raise AgentRequestError("Агент не подключён. Укажите URL в настройках и повторите запрос.")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key

    if protocol == "OpenAI-Compatible (/v1/chat/completions)":
        messages_payload = []
        sys_text = settings.get("system_prompt", "")
        if settings.get("sync_student_context", True):
            sys_text += "\n\nАктуальный календарь и дедлайны студента (JSON):\n" + json.dumps(
                student_context, ensure_ascii=False
            )
        messages_payload.append({"role": "system", "content": sys_text})

        if chat["uploaded_files"]:
            files_context = "\n\n".join(
                f"File: {f['name']} ({f['type']}, {f['size_kb']} KB)\n{f['content_preview']}"
                for f in chat["uploaded_files"]
            )
            messages_payload.append(
                {
                    "role": "system",
                    "content": f"Attached files in this chat:\n{files_context}",
                }
            )

        for m in chat["messages"]:
            messages_payload.append({"role": m["role"], "content": m["content"]})

        payload = {
            "model": settings.get("agent_id") or "gpt-4o-mini",
            "messages": messages_payload,
            "temperature": settings.get("temperature", 0.3),
        }
    else:
        payload = {
            "chat_id": chat["id"],
            "agent_id": settings.get("agent_id", "campusgdg-student-agent-v1"),
            "message": user_message,
            "system_prompt": settings.get("system_prompt", ""),
            "temperature": settings.get("temperature", 0.3),
            "student_context": (student_context if settings.get("sync_student_context", True) else {}),
            "history": [{"role": m["role"], "content": m["content"]} for m in chat["messages"][:-1]],
            "tools": {
                "web_search": chat.get("web_search_enabled", False),
                "connect_drive": chat.get("drive_connected", False),
                "drive_source": chat.get("drive_source", ""),
            },
            "files": [
                {
                    "name": f["name"],
                    "mime_type": f["type"],
                    "size_kb": f["size_kb"],
                    "text_preview": f["content_preview"],
                    "base64": f["base64"],
                }
                for f in chat["uploaded_files"]
            ],
        }

    try:
        response = requests.post(
            api_url, headers=headers, json=payload, timeout=int(settings.get("timeout", 60))
        )
        response.raise_for_status()
        data = response.json()
    except requests.Timeout:
        raise AgentRequestError(
            "Время ожидания истекло. Проверьте подключение и историю диалога перед повтором.", uncertain=True
        ) from None
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        hint = {
            401: "Проверьте ключ доступа в настройках.",
            403: "Проверьте права ключа доступа.",
            404: "Проверьте полный URL метода чата.",
            429: "Лимит запросов исчерпан. Попробуйте позже.",
        }.get(code, "Сервис временно недоступен. Попробуйте позже.")
        raise AgentRequestError(f"Сервер вернул HTTP {code}. {hint}", uncertain=code >= 500) from None
    except requests.RequestException:
        raise AgentRequestError(
            "Не удалось получить ответ сервера. Проверьте сеть и URL в настройках.", uncertain=True
        ) from None
    except ValueError:
        raise AgentRequestError(
            "Сервер вернул ответ в неподдерживаемом формате. Проверьте протокол в настройках.", uncertain=True
        ) from None

    reply = None
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            reply = message.get("content") if isinstance(message, dict) else None
        if not isinstance(reply, str):
            reply = next(
                (
                    data[k]
                    for k in ("output", "response", "reply", "answer", "content", "text")
                    if isinstance(data.get(k), str)
                ),
                None,
            )
    if not isinstance(reply, str) or not reply.strip():
        raise AgentRequestError(
            "В ответе сервера нет текста. Проверьте выбранный протокол API.", uncertain=True
        )
    logs = apply_agent_side_effects(user_message, data)
    st.session_state.agent_connection = {
        "signature": connection_signature(settings),
        "state": "verified",
        "checked_at": local_now().strftime("%H:%M"),
    }
    return (("\n".join(logs) + "\n\n") if logs else "") + reply


def validate_agent_url(value):
    if not value.strip():
        return True
    try:
        parsed = urlsplit(value.strip())
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


def check_saved_address():
    settings = st.session_state.agent_settings
    if not settings.get("api_url", "").strip():
        st.warning("Сначала сохраните URL агента.")
        return
    headers = {}
    if settings.get("api_key"):
        headers = {"Authorization": f"Bearer {settings['api_key']}", "X-API-Key": settings["api_key"]}
    with st.spinner("Проверяем доступность адреса…"):
        try:
            response = requests.get(settings["api_url"], headers=headers, timeout=8)
            response.raise_for_status()
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code == 405:
                st.info(
                    "Сервер доступен, но этот адрес принимает только сообщения. Подключение подтвердится после ответа в чате."
                )
                st.session_state.agent_connection = {
                    "signature": connection_signature(settings),
                    "state": "reachable",
                }
            else:
                st.error(f"Проверка вернула HTTP {code}. Проверьте URL, ключ и права доступа.")
                st.session_state.agent_connection = {
                    "signature": connection_signature(settings),
                    "state": "error",
                }
            return
        except requests.RequestException:
            st.error("Адрес недоступен. Проверьте сеть, URL и работу сервера. Настройки сохранены.")
            st.session_state.agent_connection = {
                "signature": connection_signature(settings),
                "state": "error",
            }
            return
    st.session_state.agent_connection = {"signature": connection_signature(settings), "state": "reachable"}
    st.success(
        f"Адрес ответил: HTTP {response.status_code}. Ответ агента ещё не проверен — отправьте сообщение в чате."
    )


@st.dialog("Настройки агента", width="medium")
def open_settings_dialog():
    current = st.session_state.agent_settings
    st.caption(
        "Подключение хранится в текущей серверной сессии Streamlit. Сохранение параметров не подтверждает работу агента."
    )
    _, label = connection_state()
    st.info(label)
    if VERTEX_RESOURCE:
        st.caption("В проекте есть конфигурация Vertex AI. Текущие параметры подключения сохранены ниже.")
        if st.button("Использовать конфигурацию проекта"):
            current.update(
                api_url=DEFAULT_AGENT_URL, protocol="Custom Agent REST (JSON)", agent_id=VERTEX_RESOURCE
            )
            st.session_state.pop("agent_connection", None)
            st.rerun()
    with st.form("agent_settings_form", clear_on_submit=False):
        url = st.text_input(
            "URL агента",
            value=current["api_url"],
            placeholder="https://ваш-сервер/api/v1/chat",
            help="Полный адрес метода, который принимает сообщения. Пустое поле отключает отправку запросов.",
        )
        key = st.text_input(
            "Ключ доступа",
            value=current["api_key"],
            type="password",
            help="Ключ авторизации вашего сервера. Передаётся в заголовках Authorization и X-API-Key. Не включается в экспорт.",
        )
        protocol = st.selectbox(
            "Протокол API",
            ["Custom Agent REST (JSON)", "OpenAI-Compatible (/v1/chat/completions)"],
            index=0 if current["protocol"] == "Custom Agent REST (JSON)" else 1,
            format_func=lambda p: "REST агента (JSON)" if p.startswith("Custom") else "Совместимый с OpenAI",
        )
        agent_id = st.text_input("Идентификатор агента или модели", value=current["agent_id"])
        sync = st.toggle(
            "Передавать расписание, задачи и курсы",
            value=current.get("sync_student_context", True),
            help="Прикреплённые файлы и история диалога передаются отдельно при отправке сообщения.",
        )
        with st.expander("Дополнительные параметры"):
            timeout = st.number_input(
                "Ожидание ответа, секунд", min_value=5, max_value=300, value=int(current["timeout"]), step=5
            )
            temperature = st.slider("Вариативность ответа", 0.0, 1.5, float(current["temperature"]), 0.05)
            prompt = st.text_area("Системная инструкция", value=current["system_prompt"], height=120)
        if st.form_submit_button("Сохранить настройки", type="primary"):
            if not validate_agent_url(url):
                st.error(
                    "Укажите полный URL с http:// или https://. Логин и пароль вводить в URL нельзя. Поля сохранены для исправления."
                )
            else:
                updated = {
                    "api_url": url.strip(),
                    "api_key": key.strip(),
                    "protocol": protocol,
                    "agent_id": agent_id.strip(),
                    "system_prompt": prompt.strip(),
                    "temperature": temperature,
                    "timeout": timeout,
                    "sync_student_context": sync,
                }
                if connection_signature(updated) != connection_signature(current):
                    st.session_state.pop("agent_connection", None)
                st.session_state.agent_settings = updated
                st.session_state.settings_saved_notice = True
                st.rerun()
    st.caption(
        "Проверка ниже использует сохранённые параметры. Она не отправляет сообщение и не меняет расписание."
    )
    if st.button("Проверить сохранённый адрес"):
        check_saved_address()
    st.caption(
        "Чтобы отключить агента, сохраните пустой URL. Ключ и остальные параметры можно оставить для следующего подключения."
    )


def render_pending(chat, history):
    pending = chat.get("pending_request")
    if not pending:
        return
    if pending["status"] == "queued":
        pending["status"] = "running"
        with history:
            with st.status("Агент готовит ответ…", expanded=False) as status:
                try:
                    reply = call_agent_api(pending["prompt"], chat)
                except AgentRequestError as exc:
                    pending.update(status="failed", error=str(exc), uncertain=exc.uncertain)
                    st.session_state.agent_connection = {
                        "signature": connection_signature(st.session_state.agent_settings),
                        "state": "error",
                    }
                    status.update(label="Ответ не получен", state="error")
                except Exception:
                    pending.update(
                        status="failed",
                        error="Не удалось обработать ответ. Настройки и история сохранены.",
                        uncertain=True,
                    )
                    status.update(label="Ответ не получен", state="error")
                else:
                    chat["messages"].append({"role": "assistant", "content": reply})
                    chat.pop("pending_request", None)
                    status.update(label="Ответ получен", state="complete")
        st.rerun()
    if pending["status"] == "running":
        # A Streamlit rerun may interrupt a blocking POST. Never repeat it silently.
        pending.update(
            status="failed",
            error="Ожидание запроса прервано. Сервер мог его обработать; проверьте историю перед повтором.",
            uncertain=True,
        )
    st.error(pending.get("error", "Ответ не получен."))
    if pending.get("uncertain"):
        st.caption(
            "Сервер мог выполнить действия, даже если ответ не дошёл. Повтор может создать задачу или событие ещё раз."
        )
        repeat_allowed = st.checkbox(
            "Я проверил результат и хочу повторить запрос", key=f"confirm_retry_{chat['id']}"
        )
    else:
        repeat_allowed = True
    if st.button("Повторить запрос", type="primary", disabled=not repeat_allowed, key=f"retry_{chat['id']}"):
        pending.update(status="queued")
        st.rerun()
    if st.button("Продолжить без повтора", key=f"dismiss_{chat['id']}"):
        chat.pop("pending_request", None)
        st.rerun()


def render_chat_interface(chat):
    st.caption(chat["title"])
    history = st.container(height=340, border=False, key="chat_history")
    with history:
        query = st.session_state.search_query.strip().casefold()
        messages = [m for m in chat["messages"] if not query or query in str(m["content"]).casefold()]
        if query:
            st.info(f"По запросу «{st.session_state.search_query}» найдено сообщений: {len(messages)}")
        elif not messages:
            st.markdown(
                '<div class="empty-chat"><h2>С чего начнём?</h2><p>Разберите сложную тему, составьте план подготовки или прикрепите конспект. Расписание и задачи можно передать агенту вместе с вопросом.</p></div>',
                unsafe_allow_html=True,
            )
            if not st.session_state.agent_settings.get("api_url"):
                st.info(
                    "Для ответа нужен агент. Откройте настройки и укажите URL сервера. Календарь и задачи доступны без подключения."
                )
            if st.button("Помоги спланировать учебный день", key="starter_prompt"):
                queue_prompt(
                    "Помоги спланировать учебный день с учётом моего расписания и ближайших сроков.", chat
                )
                st.rerun()
        for message in messages:
            with st.chat_message(
                message["role"],
                avatar=":material/person:" if message["role"] == "user" else ":material/school:",
            ):
                st.caption("Вы" if message["role"] == "user" else "CampusGDG")
                if message.get("attachments"):
                    st.markdown(
                        '<div class="attachment-bar">'
                        + "".join(
                            f'<span class="attachment-chip">{escape(str(name))}</span>'
                            for name in message["attachments"]
                        )
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                st.markdown(str(message["content"]))
    for warning in chat.get("upload_warnings", []):
        st.warning(warning)
    render_pending(chat, history)
    with st.popover("Файлы и инструменты", icon=":material/attach_file:", use_container_width=False):
        st.caption(
            "Файлы прикрепляются скрепкой в поле сообщения. До 10 файлов, каждый до 10 МБ. Они остаются в контексте этого диалога."
        )
        if not chat["uploaded_files"]:
            st.caption("Прикреплённых файлов пока нет.")
        for index, file in enumerate(chat["uploaded_files"]):
            st.text(f"{file['name']} · {file['size_kb']} КБ")
            if st.button("Убрать файл из контекста", key=f"remove_file_{chat['id']}_{index}"):
                chat["uploaded_files"].pop(index)
                st.rerun()
        st.divider()
        chat["web_search_enabled"] = st.toggle(
            "Запрашивать поиск в интернете",
            value=chat.get("web_search_enabled", False),
            key=f"web_{chat['id']}",
        )
        chat["drive_connected"] = st.toggle(
            "Передавать ссылку на Google Диск",
            value=chat.get("drive_connected", False),
            key=f"drive_{chat['id']}",
        )
        if chat["drive_connected"]:
            chat["drive_source"] = st.text_input(
                "Ссылка или папка Google Диска",
                value=chat.get("drive_source", ""),
                key=f"drive_source_{chat['id']}",
            )
        st.caption(
            "Доступность поиска и Google Диска зависит от сервера агента. Эти параметры только передают запрос; доступ к Диску здесь не проверяется."
        )
        if st.session_state.agent_settings.get("protocol", "").startswith("OpenAI"):
            st.caption(
                "В совместимом протоколе отправляются текстовые выдержки файлов. Изображения и содержимое PDF/DOCX через этот протокол не передаются."
            )
    if chat["uploaded_files"]:
        st.caption(
            f"В контексте файлов: {len(chat['uploaded_files'])}. Управление — в разделе «Файлы и инструменты»."
        )
    submission = st.chat_input(
        "Напишите сообщение или прикрепите файл",
        key=f"chat_input_box_{chat['id']}",
        accept_file="multiple",
        file_type=["pdf", "txt", "csv", "docx", "md", "json", "png", "jpg", "jpeg", "webp", "gif"],
        max_upload_size=10,
        max_chars=32000,
        disabled=bool(chat.get("pending_request")),
    )
    st.caption(
        "Enter — отправить · Shift+Enter — новая строка. История хранится в текущей сессии; важное можно экспортировать через меню."
    )
    if submission:
        text = submission if isinstance(submission, str) else submission.text
        files = [] if isinstance(submission, str) else submission.files
        chat["upload_warnings"] = process_uploaded_files(files, chat)
        if files and not text.strip():
            text = "Разбери прикреплённые материалы и выдели главное."
        if text.strip():
            queue_prompt(text, chat)
            st.rerun()


def clear_search():
    st.session_state.search_query = ""
    st.session_state.search_input = ""


def clear_dialog_search():
    st.session_state.sidebar_chat_search = ""


def render_sidebar():
    with st.sidebar:
        with st.container(key="sidebar_primary"):
            st.markdown('<p class="sidebar-brand">CampusGDG</p>', unsafe_allow_html=True)
            with st.container(key="sidebar_new_chat"):
                if st.button("Новый диалог", icon=":material/edit_square:", key="new_chat_button"):
                    clear_dialog_search()
                    create_new_chat()
                    navigate(CHAT)
            view = st.radio(
                "Разделы",
                list(VIEW_NAMES),
                format_func=lambda item: f"{VIEW_ICONS[item]} {VIEW_NAMES[item]}",
                index=list(VIEW_NAMES).index(st.session_state.active_view)
                if st.session_state.active_view in VIEW_NAMES
                else 0,
                key="nav_view",
                label_visibility="collapsed",
                width="stretch",
            )
            if view != st.session_state.active_view:
                st.session_state.active_view = view
                st.rerun()

        with st.container(key="sidebar_history"):
            with st.container(horizontal=True, vertical_alignment="center", key="sidebar_recent_heading"):
                st.caption("Недавние")
                with st.popover("Поиск", icon=":material/search:"):
                    st.text_input("Поиск диалогов", key="sidebar_chat_search", placeholder="Название диалога")
                    if st.session_state.get("sidebar_chat_search"):
                        st.button("Очистить поиск диалогов", on_click=clear_dialog_search)
            query = st.session_state.get("sidebar_chat_search", "").strip().casefold()
            ids = [
                cid
                for cid in reversed(st.session_state.chats)
                if not query or query in st.session_state.chats[cid]["title"].casefold()
            ]
            if query:
                st.caption(f"Найдено диалогов: {len(ids)}")
            visible_ids = ids[:12]
            if len(ids) > 12 and st.toggle("Показать все диалоги"):
                visible_ids = ids
            if not visible_ids:
                st.caption("Диалоги не найдены. Измените запрос или очистите поиск.")
            for cid in visible_ids:
                chat = st.session_state.chats[cid]
                active = cid == st.session_state.current_chat_id and st.session_state.active_view in (
                    CHAT,
                    SPLIT,
                )
                if st.button(
                    chat["title"],
                    key=f"select_chat_{cid}",
                    use_container_width=True,
                    type="primary" if active else "secondary",
                    help=chat["title"],
                ):
                    st.session_state.current_chat_id = cid
                    clear_search()
                    navigate(CHAT)
            if st.session_state.get("deleted_chat"):
                if st.button("Восстановить удалённый диалог", use_container_width=True):
                    chat = st.session_state.pop("deleted_chat")
                    st.session_state.chats[chat["id"]] = chat
                    st.session_state.current_chat_id = chat["id"]
                    navigate(CHAT)

        with st.container(key="sidebar_footer"):
            if st.button("Настройки агента", icon=":material/settings:", use_container_width=True):
                open_settings_dialog()
            state, label = connection_state()
            st.markdown(f'<div class="connection {state}">{escape(label)}</div>', unsafe_allow_html=True)
            st.caption("Локальная сессия · данные не синхронизируются с вузом")


def render_shell():
    if "next_view" in st.session_state:
        st.session_state.nav_view = st.session_state.pop("next_view")
    render_sidebar()

    chat = get_current_chat()
    left, right = st.columns([1.5, 1])
    with left:
        st.title(VIEW_NAMES.get(st.session_state.active_view, "Главная"))
        state, label = connection_state()
        st.markdown(f'<div class="connection {state}">{escape(label)}</div>', unsafe_allow_html=True)
    with right:
        with st.container(key="header_actions"):
            a, b = st.columns(2)
            with a:
                if st.button("Настройки", icon=":material/settings:", use_container_width=True):
                    open_settings_dialog()
            with b:
                with st.popover("Меню", icon=":material/more_horiz:", use_container_width=True):
                    if st.session_state.active_view in (CHAT, SPLIT):
                        st.session_state.search_query = st.text_input(
                            "Поиск по сообщениям",
                            value=st.session_state.search_query,
                            key="search_input",
                            placeholder="Слово или фраза",
                        )
                        if st.session_state.search_query:
                            st.button("Очистить поиск", on_click=clear_search)
                        with st.form(f"rename_{chat['id']}"):
                            name = st.text_input(
                                "Название диалога", value=chat["title"], key=f"rename_input_{chat['id']}"
                            )
                            if st.form_submit_button("Переименовать"):
                                if name.strip():
                                    rename_chat(chat["id"], name)
                                    st.rerun()
                                st.error("Введите название диалога.")
                        if st.button("Убрать текущий диалог", disabled=bool(chat.get("pending_request"))):
                            delete_chat(chat["id"])
                            st.rerun()
                        st.caption(
                            "Убранный диалог можно восстановить в боковой панели до следующего удаления."
                        )
                    bundle = {
                        "current_chat": {k: v for k, v in chat.items() if k != "pending_request"},
                        "calendar_events": st.session_state.calendar_events,
                        "student_tasks": st.session_state.student_tasks,
                    }
                    st.download_button(
                        "Скачать чат и расписание",
                        data=json.dumps(bundle, ensure_ascii=False, indent=2),
                        file_name=f"campusgdg_{today_local():%Y%m%d}.json",
                        mime="application/json",
                        use_container_width=True,
                    )
    if st.session_state.pop("settings_saved_notice", False):
        st.success("Настройки сохранены. Изменённое подключение нужно проверить запросом к агенту.")
    st.divider()
    if notice := st.session_state.pop("ui_notice", None):
        st.success(notice)
    if st.session_state.active_view == HOME:
        render_student_dashboard()
    elif st.session_state.active_view == PLANNER:
        render_planner()
    elif st.session_state.active_view == SPLIT:
        a, b = st.columns([1.6, 1], gap="large")
        with a:
            render_chat_interface(chat)
        with b:
            st.subheader("Ближайшие события")
            events = sorted(
                (
                    e
                    for e in st.session_state.calendar_events
                    if e.get("date", "") >= today_local().isoformat()
                ),
                key=lambda e: (e["date"], e.get("time", "")),
            )[:4]
            if not events:
                st.info("Предстоящих событий пока нет.")
            for event in events:
                render_event(event)
            if st.button("К календарю и задачам", use_container_width=True):
                navigate(PLANNER)
    else:
        render_chat_interface(chat)


init_session_state()
render_shell()

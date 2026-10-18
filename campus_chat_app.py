import base64
import calendar
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import uuid
import requests
import streamlit as st


def load_vertex_connection():
  connection = {}
  for name in ("vertex.config.json", "agent.connection.json"):
    config_path = Path(__file__).with_name(name)
    if config_path.exists():
      connection.update(json.loads(config_path.read_text(encoding="utf-8")))
  return connection


VERTEX_CONNECTION = load_vertex_connection()
VERTEX_RESOURCE = VERTEX_CONNECTION.get("agent_engine_resource", "")
DEFAULT_AGENT_URL = VERTEX_CONNECTION.get("api_url") or os.environ.get("AGENT_API_URL") or (
    "http://127.0.0.1:8000/api/v1/chat" if VERTEX_RESOURCE else ""
)

# =====================================================================
# КОНФИГУРАЦИЯ СТРАНИЦЫ CAMPUSGDG
# =====================================================================
st.set_page_config(
    page_title="CampusGDG — Student Hub & Assistant",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =====================================================================
# КАСТОМНЫЕ СТИЛИ (CAMPUSGDG DARK UI + КНОПКИ ВНУТРИ СТРОКИ ВВОДА ЧАТА)
# =====================================================================
CUSTOM_CSS = """
<style>
/* Основной фон приложения и сайдбара */
.stApp {
    background: radial-gradient(circle at 50% 10%, #161d2f 0%, #0d111c 70%, #090c14 100%);
    color: #e2e8f0;
}
[data-testid="stSidebar"] {
    background-color: #0e131f !important;
    border-right: 1px solid rgba(255, 255, 255, 0.08);
}

/* Скрытие стандартного хедера Streamlit для чистого вида */
header[data-testid="stHeader"] {
    background: transparent !important;
}

/* Шапка приложения (CampusGDG Assistant + статус + иконки) */
.campus-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.65rem 1.1rem;
    margin-bottom: 0.75rem;
    background: rgba(19, 25, 38, 0.78);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    backdrop-filter: blur(12px);
}
.campus-header-left {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
}
.campus-title {
    font-size: 1.25rem;
    font-weight: 700;
    color: #f8fafc;
    margin: 0;
}
.campus-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    background: rgba(16, 185, 129, 0.15);
    color: #34d399;
    border: 1px solid rgba(16, 185, 129, 0.35);
}
.campus-badge.demo {
    background: rgba(59, 130, 246, 0.15);
    color: #60a5fa;
    border: 1px solid rgba(59, 130, 246, 0.35);
}

/* =================================================================
   ЕДИНАЯ КАПСУЛА ВВОДА ЧАТА: [ ＋ ] [ 📎 ] [ Поле ввода ... ] [ Send ➤ ]
   Кнопки + и 📎 находятся ВНУТРИ самой строки ввода чата внизу
   ================================================================= */
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) {
    background: rgba(18, 25, 43, 0.95) !important;
    border: 1.5px solid rgba(96, 165, 250, 0.32) !important;
    border-radius: 999px !important;
    padding: 6px 14px !important;
    align-items: center !important;
    gap: 6px !important;
    box-shadow: 0 0 28px rgba(59, 130, 246, 0.18), inset 0 1px 2px rgba(255, 255, 255, 0.06) !important;
    margin-top: 0.35rem !important;
}

/* Круглые иконки [＋] и [📎] внутри левой части капсулы ввода */
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) .stButton > button,
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) [data-testid="stPopover"] > div > button {
    background: rgba(59, 130, 246, 0.14) !important;
    border: 1px solid rgba(96, 165, 250, 0.3) !important;
    border-radius: 50% !important;
    width: 40px !important;
    height: 40px !important;
    min-height: 40px !important;
    padding: 0 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    color: #cbd5e1 !important;
    font-size: 1.15rem !important;
    transition: all 0.2s ease !important;
    box-shadow: 0 0 12px rgba(59, 130, 246, 0.2) !important;
}
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) .stButton > button:hover,
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) [data-testid="stPopover"] > div > button:hover {
    background: linear-gradient(135deg, rgba(59, 130, 246, 0.35), rgba(139, 92, 246, 0.35)) !important;
    border-color: #60a5fa !important;
    color: #ffffff !important;
    box-shadow: 0 0 16px rgba(96, 165, 250, 0.55) !important;
}

/* Скрываем стандартную стрелочку у popover, чтобы кнопка была чистым плюсом [＋] */
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) [data-testid="stPopover"] > div > button svg {
    display: none !important;
}

/* Поле ввода текста бесшовно встроено внутрь капсулы */
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) [data-testid="stChatInput"] {
    background: transparent !important;
    padding: 0 !important;
}
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) [data-testid="stChatInput"] > div {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) [data-testid="stChatInputTextArea"] {
    color: #f8fafc !important;
    font-size: 0.96rem !important;
}

/* Кнопка Send ➤ справа внутри капсулы ввода (градиент как на макете) */
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) [data-testid="stChatInputSubmitButton"] {
    background: linear-gradient(90deg, #2563eb 0%, #7c3aed 100%) !important;
    color: #ffffff !important;
    border-radius: 999px !important;
    padding: 0 16px !important;
    height: 38px !important;
    width: auto !important;
    min-width: 84px !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    box-shadow: 0 0 18px rgba(124, 58, 237, 0.5) !important;
}
[data-testid="stHorizontalBlock"]:has([data-testid="stChatInput"]) [data-testid="stChatInputSubmitButton"]::before {
    content: "Send";
    font-weight: 600;
    font-size: 0.86rem;
    margin-right: 6px;
}

/* Карточки KPI на Dashboard */
.kpi-card {
    background: linear-gradient(145deg, rgba(30, 41, 59, 0.65), rgba(15, 23, 42, 0.85));
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    padding: 1.1rem 1.25rem;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
    transition: transform 0.2s ease, border-color 0.2s ease;
}
.kpi-card:hover {
    border-color: rgba(56, 189, 248, 0.4);
}
.kpi-label {
    font-size: 0.8rem;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
    margin-bottom: 0.35rem;
}
.kpi-value {
    font-size: 1.65rem;
    font-weight: 800;
    color: #f8fafc;
    line-height: 1.2;
}
.kpi-sub {
    font-size: 0.8rem;
    color: #38bdf8;
    margin-top: 0.3rem;
}

/* Карточка события календаря */
.event-card {
    background: rgba(30, 41, 59, 0.5);
    border-left: 4px solid #38bdf8;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
    border-right: 1px solid rgba(255, 255, 255, 0.06);
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px;
    padding: 0.75rem 1rem;
    margin-bottom: 0.6rem;
}
.event-card.exam {
    border-left-color: #f43f5e;
    background: rgba(244, 63, 94, 0.08);
}
.event-card.deadline {
    border-left-color: #f59e0b;
    background: rgba(245, 158, 11, 0.08);
}
.event-card.study {
    border-left-color: #a855f7;
}
.event-time {
    font-size: 0.78rem;
    font-weight: 700;
    color: #7dd3fc;
}
.event-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: #f8fafc;
    margin: 2px 0;
}
.event-meta {
    font-size: 0.8rem;
    color: #94a3b8;
}

/* Мини-сетка календаря */
.cal-grid {
    width: 100%;
    border-collapse: separate;
    border-spacing: 4px;
    text-align: center;
    font-size: 0.82rem;
}
.cal-grid th {
    color: #64748b;
    font-weight: 600;
    padding: 4px;
}
.cal-grid td {
    padding: 6px 4px;
    border-radius: 8px;
    background: rgba(30, 41, 59, 0.35);
    color: #cbd5e1;
}
.cal-grid td.today {
    background: rgba(59, 130, 246, 0.35);
    border: 1px solid #60a5fa;
    color: #ffffff;
    font-weight: 700;
}
.cal-grid td.has-event {
    border-bottom: 2px solid #38bdf8;
}

/* Зона Drag & Drop внутри чата (как на скриншоте 2) */
.dropzone-wrapper {
    border: 2px dashed #38bdf8;
    border-radius: 18px;
    padding: 1.35rem;
    margin: 0.6rem 0;
    background: radial-gradient(circle at center, rgba(56, 189, 248, 0.14) 0%, rgba(139, 92, 246, 0.08) 60%, rgba(15, 23, 42, 0.9) 100%);
    box-shadow: 0 0 28px rgba(56, 189, 248, 0.15), inset 0 0 20px rgba(139, 92, 246, 0.1);
    text-align: center;
}
.dropzone-title {
    font-size: 1.15rem;
    font-weight: 700;
    color: #f8fafc;
    margin-bottom: 0.2rem;
}
.dropzone-subtitle {
    font-size: 0.88rem;
    color: #7dd3fc;
    margin-bottom: 0.5rem;
}

/* Бейджи прикрепленных файлов и активных режимов */
.attachment-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0.35rem 0;
}
.attachment-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 0.8rem;
    background: rgba(59, 130, 246, 0.16);
    color: #93c5fd;
    border: 1px solid rgba(59, 130, 246, 0.35);
}
.mode-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 0.8rem;
    background: rgba(139, 92, 246, 0.18);
    color: #c4b5fd;
    border: 1px solid rgba(139, 92, 246, 0.4);
}

/* Сообщения чата */
[data-testid="stChatMessage"] {
    background: rgba(30, 41, 59, 0.45);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 16px;
    padding: 0.9rem 1.15rem;
    margin-bottom: 0.65rem;
}

/* Кнопки в сайдбаре */
[data-testid="stSidebar"] .stButton > button {
    border-radius: 10px;
    text-align: left;
    justify-content: flex-start;
    font-weight: 500;
    border: 1px solid rgba(255, 255, 255, 0.08);
    transition: all 0.2s ease;
}
[data-testid="stSidebar"] .stButton > button:hover {
    border-color: rgba(96, 165, 250, 0.5);
    background: rgba(59, 130, 246, 0.12);
}

/* Заголовок секции в сайдбаре */
.sidebar-section-label {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #64748b;
    margin: 1rem 0 0.45rem 0.2rem;
    font-weight: 600;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# =====================================================================
# ИНИЦИАЛИЗАЦИЯ СОСТОЯНИЯ (SESSION STATE)
# =====================================================================
def init_session_state():
  today = date.today()
  tomorrow = today + timedelta(days=1)
  in_two_days = today + timedelta(days=2)
  in_four_days = today + timedelta(days=4)

  # По умолчанию открываем чат или Dashboard
  if "active_view" not in st.session_state:
    st.session_state.active_view = "💬 Чат с Агентом"

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

  # Apply the configured connection once to chats opened before Vertex setup.
  if VERTEX_RESOURCE and st.session_state.get("vertex_connection_applied") != DEFAULT_AGENT_URL:
    previous_url = st.session_state.agent_settings.get("api_url", "").strip()
    if not previous_url or previous_url.startswith(("http://127.0.0.1:8000/", "http://localhost:8000/")):
      st.session_state.agent_settings.update(
          api_url=DEFAULT_AGENT_URL,
          protocol="Custom Agent REST (JSON)",
          agent_id=VERTEX_RESOURCE,
      )
    elif (st.session_state.agent_settings.get("api_url") == DEFAULT_AGENT_URL
          and st.session_state.agent_settings.get("agent_id") == "campusgdg-student-agent-v1"):
      st.session_state.agent_settings["agent_id"] = VERTEX_RESOURCE
    st.session_state.vertex_connection_applied = DEFAULT_AGENT_URL

  # События календаря студента
  if "calendar_events" not in st.session_state:
    st.session_state.calendar_events = [
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
    ]

  # Задачи и дедлайны студента
  if "student_tasks" not in st.session_state:
    st.session_state.student_tasks = [
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
    ]

  # Курсы и прогресс студента
  if "student_courses" not in st.session_state:
    st.session_state.student_courses = [
        {"name": "Computer Science", "grade": "94%", "gpa": 4.0, "credits": 5},
        {"name": "Математика и ML", "grade": "89%", "gpa": 3.7, "credits": 5},
        {"name": "Базы данных и SQL", "grade": "91%", "gpa": 3.9, "credits": 4},
        {"name": "AI & Deep Learning", "grade": "96%", "gpa": 4.0, "credits": 4},
    ]

  # Чаты
  if "chats" not in st.session_state:
    default_id = str(uuid.uuid4())
    second_id = str(uuid.uuid4())
    third_id = str(uuid.uuid4())
    st.session_state.chats = {
        default_id: {
            "id": default_id,
            "title": "Подготовка к Лаб №3 и Расписание",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Посмотри мой календарь и дедлайны на эту неделю. С чего"
                        " лучше начать подготовку сегодня?"
                    ),
                    "attachments": ["Lecture_Notes_Graphs.pdf"],
                },
                {
                    "role": "assistant",
                    "content": (
                        "Я проверил твой календарь и список задач в **CampusGDG"
                        " Dashboard**:\n\n1. **Сегодня (10:00 и 13:00):** у тебя"
                        " лекция по Алгоритмам и семинар по Линейной алгебре.\n2."
                        " **Главный приоритет на завтра (до 23:59):** сдача"
                        " **Лабораторной №3 (Графы и Дейкстра)** по курсу Computer"
                        " Science.\n3. **Через 2 дня:** ДЗ №4 по собственным"
                        " векторам.\n\nРекомендую сегодня после 15:00 выделить 1.5"
                        " часа на разбор алгоритма Дейкстры по файлу"
                        " `Lecture_Notes_Graphs.pdf`. Хочешь, я добавлю этот слот"
                        " боталки в твой календарь?"
                    ),
                },
            ],
            "uploaded_files": [
                {
                    "name": "Lecture_Notes_Graphs.pdf",
                    "type": "application/pdf",
                    "size_kb": 540.0,
                    "content_preview": "Конспект лекции: Алгоритм Дейкстры, очереди с приоритетом...",
                    "base64": "",
                }
            ],
            "web_search_enabled": False,
            "drive_connected": False,
            "drive_source": "",
        },
        second_id: {
            "id": second_id,
            "title": "CampusGDG Project Pitch",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Помоги структурировать питч нашего проекта для"
                        " CampusGDG."
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        "Давай разобьем питч **CampusGDG** на 4 ключевых блока:\n\n"
                        "1. **Проблема студента:** разрозненные дедлайны, конспекты"
                        " и расписание.\n2. **Решение:** единый хаб CampusGDG с"
                        " календарем и ИИ-агентом.\n3. **Демо:** живое добавление"
                        " задач в календарь и разбор PDF прямо из чата.\n4."
                        " **Интеграция:** подключение любого агентского бэкенда"
                        " через окно настроек `⚙️`."
                    ),
                },
            ],
            "uploaded_files": [],
            "web_search_enabled": False,
            "drive_connected": False,
            "drive_source": "",
        },
        third_id: {
            "id": third_id,
            "title": "Шпаргалка по SQL к Midterm",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "messages": [{
                "role": "assistant",
                "content": (
                    "Готов помочь с подготовкой к **Midterm Exam: Базы данных"
                    " и SQL**! Загрузи билеты или спроси про нормализацию и JOIN'ы."
                ),
            }],
            "uploaded_files": [],
            "web_search_enabled": False,
            "drive_connected": False,
            "drive_source": "",
        },
    }
    st.session_state.current_chat_id = default_id

  if "show_dropzone" not in st.session_state:
    st.session_state.show_dropzone = False

  if "dropzone_filter" not in st.session_state:
    st.session_state.dropzone_filter = "all"

  if "search_query" not in st.session_state:
    st.session_state.search_query = ""

  if "selected_cal_date" not in st.session_state:
    st.session_state.selected_cal_date = today


init_session_state()


# =====================================================================
# ХЕЛПЕРЫ УПРАВЛЕНИЯ ЧАТАМИ И ОТПРАВКИ ЗАДАЧ АГЕНТУ ИЗ DASHBOARD
# =====================================================================
def create_new_chat(title: str = "New Chat") -> str:
  new_id = str(uuid.uuid4())
  st.session_state.chats[new_id] = {
      "id": new_id,
      "title": title,
      "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
      "messages": [],
      "uploaded_files": [],
      "web_search_enabled": False,
      "drive_connected": False,
      "drive_source": "",
  }
  st.session_state.current_chat_id = new_id
  st.session_state.show_dropzone = False
  return new_id


def delete_chat(chat_id: str):
  if chat_id in st.session_state.chats:
    del st.session_state.chats[chat_id]
  if not st.session_state.chats:
    create_new_chat()
  elif st.session_state.current_chat_id == chat_id:
    st.session_state.current_chat_id = next(iter(st.session_state.chats.keys()))


def rename_chat(chat_id: str, new_title: str):
  cleaned = new_title.strip()
  if cleaned and chat_id in st.session_state.chats:
    st.session_state.chats[chat_id]["title"] = cleaned


def get_current_chat() -> dict:
  cid = st.session_state.current_chat_id
  if cid not in st.session_state.chats:
    create_new_chat()
  return st.session_state.chats[st.session_state.current_chat_id]


def send_prompt_from_dashboard(prompt_text: str, chat_title: str = ""):
  """Переключает студента из Dashboard в Чат (или Split-режим) и сразу отправляет запрос агенту."""
  current_chat = get_current_chat()
  if chat_title and current_chat["title"] == "New Chat" and not current_chat["messages"]:
    current_chat["title"] = chat_title

  current_chat["messages"].append({"role": "user", "content": prompt_text})
  reply = call_agent_api(prompt_text, current_chat)
  current_chat["messages"].append({"role": "assistant", "content": reply})

  if st.session_state.active_view == "📊 Dashboard студента":
    st.session_state.active_view = "⚡ Split: Чат + Календарь"


# =====================================================================
# ОБРАБОТКА ЗАГРУЖЕННЫХ ФАЙЛОВ
# =====================================================================
def process_uploaded_files(uploaded_files, current_chat: dict):
  if not uploaded_files:
    return
  existing_names = {f["name"] for f in current_chat["uploaded_files"]}
  added_count = 0
  for uf in uploaded_files:
    if uf.name in existing_names:
      continue
    raw_bytes = uf.getvalue()
    size_kb = round(len(raw_bytes) / 1024, 1)
    b64_data = base64.b64encode(raw_bytes).decode("utf-8")

    content_preview = ""
    if uf.name.lower().endswith((".txt", ".csv", ".md", ".json")):
      try:
        content_preview = raw_bytes.decode("utf-8", errors="ignore")[:4000]
      except Exception:
        content_preview = ""

    current_chat["uploaded_files"].append({
        "name": uf.name,
        "type": uf.type or "application/octet-stream",
        "size_kb": size_kb,
        "content_preview": content_preview,
        "base64": b64_data,
    })
    added_count += 1

  if added_count > 0:
    st.toast(f"Прикреплено файлов: {added_count}", icon="📎")


# =====================================================================
# АВТОМАТИЧЕСКИЕ ДЕЙСТВИЯ АГЕНТА С КАЛЕНДАРЕМ И ЗАДАЧАМИ (TOOL HOOKS)
# =====================================================================
def apply_agent_side_effects(user_message: str, agent_data: dict | None = None) -> list[str]:
  applied_logs = []

  if isinstance(agent_data, dict):
    for ev in agent_data.get("add_calendar_events", []):
      if isinstance(ev, dict) and ev.get("title"):
        st.session_state.calendar_events.append({
            "id": str(uuid.uuid4()),
            "title": ev.get("title"),
            "course": ev.get("course", "Общее"),
            "date": ev.get("date", date.today().strftime("%Y-%m-%d")),
            "time": ev.get("time", "16:00 - 17:00"),
            "location": ev.get("location", "Онлайн"),
            "type": ev.get("type", "study"),
        })
        applied_logs.append(f"📅 Добавлено в календарь: **{ev.get('title')}**")

    for tsk in agent_data.get("add_tasks", []):
      if isinstance(tsk, dict) and tsk.get("title"):
        st.session_state.student_tasks.insert(
            0,
            {
                "id": str(uuid.uuid4()),
                "title": tsk.get("title"),
                "course": tsk.get("course", "Общее"),
                "due_date": tsk.get("due_date", date.today().strftime("%Y-%m-%d")),
                "priority": tsk.get("priority", "Средний"),
                "done": False,
            },
        )
        applied_logs.append(f"📌 Добавлена задача: **{tsk.get('title')}**")

  lower_msg = user_message.lower()
  if not applied_logs and ("добавь в календарь" in lower_msg or "запланируй" in lower_msg):
    target_date = date.today()
    if "завтра" in lower_msg:
      target_date = date.today() + timedelta(days=1)
    elif "послезавтра" in lower_msg:
      target_date = date.today() + timedelta(days=2)

    time_match = re.search(r"(\d{1,2}:\d{2})", user_message)
    event_time = f"{time_match.group(1)}" if time_match else "17:00 - 18:00"

    cleaned_title = re.sub(
        r"(?i)(добавь в календарь|запланируй|завтра|сегодня|послезавтра|в\s*\d{1,2}:\d{2})",
        "",
        user_message,
    ).strip(" :,-.")
    if not cleaned_title:
      cleaned_title = "Учебная сессия (CampusGDG)"

    st.session_state.calendar_events.append({
        "id": str(uuid.uuid4()),
        "title": cleaned_title[:60].capitalize(),
        "course": "Самоподготовка",
        "date": target_date.strftime("%Y-%m-%d"),
        "time": event_time,
        "location": "CampusGDG Study Plan",
        "type": "study",
    })
    applied_logs.append(
        f"📅 Событие **«{cleaned_title[:60].capitalize()}»** автоматически добавлено в твой календарь на `{target_date.strftime('%Y-%m-%d')}` ({event_time})!"
    )

  if not applied_logs and ("добавь задачу" in lower_msg or "создай дедлайн" in lower_msg):
    cleaned_task = re.sub(
        r"(?i)(добавь задачу|создай дедлайн)", "", user_message
    ).strip(" :,-.")
    if cleaned_task:
      st.session_state.student_tasks.insert(
          0,
          {
              "id": str(uuid.uuid4()),
              "title": cleaned_task[:70].capitalize(),
              "course": "Учебный план",
              "due_date": (date.today() + timedelta(days=2)).strftime("%Y-%m-%d"),
              "priority": "Высокий",
              "done": False,
          },
      )
      applied_logs.append(
          f"📌 Задача **«{cleaned_task[:70].capitalize()}»** добавлена в твой трекер дедлайнов!"
      )

  return applied_logs


# =====================================================================
# ВЫЗОВ ВНЕШНЕГО АГЕНТА ПО API (С ПЕРЕДАЧЕЙ КОНТЕКСТА СТУДЕНТА)
# =====================================================================
def call_agent_api(user_message: str, chat: dict) -> str:
  settings = st.session_state.agent_settings
  api_url = settings.get("api_url", "").strip()
  api_key = settings.get("api_key", "").strip()
  protocol = settings.get("protocol", "Custom Agent REST (JSON)")

  student_context = {
      "today": date.today().strftime("%Y-%m-%d"),
      "calendar_events": st.session_state.calendar_events,
      "tasks": st.session_state.student_tasks,
      "courses": st.session_state.student_courses,
  }

  if not api_url:
    side_effects = apply_agent_side_effects(user_message)
    attached_names = [f["name"] for f in chat["uploaded_files"]]
    active_tools = []
    if chat.get("web_search_enabled"):
      active_tools.append("🌐 Web Search")
    if chat.get("drive_connected"):
      active_tools.append(
          f"☁️ Google Drive ({chat.get('drive_source') or 'подключен'})"
      )

    meta_lines = []
    if side_effects:
      meta_lines.extend(side_effects)
    if attached_names:
      meta_lines.append(f"📎 **Файлы в контексте:** `{', '.join(attached_names)}`")
    if active_tools:
      meta_lines.append(
          f"🛠️ **Активные инструменты:** {', '.join(active_tools)}"
      )

    meta_block = "\n\n".join(meta_lines)
    if meta_block:
      meta_block += "\n\n"

    pending_tasks = [t for t in st.session_state.student_tasks if not t["done"]]
    today_str = date.today().strftime("%Y-%m-%d")
    today_events = [
        e for e in st.session_state.calendar_events if e["date"] == today_str
    ]

    schedule_hint = (
        f"Сегодня у тебя по календарю **{len(today_events)}** зан., а активных"
        f" дедлайнов — **{len(pending_tasks)}**."
    )

    return (
        f"{meta_block}*(Демо-режим CampusGDG — API агента можно подключить по кнопке ⚙️"
        f" справа вверху)*\n\nЯ обработал твой запрос: **«{user_message}»**.\n\n"
        f"📊 **Связка с твоим Dashboard:** {schedule_hint}\n"
        "💡 *Подсказка:* напиши в чат `Добавь в календарь повтор матана завтра в 19:00` или `Добавь задачу сдать эссе` — и они сразу появятся в твоем расписании и списке дедлайнов!"
    )

  headers = {"Content-Type": "application/json"}
  if api_key:
    headers["Authorization"] = f"Bearer {api_key}"
    headers["X-API-Key"] = api_key

  if protocol == "OpenAI-Compatible (/v1/chat/completions)":
    messages_payload = []
    sys_text = settings.get("system_prompt", "")
    if settings.get("sync_student_context", True):
      sys_text += (
          "\n\nАктуальный календарь и дедлайны студента (JSON):\n"
          + json.dumps(student_context, ensure_ascii=False)
      )
    messages_payload.append({"role": "system", "content": sys_text})

    if chat["uploaded_files"]:
      files_context = "\n\n".join(
          f"File: {f['name']} ({f['type']}, {f['size_kb']} KB)\n{f['content_preview']}"
          for f in chat["uploaded_files"]
      )
      messages_payload.append({
          "role": "system",
          "content": f"Attached files in this chat:\n{files_context}",
      })

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
        "student_context": (
            student_context if settings.get("sync_student_context", True) else {}
        ),
        "history": [
            {"role": m["role"], "content": m["content"]}
            for m in chat["messages"][:-1]
        ],
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
        api_url,
        headers=headers,
        json=payload,
        timeout=int(settings.get("timeout", 60)),
    )
    response.raise_for_status()
    data = response.json()

    side_effects = apply_agent_side_effects(user_message, data if isinstance(data, dict) else None)
    prefix = ("\n".join(side_effects) + "\n\n") if side_effects else ""

    if isinstance(data, dict):
      if "choices" in data and len(data["choices"]) > 0:
        return prefix + data["choices"][0].get("message", {}).get("content", str(data))
      for key in ("output", "response", "reply", "answer", "content", "text"):
        if key in data and isinstance(data[key], str):
          return prefix + data[key]
    return prefix + json.dumps(data, ensure_ascii=False, indent=2)

  except Exception as exc:
    return f"⚠️ **Ошибка при обращении к API агента:**\n```text\n{exc}\n```"


# =====================================================================
# МОДАЛЬНОЕ ОКНО НАСТРОЕК (ПО КНОПКЕ С ШЕСТЕРЕНКОЙ ⚙️ СПРАВА ВВЕРХУ)
# =====================================================================
@st.dialog("⚙️ Настройки подключения к Агенту CampusGDG", width="large")
def open_settings_dialog():
  st.caption(
      "Подключите вашего ИИ-агента. Агент автоматически получает доступ к "
      "календарю, дедлайнам и файлам студента и может добавлять новые события."
  )

  current = st.session_state.agent_settings

  if VERTEX_RESOURCE:
    st.info("Google Cloud Vertex AI Agent Engine настроен. Для запросов используйте API-адрес ниже и ваш секретный ключ.")
    st.caption(f"Проект: {VERTEX_CONNECTION.get('project', '')}")
    st.code(VERTEX_RESOURCE, language=None)
    if st.button("Подключить настроенный Vertex AI агент", use_container_width=True):
      current.update(api_url=DEFAULT_AGENT_URL, protocol="Custom Agent REST (JSON)", agent_id=VERTEX_RESOURCE)
      st.rerun()

  protocol = st.selectbox(
      "Формат API протокола",
      options=[
          "Custom Agent REST (JSON)",
          "OpenAI-Compatible (/v1/chat/completions)",
      ],
      index=0 if current["protocol"] == "Custom Agent REST (JSON)" else 1,
  )

  api_url = st.text_input(
      "Agent API Endpoint URL",
      value=current["api_url"],
      placeholder="https://your-agent-server.com/api/v1/chat",
      help="Оставьте пустым для работы в локальном Демо-режиме",
  )

  api_key = st.text_input(
      "API Key / Bearer Token",
      value=current["api_key"],
      type="password",
      placeholder="Вставьте секретный ключ (CAMPUS_API_KEY)",
      help="Без верного ключа шлюз агента отвечает 401. Ключ хранится только в сессии браузера.",
  )

  col1, col2 = st.columns(2)
  with col1:
    agent_id = st.text_input(
        "ID Агента / Имя модели",
        value=current["agent_id"],
        placeholder="campusgdg-student-agent-v1",
    )
  with col2:
    timeout = st.number_input(
        "Таймаут запроса (сек)",
        min_value=5,
        max_value=300,
        value=int(current["timeout"]),
        step=5,
    )

  sync_context = st.toggle(
      "📅 Передавать агенту расписание календаря и список дедлайнов студента (student_context)",
      value=current.get("sync_student_context", True),
  )

  temperature = st.slider(
      "Temperature",
      min_value=0.0,
      max_value=1.5,
      value=float(current["temperature"]),
      step=0.05,
  )

  system_prompt = st.text_area(
      "System Prompt",
      value=current["system_prompt"],
      height=80,
  )

  with st.expander("📋 Схема JSON-обмена (Запрос к агенту и управление Календарем)"):
    st.markdown(
        "Ваш агент получает `student_context` (календарь, дедлайны, курсы) и может "
        "вернуть в ответе `add_calendar_events` или `add_tasks`, чтобы автоматически "
        "добавить событие на Dashboard студента:"
    )
    st.code(
        json.dumps(
            {
                "response": "Я добавил консультацию в твой календарь!",
                "add_calendar_events": [
                    {
                        "title": "Консультация по Алгоритмам",
                        "course": "Computer Science",
                        "date": date.today().strftime("%Y-%m-%d"),
                        "time": "18:00 - 19:00",
                        "location": "Онлайн",
                        "type": "study",
                    }
                ],
                "add_tasks": [
                    {
                        "title": "Прорешать 5 задач на графы",
                        "course": "Computer Science",
                        "due_date": (date.today() + timedelta(days=1)).strftime("%Y-%m-%d"),
                        "priority": "Высокий",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        language="json",
    )

  btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 1])
  with btn_col1:
    if st.button("💾 Сохранить настройки", type="primary", use_container_width=True):
      st.session_state.agent_settings = {
          "api_url": api_url.strip(),
          "api_key": api_key.strip(),
          "protocol": protocol,
          "agent_id": agent_id.strip(),
          "system_prompt": system_prompt.strip(),
          "temperature": temperature,
          "timeout": timeout,
          "sync_student_context": sync_context,
      }
      st.toast("Настройки агента сохранены!", icon="✅")
      st.rerun()

  with btn_col2:
    if st.button("🔌 Проверить соединение", use_container_width=True):
      if not api_url.strip():
        st.warning("Сначала введите URL эндпоинта агента.")
      else:
        try:
          headers = {}
          if api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
          resp = requests.get(api_url.strip(), headers=headers, timeout=8)
          resp.raise_for_status()
          st.success(f"Эндпоинт ответил со статусом HTTP {resp.status_code}")
        except Exception as e:
          st.error(f"Не удалось достучаться до эндпоинта: {e}")

  with btn_col3:
    if st.button("Сброс", use_container_width=True):
      st.session_state.agent_settings["api_url"] = ""
      st.session_state.agent_settings["api_key"] = ""
      st.toast("Переключено в Демо-режим", icon="ℹ️")
      st.rerun()


# =====================================================================
# РЕНДЕР МИНИ-СЕТКИ МЕСЯЦА В КАЛЕНДАРЕ
# =====================================================================
def render_month_calendar_html(target_date: date, events: list[dict]) -> str:
  cal = calendar.Calendar(firstweekday=0)
  month_days = cal.monthdayscalendar(target_date.year, target_date.month)
  month_name = target_date.strftime("%B %Y")

  event_days = set()
  prefix = target_date.strftime("%Y-%m-")
  for ev in events:
    if ev["date"].startswith(prefix):
      try:
        d_num = int(ev["date"].split("-")[2])
        event_days.add(d_num)
      except Exception:
        pass

  rows_html = ""
  for week in month_days:
    cells = ""
    for day_num in week:
      if day_num == 0:
        cells += "<td></td>"
      else:
        classes = []
        if day_num == date.today().day and target_date.month == date.today().month:
          classes.append("today")
        if day_num in event_days:
          classes.append("has-event")
        cls_attr = f' class="{" ".join(classes)}"' if classes else ""
        dot = " •" if day_num in event_days else ""
        cells += f"<td{cls_attr}>{day_num}{dot}</td>"
    rows_html += f"<tr>{cells}</tr>"

  return f"""
  <div style="background:rgba(15,23,42,0.65); border:1px solid rgba(255,255,255,0.07); border-radius:14px; padding:0.85rem; margin-bottom:0.85rem;">
      <div style="font-weight:700; color:#f8fafc; margin-bottom:0.5rem; text-align:center;">
          📅 {month_name}
      </div>
      <table class="cal-grid">
          <thead>
              <tr><th>Пн</th><th>Вт</th><th>Ср</th><th>Чт</th><th>Пт</th><th>Сб</th><th>Вс</th></tr>
          </thead>
          <tbody>{rows_html}</tbody>
      </table>
  </div>
  """


# =====================================================================
# КОМПОНЕНТ: ПОЛНОЦЕННЫЙ DASHBOARD СТУДЕНТА
# =====================================================================
def render_student_dashboard():
  today_str = date.today().strftime("%Y-%m-%d")
  today_events = [
      e for e in st.session_state.calendar_events if e["date"] == today_str
  ]
  pending_tasks = [t for t in st.session_state.student_tasks if not t["done"]]
  completed_tasks = [t for t in st.session_state.student_tasks if t["done"]]
  total_tasks = len(st.session_state.student_tasks)
  completion_pct = (
      int(round(len(completed_tasks) / total_tasks * 100)) if total_tasks else 100
  )
  avg_gpa = round(
      sum(c["gpa"] for c in st.session_state.student_courses)
      / max(len(st.session_state.student_courses), 1),
      2,
  )

  # 1. Верхние KPI-карточки студента
  k1, k2, k3, k4 = st.columns(4)
  with k1:
    next_ev = today_events[0]["title"][:26] + "..." if today_events else "Свободный день"
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-label">📅 Занятий сегодня</div>
            <div class="kpi-value">{len(today_events)}</div>
            <div class="kpi-sub">Ближайшее: {next_ev}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
  with k2:
    high_prio = sum(1 for t in pending_tasks if t["priority"] == "Высокий")
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-label">🔥 Активные дедлайны</div>
            <div class="kpi-value">{len(pending_tasks)}</div>
            <div class="kpi-sub">Высокий приоритет: {high_prio}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
  with k3:
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-label">✅ Прогресс задач</div>
            <div class="kpi-value">{completion_pct}%</div>
            <div class="kpi-sub">Закрыто {len(completed_tasks)} из {total_tasks}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
  with k4:
    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-label">🎓 Текущий GPA</div>
            <div class="kpi-value">{avg_gpa}</div>
            <div class="kpi-sub">Активных курсов: {len(st.session_state.student_courses)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

  st.write("")

  # Быстрые ИИ-действия для студента (One-click Agent Actions)
  qa_col1, qa_col2, qa_col3 = st.columns(3)
  with qa_col1:
    if st.button(
        "✨ Составить ИИ-план подготовки на день",
        use_container_width=True,
        type="primary",
    ):
      send_prompt_from_dashboard(
          "Проанализируй мое расписание на сегодня и горящие дедлайны. Составь"
          " почасовой план подготовки на вечер с перерывами.",
          chat_title="План подготовки на день",
      )
      st.rerun()
  with qa_col2:
    if st.button(
        "📅 Найти свободные окна в календаре",
        use_container_width=True,
    ):
      send_prompt_from_dashboard(
          "Посмотри мой календарь на ближайшие 4 дня и подскажи лучшие"
          " свободные слоты для подготовки к Midterm по Базам данных.",
          chat_title="Окна в календаре",
      )
      st.rerun()
  with qa_col3:
    if st.button(
        "🧠 Сделать выжимку и билеты по конспектам",
        use_container_width=True,
    ):
      send_prompt_from_dashboard(
          "Составь краткую шпаргалку и 5 проверочных вопросов по загруженным"
          " материалам и моим ближайшим экзаменам.",
          chat_title="Шпаргалка к экзамену",
      )
      st.rerun()

  st.write("")

  # 2. Основная сетка Dashboard: Слева — Календарь и Расписание, Справа — Дедлайны и Курсы
  left_col, right_col = st.columns([6, 6], gap="large")

  with left_col:
    st.subheader("📅 Календарь и Расписание")

    cal_top1, cal_top2 = st.columns([3, 2])
    with cal_top1:
      selected_date = st.date_input(
          "Выберите дату просмотра",
          value=st.session_state.selected_cal_date,
          key="dashboard_date_picker",
      )
      st.session_state.selected_cal_date = selected_date
    with cal_top2:
      show_all_dates = st.toggle("Показать всю неделю", value=True)

    st.markdown(
        render_month_calendar_html(selected_date, st.session_state.calendar_events),
        unsafe_allow_html=True,
    )

    with st.expander("➕ Добавить пару, дедлайн или боталку в календарь"):
      with st.form("add_cal_event_form", clear_on_submit=True):
        ev_title = st.text_input(
            "Название события", placeholder="Например: Консультация по Матану"
        )
        fc1, fc2 = st.columns(2)
        with fc1:
          ev_course = st.text_input("Предмет / Курс", value="Computer Science")
          ev_date = st.date_input("Дата", value=selected_date)
        with fc2:
          ev_time = st.text_input("Время", value="16:00 - 17:30")
          ev_type = st.selectbox(
              "Тип события",
              options=["lecture", "study", "deadline", "exam"],
              format_func=lambda x: {
                  "lecture": "📘 Лекция / Семинар",
                  "study": "🟣 Подготовка / Боталка",
                  "deadline": "🟠 Дедлайн сдачи",
                  "exam": "🔴 Экзамен / Коллоквиум",
              }[x],
          )
        ev_loc = st.text_input("Аудитория / Ссылка", value="Онлайн / Google Meet")
        if st.form_submit_button("Сохранить в календарь", type="primary"):
          if ev_title.strip():
            st.session_state.calendar_events.append({
                "id": str(uuid.uuid4()),
                "title": ev_title.strip(),
                "course": ev_course.strip() or "Общее",
                "date": ev_date.strftime("%Y-%m-%d"),
                "time": ev_time.strip(),
                "location": ev_loc.strip(),
                "type": ev_type,
            })
            st.toast("Событие добавлено в календарь!", icon="📅")
            st.rerun()

    events_list = sorted(
        st.session_state.calendar_events,
        key=lambda x: (x["date"], x["time"]),
    )
    if not show_all_dates:
      sel_str = selected_date.strftime("%Y-%m-%d")
      events_list = [e for e in events_list if e["date"] == sel_str]

    if not events_list:
      st.info("На выбранную дату событий нет. Отличное время для отдыха или самоподготовки!")
    else:
      for ev in events_list:
        type_cls = ev.get("type", "lecture")
        badge_map = {
            "lecture": "📘 Занятие",
            "study": "🟣 Подготовка",
            "deadline": "🟠 Дедлайн",
            "exam": "🔴 Экзамен",
        }
        ev_col1, ev_col2 = st.columns([5, 1])
        with ev_col1:
          st.markdown(
              f"""
              <div class="event-card {type_cls}">
                  <div class="event-time">{ev['date']} • {ev['time']} — {badge_map.get(type_cls, 'Событие')}</div>
                  <div class="event-title">{ev['title']}</div>
                  <div class="event-meta">🎓 {ev['course']} &nbsp;|&nbsp; 📍 {ev['location']}</div>
              </div>
              """,
              unsafe_allow_html=True,
          )
        with ev_col2:
          if st.button(
              "🤖",
              key=f"ask_ev_{ev['id']}",
              help="Попросить агента помочь с подготовкой к этому событию",
              use_container_width=True,
          ):
            send_prompt_from_dashboard(
                f"Помоги мне подготовиться к событию из моего календаря: «{ev['title']}» "
                f"(Курс: {ev['course']}, Дата: {ev['date']}, Время: {ev['time']}). "
                "Составь краткий план действий и список ключевых тем.",
                chat_title=ev["title"][:28],
            )
            st.rerun()
          if st.button(
              "🗑️",
              key=f"del_ev_{ev['id']}",
              help="Удалить событие из календаря",
              use_container_width=True,
          ):
            st.session_state.calendar_events = [
                item
                for item in st.session_state.calendar_events
                if item["id"] != ev["id"]
            ]
            st.rerun()

  with right_col:
    st.subheader("📌 Дедлайны и Задания")

    with st.expander("➕ Добавить задание / лабораторную"):
      with st.form("add_student_task_form", clear_on_submit=True):
        t_title = st.text_input(
            "Что нужно сделать?",
            placeholder="Например: Сдать отчет по практике",
        )
        tc1, tc2, tc3 = st.columns(3)
        with tc1:
          t_course = st.text_input("Предмет", value="Computer Science")
        with tc2:
          t_due = st.date_input("Срок сдачи", value=date.today() + timedelta(days=2))
        with tc3:
          t_prio = st.selectbox("Приоритет", ["Высокий", "Средний", "Низкий"])

        if st.form_submit_button("Добавить задачу", type="primary"):
          if t_title.strip():
            st.session_state.student_tasks.insert(
                0,
                {
                    "id": str(uuid.uuid4()),
                    "title": t_title.strip(),
                    "course": t_course.strip(),
                    "due_date": t_due.strftime("%Y-%m-%d"),
                    "priority": t_prio,
                    "done": False,
                },
            )
            st.toast("Задача добавлена!", icon="📌")
            st.rerun()

    for task in st.session_state.student_tasks:
      t_col1, t_col2, t_col3 = st.columns([0.6, 4.4, 1.3])
      with t_col1:
        checked = st.checkbox(
            "Готово",
            value=task["done"],
            key=f"chk_task_{task['id']}",
            label_visibility="collapsed",
        )
        if checked != task["done"]:
          task["done"] = checked
          st.rerun()

      with t_col2:
        prio_color = {
            "Высокий": "#f43f5e",
            "Средний": "#f59e0b",
            "Низкий": "#10b981",
        }.get(task["priority"], "#60a5fa")
        strike = "text-decoration: line-through; opacity: 0.55;" if task["done"] else ""
        st.markdown(
            f"""
            <div style="{strike} padding: 2px 0 8px 0;">
                <span style="font-weight:600; color:#f8fafc;">{task['title']}</span><br/>
                <span style="font-size:0.78rem; color:#94a3b8;">
                    🎓 {task['course']} • ⏰ до <b>{task['due_date']}</b> •
                    <span style="color:{prio_color}; font-weight:600;">{task['priority']}</span>
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

      with t_col3:
        if st.button(
            "✨ В чат",
            key=f"solve_task_{task['id']}",
            help="Разобрать и решить эту задачу вместе с ИИ-агентом",
            use_container_width=True,
        ):
          send_prompt_from_dashboard(
              f"Помоги мне выполнить учебную задачу: «{task['title']}» "
              f"по предмету «{task['course']}» (дедлайн: {task['due_date']}). "
              "Разбей её на понятные шаги и дай стартовый шаблон решения.",
              chat_title=task["title"][:28],
          )
          st.rerun()

    st.divider()
    st.subheader("🎓 Мои Курсы и Успеваемость")
    for course in st.session_state.student_courses:
      cc1, cc2, cc3 = st.columns([3.5, 1.2, 1.3])
      with cc1:
        st.markdown(f"**{course['name']}** (`{course['credits']} ECTS`)")
      with cc2:
        st.markdown(f"Оценка: **{course['grade']}**")
      with cc3:
        st.markdown(f"GPA: `{course['gpa']}`")


# =====================================================================
# КОМПОНЕНТ: ИНТЕРФЕЙС ЧАТА CAMPUSGDG
# Кнопки [＋] и [📎] встроены ВНУТРЬ нижней капсулы ввода чата
# =====================================================================
def render_chat_interface(current_chat: dict):
  # 1. Окно истории сообщений (прокручиваемая область чата)
  chat_container = st.container(height=500, border=False)
  with chat_container:
    messages_to_show = current_chat["messages"]
    query = st.session_state.search_query.strip().lower()
    if query:
      messages_to_show = [
          m for m in messages_to_show if query in m["content"].lower()
      ]
      st.info(
          f"🔍 Фильтр по запросу «{st.session_state.search_query}»: найдено "
          f"сообщений — {len(messages_to_show)}"
      )

    if not messages_to_show and not query:
      st.markdown(
          """
          <div style="text-align:center; padding: 3.5rem 1rem; color: #94a3b8;">
              <h3 style="color:#f8fafc; margin-bottom:0.4rem;">Привет! Я твой учебный ИИ-агент CampusGDG ✨</h3>
              <p style="font-size:0.95rem;">
                  Я вижу твое расписание и дедлайны из <b>Dashboard</b>. Нажми <b>＋</b> или <b>📎</b> внутри строки ввода внизу,
                  чтобы прикрепить конспект, либо попроси меня запланировать подготовку в календаре!
              </p>
          </div>
          """,
          unsafe_allow_html=True,
      )

    for msg in messages_to_show:
      avatar = "🧑‍💻" if msg["role"] == "user" else "✨"
      with st.chat_message(msg["role"], avatar=avatar):
        if msg.get("attachments"):
          chips = " ".join(
              f'<span class="attachment-chip">📄 {name}</span>'
              for name in msg["attachments"]
          )
          st.markdown(
              f'<div class="attachment-bar">{chips}</div>',
              unsafe_allow_html=True,
          )
        st.markdown(msg["content"])

    # Если открыта зона Drag & Drop (по клику на 📎 или выбору в меню ＋) — она открывается прямо внутри области чата
    if st.session_state.show_dropzone:
      filter_mode = st.session_state.dropzone_filter
      if filter_mode == "document":
        allowed_types = ["pdf", "txt", "csv", "docx", "md", "json"]
        subtitle_text = "(PDF, TXT, CSV, DOCX, JSON supported)"
        header_text = "Drop documents here to upload"
      elif filter_mode == "image":
        allowed_types = ["png", "jpg", "jpeg", "webp", "gif"]
        subtitle_text = "(PNG, JPG, WEBP, GIF images supported)"
        header_text = "Drop images here to upload"
      else:
        allowed_types = [
            "pdf",
            "txt",
            "csv",
            "docx",
            "md",
            "json",
            "png",
            "jpg",
            "jpeg",
            "webp",
        ]
        subtitle_text = "(PDF, TXT, CSV, images supported)"
        header_text = "Drop files here to upload"

      st.markdown(
          f"""
          <div class="dropzone-wrapper">
              <div style="font-size:2.2rem; margin-bottom:0.2rem;">📄⬆️📎</div>
              <div class="dropzone-title">{header_text}</div>
              <div class="dropzone-subtitle">{subtitle_text}</div>
          </div>
          """,
          unsafe_allow_html=True,
      )

      uploaded = st.file_uploader(
          "Перетащите файлы сюда или нажмите Browse files",
          type=allowed_types,
          accept_multiple_files=True,
          key=f"uploader_{current_chat['id']}_{filter_mode}",
      )
      if uploaded:
        process_uploaded_files(uploaded, current_chat)

      if st.button("✖️ Закрыть окно загрузки", key="close_dropzone_btn"):
        st.session_state.show_dropzone = False
        st.rerun()

  # 2. Чипы прикрепленных файлов и активных режимов (прямо над капсулой ввода)
  has_files = len(current_chat["uploaded_files"]) > 0
  has_modes = current_chat.get("web_search_enabled") or current_chat.get(
      "drive_connected"
  )

  if has_files or has_modes:
    chips_html = []
    for f in current_chat["uploaded_files"]:
      chips_html.append(
          f'<span class="attachment-chip">📎 {f["name"]} ({f["size_kb"]} KB)</span>'
      )
    if current_chat.get("web_search_enabled"):
      chips_html.append('<span class="mode-chip">🌐 Web Search: ON</span>')
    if current_chat.get("drive_connected"):
      src = current_chat.get("drive_source") or "Connected"
      chips_html.append(f'<span class="mode-chip">☁️ Drive: {src}</span>')

    bar_col1, bar_col2 = st.columns([6, 1])
    with bar_col1:
      st.markdown(
          f'<div class="attachment-bar">{"".join(chips_html)}</div>',
          unsafe_allow_html=True,
      )
    with bar_col2:
      if st.button("Очистить📎", help="Убрать прикрепленные файлы из контекста"):
        current_chat["uploaded_files"] = []
        st.rerun()

  # 3. ЕДИНАЯ КАПСУЛА ВВОДА ВНИЗУ ЧАТА:
  # [ ＋ ] [ 📎 ] [ Ask CampusGDG anything, or drag files here... ] [ Send ➤ ]
  # Все элементы находятся внутри одного горизонтального блока!
  plus_col, clip_col, input_col = st.columns(
      [0.65, 0.65, 10.7], vertical_alignment="center"
  )

  with plus_col:
    with st.popover("＋", help="Добавить документ, фото, Диск или Веб-поиск"):
      st.markdown("**Действия и инструменты**")

      if st.button("📄 Upload Document", use_container_width=True):
        st.session_state.dropzone_filter = "document"
        st.session_state.show_dropzone = True
        st.rerun()

      if st.button("🖼️ Add Image", use_container_width=True):
        st.session_state.dropzone_filter = "image"
        st.session_state.show_dropzone = True
        st.rerun()

      drive_state = current_chat.get("drive_connected", False)
      drive_label = (
          "☁️ Disconnect Drive (Active)" if drive_state else "☁️ Connect Drive"
      )
      if st.button(drive_label, use_container_width=True):
        current_chat["drive_connected"] = not drive_state
        if current_chat["drive_connected"] and not current_chat.get(
            "drive_source"
        ):
          current_chat["drive_source"] = "My Drive / Конспекты семестра"
        st.rerun()

      if current_chat.get("drive_connected"):
        current_chat["drive_source"] = st.text_input(
            "Папка / Ссылка Google Drive",
            value=current_chat.get("drive_source", ""),
            placeholder="https://drive.google.com/...",
            key=f"drive_input_{current_chat['id']}",
        )

      web_state = current_chat.get("web_search_enabled", False)
      web_label = "🌐 Web Search: ON ✅" if web_state else "🌐 Web Search"
      if st.button(web_label, use_container_width=True):
        current_chat["web_search_enabled"] = not web_state
        st.rerun()

  with clip_col:
    if st.button(
        "📎",
        help="Прикрепить файлы (Drag & Drop: PDF, TXT, CSV, изображения)",
    ):
      st.session_state.dropzone_filter = "all"
      st.session_state.show_dropzone = not st.session_state.show_dropzone
      st.rerun()

  with input_col:
    user_prompt = st.chat_input(
        "Ask CampusGDG anything, or drag files here...",
        key=f"chat_input_box_{current_chat['id']}",
    )

  if user_prompt:
    if (
        current_chat["title"] == "New Chat"
        and len(current_chat["messages"]) == 0
    ):
      short_title = user_prompt[:32] + ("..." if len(user_prompt) > 32 else "")
      current_chat["title"] = short_title

    attached_names = [f["name"] for f in current_chat["uploaded_files"]]
    current_chat["messages"].append({
        "role": "user",
        "content": user_prompt,
        "attachments": attached_names,
    })

    with chat_container:
      with st.chat_message("user", avatar="🧑‍💻"):
        if attached_names:
          chips = " ".join(
              f'<span class="attachment-chip">📄 {name}</span>'
              for name in attached_names
          )
          st.markdown(
              f'<div class="attachment-bar">{chips}</div>',
              unsafe_allow_html=True,
          )
        st.markdown(user_prompt)

      with st.chat_message("assistant", avatar="✨"):
        with st.spinner("●●● typing..."):
          reply_text = call_agent_api(user_prompt, current_chat)
        st.markdown(reply_text)

    current_chat["messages"].append({"role": "assistant", "content": reply_text})
    st.rerun()


# =====================================================================
# САЙДБАР: НАВИГАЦИЯ (DASHBOARD / ЧАТ / SPLIT) И ИСТОРИЯ ЧАТОВ
# =====================================================================
with st.sidebar:
  top_col1, top_col2 = st.columns([4, 1])
  with top_col1:
    st.markdown("### ✨ **CampusGDG**")
  with top_col2:
    if st.button("📝", help="Быстрое создание нового чата"):
      create_new_chat()
      st.session_state.active_view = "💬 Чат с Агентом"
      st.rerun()

  st.markdown(
      '<div class="sidebar-section-label">Рабочее пространство</div>',
      unsafe_allow_html=True,
  )
  view_options = [
      "📊 Dashboard студента",
      "💬 Чат с Агентом",
      "⚡ Split: Чат + Календарь",
  ]
  selected_view = st.radio(
      "Режим отображения",
      options=view_options,
      index=view_options.index(st.session_state.active_view),
      label_visibility="collapsed",
  )
  if selected_view != st.session_state.active_view:
    st.session_state.active_view = selected_view
    st.rerun()

  st.write("")
  if st.button("＋ New Chat", use_container_width=True, type="primary"):
    create_new_chat()
    if st.session_state.active_view == "📊 Dashboard студента":
      st.session_state.active_view = "💬 Чат с Агентом"
    st.rerun()

  st.markdown(
      '<div class="sidebar-section-label">Recent Chats</div>',
      unsafe_allow_html=True,
  )

  chat_ids = list(st.session_state.chats.keys())
  for cid in chat_ids:
    chat_item = st.session_state.chats[cid]
    is_active = cid == st.session_state.current_chat_id

    row_col1, row_col2 = st.columns([5, 1])
    with row_col1:
      label = f"{'🟣 ' if is_active else '💬 '}{chat_item['title']}"
      if st.button(
          label,
          key=f"select_chat_{cid}",
          use_container_width=True,
          type="secondary",
      ):
        st.session_state.current_chat_id = cid
        if st.session_state.active_view == "📊 Dashboard студента":
          st.session_state.active_view = "💬 Чат с Агентом"
        st.rerun()

    with row_col2:
      with st.popover("⋮", help="Переименовать или удалить чат"):
        st.caption(f"Создан: {chat_item['created_at']}")
        new_name = st.text_input(
            "Название чата",
            value=chat_item["title"],
            key=f"rename_input_{cid}",
        )
        c1, c2 = st.columns(2)
        with c1:
          if st.button(
              "✏️ Сохранить", key=f"save_name_{cid}", use_container_width=True
          ):
            rename_chat(cid, new_name)
            st.rerun()
        with c2:
          if st.button(
              "🗑️ Удалить", key=f"del_chat_{cid}", use_container_width=True
          ):
            delete_chat(cid)
            st.rerun()


# =====================================================================
# ШАПКА ПРИЛОЖЕНИЯ С КНОПКОЙ НАСТРОЕК АГЕНТА (⚙️ СПРАВА)
# =====================================================================
current_chat = get_current_chat()
api_configured = bool(st.session_state.agent_settings.get("api_url", "").strip())

hdr_left, hdr_right = st.columns([6.5, 2])
with hdr_left:
  badge_html = (
      '<span class="campus-badge">● Active (Agent API)</span>'
      if api_configured
      else '<span class="campus-badge demo">● Active (Demo Mode)</span>'
  )
  subtitle_context = (
      "Student Workspace & Calendar"
      if st.session_state.active_view == "📊 Dashboard студента"
      else current_chat["title"]
  )
  st.markdown(
      f"""
      <div class="campus-header">
          <div class="campus-header-left">
              <span class="campus-title">CampusGDG Assistant</span>
              {badge_html}
              <span style="color:#64748b; font-size:0.88rem; margin-left:6px;">
                  — {subtitle_context}
              </span>
          </div>
      </div>
      """,
      unsafe_allow_html=True,
  )

with hdr_right:
  icon_col1, icon_col2, icon_col3 = st.columns(3)

  with icon_col1:
    if st.button("⚙️", help="Настройки API Агента", use_container_width=True):
      open_settings_dialog()

  with icon_col2:
    with st.popover("🔍", help="Поиск по сообщениям чата", use_container_width=True):
      st.session_state.search_query = st.text_input(
          "Фильтр сообщений в чате",
          value=st.session_state.search_query,
          placeholder="Введите слово для поиска...",
      )
      if st.session_state.search_query and st.button("Сбросить фильтр"):
        st.session_state.search_query = ""
        st.rerun()

  with icon_col3:
    with st.popover("🔗", help="Экспорт данных", use_container_width=True):
      st.caption("Экспорт чата и расписания студента")
      export_bundle = {
          "current_chat": current_chat,
          "calendar_events": st.session_state.calendar_events,
          "student_tasks": st.session_state.student_tasks,
      }
      st.download_button(
          label="📥 Скачать JSON",
          data=json.dumps(export_bundle, ensure_ascii=False, indent=2),
          file_name=f"campusgdg_export_{date.today().strftime('%Y%m%d')}.json",
          mime="application/json",
          use_container_width=True,
      )


# =====================================================================
# ОТРИСОВКА ВЫБРАННОГО РЕЖИМА ЭКРАНА
# =====================================================================
if st.session_state.active_view == "📊 Dashboard студента":
  render_student_dashboard()

elif st.session_state.active_view == "💬 Чат с Агентом":
  render_chat_interface(current_chat)

else:
  # ⚡ Split-режим: Чат слева + Живой Календарь и Дедлайны справа
  chat_col, side_dash_col = st.columns([7, 4.5], gap="large")
  with chat_col:
    render_chat_interface(current_chat)

  with side_dash_col:
    st.markdown("#### 📅 Календарь и Дедлайны (Live)")
    st.markdown(
        render_month_calendar_html(date.today(), st.session_state.calendar_events),
        unsafe_allow_html=True,
    )

    st.markdown("**Ближайшие события:**")
    for ev in sorted(
        st.session_state.calendar_events, key=lambda x: (x["date"], x["time"])
    )[:4]:
      type_cls = ev.get("type", "lecture")
      st.markdown(
          f"""
          <div class="event-card {type_cls}">
              <div class="event-time">{ev['date']} • {ev['time']}</div>
              <div class="event-title">{ev['title']}</div>
              <div class="event-meta">🎓 {ev['course']} • 📍 {ev['location']}</div>
          </div>
          """,
          unsafe_allow_html=True,
      )

    st.markdown("**Горящие задачи:**")
    for task in [t for t in st.session_state.student_tasks if not t["done"]][:4]:
      tc1, tc2 = st.columns([0.15, 0.85])
      with tc1:
        done_now = st.checkbox(
            "done",
            value=task["done"],
            key=f"split_chk_{task['id']}",
            label_visibility="collapsed",
        )
        if done_now != task["done"]:
          task["done"] = done_now
          st.rerun()
      with tc2:
        st.markdown(
            f"**{task['title']}**  \n"
            f"<span style='font-size:0.78rem; color:#94a3b8;'>🎓 {task['course']} • до {task['due_date']}</span>",
            unsafe_allow_html=True,
        )

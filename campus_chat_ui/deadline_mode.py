"""The 'До сдачи' workspace: evidence, remaining effort and a reviewable calendar diff."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta
from html import escape
import json
import logging
import re
import traceback
from urllib.parse import urlsplit

import requests
import streamlit as st

from agent.attachments import attachment_document, decode_attachments
from agent.deadline import build_plan, digest, review_work, run_deadline
from agent.gateway_review import GatewayReviewError, run_gateway_review
from campuspulse.config import Settings
from campuspulse.models import AnalysisResult, DeadlineRequest
from campuspulse.security import validate_repository_url
from campus_chat_ui.deadline_state import apply_plan, owned, plan_events, undo_plan
from campus_chat_ui.tool_activity import public_steps, render_activity

LABELS = {"satisfied": "Подтверждено", "not_satisfied": "Нужна работа", "unverified": "Не проверено"}
REVIEW_MESSAGES = {
    "The proposed conclusion could not be grounded in sufficient collected evidence.":
        "Вывод агента не прошёл проверку по полученным источникам.",
    "The assignment requirement needs clarification.": "Нужно уточнить условие задания.",
    "Gemini did not assess this requirement.": "Агент не вернул оценку этого требования.",
    "An unsupported or ambiguous conclusion was marked unverified.":
        "Часть требований осталась непроверенной. Причины указаны в карточках ниже.",
    "A missing finding was marked unverified.": "Агент пропустил требование; оно осталось непроверенным.",
}
VERIFICATION_REASONS = {
    "missing_finding": "Агент не вернул оценку требования.",
    "ambiguous_requirement": "В условии есть неоднозначность; сначала нужно её уточнить.",
    "unknown_evidence_id": "Агент сослался на источник, которого нет среди полученных данных.",
    "url_in_explanation": "Агент указал ссылку в обход проверяемых идентификаторов источников.",
    "no_direct_negative_observation": "Нет прямого наблюдения, подтверждающего невыполнение требования.",
    "missing_evidence_id": "Агент не указал идентификатор подтверждающего источника.",
    "missing_citation": "Агент не привёл точную цитату из источника.",
    "citation_mismatch": "Цитата агента не совпала с текстом указанного источника.",
    "citation_coverage_mismatch": "Не для всех указанных источников приведены проверяемые цитаты.",
    "incorrect_basis": "Обоснование агента не соответствует предложенному статусу.",
}
LOGGER = logging.getLogger(__name__)


def duration(minutes):
    hours, rest = divmod(int(minutes), 60)
    return (f"{hours} ч " if hours else "") + (f"{rest} мин" if rest or not hours else "")


def request_signature(payload):
    return digest({k: v for k, v in payload.items() if k not in {"start_at", "previous_plan"}})


def _accept_response(data, payload):
    if not isinstance(data, dict) or not isinstance(data.get("deadline"), dict):
        raise ValueError("Подключённый агент не вернул структурированную проверку «До сдачи». Обычный ответ чата не заменяет проверку требований.")
    result = data["deadline"]
    if result.get("action") != payload["action"]:
        raise ValueError("Сервер вернул результат другой операции. Повторите запрос.")
    if payload["action"] == "analyze":
        if (not isinstance(result.get("document_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", result["document_sha256"])
                or result.get("repository_url") != payload["repository_url"]):
            raise ValueError("Проверка не привязана к выбранному заданию и репозиторию.")
        analysis = AnalysisResult.model_validate(result["analysis"])
        result = {**result, "work": review_work(analysis, result["document_sha256"])}
    else:
        expected = build_plan(DeadlineRequest.model_validate(payload))
        if result.get("plan") != expected:
            raise ValueError("План не соответствует отправленным ограничениям. Календарь не изменён.")
    return result


def _run_pending(workspace, settings):
    pending = workspace.get("pending")
    if not pending:
        return
    if (pending["status"] == "failed"
            and pending.get("error", "").startswith("Подключённый агент не вернул структурированную проверку")):
        pending["transport"] = "gateway-text"
        workspace["compatibility_url"] = settings.get("api_url", "")
        pending["error"] = "Этот агент использует старый формат API. Запустите совместимую проверку через тот же gateway."
    if pending["status"] == "running":
        pending.update(status="failed", error="Запрос был прерван. Последний результат сохранён; повторите проверку вручную.")
    if pending["status"] == "queued":
        pending["status"] = "running"
        label = "Проверяем задание и репозиторий…" if pending["payload"]["action"] == "analyze" else "Пересчитываем свободные интервалы…"
        with st.status(label, expanded=True) as status:
            local_plan = pending["payload"]["action"] == "plan"
            st.caption("Рассчитываем план локально по вашим ограничениям." if local_plan else
                       "Ожидаем результат агента. Журнал фактических действий появится после ответа.")
            try:
                key = settings.get("api_key", "").strip()
                if local_plan:
                    data = run_deadline(pending["payload"], None, Settings(_env_file=None))
                    for step in data["steps"]:
                        step["summary"] = "Локальный расчёт; облачный агент не вызывался."
                        step["result"]["execution"] = "local"
                else:
                    if not settings.get("api_url", "").strip():
                        raise ValueError("Укажите адрес агента и ключ в настройках.")
                    if settings.get("protocol", "").startswith("OpenAI"):
                        raise ValueError("Для этого режима выберите Custom Agent REST (JSON) в настройках.")
                    compatibility = (pending.get("transport") == "gateway-text"
                                     or workspace.get("compatibility_url") == settings["api_url"])
                    if compatibility:
                        st.caption("Совместимый режим: модель через ваш gateway, чтение публичного GitHub — через наш локальный MCP.")
                        data = run_gateway_review(pending["payload"], pending.get("files", []), settings,
                                                  progress=st.caption)
                    else:
                        response = requests.post(settings["api_url"].strip(),
                            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                            json={"message": "До сдачи", "mode": "deadline", "deadline_request": pending["payload"],
                                  "files": pending.get("files", [])},
                            timeout=min(300, max(240, int(settings.get("timeout", 60)))))
                        response.raise_for_status()
                        data = response.json()
                        if not isinstance(data, dict) or not isinstance(data.get("deadline"), dict):
                            pending["transport"] = "gateway-text"
                            workspace["compatibility_url"] = settings["api_url"]
                            raise GatewayReviewError("Этот агент использует старый формат API. Запустите совместимую проверку через тот же gateway.")
                result = _accept_response(data, pending["payload"])
                if result["action"] == "analyze":
                    document = attachment_document(decode_attachments(pending["files"])[0])
                    if result["document_sha256"] != document.sha256:
                        raise ValueError("Сервер проверил другой файл. Предыдущий результат сохранён.")
                    old = workspace.get("review", {})
                    if (old.get("document_sha256"), old.get("repository_url")) != (result["document_sha256"], result["repository_url"]):
                        workspace["estimates"] = {}
                        for widget_key in list(st.session_state):
                            if widget_key.startswith("deadline_estimate_"):
                                del st.session_state[widget_key]
                    workspace.update(review=result, plan=None, source_file=deepcopy(pending["files"][0]))
                else:
                    workspace.update(plan=result["plan"], plan_signature=request_signature(pending["payload"]),
                                     last_request=deepcopy(pending["payload"]))
                steps = public_steps(data.get("steps", []), secrets=(key,))
                workspace["activity"] = steps if result["action"] == "analyze" else (workspace.get("activity", []) + steps)[-64:]
                if not local_plan:
                    st.session_state.agent_connection = {"signature": tuple(settings.get(k, "")
                        for k in ("api_url", "api_key", "protocol", "agent_id")),
                        "state": "verified", "checked_at": datetime.now().strftime("%H:%M")}
                workspace.pop("pending", None)
                status.update(label="Результат получен", state="complete")
            except requests.Timeout:
                pending.update(status="failed", error="Сервер не ответил за отведённое время. Проверка не меняет календарь; её можно повторить.")
                status.update(label="Время ожидания истекло", state="error")
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                hint = {401: "Проверьте ключ в настройках.", 429: "Лимит запросов исчерпан; повторите позже.",
                        502: "Агент не выполнил проверку. Предыдущий результат сохранён.",
                        503: "Проверьте конфигурацию сервера."}.get(code, "Проверьте настройки и формат задания.")
                pending.update(status="failed", error=f"Сервер вернул HTTP {code}. {hint}")
                status.update(label="Результат не получен", state="error")
            except requests.RequestException:
                pending.update(status="failed", error="Нет ответа сервера. Проверьте подключение; файл и последний результат сохранены.")
                status.update(label="Нет соединения", state="error")
            except (GatewayReviewError, TimeoutError) as exc:
                pending.update(status="failed", error=str(exc) if isinstance(exc, GatewayReviewError) else
                               "Истекло время совместимой проверки. Последний результат сохранён; повторите вручную.")
                status.update(label="Проверка не завершена", state="error")
            except (ValueError, KeyError) as exc:
                # Only our fixed validation messages may reach UI; never dump a provider response.
                message = str(exc) if type(exc) is ValueError else "Сервер вернул неполный результат. Обновите runtime."
                pending.update(status="failed", error=message)
                status.update(label="Проверьте данные", state="error")
            except Exception as exc:
                # Diagnostic metadata only: never provider bodies, exception text, args or locals.
                LOGGER.error("Deadline operation failed: type=%s; frames=%s", type(exc).__name__,
                             [(frame.name, frame.lineno) for frame in traceback.extract_tb(exc.__traceback__)[-6:]])
                pending.update(status="failed", error="Не удалось обработать результат. Последняя проверка и календарь сохранены.")
                status.update(label="Результат не обработан", state="error")
        st.rerun()
    st.error(pending["error"])
    retry_label = "Проверить через совместимый режим" if pending.get("transport") == "gateway-text" else "Повторить операцию"
    if st.button(retry_label, key="deadline_retry"):
        pending["status"] = "queued"
        st.rerun()
    if st.button("Закрыть ошибку", key="deadline_dismiss"):
        workspace.pop("pending", None)
        st.rerun()


def _timeline(blocks, empty="Пока нет учебных блоков.", *, changed=None, change_label="Новое время"):
    if not blocks:
        st.caption(empty)
        return
    days = {}
    for block in blocks:
        instant = datetime.fromisoformat(block["start_at"])
        end = datetime.fromisoformat(block["end_at"])
        highlight = (block["work_id"], block["start_at"], block["end_at"]) in (changed or set())
        label = f'<small class="deadline-change-label">{change_label}</small>' if highlight else ""
        days.setdefault(instant.strftime("%d.%m"), []).append(
            f'<li class="{"deadline-change" if highlight else ""}"><span class="deadline-time">{instant:%H:%M}–{end:%H:%M}</span>'
            f'<span>{escape(block["title"])}{label}</span></li>')
    st.markdown('<div class="deadline-timeline">' + "".join(
        f'<div class="deadline-day"><strong>{day}</strong><ul>{"".join(items)}</ul></div>'
        for day, items in days.items()) + '</div>', unsafe_allow_html=True)


def _queue_plan(workspace, payload):
    # Validate before dispatch; invalid data never starts a network request.
    build_plan(DeadlineRequest.model_validate(payload))
    workspace["pending"] = {"status": "queued", "payload": deepcopy(payload)}


def render_deadline_mode(chat, *, date_field, now):
    workspace = st.session_state.setdefault("deadline_workspace", {"activity": [], "day_limits": {}, "applied": {}})
    saved_estimates = workspace.setdefault("estimates", {})
    schedule = workspace.setdefault("schedule", {})
    st.markdown("### Успею сдать?")
    st.write("Проверьте требования по репозиторию, оцените оставшуюся работу и найдите для неё время.")
    st.caption("Проверку выполняет подключённый агент. План рассчитывается локально; данные хранятся в текущей сессии. "
               "Загрузите условия проекта; расписание занятий добавляется в календарь.")
    _run_pending(workspace, st.session_state.agent_settings)
    busy = bool(workspace.get("pending"))
    with st.expander("Задание и репозиторий", expanded=not bool(workspace.get("review"))):
        with st.form("deadline_review_form"):
            uploaded = st.file_uploader("Файл задания", type=["pdf", "docx", "txt", "md"], max_upload_size=10,
                                       help="Текстовый PDF, DOCX, TXT или MD. Файл передаётся агенту целиком.")
            existing = [f for f in chat.get("uploaded_files", []) if f["name"].lower().endswith((".pdf", ".docx", ".txt", ".md"))]
            selected = st.selectbox("Или файл из текущего чата", [""] + [f["name"] for f in existing],
                                    format_func=lambda name: name or "Выбрать файл")
            repository = st.text_input("GitHub-репозиторий задания", placeholder="https://github.com/команда/проект")
            submitted = st.form_submit_button("Проверить готовность", type="primary", disabled=busy)
        if submitted:
            try:
                repo = validate_repository_url(repository.strip())
                file = ({"name": uploaded.name, "base64": base64.b64encode(uploaded.getvalue()).decode("ascii")}
                        if uploaded else next((deepcopy(f) for f in existing if f["name"] == selected), None))
                if file is None:
                    raise ValueError("Прикрепите файл задания или выберите его из чата.")
                attachment_document(decode_attachments([file])[0])
                workspace["pending"] = {"status": "queued", "payload": {"action": "analyze", "repository_url": repo.url},
                                        "files": [file]}
                st.rerun()
            except ValueError:
                st.error("Укажите HTTPS-ссылку на GitHub и один читаемый файл задания размером до 10 МБ.")
    review = workspace.get("review")
    if not review:
        st.info("Начните с файла задания и репозитория. Здесь появятся требования, доказательства и план завершения.")
        return
    st.caption(f"Последняя проверка: {review['filename']} · {review['repository_url']}")
    if workspace.get("source_file") and st.button("Проверить репозиторий снова", disabled=busy):
        workspace["pending"] = {"status": "queued", "payload": {"action": "analyze", "repository_url": review["repository_url"]},
                                "files": [deepcopy(workspace["source_file"])]}
        st.rerun()
    analysis = review["analysis"]
    if not analysis.get("live", False):
        st.warning("Результат получен с внедрёнными тестовыми зависимостями; он не подтверждает работу реальных сервисов.")
    for warning in analysis.get("warnings", []):
        st.warning(REVIEW_MESSAGES.get(warning, warning))
    metrics = st.columns(3)
    for column, (status, label) in zip(metrics, LABELS.items()):
        column.metric(label, sum(w["status"] == status for w in review["work"]))
    review_tab, plan_tab = st.tabs(["Готовность работы", "План до дедлайна"])
    estimates = {}
    with review_tab:
        st.caption("Для неподтверждённых требований укажите оставшееся время. «Не проверено» означает задачу на проверку, а не доказанную ошибку.")
        evidence = {e["id"]: e for e in analysis["evidence"]}
        requirements = {r["id"]: r for r in analysis["requirements"]}
        verification = analysis.get("usage", {}).get("verification", [])
        checks = {c["requirement_id"]: c for c in verification[-1].get("checks", [])} if verification else {}
        for item in review["work"]:
            with st.expander(f"{LABELS[item['status']]} · {item['title']}"):
                st.write(REVIEW_MESSAGES.get(item["explanation"], item["explanation"]))
                ambiguity = requirements.get(item["requirement_id"], {}).get("ambiguity")
                if ambiguity:
                    st.info("Нужно уточнить: " + ambiguity)
                check = checks.get(item["requirement_id"])
                if check and check.get("reasons"):
                    st.caption("Почему статус не подтверждён:")
                    for reason in check["reasons"]:
                        if reason in VERIFICATION_REASONS:
                            st.write(VERIFICATION_REASONS[reason])
                elif item["explanation"] in REVIEW_MESSAGES and not check and not ambiguity:
                    st.caption("Точная причина не записана в этой проверке. Повторная проверка сохранит диагностику.")
                st.caption(f"Задание · страница/абзац {item['source']['page']}")
                st.code(item["source"]["quote"], language=None, wrap_lines=True)
                if item.get("recommendation"):
                    st.write(item["recommendation"])
                for evidence_id in item["evidence_ids"]:
                    source = evidence.get(evidence_id)
                    if source:
                        st.caption(f"Получено: {source['retrieved_at']}")
                        if urlsplit(source["source_url"]).scheme == "https" and urlsplit(source["source_url"]).netloc == "github.com":
                            st.link_button("Открыть доказательство в GitHub", source["source_url"])
                        st.text(source["summary"])
                if item["status"] != "satisfied":
                    hours = st.number_input("Осталось работы, часов", min_value=0.0, max_value=168.0,
                        value=saved_estimates.get(item["id"], 0) / 60, step=0.25, key="deadline_estimate_" + item["id"],
                        help="Ваша оценка. Ноль означает, что оценка ещё не задана.")
                    estimates[item["id"]] = round(hours * 60)
                    saved_estimates[item["id"]] = estimates[item["id"]]
        if evidence:
            with st.expander(f"Полученные источники · {len(evidence)}"):
                st.caption("Это фактически прочитанные данные. Само чтение файла ещё не подтверждает выполнение требования.")
                for source in evidence.values():
                    st.caption(f"{source['id']} · {source['tool']} · получено {source['retrieved_at']}")
                    parsed = urlsplit(source["source_url"])
                    if parsed.scheme == "https" and parsed.netloc == "github.com":
                        st.link_button("Открыть источник в GitHub", source["source_url"])
                    st.code(source["summary"], language=None, wrap_lines=True)
    with plan_tab:
        st.caption("Свободное время рассчитывается по календарю. Событие без времени окончания исключает весь день, пока вы не уточните интервал.")
        with st.expander("Ограничения по отдельным дням"):
            with st.form("deadline_limit_form"):
                limit_day = date_field("Дата ограничения", now.date() + timedelta(days=1), "deadline_limit_date")
                limit_hours = st.number_input("В этот день доступно, часов", min_value=0.0, max_value=24.0, value=1.0, step=0.25)
                save_limit = st.form_submit_button("Сохранить ограничение", disabled=busy)
            if save_limit and limit_day:
                workspace["day_limits"][limit_day.isoformat()] = round(limit_hours * 60)
                st.rerun()
            for day, minutes in list(workspace["day_limits"].items()):
                st.caption(f"{day}: максимум {duration(minutes)}")
                if st.button("Убрать ограничение", key="deadline_remove_limit_" + day, disabled=busy):
                    del workspace["day_limits"][day]
                    st.rerun()
        with st.expander("Дедлайн и учебное окно", expanded=not bool(workspace.get("plan"))):
            with st.form("deadline_plan_form"):
                a, b = st.columns(2)
                with a:
                    default_due = datetime.fromisoformat(schedule["due_date"]).date() if schedule.get("due_date") else now.date() + timedelta(days=3)
                    due_date = date_field("Дата сдачи", default_due, "deadline_due_date")
                    day_start = st.text_input("Начало учебного окна", value=schedule.get("day_start", "09:00"), help="ЧЧ:ММ, UTC+5")
                with b:
                    due_time = st.text_input("Время сдачи", value=schedule.get("due_time", "23:59"), help="ЧЧ:ММ, UTC+5")
                    day_end = st.text_input("Конец учебного окна", value=schedule.get("day_end", "21:00"), help="ЧЧ:ММ, UTC+5")
                daily_hours = st.number_input("Доступно для этого задания в день, часов", min_value=0.0,
                                              max_value=24.0, value=schedule.get("daily_hours", 4.0), step=0.25)
                calculate = st.form_submit_button("Рассчитать план", type="primary", disabled=busy)
        due_at = f"{due_date.isoformat()}T{due_time}:00+05:00" if due_date else ""
        schedule.update(day_start=day_start, day_end=day_end, due_time=due_time, daily_hours=daily_hours)
        if due_date:
            schedule["due_date"] = due_date.isoformat()
        if due_date:
            st.caption(f"Сдача: {due_date:%d.%m.%Y} в {due_time} · учебное окно {day_start}–{day_end} · до {daily_hours:g} ч в день · UTC+5")
        owner = digest([review["document_sha256"], review["repository_url"]])[:24]
        previous = workspace["applied"].get(owner) or workspace.get("plan")
        if previous and previous.get("deadline_id") != owner:
            previous = None
        payload = {"action": "plan", "repository_url": review["repository_url"],
                   "document_sha256": review["document_sha256"], "analysis": analysis, "estimates": estimates,
                   "start_at": now.isoformat(), "due_at": due_at, "day_start": day_start, "day_end": day_end,
                   "daily_minutes": round(daily_hours * 60), "day_limits": deepcopy(workspace["day_limits"]),
                   "calendar_events": deepcopy(st.session_state.calendar_events), "previous_plan": previous}
        if calculate:
            try:
                _queue_plan(workspace, payload)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc) if type(exc) is ValueError else "Проверьте оценки, дату сдачи и интервалы времени.")
        plan = workspace.get("plan")
        if plan:
            _render_plan(workspace, plan, payload, now, busy)
    render_activity(workspace.get("activity", []), expanded=True)
    st.download_button("Скачать проверку и план", json.dumps({"review": review, "plan": workspace.get("plan"),
        "steps": workspace.get("activity", [])}, ensure_ascii=False, indent=2), "campuspulse-deadline.json", "application/json")


def _render_plan(workspace, plan, draft, now, busy):
    deficit = plan["deficit_minutes"]
    if deficit:
        st.error(f"До сдачи не помещается {duration(deficit)} работы. Требования сохранены в списке ниже.")
    else:
        st.success("Работа помещается до дедлайна по вашим оценкам времени.")
    a, b, c = st.columns(3)
    a.metric("Осталось работы", duration(plan["required_minutes"]))
    b.metric("Свободно до сдачи", duration(plan["available_minutes"]))
    c.metric("Дефицит" if deficit else "Запас", duration(deficit if deficit else plan["slack_minutes"]))
    st.caption("Расчёт по вашим оценкам и текущему календарю; это не гарантия готовности задания.")
    for warning in plan["warnings"]:
        st.warning(warning)
    for item in plan["unscheduled"]:
        st.write(f"Не запланировано: {item['title']} · {duration(item['minutes'])}")
    before = [block for change in plan["changes"] for block in change["before"]]
    old_intervals = {(b["work_id"], b["start_at"], b["end_at"]) for b in before}
    new_intervals = {(b["work_id"], b["start_at"], b["end_at"]) for b in plan["blocks"]}
    with st.container(key="deadline_comparison"):
        a, b = st.columns(2, gap="large")
        with a:
            st.markdown("#### Было")
            _timeline(sorted(before, key=lambda block: block["start_at"]), "Первый расчёт: предыдущего плана нет.",
                      changed=old_intervals - new_intervals, change_label="Заменяется")
        with b:
            st.markdown("#### Предлагается")
            _timeline(plan["blocks"], changed=new_intervals - old_intervals if before else None)
    changed = [change for change in plan["changes"] if change["kind"] != "unchanged"]
    if changed:
        labels = {"added": "Добавлено", "moved": "Перераспределено", "removed": "Убрано из плана"}
        st.caption(" · ".join(f"{labels[kind]}: {sum(c['kind'] == kind for c in changed)}" for kind in labels))
    current = st.session_state.calendar_events
    applied = workspace["applied"].get(plan["deadline_id"])
    is_applied = (applied is not None and applied["id"] == plan["id"]
                  and [e for e in current if owned(e, plan["deadline_id"])] == plan_events(plan))
    stale = request_signature(draft) != workspace.get("plan_signature")
    if is_applied:
        st.info("Этот план применён к календарю текущей сессии.")
    elif stale:
        st.warning("Данные или календарь изменились после расчёта. Пересчитайте план перед применением.")
    if st.button("Применить к календарю", type="primary", disabled=busy or stale or is_applied):
        try:
            result, undo = apply_plan(current, plan)
            undo["previous_plan"] = deepcopy(workspace["applied"].get(plan["deadline_id"]))
            st.session_state.calendar_events = result
            workspace["undo"] = undo
            workspace["applied"][plan["deadline_id"]] = deepcopy(plan)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    if st.button("Завтра только час — пересчитать", disabled=busy, icon=":material/update:"):
        try:
            workspace["day_limits"][tomorrow] = 60
            payload = deepcopy(draft)
            payload["day_limits"] = deepcopy(workspace["day_limits"])
            payload["previous_plan"] = workspace["applied"].get(plan["deadline_id"]) or plan
            _queue_plan(workspace, payload)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc) if type(exc) is ValueError else "Проверьте ограничения и оценки времени.")
    if workspace.get("undo") and st.button("Отменить последнее применение", disabled=busy):
        try:
            undo = workspace["undo"]
            result = undo_plan(st.session_state.calendar_events, undo)
            st.session_state.calendar_events = result
            if undo["previous_plan"]:
                workspace["applied"][undo["owner"]] = undo["previous_plan"]
            else:
                workspace["applied"].pop(undo["owner"], None)
            workspace.pop("undo", None)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

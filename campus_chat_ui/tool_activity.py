"""Observable tool receipts; private model reasoning is never rendered or exported."""
import re

import streamlit as st

SENSITIVE = re.compile(r"token|secret|password|authorization|api.?key|base64", re.I)


def public_steps(steps, secrets=()):
    def clean(value, depth=0):
        if depth > 5:
            return "[сокращено]"
        if isinstance(value, dict):
            return {str(k): clean(v, depth + 1) for k, v in list(value.items())[:30] if not SENSITIVE.search(str(k))}
        if isinstance(value, list):
            return [clean(v, depth + 1) for v in value[:30]]
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, "[скрыто]")
            return value[:4000] + ("… [сокращено]" if len(value) > 4000 else "")
        return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:200]

    output = []
    for step in (steps if isinstance(steps, list) else [])[:64]:
        if not isinstance(step, dict) or step.get("type") not in {"tool_call", "decision"}:
            continue
        output.append(clean({k: step[k] for k in ("type", "tool", "status", "summary", "args", "result", "timestamp")
                             if k in step}))
    return output


def render_activity(steps, *, expanded=False):
    steps = public_steps(steps)
    count = sum(s.get("type") == "tool_call" for s in steps)
    if not steps:
        st.caption("Сервер не передал журнал действий для этого ответа.")
        return
    with st.expander(f"Действия агента · вызовов инструментов: {count}", expanded=expanded):
        for index, step in enumerate(steps, 1):
            if step.get("type") == "decision":
                st.caption(str(step.get("summary", "Проверка результата")))
                continue
            status = "Ошибка" if step.get("status") == "error" else "Получен результат"
            st.markdown(f"**{index}. {step.get('tool', 'Инструмент')}** · {status}")
            if step.get("summary"):
                st.caption(step["summary"])
            st.json({"аргументы": step.get("args", {}), "результат": step.get("result", {})}, expanded=False)

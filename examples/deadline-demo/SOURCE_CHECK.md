# Проверенные исходные факты — 22.10.2026

Проверка выполнена через подключённый GitHub plugin/MCP в этой сессии. Это подготовка
сценария, а не отчёт об успешном вызове GitHub MCP вашим облачным CampusAgent.

Репозиторий: https://github.com/irrumi/Campusgdg, публичный, основная ветка `main`.
Последний опубликованный коммит на момент проверки:
`e1fc985d8a6bf0badd7fe7f097f92dc1b725a6e2`.

## Что наблюдалось в файлах

- [README](https://github.com/irrumi/Campusgdg/blob/e1fc985d8a6bf0badd7fe7f097f92dc1b725a6e2/README.md)
  содержит `pip install -r requirements.txt` и `streamlit run campus_chat_app.py`.
  Это положительное свидетельство для требования 1.
- [MCP server](https://github.com/irrumi/Campusgdg/blob/e1fc985d8a6bf0badd7fe7f097f92dc1b725a6e2/agent/mcp_server.py)
  создаёт FastMCP и вызывает `mcp.tool()(_fn)` для функций из `TOOLS`.
  Это свидетельство регистрации инструментов, а не доказательство успешного
  сетевого обращения из развёрнутого приложения.
- [Gateway](https://github.com/irrumi/Campusgdg/blob/e1fc985d8a6bf0badd7fe7f097f92dc1b725a6e2/agent/server.py)
  в опубликованном `chat` передаёт `query` только `message`, `history`,
  `student_context`. `ChatIn` не объявляет `files`, а разрешённые дополнительные
  поля не пересылаются. Это конкретная проблема пути передачи файлов.
- [Планировщик](https://github.com/irrumi/Campusgdg/blob/e1fc985d8a6bf0badd7fe7f097f92dc1b725a6e2/agent/tools.py#L37)
  `plan_study_week` принимает `tasks`, `hours_per_day`, `start`; внутри ведёт
  дневную ёмкость, без занятых интервалов календаря. Также может выдавать элементы
  с `late=True`. Это ограничение опубликованного инструмента планирования.

Локальные изменения режима «До сдачи» уже исправляют эти места. GitHub-анализ
видит опубликованный репозиторий, а не локальные незакоммиченные файлы.
После публикации исправлений состояние репозитория изменится: повторите проверку
и не обещайте жюри заранее сохранённые статусы или те же открытые задачи.

## Чего эта проверка не доказывает

- Запрос combined commit status вернул пустой список `statuses`. Это не доказывает
  ни падение, ни успех GitHub Actions; CI-проблему в сценарии не заявляем.
  Позже отдельная настоящая проверка через наш Custom Public GitHub MCP получила
  [workflow run 39668268608](https://github.com/irrumi/Campusgdg/actions/runs/39668268608):
  `Offline checks`, `completed`, `success`, `head_sha=e1fc985d8a6bf0badd7fe7f097f92dc1b725a6e2`.
  Это наблюдение CI опубликованного коммита, не проверка новой локальной версии.
- Не выполнялись новая облачная публикация и полноценный запрос Guardian через
  развёрнутый CampusAgent. Доступ GitHub plugin не подтверждает настройку
  `GITHUB_TOKEN` в Agent Engine.
- Здесь нет сохранённого поддельного `AnalysisResult`. Итоговые статусы должен
  получить агент при реальной проверке; сомнительные случаи остаются `unverified`.

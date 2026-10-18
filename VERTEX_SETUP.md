# CampusGDG: Vertex AI через Google Cloud Run

API endpoint: `https://campus-agent-api-f2c6yodnta-uc.a.run.app/api/v1/chat`

В настройках приложения выберите Custom Agent REST (JSON), укажите этот
endpoint и ваш прежний секретный CAMPUS_API_KEY. Ключ хранится в сессии
интерфейса; на сервере развёрнут только его SHA-256 хеш. Сам хеш в поле
API Key вводить нельзя.

Для другого компьютера передайте обновлённый campus_chat_app.py и
agent.connection.json. Этот JSON содержит только адрес и идентификаторы,
секретов в нём нет. Google Cloud CLI и локальный сервер для интерфейса
не требуются:

```powershell
python -m pip install streamlit requests
python -m streamlit run campus_chat_app.py
```

Окно настроек автоматически использует публичный HTTPS-адрес и заменяет
сохранённый локальный адрес. Существующий ключ в сессии сохраняется.

Cloud Run: campus-agent-api, регион us-central1.
Agent Engine: projects/601118732870/locations/us-central1/reasoningEngines/3854347082139697152.
Служебный аккаунт: campus-agent-run@project-49635439-1824-4699-90f.iam.gserviceaccount.com.
Ему выдана роль Vertex AI User. Минимум экземпляров 0, максимум 2;
после простоя первый запрос может запускать контейнер.

Проверка GET /api/v1/chat возвращает 200 только при доступном ресурсе
Vertex AI. POST /api/v1/chat требует секретный ключ. Тесты подтвердили
доступ к ресурсу Vertex AI из Cloud Run и ответы 401 без правильного ключа.

Повторное развёртывание из этого проекта:

```powershell
.venv\Scripts\python.exe deploy_cloud_run.py
```

Команда использует локальные Google ADC и CAMPUS_API_KEY_SHA256 из .env.
.env и файлы Google-авторизации не включаются в контейнер.
Развёртывание создаёт оплачиваемые ресурсы Cloud Run, Build и Artifact Registry.

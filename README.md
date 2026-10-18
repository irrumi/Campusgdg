# CampusPulse AI — Smart Autonomous Student Agent (MCP + Vertex AI)

Autonomous AI agent that helps students manage the academic week: it computes deadlines,
plans study time, reads lecture notes, checks GitHub assignment repos and searches the web.
Gemini decides *which* tool to call in a reasoning loop; every thought and tool call is returned
to the UI. The agent is deployed to **Vertex AI Agent Engine (Reasoning Engine)**.

> The earlier Assignment Guardian prototype is described in [docs/GUARDIAN.md](docs/GUARDIAN.md).

## Architecture

```
Streamlit UI (campus_chat_app.py)  --HTTPS + API key-->  Gateway (agent/server.py, Cloud Run)
                                                              |
                                                              v
                                       Vertex AI Agent Engine: CampusAgent (agent/agent.py)
                                                              |  Gemini function calling loop
                                                              v
                                       Tools (agent/tools.py)  <-- also exposed via MCP (agent/mcp_server.py)
```

| Component | File |
|---|---|
| Agent core, reasoning loop (`set_up` / `query`) | [agent/agent.py](agent/agent.py) |
| Tools | [agent/tools.py](agent/tools.py) |
| MCP server (FastMCP, stdio) | [agent/mcp_server.py](agent/mcp_server.py) |
| HTTP gateway with API-key auth and rate limit | [agent/server.py](agent/server.py) |
| Reasoning Engine deployment | [deploy.py](deploy.py) |
| UI | [campus_chat_app.py](campus_chat_app.py) |
| Presentation | [docs/presentation.pptx](docs/presentation.pptx) |

## Tools (MCP / function calling)

| Tool | Purpose |
|---|---|
| `get_current_datetime` | Current date and weekday for relative dates |
| `calculate_deadlines` | Days left and urgency per assignment |
| `plan_study_week` | Spreads study hours before deadlines |
| `read_local_file` | Reads txt/md/PDF notes from `notes/` (sandboxed to `CAMPUS_NOTES_DIR`) |
| `web_search` | DuckDuckGo / Wikipedia lookup |
| `github_repo_status` | Commits and open issues of an assignment repo (optional `GITHUB_TOKEN`) |

Run the MCP server: `python -m agent.mcp_server`. Example client config:
```json
{"mcpServers": {"campuspulse": {"command": "python", "args": ["-m", "agent.mcp_server"]}}}
```

## Quick start (UI against the hosted agent)

```
pip install -r requirements.txt
streamlit run campus_chat_app.py
```
In the UI open ⚙️ settings and enter:
- **Agent API Endpoint URL**: the gateway URL ending in `/api/v1/chat` (provided to the jury privately)
- **API Key / Bearer Token**: the access key (provided privately)
- Protocol: `Custom Agent REST (JSON)`

Without the correct key the gateway returns `401`. The key is never stored in this repo;
the gateway keeps only its SHA-256 hash (`CAMPUS_API_KEY_SHA256`).

## Setup from scratch (your own Google Cloud project)

1. Create a Google Cloud project with billing (the free trial is enough), then:
   ```
   gcloud auth application-default login
   gcloud services enable aiplatform.googleapis.com storage.googleapis.com cloudbuild.googleapis.com run.googleapis.com
   gcloud storage buckets create gs://<PROJECT>-agent --location=us-central1
   pip install -r requirements.txt
   ```
2. Try the agent locally: `python -m agent.agent "What is due this week?"` (set `GOOGLE_CLOUD_PROJECT`).
3. Deploy to Agent Engine: `python deploy.py --project <PROJECT> --bucket gs://<PROJECT>-agent`.
   The printed `resource_name` is your `AGENT_ENGINE_RESOURCE`.
4. Create an access key and its hash:
   ```
   python -c "import secrets,hashlib; k='cp_'+secrets.token_urlsafe(32); print(k); print(hashlib.sha256(k.encode()).hexdigest())"
   ```
   Keep the key; use only the hash on the server.
5. Deploy the gateway to Cloud Run (uses [Procfile](Procfile), no Docker needed):
   ```
   gcloud run deploy campuspulse-gateway --source . --region us-central1 --allow-unauthenticated \
     --set-env-vars "CAMPUS_API_KEY_SHA256=<hash>,AGENT_ENGINE_RESOURCE=<resource_name>,GOOGLE_CLOUD_PROJECT=<PROJECT>"
   gcloud projects add-iam-policy-binding <PROJECT> \
     --member "serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com" --role roles/aiplatform.user
   ```
6. Run the gateway locally instead (optional): copy `.env.example` to `.env`, then
   `uvicorn agent.server:app --port 8000`.

## API

`POST /api/v1/chat` with header `Authorization: Bearer <key>`:
```json
{"message": "...", "history": [], "student_context": {}}
```
Response: `{"output": "...", "steps": [{"type": "tool_call", "tool": "...", "args": {}, "result": {}}]}`

## Security

- API-key auth (constant-time hash comparison), 10 requests/minute per key, input size limits.
- `.env`, `vertex.config.json`, keys and logs are git-ignored.
- Set a budget alert in Google Cloud Billing as a safety net.

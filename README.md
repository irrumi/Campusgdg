# CampusPulse AI — Smart Autonomous Student Agent (MCP + Vertex AI)

Autonomous AI agent that helps students manage the academic week: it computes deadlines,
plans study time, reads lecture notes, checks GitHub assignment repos and searches the web.
Gemini decides *which* tool to call in a bounded loop. The UI shows observable tool calls,
results and evidence, never private model reasoning. The cloud target is
**Vertex AI Agent Engine (Reasoning Engine)**.

The active demo UI is `campus_chat_app.py`. The `campuspulse/` package provides
Guardian's extraction, evidence checks and shared models used by «До сдачи».
Its separate FastAPI UI is documented in [docs/GUARDIAN.md](docs/GUARDIAN.md);
it is an additional entry point, not a required step in the main demo.

## «До сдачи» / Deadline review and replanning

The new sidebar section joins the grounded Guardian with the student calendar:

1. Upload a text PDF, DOCX, TXT or MD assignment and enter its GitHub repository.
2. The native Guardian uses Gemini and the **official GitHub MCP server** to check cited requirements.
   An existing chat-only gateway can instead use explicit compatibility mode: the same cloud model
   endpoint plus our **Custom Public GitHub MCP**, with no new provider keys or GCP changes.
   Citations and evidence are validated locally. Findings are confirmed, proven gaps or unverified.
3. Enter your remaining-time estimates for gaps and verification tasks. Choose a deadline,
   study window, daily capacity and optional limits for individual days.
4. Calculate a proposal around actual calendar intervals. Work never spills after the deadline;
   any unscheduled minutes remain visible as a deficit. Events without an end time exclude
   their day with a warning until the interval is clarified.
5. Compare **Было / Предлагается**, then apply locally. Replanning replaces only this
   assignment's blocks, preserves unrelated events and offers Undo. A changed calendar
   invalidates an outstanding proposal.
6. Use **«Завтра только час — пересчитать»** to demonstrate a changed constraint live.
   Recheck the repository after a commit to update readiness, then recalculate using stable work IDs.

The main chat now forwards actual attachment bytes through the gateway to Agent Engine.
Documents are parsed in memory with page/paragraph numbers; PNG/JPEG/WebP use Gemini image parts.
No preview is substituted for the original file. Limits: 10 files, 10 MiB each, 20 MiB total,
200,000 extracted chat characters. Scanned documents require OCR. The optional OpenAI-compatible
chat protocol still sends text previews; «До сдачи» requires Custom Agent REST.

See [the local demo guide](docs/LOCAL_DEMO.md) and [runtime details](docs/DEADLINE_MODE.md).
Planning and replanning run locally without provider keys or cloud calls. Assignment analysis
uses the selected gateway and requires a structured deadline review from its agent.
An old runtime cannot silently return a generic chat answer as a successful review;
choose **«Проверить через совместимый режим»** to perform a real schema-validated review.
Source changes do **not** update the existing cloud deployment.

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
| Existing-gateway review / Custom Public GitHub MCP | [agent/gateway_review.py](agent/gateway_review.py), [agent/public_github_mcp.py](agent/public_github_mcp.py) |
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
| `plan_before_deadline` | Calendar-aware proposal, deficit, preserved blocks and a before/after diff |

Run the MCP server: `python -m agent.mcp_server`. Example client config:
```json
{"mcpServers": {"campuspulse": {"command": "python", "args": ["-m", "agent.mcp_server"]}}}
```

## Quick start (UI against the hosted agent)

Use [the local launcher](docs/LOCAL_DEMO.md): `python scripts/run_local.py`.
By default it starts the UI against the existing gateway. No new Gemini/GitHub keys
or GCP changes are required. Only the existing gateway access key is needed.
An optional `--standalone` mode is documented separately for a personal-key agent.

```
pip install -r requirements.txt
streamlit run campus_chat_app.py
```
In the UI open ⚙️ settings and enter:
- **Agent API Endpoint URL**: prefilled from [agent.connection.json](agent.connection.json); use the hosted HTTPS address ending in `/api/v1/chat`
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
   Deployment and the gateway use the pinned `google-cloud-agentplatform` SDK 2.5.0.
   Validate configuration without cloud access with the same command plus `--dry-run`.
   For «До сдачи», also pass `--github-secret <SECRET_ID> --secret-version <VERSION>`
   for an existing Secret Manager version containing a read-only GitHub token.
   Grant the runtime identity access to that version; only the secret reference is packaged.
4. Create an access key and its hash:
   ```
   python -c "import secrets,hashlib; k='cp_'+secrets.token_urlsafe(32); print(k); print(hashlib.sha256(k.encode()).hexdigest())"
   ```
   Keep the key; use only the hash on the server.
5. Put `CAMPUS_API_KEY_SHA256=<hash>` in the local `.env` file and deploy the gateway:
   ```
   python deploy_cloud_run.py
   ```
   This uses local ADC and `vertex.config.json` (falling back to the portable
   `agent.connection.json`), creates dedicated runtime/build service accounts,
   and builds [Dockerfile.agent-api](Dockerfile.agent-api) in an isolated source
   directory containing only the gateway code and requirements. The existing root
   Dockerfile remains available for the Assignment Guardian app. No local `.env`,
   ADC, or plaintext API key is uploaded. Deployment updates the portable connection
   file with the public HTTPS endpoint. See [VERTEX_SETUP.md](VERTEX_SETUP.md).
6. Run the gateway locally instead (optional): export `CAMPUS_API_KEY_SHA256`,
   `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` and optionally `AGENT_ENGINE_RESOURCE`
   in the process environment, then `uvicorn agent.server:app --host 127.0.0.1 --port 8000`.
   The gateway imports without reading dotenv or credential/configuration files.
   POST requests fail closed with 503 when its API key hash is not configured.

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

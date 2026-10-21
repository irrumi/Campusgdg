# «До сдачи»: проверка, выполнимый план и изменение условий

The default [local UI launcher](LOCAL_DEMO.md) uses the existing gateway and its
application access key. No new Gemini/GitHub keys or GCP changes are required.
Native analysis requires a structured deadline API. A chat-only gateway can use
the explicit compatibility flow documented below. Planning and
replanning execute locally after that analysis; their receipts explicitly say so.
The optional `--standalone` mode runs a separate agent with personal provider keys.

## Runtime

`campus_chat_app.py` → authenticated `agent.server` → `CampusAgent.query(mode="deadline")`
→ `agent.deadline.run_deadline` → existing `GuardianAgent` / `GitHubGateway` or
`plan_before_deadline`. The same planning function is in the custom FastMCP tool registry.
Guardian selects read tools with Gemini and calls the official GitHub MCP server;
ordinary chat tools use function calling. No Issue or external calendar write exists in this mode.

For an unchanged legacy deployment, the UI explicitly retries through
`agent.gateway_review`: cloud chat JSON protocol → local schema/citation validation →
model-selected reads through `agent.public_github_mcp` (real stdio MCP backed by anonymous
public GitHub REST). This custom MCP is not the official authenticated GitHub MCP.
The published gateway limits `message` to 4000 characters. Complete document text,
schemas and evidence travel in its existing `student_context` field; local context
is bounded to 200000 characters without silently truncating the assignment.
It needs only the existing gateway access key, supports public repositories, pins files/CI
to one commit, allows at most 3 selection rounds / 8 reads, and labels partial data.
The native Guardian's grounding rules are reused; no canned AnalysisResult or generic
chat answer is accepted as evidence. See [the local guide](LOCAL_DEMO.md) for limits.

After collection, at most one extra model call reviews extraction ambiguities against
the observed sources. It can resolve missing evaluation context (for example, an
observable CI provider/outcome), with exact source citations. It must preserve missing
author choices, thresholds and conflicting scope. The original ambiguity and accepted
citations remain in a verification receipt; the final finding still requires its own
grounding checks. Invalid resolution references leave the ambiguity intact.

Final grounding records machine-readable rejection reasons. At most one additional
model call may repair identifiers/citations using the same evidence; it cannot fetch
invented support or waive ambiguity. Literal JSON field fragments are offered as citation
aids, and are validated against the original content. The UI shows the actual ambiguity,
verification reasons and all collected sources, including those not accepted in a finding.

Required environment for the agent process:

- Google runtime identity (ADC locally, service account in GCP) with Vertex permissions.
- `GOOGLE_CLOUD_PROJECT`; `GOOGLE_CLOUD_LOCATION=global` for a supported model location.
- `GOOGLE_CLOUD_MODEL` as appropriate for the project's available Gemini model.
- `GITHUB_TOKEN` with read access to the selected repository and MCP endpoint.

For local execution, export these variables in the process environment and run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn agent.server:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m streamlit run campus_chat_app.py --server.address 127.0.0.1
```

The gateway additionally requires `CAMPUS_API_KEY_SHA256` (or a runtime `CAMPUS_API_KEY`
of at least 16 characters). Select Custom Agent REST and its `/api/v1/chat` URL in UI settings;
enter the matching API key there. When `AGENT_ENGINE_RESOURCE` is set, every operation
goes to that resource; remote failures never select local execution as a fallback.
No runtime dotenv file is loaded by the gateway or deadline service.

## Cloud packaging (an explicit operator step)

The native cloud-hosted deadline API needs **both** components updated.
The local legacy-gateway compatibility flow above does not require this rollout.
This implementation work does not deploy services or claim a successful cloud invocation.

- `deploy.py` packages `agent` and `campuspulse` with parsing/MCP dependencies.
  It and the gateway use `agentplatform.Client` from SDK 2.5.0, matching the
  pinned cloud dependencies. `--dry-run` validates configuration without cloud access
  or loading local credential/configuration files; deployment settings are explicit CLI arguments.
  Its optional `--github-secret SECRET_ID --secret-version 1` passes a Secret Manager
  reference as `GITHUB_TOKEN`; never pass the token value on the command line.
  The Agent Engine runtime service identity needs access to that secret version.
  This script creates a new resource: point the gateway's `AGENT_ENGINE_RESOURCE`
  to the intended new resource and retain the previous identity for rollback.
- `Dockerfile.agent-api`, `requirements-cloudrun.txt` and `deploy_cloud_run.py`
  include both source packages. Source packaging excludes dotenv, credentials and caches.
- Gateway health reports its own `gateway_capabilities`; this does not prove that the
  remote runtime supports the feature. A real authenticated analyze request proves the path.
  The gateway rejects a missing `deadline` response from an old runtime with HTTP 502.

## Data contract

`POST /api/v1/chat`, authenticated with the existing API key:

```json
{
  "message": "До сдачи",
  "mode": "deadline",
  "files": [{"name": "assignment.pdf", "base64": "<original file bytes encoded as base64>"}],
  "deadline_request": {
    "action": "analyze",
    "repository_url": "https://github.com/owner/repository"
  }
}
```

Response contains `output`, public `steps`, and `deadline` with the document SHA-256,
normalized repository URL, full `AnalysisResult` and stable work IDs. Readiness always
reflects the recorded analysis, not a live subscription to commits. Explicitly recheck to refresh it.

The `plan` action takes that analysis snapshot, document SHA, estimates in minutes,
timezone-aware `start_at`/`due_at`, a daily study window, daily capacity, optional per-day
limits, current calendar events and an optional previous plan. `DeadlineRequest` in
`campuspulse/models.py` defines the transport. The UI executes the registered
`plan_before_deadline` function locally and verifies the plan against the exact
submitted constraints before offering application. The cloud plan endpoint remains
available for API clients; the UI does not depend on it for calculation or replanning.

Daily capacity is the time the student allocates to this assignment, further bounded
by actual free calendar intervals. Planning is bounded to 31 days, 100 requirements and 500 calendar entries. Estimates
are explicit student input. Missing estimates block calculation. Unverified requirements
remain verification work. Sessions are at most 90 minutes; the algorithm preserves valid
previous sessions, then fills free intervals without overlap. A deficit never silently
removes requirements. Events with incomplete intervals conservatively block their day.
Past sessions are not rescheduled into the past. All scheduling uses Asia/Qyzylorda.

Application validates the full proposal and calendar revision before one session-state
replacement. Only events marked with this assignment's `deadline_id` are replaced;
other events are preserved. Undo requires the expected resulting calendar revision.
The plan and review are transient session data; use «Скачать проверку и план» to export.
There is no Google Calendar synchronization in this implementation.

## Demo for judges

A prepared input pack with an assignment, repository source observations, calendar
intervals and a timed runbook is in [examples/deadline-demo](../examples/deadline-demo/README.md).
Its calendar and effort values are explicitly hypothetical participant inputs;
it contains no precomputed Guardian responses and does not prove a live rehearsal.

Use a real assignment and an accessible repository with a small number of independently
checkable requirements. Include at least one real CI/file problem and one fulfilled requirement.
Prepare your actual calendar intervals and plausible personal effort estimates in advance.
Do not present labelled sample student data or injected test responses as external evidence.

1. Ask «Успею сдать?» and submit the file/repository. Open the source quotation,
   GitHub evidence and the tool receipt. Explain uncertainty where the agent cannot verify.
2. Enter estimates and deadline; calculate. Show occupied calendar time and the free slots.
3. Apply the proposal. Switch to the regular calendar to show the same blocks.
4. Return and press «Завтра только час — пересчитать». Show the before/after schedule,
   preserved sessions and, if there is insufficient capacity, the explicit unscheduled work.
5. Apply the new proposal: no duplicate sessions; unrelated lectures remain.
6. Optionally recheck after an actual repository change. Re-enter an estimate if a
   requirement changed identity; only verified findings become confirmed.

## Verification

Offline tests use explicit fakes only for Gemini/MCP/remote boundaries. Real parsing and
the deterministic planner cover PDFs, gateway file forwarding, tool receipts, conflicts,
deficits, stable IDs, day limits, timezone conversion, stale proposals and Undo. UI evidence
is recorded separately from tests and from live GCP/MCP invocation.

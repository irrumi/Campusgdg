# Agent Engine deployment

CampusPulse deploys its read-only Guardian to a Google Cloud
`projects/PROJECT/locations/REGION/reasoningEngines/ID` resource. Google now calls
this service **Agent Runtime** in Gemini Enterprise Agent Platform. The verified
Python package is `google-cloud-agentplatform[agent_engines]==2.5.0`; its API is
`agentplatform.Client(...).runtimes.create/get`. Older documentation using
`vertexai.Client(...).agent_engines` refers to the former SDK namespace.

The deployment script packages explicit source files and pinned requirements.
It does not serialize a local agent object or require a staging bucket. Only
`async_query(document, repository_url)` is exposed. That method constructs the
real `GuardianAgent`, which uses Gemini and the official GitHub MCP server.
The cloud settings force GitHub writes off and Gemini authentication through the
runtime identity. Issue approval remains a separate local API flow.

## Status

The cloud worker verified local SDK schema and source archive behavior with
explicit test doubles. No cloud deployment, IAM changes, secret creation,
Gemini call, or remote invocation was performed by that worker. A dry run does
not verify ADC, billing, enabled APIs, IAM, quotas, model access or secret access.
Those need actual service responses before they can be reported as working.

Configuration for this project:

| Setting | Value |
| --- | --- |
| Google Cloud project | `campusgdg-511817` |
| Agent Engine region | `us-central1` |
| Gemini model | `gemini-3.8-flash` |
| Gemini location | `global` (separate from the agent region) |
| Runtime Python | `3.12` |
| Exposed operation | `async_query` |

## Prepare access

An administrator must enable billing and the Vertex AI API for the project,
and enable Secret Manager for the GitHub token. Use a dedicated runtime service
account with the model access it needs (normally `roles/aiplatform.user`). The
deploying identity needs Agent Engine creation permissions and permission to
act as the specified runtime service account. Follow the linked Google IAM
guide for the exact identity configuration.

Create a Secret Manager secret in this same project containing a GitHub token
with read access to the repositories being analyzed. Limit its repository scope
and permissions; the cloud agent does not need Issue write permission. Grant
`roles/secretmanager.secretAccessor` on this individual secret to the identity
required by the Agent Runtime configuration. For custom service accounts,
follow the runtime service account instructions. Google specifically requires
the Vertex AI service agent (`service-PROJECT_NUMBER@gcp-sa-aiplatform.iam.gserviceaccount.com`)
to fetch deployment secrets when using agent identity. Do not assume granting
only a human deployer access grants the runtime access.

The examples below use a proposed service account and secret name; they do not
assert that either resource already exists. Provision them through your normal
Google Cloud process. Do not paste a token in a command argument, source file or
deployment JSON. This script accepts only a secret ID and pinned numeric version.

Authenticate the deploying machine interactively after installing gcloud:

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project campusgdg-511817
python -m pip install -e ".[dev]"
python -m pip install -r requirements-cloud.txt
```

No service account key file is required. ADC is used by the deployment/invocation
SDK; the deployed agent uses its runtime identity. `Settings(_env_file=None)`
prevents the cloud wrapper from reading a local `.env`. `CAMPUSPULSE_PROJECT`
selects Gemini's project because Google reserves `GOOGLE_CLOUD_PROJECT` and
`GOOGLE_CLOUD_LOCATION` in deployment environment configuration.

## Inspect and deploy

Run these commands from the integrated repository after all product modules
are present. The deployment fails locally if any required source is missing.
The default mode is a dry run; `--deploy` is the explicit cloud creation step.

```powershell
python scripts/deploy.py --project campusgdg-511817 --location us-central1 --service-account campuspulse-runtime@campusgdg-511817.iam.gserviceaccount.com --github-secret campuspulse-github --secret-version 1 --dry-run --check-sdk
python scripts/deploy.py --project campusgdg-511817 --location us-central1 --service-account campuspulse-runtime@campusgdg-511817.iam.gserviceaccount.com --github-secret campuspulse-github --secret-version 1 --deploy --state data/cloud-deployment.json
```

The dry run prints the exact non-secret configuration and source hashes. The
source archive contains nine Python modules and `requirements-cloud.txt`; it
excludes dotenv files, ADC, SQLite, Git data, static assets, tests and caches.
Source size is bounded at 8 MB. Runtime scaling is limited to two instances with
minimum zero. Deployment still creates billable infrastructure and Gemini calls
can incur usage charges.

The deployment writes state before making a cloud request, saves the returned
resource name before readback, and verifies the exposed operation schema using
`runtimes.get`. It refuses to overwrite an existing state file. If status is
`needs_inspection`, inspect the cloud operations and logs before another creation
attempt. A timeout may leave a real resource; do not blindly retry or erase the
state file. A successful deployment records `invocation_verified: false` because
deployment alone does not prove Gemini or GitHub MCP access.

## Invoke and preserve evidence

```powershell
python scripts/invoke.py --state data/cloud-deployment.json --file examples/assignment.txt --repository https://github.com/OWNER/REPO --output data/cloud-invocation.json
```

Replace the assignment path and repository URL with real inputs. Alternatively
pass `--resource projects/PROJECT/locations/us-central1/reasoningEngines/ID`.
The caller needs permission to query that resource. The script parses the file
locally, checks that the remote exposes only `async_query`, then calls the actual
remote method and validates the returned `AnalysisResult`. It saves timestamps,
resource identity, input/response hashes and the complete response. The response
can contain private assignment and repository content: keep this evidence local.

`remote_response_received` means a valid remote response was received, not that
every assignment requirement passed. Inspect findings, warnings, tool activity
and evidence links. An offline result is rejected. Provider failures return a
safe message rather than credentials or raw request content. No write method is
available through this cloud API.

## Optional local web container

The Dockerfile serves FastAPI and the UI; it is not used for Agent Engine source
deployment. It copies explicit application paths and runs as a non-root user.

```powershell
docker build -t campuspulse .
docker run --rm -p 127.0.0.1:8080:8080 --env-file .env -v campuspulse-data:/app/data campuspulse
```

The container example is local only. It requires credentials configured for
that container if analysis is used. Cloud Run or other UI hosting needs separate
authentication, persistent storage design and operational setup; this Dockerfile
does not create those resources.

## Verification and references

```powershell
python -m pytest tests/test_cloud.py -q
python -m ruff check campuspulse/cloud.py scripts/deploy.py scripts/invoke.py tests/test_cloud.py
```

The tests inject fake agents and cloud responses. They also validate the real SDK
configuration and archive layout. They do not authenticate or contact cloud APIs.

Official sources checked on 2026-10-16:

- [Deploy from source files and configure environment secrets](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/deploy-an-agent)
- [Use a custom agent and query a reasoningEngines resource](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/use-a-custom-agent)
- [Set up the Agent Runtime environment](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime/setup)
- [Configure agent identity and permissions](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/set-up)
- [Gemini 3.8 Flash model and supported locations](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash)
- [Google Cloud Agent Platform package](https://pypi.org/project/google-cloud-agentplatform/2.5.0/)

Some Google guide snippets still reference the old SDK package. The implementation
uses the installed 2.5.0 package API and verifies it in the offline tests.

"""HTTP gateway for the Streamlit UI (matches its default payload).
Run: uvicorn agent.server:app --port 8000   -> UI endpoint http://localhost:8000/api/v1/chat
Set AGENT_ENGINE_RESOURCE to forward to the deployed Reasoning Engine instead of running locally.

Security: every POST requires the secret CAMPUS_API_KEY (Authorization: Bearer ... or X-API-Key).
POST requests fail closed without it. Import does not read local configuration files.
Requests are also rate-limited per client.
"""
import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from agent.agent import CampusAgent
from agent.attachments import decode_attachments
from campuspulse.security import safe_error

KEY_HASH = os.environ.get("CAMPUS_API_KEY_SHA256", "").lower()
if not KEY_HASH and len(os.environ.get("CAMPUS_API_KEY", "")) >= 16:
    KEY_HASH = hashlib.sha256(os.environ["CAMPUS_API_KEY"].encode()).hexdigest()

RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
MAX_MESSAGE_CHARS = 32_000
_hits: dict[str, deque] = defaultdict(deque)

app = FastAPI(title="CampusPulse AI")
_local = None


def require_key(request: Request):
    if len(KEY_HASH) != 64:
        raise HTTPException(503, "Configure CAMPUS_API_KEY_SHA256 before accepting requests.")
    auth = request.headers.get("authorization", "")
    given = auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
    if not hmac.compare_digest(hashlib.sha256(given.encode()).hexdigest(), KEY_HASH):
        raise HTTPException(401, "Invalid or missing API key")
    now = time.time()
    q = _hits[given]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, "Rate limit exceeded")
    q.append(now)


class ChatIn(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list)
    student_context: dict = Field(default_factory=dict)
    files: list[dict] = Field(default_factory=list, max_length=10)
    mode: Literal["chat", "deadline"] = "chat"
    deadline_request: dict | None = None

    model_config = {"extra": "allow"}


def get_remote_agent(resource: str):
    import agentplatform
    parts = resource.split("/")
    if len(parts) != 6 or parts[0] != "projects" or parts[2] != "locations" or parts[4] != "reasoningEngines":
        raise RuntimeError("Invalid AGENT_ENGINE_RESOURCE")
    client = agentplatform.Client(project=parts[1], location=parts[3])
    return client.runtimes.get(name=resource)


@app.get("/api/v1/chat")
def ping():
    resource = os.environ.get("AGENT_ENGINE_RESOURCE")
    if resource:
        try:
            get_remote_agent(resource)
        except Exception:
            raise HTTPException(503, "Vertex AI agent unavailable") from None
    backend = "vertex-agent-engine" if resource else ("gemini-api" if os.environ.get("CAMPUS_LLM_BACKEND") == "gemini" else "vertex-gemini")
    return {"status": "ok", "backend": backend,
            "gateway_capabilities": ["attachments-v1", "deadline-v1"]}


@app.post("/api/v1/chat", dependencies=[Depends(require_key)])
def chat(body: ChatIn):
    global _local
    if len(body.message) > MAX_MESSAGE_CHARS:
        raise HTTPException(413, "Message too long")
    payload = {"message": body.message, "history": body.history[-20:], "student_context": body.student_context}
    if body.files:
        try:
            decode_attachments(body.files)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        payload["files"] = body.files
    if body.mode == "deadline":
        payload.update(mode=body.mode, deadline_request=body.deadline_request)
    resource = os.environ.get("AGENT_ENGINE_RESOURCE")
    try:
        if resource:
            data = get_remote_agent(resource).query(**payload)
        else:
            _local = _local or CampusAgent()
            data = _local.query(**payload)
    except Exception as exc:
        # Remote exceptions may contain provider headers or document excerpts.
        raise HTTPException(502, safe_error(exc)) from None
    if not isinstance(data, dict) or not isinstance(data.get("output"), str) or not isinstance(data.get("steps"), list):
        raise HTTPException(502, "Agent returned an unsupported response.")
    if body.mode == "deadline":
        if not isinstance(data.get("deadline"), dict):
            raise HTTPException(502, "Update the Agent Engine runtime to enable deadline-v1.")
        return {"output": data["output"], "steps": data["steps"], "deadline": data["deadline"]}
    return {"output": data["output"], "steps": data["steps"]}

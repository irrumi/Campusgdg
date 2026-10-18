"""HTTP gateway for the Streamlit UI (matches its default payload).
Run: uvicorn agent.server:app --port 8000   -> UI endpoint http://localhost:8000/api/v1/chat
Set AGENT_ENGINE_RESOURCE to forward to the deployed Reasoning Engine instead of running locally.

Security: every POST requires the secret CAMPUS_API_KEY (Authorization: Bearer ... or X-API-Key).
The server refuses to start without it. Requests are also rate-limited per client.
"""
import hashlib
import hmac
import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from agent.agent import CampusAgent

_root = Path(__file__).resolve().parent.parent
if (_root / ".env").exists():
    for _line in (_root / ".env").read_text(encoding="utf-8").splitlines():
        _k, _, _v = _line.partition("=")
        if _k.strip() and not _k.startswith("#") and _v.strip():
            os.environ.setdefault(_k.strip(), _v.strip())
if (_root / "vertex.config.json").exists():
    _cfg = json.loads((_root / "vertex.config.json").read_text(encoding="utf-8"))
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _cfg.get("project", ""))
    os.environ.setdefault("AGENT_ENGINE_RESOURCE", _cfg.get("agent_engine_resource", ""))

KEY_HASH = os.environ.get("CAMPUS_API_KEY_SHA256", "").lower()
if not KEY_HASH and len(os.environ.get("CAMPUS_API_KEY", "")) >= 16:
    KEY_HASH = hashlib.sha256(os.environ["CAMPUS_API_KEY"].encode()).hexdigest()
if len(KEY_HASH) != 64:
    raise RuntimeError("Set CAMPUS_API_KEY_SHA256 (sha256 hex of your secret key) before starting the server.")

RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
MAX_MESSAGE_CHARS = 4000
_hits: dict[str, deque] = defaultdict(deque)

app = FastAPI(title="CampusPulse AI")
_local = None


def require_key(request: Request):
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
    history: list[dict] = []
    student_context: dict = {}

    model_config = {"extra": "allow"}


@app.get("/api/v1/chat")
def ping():
    return {"status": "ok"}


@app.post("/api/v1/chat", dependencies=[Depends(require_key)])
def chat(body: ChatIn):
    global _local
    if len(body.message) > MAX_MESSAGE_CHARS:
        raise HTTPException(413, "Message too long")
    resource = os.environ.get("AGENT_ENGINE_RESOURCE")
    if resource:
        from vertexai import agent_engines
        data = agent_engines.get(resource).query(
            message=body.message, history=body.history[-20:], student_context=body.student_context)
    else:
        _local = _local or CampusAgent()
        data = _local.query(body.message, body.history[-20:], body.student_context)
    return {"output": data["output"], "steps": data["steps"]}

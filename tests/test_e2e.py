"""Offline HTTP application checks with no service injection or browser automation.

The full configured workflow is in test_integration.py. These checks prove that
the real unconfigured app can parse and persist uploads without a demo fallback.
"""
from __future__ import annotations

import socket
from pathlib import Path

import httpx

from campuspulse.api import create_app
from campuspulse.config import Settings


async def test_unconfigured_app_remains_usable_locally_and_fails_analysis_clearly(tmp_path, monkeypatch):
    def deny_network(*args, **kwargs):
        raise AssertionError("This offline application check must not contact a service")

    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    settings = Settings(_env_file=None, gemini_api_key=None, github_token=None,
                        google_cloud_project=None, google_genai_use_vertexai=False,
                        allow_github_writes=False, allowed_hosts=["localhost"],
                        database_path=str(tmp_path / "local.sqlite3"))
    sample = Path(__file__).resolve().parents[1] / "examples" / "assignment.txt"
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            health = await client.get("/api/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok", "mode": "unconfigured", "gemini_configured": False,
                                     "github_configured": False, "allow_github_writes": False}
            uploaded = await client.post("/api/assignments",
                                         data={"repository_url": "https://github.com/offline-fixture/course-project"},
                                         files={"file": ("assignment.txt", sample.read_bytes(), "text/plain")})
            assert uploaded.status_code == 200, uploaded.text
            assignment, token = uploaded.json()["assignment"], uploaded.json()["token"]
            headers = {"X-Assignment-Token": token}
            base = f"/api/assignments/{assignment['id']}"
            assert assignment["analysis"] is None
            assert (await client.get(base)).status_code == 404
            loaded = await client.get(base, headers=headers)
            assert loaded.json() == assignment
            assert loaded.headers["cache-control"] == "no-store"
            analysis = await client.post(base + "/analyze", headers=headers)
            assert analysis.status_code == 503
            assert set(analysis.json()) == {"detail"}
            assert "Configure Gemini and GitHub MCP" in analysis.json()["detail"]
            assert (await client.get(base, headers=headers)).json()["analysis"] is None
            proposal = await client.post(base + "/actions", headers=headers, json={"requirement_id": "invented"})
            assert proposal.status_code == 409
            events = (await client.get(base + "/events", headers=headers)).json()
            assert [event["kind"] for event in events] == ["upload"]
    # A new application owns a new SQLite connection and can load the original token-scoped record.
    restarted = create_app(settings=settings)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted),
                                     base_url="http://localhost") as client:
            assert (await client.get(base, headers=headers)).json() == assignment

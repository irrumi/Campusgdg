"""Small real Gemini Developer API request; never print credentials/provider errors."""
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from campuspulse.config import Settings


async def main() -> int:
    settings = Settings(google_genai_use_vertexai=False)
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value():
        print(json.dumps({"status": "missing_GEMINI_API_KEY", "api_contacted": False}))
        return 1
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value(),
                          http_options=types.HttpOptions(timeout=60000))
    try:
        started = time.monotonic()
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents="Reply with exactly: CampusPulse Gemini connection verified.",
            config=types.GenerateContentConfig(max_output_tokens=128, temperature=0),
        )
        reply = response.text or ""
        report = {"timestamp": datetime.now(timezone.utc).isoformat(), "status": "response_received",
                  "provider": "Gemini Developer API", "model": settings.gemini_model,
                  "sdk": "google-genai", "elapsed_seconds": round(time.monotonic() - started, 2),
                  "response": reply, "usage": response.usage_metadata.model_dump(mode="json")
                  if response.usage_metadata else None}
        if "CampusPulse Gemini connection verified." not in reply:
            report["status"] = "unexpected_response"
        Path("artifacts/live-gemini-api.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["status"] == "response_received" else 1
    except Exception as exc:
        print(json.dumps({"status": "api_test_failed", "error_type": type(exc).__name__,
                          "status_code": getattr(exc, "code", None)}))
        return 1
    finally:
        await client.aio.aclose()
        client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

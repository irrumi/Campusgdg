"""Send a small test query to the configured Vertex AI agent."""
import json
from pathlib import Path


def main():
    config = {"location": "us-central1", "model_location": "global", "model": "gemini-3.1-flash-lite"}
    for name in ("agent.connection.json", "vertex.config.json"):
        path = Path(__file__).with_name(name)
        if path.exists():
            config.update(json.loads(path.read_text(encoding="utf-8")))
    resource = config.get("agent_engine_resource")
    if resource:
        import vertexai
        from vertexai import agent_engines
        vertexai.init(project=config["project"], location=config["location"])
        agent = agent_engines.get(resource)
    else:
        from agent.agent import CampusAgent
        agent = CampusAgent(project=config["project"], location=config["model_location"], model=config["model"])
    result = agent.query(message="Call get_current_datetime, then reply briefly in English with today's date.")
    print(json.dumps(result, ensure_ascii=True, default=str), flush=True)
    if not result.get("output"):
        raise RuntimeError("Agent returned no output")


if __name__ == "__main__":
    main()

"""CampusPulse agent core: Gemini + function-calling reasoning loop.

The class follows the Vertex AI Reasoning Engine contract (set_up / query),
so it can be deployed as-is and also run locally.
"""
from __future__ import annotations

import json
import os
from typing import Any

SYSTEM_PROMPT = (
    "You are CampusPulse AI, an autonomous assistant for university students. "
    "Help organize the academic week, compute deadlines, plan study time, analyze lecture notes, "
    "check GitHub assignment repos and find up-to-date info. "
    "Always call get_current_datetime first when relative dates (today, tomorrow) matter. "
    "Use tools instead of guessing; chain several tools if needed. "
    "Answer in the user's language, concisely."
)


class CampusAgent:
    def __init__(self, project: str | None = None, location: str | None = None,
                 model: str | None = None, max_steps: int = 8):
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.model = model or os.environ.get("GOOGLE_CLOUD_MODEL", "gemini-3.1-flash-lite")
        self.max_steps = max_steps

    def set_up(self) -> None:
        from google import genai
        from google.genai import types

        from agent.tools import TOOLS

        self._types = types
        self._tools = {f.__name__: f for f in TOOLS}
        self._client = genai.Client(
            vertexai=True,
            project=self.project or os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=self.location,
        )
        decls = [types.FunctionDeclaration.from_callable(client=self._client, callable=f) for f in TOOLS]
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=decls)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def query(self, message: str, history: list[dict] | None = None,
              student_context: dict | None = None, **_: Any) -> dict:
        """Run the reasoning loop. Returns {"output": str, "steps": [...]} """
        if not hasattr(self, "_client"):
            self.set_up()
        t = self._types
        contents = []
        for m in history or []:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append(t.Content(role=role, parts=[t.Part(text=m.get("content", ""))]))
        text = message
        if student_context:
            text += "\n\n[Student context JSON]\n" + json.dumps(student_context, ensure_ascii=False)
        contents.append(t.Content(role="user", parts=[t.Part(text=text)]))

        steps: list[dict] = []
        for i in range(self.max_steps):
            resp = self._client.models.generate_content(model=self.model, contents=contents, config=self._config)
            cand = resp.candidates[0].content
            contents.append(cand)
            calls = [p.function_call for p in cand.parts if p.function_call]
            thought = " ".join(p.text for p in cand.parts if p.text)
            if not calls:
                return {"output": thought, "steps": steps}
            if thought:
                steps.append({"type": "thought", "content": thought})
            result_parts = []
            for c in calls:
                args = dict(c.args or {})
                try:
                    res = self._tools[c.name](**args)
                except Exception as e:  # noqa: BLE001
                    res = {"error": str(e)}
                steps.append({"type": "tool_call", "tool": c.name, "args": args, "result": res})
                result_parts.append(t.Part.from_function_response(name=c.name, response={"result": res}))
            contents.append(t.Content(role="user", parts=result_parts))
        return {"output": "Step limit reached without a final answer.", "steps": steps}


if __name__ == "__main__":
    import sys
    out = CampusAgent().query(" ".join(sys.argv[1:]) or "What day is it and what should I study?")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))

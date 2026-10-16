"""Small stdio MCP client used to retain reproducible Ruflo orchestration evidence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import threading


class RufloClient:
    def __init__(self, entrypoint: str, cwd: str):
        self.process = subprocess.Popen(
            ["node", entrypoint, "mcp", "start"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.responses = queue.Queue()
        self.errors = []
        self.counter = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()
        self.initialize = self.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "campuspulse-orchestrator", "version": "1.0.0"},
        })
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        for line in self.process.stdout:
            try:
                self.responses.put(json.loads(line))
            except json.JSONDecodeError:
                self.errors.append(line.strip())

    def _stderr(self):
        for line in self.process.stderr:
            self.errors.append(line.strip())

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params, timeout=90):
        self.counter += 1
        request_id = self.counter
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            response = self.responses.get(timeout=timeout)
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response["result"]

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entrypoint", default=os.environ.get("RUFLO_ENTRYPOINT"))
    parser.add_argument("--cwd", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--tool")
    parser.add_argument("--params-file")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.entrypoint:
        parser.error("Set RUFLO_ENTRYPOINT to the installed ruflo/bin/ruflo.js")
    client = RufloClient(args.entrypoint, args.cwd)
    try:
        if args.tool:
            params = json.loads(Path(args.params_file).read_text(encoding="utf-8")) if args.params_file else {}
            result = client.call(args.tool, params)
        else:
            result = client.request("tools/list", {})
        output = {"initialize": client.initialize, "result": result}
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
        if args.tool:
            print(json.dumps(result, ensure_ascii=False))
        else:
            names = [tool["name"] for tool in result["tools"]]
            print(json.dumps({"server": client.initialize, "tool_count": len(names), "orchestration_tools": [n for n in names if n.startswith(("agent_", "swarm_", "task_", "memory_", "hooks_pre-task", "hooks_post-task"))]}, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()

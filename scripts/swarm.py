"""Register exactly ten Ruflo workers and execute them via documented claude -p integration.

Run registration first. Worker execution is separately evidenced by process output,
file changes and successful results; the Ruflo registry alone is not execution proof.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
from uuid import uuid4

from ruflo_mcp import RufloClient

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
PRIVATE = ARTIFACTS / "private"
LOCK = threading.Lock()


def unwrap(result):
    if result.get("isError"):
        raise RuntimeError(result)
    if "structuredContent" in result:
        return result["structuredContent"]
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except json.JSONDecodeError:
                return {"text": item["text"]}
    return result


def call(client, name, args):
    with LOCK:
        result = unwrap(client.call(name, args))
        with (ARTIFACTS / "ruflo-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "tool": name,
                                     "arguments": args, "result": result}) + "\n")
    if result.get("error"):
        raise RuntimeError(result)
    return result


def register(client):
    manifest_path = ARTIFACTS / "swarm-manifest.json"
    if manifest_path.exists():
        raise RuntimeError("Manifest exists; use existing workers, do not duplicate them")
    spec = json.loads((ROOT / "docs/swarm.json").read_text(encoding="utf-8"))
    swarm = call(client, "swarm_init", {"topology": "hierarchical-mesh", "maxAgents": 10,
        "strategy": "specialized", "config": {"coordinator": "Astra", "sharedMemory": True,
        "taskTracking": True, "maxConcurrentWorkers": 3, "worktreeIsolation": True}})
    call(client, "memory_store", {"namespace": "campuspulse", "key": "integration-contract",
        "value": (ROOT / "docs/CONTRACT.md").read_text(encoding="utf-8"),
        "provenance_type": "system_observation"})
    manifest = {"swarm": swarm, "workers": []}
    for worker in spec["workers"]:
        worker = dict(worker)
        agent_id = "campuspulse-" + worker["id"]
        spawned = call(client, "agent_spawn", {"agentId": agent_id, "agentType": worker["type"],
            "swarmId": swarm["swarmId"], "model": "inherit",
            "config": {"name": worker["role"], "instructions": worker["task"],
                       "executionRuntime": "claude-code-headless"}})
        task = call(client, "task_create", {"type": "feature", "description": worker["task"],
            "priority": "high", "assignTo": [agent_id], "tags": ["campuspulse", worker["id"]]})
        worker.update(agent_id=agent_id, task_id=task["taskId"], spawn=spawned,
                      branch="swarm/worker-" + worker["id"],
                      worktree=str(ROOT.parent / "workers" / worker["id"]))
        manifest["workers"].append(worker)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    inventory = call(client, "agent_list", {})
    status = call(client, "swarm_status", {"swarmId": swarm["swarmId"]})
    (ARTIFACTS / "registration-verification.json").write_text(json.dumps({"inventory": inventory,
        "swarm": status}, indent=2), encoding="utf-8")
    print(json.dumps({"registered": len(manifest["workers"]), "swarm": status}), flush=True)


def run_worker(client, worker, mcp_config):
    worktree = Path(worker["worktree"])
    if not worktree.exists():
        subprocess.run(["git", "worktree", "add", "-b", worker["branch"], str(worktree), "HEAD"],
                       cwd=ROOT, check=True, capture_output=True, text=True)
    session_id = str(uuid4())
    prompt = f"""You are the real execution context for Ruflo agent {worker['agent_id']},
task {worker['task_id']}, role {worker['role']}. Astra coordinates you through Ruflo.
Read docs/CONTRACT.md, campuspulse/models.py and campuspulse/config.py before coding.
Your task: {worker['task']}
Work ONLY in the ownership paths for worker {worker['id']} from the contract.
No commits, GitHub writes, deployments, secret access, extra agents or package installs.
Use codebase-memory-mcp for code discovery; index this worktree first if necessary.
The shared Python environment is {ROOT / '.venv/Scripts/python.exe'}.
If a test cannot run due to permissions or missing other workers' files, report it honestly.
Implement working files; do not stop at a plan. Finish with task ID, changed files,
branch/worktree, functionality, tests actually performed, outcome and blockers.
Return a concise final report. Do not claim cloud/live success.
"""
    PRIVATE.mkdir(parents=True, exist_ok=True)
    prompt_path = PRIVATE / f"worker-{worker['id']}-prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    output_path = PRIVATE / f"worker-{worker['id']}.jsonl"
    error_path = PRIVATE / f"worker-{worker['id']}.stderr.log"
    args = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "claude.cmd", "-p",
            "--output-format", "stream-json", "--verbose", "--session-id", session_id,
            "--permission-mode", "acceptEdits", "--permission-prompts", "none",
            "--setting-sources", "project", "--strict-mcp-config", "--mcp-config", str(mcp_config),
            "--tools", "Read,Write,Edit,Glob,Grep,Bash,WebFetch",
            "--allowedTools", "Read,Write,Edit,Glob,Grep,WebFetch",
            "mcp__codebase-memory-mcp__index_repository",
            "mcp__codebase-memory-mcp__search_graph", "mcp__codebase-memory-mcp__get_code_snippet",
            "mcp__codebase-memory-mcp__get_architecture", "mcp__ruflo__memory_retrieve"]
    with output_path.open("w", encoding="utf-8") as out, error_path.open("w", encoding="utf-8") as err:
        process = subprocess.Popen(args, cwd=worktree, stdin=subprocess.PIPE, stdout=out, stderr=err,
            text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        call(client, "agent_update", {"agentId": worker["agent_id"], "status": "busy",
            "config": {"pid": process.pid, "sessionId": session_id, "worktree": str(worktree)}})
        call(client, "task_update", {"taskId": worker["task_id"], "status": "in_progress"})
        print(json.dumps({"worker": worker["id"], "pid": process.pid, "session_id": session_id,
                          "state": "process_started"}), flush=True)
        try:
            process.communicate(prompt, timeout=1800)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=20)
    events = []
    for line in output_path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    changes = subprocess.run(["git", "status", "--short"], cwd=worktree,
                             capture_output=True, text=True, check=True).stdout
    ok = process.returncode == 0 and final.get("subtype") == "success" and bool(changes.strip())
    report = {"worker_id": worker["id"], "agent_id": worker["agent_id"], "task_id": worker["task_id"],
              "session_id": session_id, "exit_code": process.returncode, "branch": worker["branch"],
              "worktree": str(worktree), "changes": changes, "result": final,
              "execution_verified": bool(events), "implementation_reported": ok,
              "verification": "Requires coordinator review and integration tests",
              "stdout_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}
    (ARTIFACTS / f"worker-{worker['id']}-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    call(client, "agent_update", {"agentId": worker["agent_id"], "status": "idle",
        "taskCount": 1, "config": {"lastExecution": {"exitCode": process.returncode,
        "sessionId": session_id, "implementationReported": ok}}})
    # An implementation report is not integration acceptance: keep task in progress.
    call(client, "task_update", {"taskId": worker["task_id"],
        "status": "in_progress" if ok else "failed", "progress": 80 if ok else 0,
        "result": {"report": f"artifacts/worker-{worker['id']}-report.json",
                   "requiresIntegration": True, "exitCode": process.returncode}})
    print(json.dumps({"worker": worker["id"], "state": "finished", "implementation_reported": ok,
                      "exit_code": process.returncode, "changes": changes}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["register", "run"])
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--workers", default="")
    parser.add_argument("--mcp-config")
    args = parser.parse_args()
    ARTIFACTS.mkdir(exist_ok=True)
    client = RufloClient(args.entrypoint, str(ROOT))
    try:
        if args.command == "register":
            register(client)
        else:
            selected = set(args.workers.split(","))
            workers = json.loads((ARTIFACTS / "swarm-manifest.json").read_text(encoding="utf-8"))["workers"]
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(run_worker, client, w, Path(args.mcp_config))
                           for w in workers if w["id"] in selected]
                for future in as_completed(futures):
                    future.result()
    finally:
        client.close()


if __name__ == "__main__":
    main()

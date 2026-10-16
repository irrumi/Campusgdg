"""Bind real native Codex execution contexts to existing Ruflo records."""
import argparse
import json
from pathlib import Path

from ruflo_mcp import RufloClient
from swarm import ARTIFACTS, ROOT, call


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("state", choices=["start", "handoff", "accepted", "failed"])
    parser.add_argument("--worker", required=True)
    parser.add_argument("--executor", required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    manifest = json.loads((ARTIFACTS / "swarm-manifest.json").read_text(encoding="utf-8"))
    worker = next(w for w in manifest["workers"] if w["id"] == args.worker)
    client = RufloClient(args.entrypoint, str(ROOT))
    try:
        config = {"executionRuntime": "codex-native", "executor": args.executor,
                  "worktree": worker["worktree"], "executionState": args.state}
        if args.report:
            config["report"] = args.report
        call(client, "agent_update", {"agentId": worker["agent_id"],
             "status": "busy" if args.state == "start" else "idle", "config": config})
        result = {"executor": args.executor, "runtime": "codex-native", "report": args.report}
        if args.state == "accepted":
            call(client, "task_complete", {"taskId": worker["task_id"], "result": result})
        else:
            call(client, "task_update", {"taskId": worker["task_id"],
                "status": "failed" if args.state == "failed" else "in_progress",
                "progress": 80 if args.state == "handoff" else 0, "result": result})
        print(json.dumps({"worker": args.worker, "executor": args.executor, "state": args.state}))
    finally:
        client.close()


if __name__ == "__main__":
    main()

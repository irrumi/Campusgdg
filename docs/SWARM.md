# Ruflo execution record

Ruflo 3.56.10 was installed and initialized, and its MCP server returned 358 tools.
Swarm `swarm-1792172579639-84jexv` registered exactly ten specialized workers with
hierarchical-mesh topology. Shared contract text was stored in the `campuspulse`
memory namespace. Each worker had a separate branch and worktree.

The initial Claude headless attempts failed authentication before model execution
(zero input/output tokens). Their preserved `worker-01/02/03-report.json` files
are failed-attempt evidence. The corrected `execution_verified` field is false;
starting a process or receiving a result event does not establish model work.

Actual development used ten native Codex workers linked to those same Ruflo agent
and task records, following the [Ruflo Codex integration](https://github.com/ruvnet/ruflo).
The environment allowed the coordinator plus three active workers, so execution
proceeded in waves. Follow-up corrections reused the same workers.

| Worker | Role | Handoff evidence | Final task state |
| --- | --- | --- | --- |
| 01 | Architecture | [Report](../artifacts/worker-01-result.md) | Accepted |
| 02 | Parsing and requirement extraction | [Report](../artifacts/worker-02-result.md) | Accepted |
| 03 | GitHub MCP | [Report](../artifacts/worker-03-result.md) | Accepted |
| 04 | Gemini agent | [Report](../artifacts/worker-04-result.md) | Accepted |
| 05 | Backend | [Report](../artifacts/worker-05-result.md) | Accepted |
| 06 | Frontend | [Report](../artifacts/worker-06-result.md) | Accepted |
| 07 | Google Cloud | [Report](../artifacts/worker-07-result.md) | Source/SDK ready; deployment blocked by access |
| 08 | Storage | [Report](../artifacts/worker-08-result.md) | Accepted |
| 09 | QA | [Report](../artifacts/worker-09-result.md) | Accepted |
| 10 | Security and integration | [Report](../artifacts/worker-10-result.md) | Accepted |

[The final MCP snapshot](../artifacts/swarm-final.json) contains ten idle agent
records, nine completed tasks and one cloud task in progress with its external
blocker. The [manifest](../artifacts/swarm-manifest.json) records original
registration and task IDs; [events](../artifacts/ruflo-events.jsonl) record updates.
Task records and native executor reports must be read together: registry status
alone is not evidence of coding or a successful deployment.

`.mcp.json` and `.codex/config.toml` configure the pinned Ruflo MCP command.
`scripts/ruflo_mcp.py` is the JSON-RPC client; `scripts/record_worker.py` binds a
native executor to an existing record. `scripts/swarm.py` preserves the original
headless execution path and rejects a duplicate manifest during registration.
The existing swarm must not be recreated merely to obtain a cleaner status.

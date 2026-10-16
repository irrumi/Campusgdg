# Security boundaries and limitations

CampusPulse treats uploaded assignments, repository files, Issues, model output,
and MCP responses as untrusted data. The local API is intended for a trusted user
on a loopback interface. Assignment tokens protect access to an assignment; they
are not user accounts or authorization to use a shared server's GitHub identity.

## Repository and error boundaries

`security.validate_repository_url` accepts only an ASCII HTTPS `github.com`
owner/repository URL. It rejects credentials, explicit ports, alternate hosts,
query/fragment delimiters, percent encoding, backslashes, extra path segments,
dot traversal, whitespace, control characters and Unicode lookalikes. Scheme and
host case, one terminal slash and one lowercase `.git` suffix are normalized.
Owner and repository case is preserved; accepted canonical URLs validate again
unchanged. No network request, DNS lookup or arbitrary URL fetch occurs here.

The GitHub gateway also checks a supplied `RepositoryRef` against its owner and
name, because shared Pydantic models alone are not a security boundary. Repository
paths and refs have independent validation. The MCP endpoint is fixed to
`https://api.githubcopilot.com/mcp/`; model arguments cannot override it, the
repository owner, or the repository name.

`safe_error` returns fixed public messages. It never renders exception text,
arguments, response headers, request bodies, causes, notes or exception-group
members. Known application exception names are matched with their full module
and inheritance chain solely to select fixed text, avoiding provider imports and
import cycles. This mapping must never grant permission, choose an HTTP status,
or authorize a retry; callers use actual exception types for those decisions.
Unknown errors receive a generic message. New provider errors remain redacted
without a new mapping, at the cost of less specific diagnostics.

## Uploads and model execution

Parsing is local. The parser does not execute repository code, macros or embedded
commands, extract ZIP entries to disk, fetch document links, or perform OCR.

| Input | Enforced bounds |
| --- | --- |
| Every document | 10 MiB input; 100 pages/paragraphs; 100,000 characters per source; 500,000 characters total |
| DOCX | 512 ZIP entries; 8 MiB per expanded entry; 25 MiB total; compression ratio at most 200; no encrypted entries, duplicate/unsafe paths or unsupported compression |
| DOCX XML | DTD/entity declarations rejected, including UTF-16 declarations; depth at most 128 and 100,000 elements |
| PDF | Encrypted and textless documents rejected; bounded pypdf stream decoding, parsing work, page-tree traversal and nested form invocations |

PDF limits apply before stream decompression through pypdf's configuration, then
the parser checks expanded page/total stream sizes. These are in-process limits,
not a parser sandbox or a proof that every malformed document is cheap to parse.
DOCX source numbers identify body paragraphs; they do not represent rendered Word
page numbers. TXT and Markdown source numbers identify blank-line paragraphs.

The extraction model has no tools. Every returned quote must match its claimed
source after whitespace normalization, and duplicate identifiers or invalid
structured output are rejected. Extraction cannot prove that a paraphrased
requirement correctly captures every nuance of its source.

The analysis agent exposes four read operations: repository, file, issues and CI.
Unknown, invalid and failed tool calls consume the maximum eight-call budget.
Automatic SDK tool execution is disabled. The application selects the repository
and translates the bounded arguments into discovered, validated MCP schemas;
the model cannot request raw MCP methods, external fetches or Issue creation.
Read sessions explicitly request read-only tools, and known read tools must also
advertise the expected read-only annotation.

Conclusions reference collected evidence IDs and exact supporting quotations.
Unsupported assertions and ambiguous requirements are downgraded to `unverified`.
Empty or partial listings cannot prove absence. These checks establish provenance
and limit capabilities; they cannot guarantee semantic correctness or eliminate
prompt injection's influence on which allowed evidence the model selects.
Review the original source and proposed Issue before approving it.

## Approval and persistence

An Issue proposal is persisted before approval. Its hash is SHA-256 of UTF-8 JSON
containing exactly `repository.model_dump(mode="json")`, `title` and `body`, using
sorted keys, compact separators and `ensure_ascii=False`. Neither Unicode nor
whitespace is normalized at this stage. The hash binds the displayed content; it
is not a password, signature, or substitute for the assignment bearer token.

SQLite stores SHA-256 digests of randomly generated 256-bit tokens. Token
comparisons use `hmac.compare_digest`; unknown and unauthorized assignment IDs
both raise `KeyError`. Queries use parameters. Proposal fields are protected by
an immutable database trigger. A transaction atomically checks the stored hash
and changes `pending` to `executing`, so concurrent approvals have one winner.

The API requires literal JSON `approved: true`, the matching immutable hash and
enabled GitHub writes. It reads the created Issue back and compares the repository,
number, title, body and verified state before recording success. An uncertain
post-invocation outcome becomes `unknown`. Failed/unknown actions cannot be
automatically retried; a crash can leave `executing`, which also remains claimed.
Inspect GitHub before creating another proposal after an uncertain outcome.

The database stores assignment text, repository evidence, proposals and audit
events in plaintext. File access is therefore sensitive. There is no built-in
retention policy, encrypted database, account recovery, per-user repository ACL,
or multi-tenant authorization system. Audit events are operational records, not
tamper-evident logs against a host/database administrator. Callers of low-level
storage methods must supply already-redacted event/error messages.

## Credentials and cloud packaging

GitHub tokens stay on the server and are placed only in the official MCP request
authorization header. Settings exclude both provider secrets from `repr` and use
`SecretStr`. These safeguards do not encrypt process memory or authorize logging
raw settings, environments or provider objects. Limit the token to the selected
repositories and necessary permissions; read-only cloud analysis needs no Issue
write permission.

The source deployment uses the installed `google-cloud-agentplatform` 2.5.0 API.
Its archive is built from an explicit list of nine Python modules plus the cloud
requirements file, with symlink/path checks and an 8 MB source limit. Dotenv files,
ADC, databases, caches and Git metadata are outside that list. A Secret Manager ID
and pinned version are included in deployment configuration, never the token
value. No credential-bearing local agent object is pickled for deployment.

The cloud wrapper exposes only `async_query`, constructs clients at runtime,
disables local dotenv and Gemini API-key authentication, uses runtime identity,
and forces GitHub writes off. Cloud IAM, secret access and billing are operational
prerequisites. Local SDK/schema tests or a dry run do not prove a cloud deployment
or remote invocation succeeded. Follow `DEPLOYMENT.md` and inspect actual remote
responses before reporting either outcome.

Analysis sends assignment content and selected repository evidence to Gemini and
the GitHub service. The application is not a secret scanner or data-loss-prevention
filter: credentials already embedded in uploaded or repository content could be
processed as content. Use data and repositories appropriate for these providers.

## Deployment limits and review record

Keep development bound to `127.0.0.1`. Public/shared hosting requires user
authentication, explicit repository authorization, TLS, resource/concurrency
quotas, protected persistent storage and operational logging policy. Per-request
size limits do not rate-limit repeated uploads or model calls. No certification,
penetration-test result or production security guarantee is claimed.

API requests must use an exact configured hostname. The default `allowed_hosts`
contains `localhost`, `127.0.0.1` and `[::1]`; a deployment hostname must be added
explicitly. The guard rejects arbitrary hosts even when their Origin matches,
closing the reviewed loopback DNS-rebinding path. Cross-origin mutations are
rejected, no wildcard CORS is enabled, and API responses use `Cache-Control:
no-store`. Host/Origin checks are not a replacement for public-service identity
and authorization.

The worker10 review inspected parser/extraction, GitHub MCP, storage and cloud
source in the integrated checkout, plus the agent/backend workers' current files.
No credentials were read and no external writes, Issue creation, cloud deployment
or live calls were performed for this review. Integration findings are tracked
with the coordinator:

| Finding | Source review outcome |
| --- | --- |
| An upstream `HTTPException` could bypass redaction through the API's general HTTP handler | Backend changed provider failures to fixed `safe_error` responses and distinguishes API-authored errors from framework/provider HTTP errors; regression test added. |
| Same-Origin validation trusted an arbitrary Host header, leaving a loopback DNS-rebinding route | Backend added exact configured-host validation before API handling; regression test added for an attacker host with a matching Origin. |
| Cloud wrapper rejected documents containing any blank PDF page, although parsing preserves blank page numbering | Coordinator changed the requirement to at least one nonblank page and added a page-number preservation regression test. |

The corrected source and regression tests were inspected. The coordinator must
still run the final integrated suite after collecting all workers' files; the
worker10 verification below does not include the backend's final changes.

Offline verification from the worker10 worktree:

```powershell
python -m pytest tests/test_security.py -q
python -m pytest tests/test_security.py tests/test_storage.py tests/test_parsing.py tests/test_extraction.py tests/test_github_mcp.py tests/test_cloud.py -q
python -m ruff check campuspulse/security.py tests/test_security.py
```

The security suite covers 277 cases, including URL confusion/control characters,
exact storage hash compatibility and hostile exceptions. The combined command
passed 505 cases, with one SDK Pydantic deprecation warning; Ruff passed. These
are offline checks with explicit fakes where providers are involved. They do not
establish live-service correctness or successful cloud deployment.

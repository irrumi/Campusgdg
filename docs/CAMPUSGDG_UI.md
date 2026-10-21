# CampusGDG Streamlit UI contract

Scope: `campus_chat_app.py` only. CampusPulse's FastAPI UI and backend contracts in
`docs/CONTRACT.md` remain separate. The user's redesign brief is the authority for
Russian copy, the existing dark identity, data preservation and honest API states.

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | Streamlit selectbox and radio | campus_chat_app.py | Streamlit authored BaseWeb popup; Russian labels | tests/test_campus_chat_ui.py and browser report |
| Date | date_field and display_date | campus_chat_app.py | Typed DD.MM.YYYY; read-only month grid; Asia/Qyzylorda | tests/test_campus_chat_ui.py and browser report |
| Form | Streamlit form | campus_chat_app.py | Explicit submit; inline validation; retain invalid input | tests/test_campus_chat_ui.py |
| Scrollbar | Global CSS | campus_chat_ui/styles.css | Standard scrollbar-color plus WebKit fallback; forced colors | browser report |
| Toast | Streamlit info/success/error/status | campus_chat_app.py | Persistent inline feedback; no critical toast-only content | tests/test_campus_chat_ui.py and browser report |
| CRUD | Session state helpers | campus_chat_app.py | Local additions; reversible completion; one-step restore for removed chat/event | tests/test_campus_chat_ui.py |

The existing session dictionaries, agent settings and outbound REST/OpenAI-compatible
payloads stay intact. New keys are UI metadata only. Navigation never resets user
data or agent settings. Secrets are masked, server-session-only and excluded from
export and error messages. No credential files are read during development.

POST success requires nonempty text in a supported response field. Configuration
is not connectivity; GET availability is not an agent answer. There is no generated
offline reply. Only explicit actions returned by the agent affect tasks/calendar.
Queued -> running -> success/failed is held per chat. Rerunning an interrupted POST
never sends it again automatically. Ambiguous retries require the user's explicit
acknowledgement because the server may already have applied an action.

New sessions have empty data and one blank chat. Optional учебные примеры contain
no agent messages; every sample event/task/course has `_example: true`. Existing
session data is retained. Data is transient: use JSON export before closing the
session. Export keeps the existing chat/calendar/tasks shape and excludes settings.

Forms retain input after success and reject duplicate identical submissions.
Create returns to the same list; dates/filters can exclude the new item, which the
form explains. Calendar periods are real bounded day/week/month ranges, and tasks
sort by due date. The initial calendar grid is informational; selecting a date uses
the typed date field. Framework-owned file chooser, password visibility control,
sidebar chrome and other browser-owned accessibility strings may use the framework
locale; all application-owned labels and date strings are Russian.

Verification: `.venv/Scripts/python.exe -m pytest tests/test_campus_chat_ui.py`.
Browser evidence is recorded in `artifacts/campusgdg-ui/verification.md`.

## Sidebar refinement

The Gemini-inspired sidebar keeps the existing native radio navigation and its
stored values, with a Material icon per section. New-dialog creation precedes the
navigation. Recent chats are flat rows; the active chat has a filled pill. Long
titles truncate visually but retain the full accessible label/help and open to a
wrapping title in the main workspace. Local case-insensitive title search has a
clear action and a no-results state; it never removes chats or switches selection.
Creating a new dialog clears that title filter. History scrolls within its region
when needed; the settings footer remains reachable. Native Streamlit owns sidebar
collapse/expand and the mobile overlay; no replacement JavaScript drawer is added.

Reference: https://support.google.com/gemini/answer/13666746?co=GENIE.Platform%3DDesktop&hl=en

## Connection defaults after origin/main integration

The upstream connection loader is retained: an explicit environment endpoint takes
priority over local connection metadata, which overrides the portable project
defaults. A different local Vertex resource must not inherit the portable gateway.
These defaults initialize new sessions only. Existing `agent_settings`, including
an empty URL, a localhost endpoint, a saved key and a custom model/protocol, are
never automatically replaced during a code update. Applying project defaults to
an existing session uses the explicit action in the settings dialog. Credential
files are not changed by the integration.

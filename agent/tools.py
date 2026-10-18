"""CampusPulse tools. Plain Python functions with type hints and docstrings.

The same functions are used for Gemini function calling (agent.py) and are
exposed through MCP (mcp_server.py).
"""
from __future__ import annotations

import os
from datetime import date, datetime

import requests

TIMEOUT = 15


def calculate_deadlines(assignments: list[dict], today: str = "") -> list[dict]:
    """Calculate days left and urgency for assignments, sorted by urgency.

    Args:
        assignments: List of dicts like {"course": "Calculus", "title": "HW3", "due": "2026-10-25"}.
        today: Optional ISO date (YYYY-MM-DD). Defaults to the current date.
    """
    now = date.fromisoformat(today) if today else date.today()
    result = []
    for a in assignments:
        try:
            due = date.fromisoformat(str(a["due"])[:10])
        except (KeyError, ValueError):
            result.append({**a, "error": "invalid or missing 'due' date"})
            continue
        left = (due - now).days
        urgency = "overdue" if left < 0 else "critical" if left <= 1 else "high" if left <= 3 else "normal"
        result.append({**a, "days_left": left, "urgency": urgency})
    return sorted(result, key=lambda x: x.get("days_left", 9999))


def plan_study_week(tasks: list[dict], hours_per_day: float = 4.0, start: str = "") -> list[dict]:
    """Distribute study tasks over days before their deadlines (earliest deadline first).

    Args:
        tasks: List of {"title": str, "due": "YYYY-MM-DD", "hours": float}.
        hours_per_day: Study hours available per day.
        start: Optional ISO start date, defaults to today.
    """
    cur = date.fromisoformat(start) if start else date.today()
    capacity: dict[str, float] = {}
    plan = []
    for t in sorted(tasks, key=lambda x: x.get("due", "9999-12-31")):
        remaining = float(t.get("hours", 1))
        due = date.fromisoformat(t["due"][:10]) if t.get("due") else None
        d = cur
        while remaining > 0:
            key = d.isoformat()
            free = hours_per_day - capacity.get(key, 0.0)
            if free > 0:
                used = min(free, remaining)
                capacity[key] = capacity.get(key, 0.0) + used
                remaining -= used
                plan.append({"date": key, "task": t.get("title", "task"), "hours": round(used, 2),
                             "late": bool(due and d > due)})
            d = date.fromordinal(d.toordinal() + 1)
    return plan


def read_local_file(path: str, max_chars: int = 6000) -> str:
    """Read a text/markdown/PDF lecture note from the local notes folder (CAMPUS_NOTES_DIR, default ./notes).

    Args:
        path: File path relative to the notes folder, e.g. "calculus/lecture1.pdf".
        max_chars: Maximum characters to return.
    """
    root = os.path.abspath(os.environ.get("CAMPUS_NOTES_DIR", "notes"))
    full = os.path.abspath(os.path.join(root, path))
    if os.path.commonpath([root, full]) != root:
        return "Error: access outside notes folder is not allowed."
    if not os.path.isfile(full):
        files = []
        for dp, _, fs in os.walk(root):
            files += [os.path.relpath(os.path.join(dp, f), root) for f in fs]
        return f"File not found. Available files: {files[:50]}"
    if full.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            text = "\n".join((p.extract_text() or "") for p in PdfReader(full).pages)
        except Exception as e:  # noqa: BLE001
            return f"PDF read error: {e}"
    else:
        with open(full, encoding="utf-8", errors="replace") as f:
            text = f.read()
    return text[:max_chars]


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web for up-to-date info (events, campus info, facts) via DuckDuckGo/Wikipedia.

    Args:
        query: Search query.
        max_results: Number of results to return.
    """
    try:
        r = requests.get("https://api.duckduckgo.com/",
                         params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                         timeout=TIMEOUT)
        data = r.json()
        out = []
        if data.get("AbstractText"):
            out.append({"title": data.get("Heading"), "snippet": data["AbstractText"], "url": data.get("AbstractURL")})
        for t in data.get("RelatedTopics", []):
            if "Text" in t:
                out.append({"title": t["Text"][:80], "snippet": t["Text"], "url": t.get("FirstURL")})
        if out:
            return out[:max_results]
        w = requests.get("https://en.wikipedia.org/w/api.php",
                         params={"action": "opensearch", "search": query, "limit": max_results, "format": "json"},
                         timeout=TIMEOUT).json()
        return [{"title": t, "snippet": d, "url": u} for t, d, u in zip(w[1], w[2], w[3])]
    except Exception as e:  # noqa: BLE001
        return [{"error": f"search failed: {e}"}]


def github_repo_status(repo: str, limit: int = 5) -> dict:
    """Check a GitHub assignment repo: description, last commits, open issues/PRs.

    Args:
        repo: "owner/name", e.g. "octocat/Hello-World".
        limit: Number of recent commits and issues to return.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    base = f"https://api.github.com/repos/{repo}"
    try:
        info = requests.get(base, headers=headers, timeout=TIMEOUT)
        info.raise_for_status()
        info = info.json()
        commits = requests.get(f"{base}/commits", headers=headers, params={"per_page": limit}, timeout=TIMEOUT).json()
        issues = requests.get(f"{base}/issues", headers=headers,
                              params={"state": "open", "per_page": limit}, timeout=TIMEOUT).json()
        return {
            "repo": info["full_name"], "description": info.get("description"),
            "pushed_at": info.get("pushed_at"), "open_issues": info.get("open_issues_count"),
            "recent_commits": [{"sha": c["sha"][:7], "message": c["commit"]["message"].splitlines()[0],
                                "date": c["commit"]["author"]["date"]} for c in commits if isinstance(c, dict)],
            "open_items": [{"number": i["number"], "title": i["title"]} for i in issues if isinstance(i, dict)],
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"GitHub request failed: {e}"}


def get_current_datetime() -> str:
    """Return the current local date, time and weekday (ISO format)."""
    n = datetime.now()
    return f"{n.isoformat(timespec='minutes')} ({n.strftime('%A')})"


TOOLS = [get_current_datetime, calculate_deadlines, plan_study_week,
         read_local_file, web_search, github_repo_status]

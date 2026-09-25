"""Gurukul Publisher connector for Claude (MCP over stdio, standard library only).

Claude uses it to read and change the posting queue, trigger a publishing run,
and read run results and account stats. All publishing happens in GitHub Actions;
this connector only talks to the GitHub API, so the computer it runs on never
handles videos or social-media tokens.

Environment (set in claude_desktop_config.json):
  GITHUB_TOKEN   fine-grained token for this one repository:
                 Contents read/write, Actions read/write, Metadata read
  GITHUB_REPO    owner/name, e.g. rahulganorkar/gurukul-publisher
  GITHUB_BRANCH  optional, default main
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.1.0"
API = "https://api.github.com"
REPO = os.environ.get("GITHUB_REPO", "").strip()
BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip() or "main"
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
ID_RE = re.compile(r"^[A-Za-z0-9._-]{3,120}$")
OPEN_STATUSES = {"queued", "failed", "missed", "cancelled"}
_CTX = ssl.create_default_context()  # uses the OS certificate store (works behind corporate proxies)


class GhError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- GitHub API
def gh(method: str, path: str, body: dict | None = None, raw: bool = False):
    if not TOKEN or not REPO:
        raise GhError(0, "The connector is not configured: set GITHUB_TOKEN and GITHUB_REPO "
                         "in claude_desktop_config.json, then restart Claude.")
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"gurukul-publisher-mcp/{VERSION}",
        **({"Content-Type": "application/json"} if data else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=_CTX) as resp:
            payload = resp.read()
            if raw:
                return payload.decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise GhError(exc.code, f"GitHub API {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise GhError(0, f"Could not reach GitHub ({exc.reason}). Check the network or proxy.") from None


def repo_path(p: str) -> str:
    return f"/repos/{REPO}/contents/{urllib.parse.quote(p)}"


def read_json_file(p: str) -> tuple[dict, str | None]:
    meta = gh("GET", repo_path(p) + f"?ref={BRANCH}")
    content = base64.b64decode(meta.get("content", "")).decode("utf-8")
    return json.loads(content), meta.get("sha")


def write_json_file(p: str, data: dict, message: str, sha: str | None = None) -> None:
    body = {"message": message, "branch": BRANCH,
            "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()).decode()}
    if sha:
        body["sha"] = sha
    gh("PUT", repo_path(p), body)


def queue_paths() -> list[str]:
    tree = gh("GET", f"/repos/{REPO}/git/trees/{BRANCH}?recursive=1")
    return sorted(t["path"] for t in tree.get("tree", [])
                  if t["type"] == "blob" and t["path"].startswith("queue/") and t["path"].endswith(".json"))


def summarize(post: dict) -> dict:
    yt = post.get("youtube") or {}
    result = post.get("result") or {}
    return {k: v for k, v in {
        "id": post.get("id"), "account": post.get("account"), "publish_at": post.get("publish_at"),
        "status": post.get("status"), "episode": post.get("episode"),
        "title": yt.get("title") or (post.get("caption") or "").split("\n")[0][:90],
        "url": result.get("url"), "last_error": post.get("last_error"),
        "attempts": post.get("attempts"),
    }.items() if v not in (None, "", [])}


# ---------------------------------------------------------------- tools
def t_queue_list(args: dict) -> dict:
    account, status = args.get("account"), args.get("status")
    date_from, date_to = args.get("from_date", ""), args.get("to_date", "9999")
    limit = max(1, min(int(args.get("limit", 40)), 100))
    picked = []
    for p in queue_paths():
        name = p.split("/", 1)[1][:-5]
        day = name[:10]
        if day < date_from or day > date_to:
            continue
        if account and f"_{account}_" not in f"_{name}_":
            continue
        picked.append(p)
    rows = []
    for p in picked:
        if len(rows) >= limit:
            break
        post, _ = read_json_file(p)
        if status and post.get("status") != status:
            continue
        rows.append(summarize(post))
    return {"matched_files": len(picked), "returned": len(rows), "posts": rows,
            "note": "Filters by date and account use file names; status is checked per file."}


def t_queue_get(args: dict) -> dict:
    pid = _check_id(args.get("id"))
    post, _ = read_json_file(f"queue/{pid}.json")
    return post


def _check_id(pid) -> str:
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise GhError(400, "id must be 3-120 characters: letters, digits, dot, dash, underscore")
    return pid


def _validate_new(post: dict) -> list[str]:
    problems = [f"missing {k}" for k in ("id", "account", "publish_at", "video") if not post.get(k)]
    if post.get("id") and not ID_RE.match(str(post["id"])):
        problems.append("bad id")
    v = post.get("video") or {}
    if not (v.get("drive_file_id") or v.get("url")):
        problems.append("video needs drive_file_id or url")
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", str(post.get("publish_at", ""))):
        problems.append("publish_at must be ISO like 2026-10-02T10:30:00+05:30")
    acct = str(post.get("account", ""))
    if acct.endswith("_ig") and not post.get("caption"):
        problems.append("Instagram posts need caption")
    if acct.endswith("_yt") and not (post.get("youtube") or {}).get("title"):
        problems.append("YouTube posts need youtube.title")
    return problems


def t_queue_add(args: dict) -> dict:
    posts = args.get("posts") or []
    if not isinstance(posts, list) or not posts:
        raise GhError(400, "posts must be a non-empty list")
    if len(posts) > 30:
        raise GhError(400, "add at most 30 posts per call")
    existing = set(queue_paths())
    created, rejected = [], []
    for post in posts:
        problems = _validate_new(post)
        path = f"queue/{post.get('id')}.json"
        if path in existing:
            problems.append("a post with this id already exists (use queue_update)")
        if problems:
            rejected.append({"id": post.get("id"), "problems": problems})
            continue
        post = {**post, "status": "queued", "attempts": 0}
        write_json_file(path, post, f"queue: add {post['id']}")
        created.append(post["id"])
    return {"created": created, "rejected": rejected}


def t_queue_update(args: dict) -> dict:
    pid = _check_id(args.get("id"))
    changes = args.get("changes") or {}
    if not isinstance(changes, dict) or not changes:
        raise GhError(400, "changes must be a non-empty object")
    forbidden = {"id", "result", "history", "attempts"} & set(changes)
    if forbidden:
        raise GhError(400, f"these fields are managed by the publisher: {sorted(forbidden)}")
    if "status" in changes and changes["status"] != "queued":
        raise GhError(400, "status can only be set back to queued (use queue_cancel to cancel)")
    for attempt in range(2):
        post, sha = read_json_file(f"queue/{pid}.json")
        if post.get("status") not in OPEN_STATUSES:
            raise GhError(409, f"post is already {post.get('status')}; it can no longer be edited")
        if changes.get("status") == "queued":
            post["attempts"] = 0
        for k, v in changes.items():
            if k == "youtube" and isinstance(v, dict):
                post.setdefault("youtube", {}).update(v)
            else:
                post[k] = v
        if "publish_at" in changes and post.get("status") in {"failed", "missed", "cancelled"}:
            post["status"], post["attempts"] = "queued", 0
        try:
            write_json_file(f"queue/{pid}.json", post, f"queue: update {pid}", sha)
            return summarize(post)
        except GhError as exc:
            if exc.status == 409 and attempt == 0:
                continue  # the publisher changed it meanwhile; re-read and retry once
            raise
    raise GhError(409, "could not save after retry")


def t_queue_cancel(args: dict) -> dict:
    pid = _check_id(args.get("id"))
    post, sha = read_json_file(f"queue/{pid}.json")
    if post.get("status") not in {"queued", "failed", "missed"}:
        raise GhError(409, f"post is {post.get('status')}; only queued, failed or missed posts can be cancelled")
    post["status"] = "cancelled"
    write_json_file(f"queue/{pid}.json", post, f"queue: cancel {pid}", sha)
    return summarize(post)


def t_run_now(_args: dict) -> dict:
    gh("POST", f"/repos/{REPO}/actions/workflows/publish.yml/dispatches", {"ref": BRANCH})
    return {"started": True, "note": "A publishing run was queued. It usually starts within a minute."}


def t_recent_runs(args: dict) -> dict:
    n = max(1, min(int(args.get("limit", 5)), 20))
    data = gh("GET", f"/repos/{REPO}/actions/workflows/publish.yml/runs?per_page={n}")
    return {"runs": [{"started": r.get("run_started_at"), "status": r.get("status"),
                      "result": r.get("conclusion"), "trigger": r.get("event"), "url": r.get("html_url")}
                     for r in data.get("workflow_runs", [])]}


def t_status(_args: dict) -> dict:
    out = {}
    for key, path in (("last_run", "state/last_run.json"), ("stats", "stats/latest.json"),
                      ("accounts", "config/accounts.json"), ("tokens", "state/tokens.json")):
        try:
            out[key], _ = read_json_file(path)
        except GhError as exc:
            out[key] = None if exc.status == 404 else {"error": str(exc)}
    return out


POST_SCHEMA = {
    "type": "object",
    "required": ["id", "account", "publish_at", "video"],
    "properties": {
        "id": {"type": "string", "description": "File name without .json, e.g. 2026-10-02T1030_hi_ig_ep008 (date first)."},
        "account": {"type": "string", "description": "Account key from config/accounts.json: hi_ig, hi_yt, en_ig, en_yt."},
        "publish_at": {"type": "string", "description": "ISO date-time with offset, e.g. 2026-10-02T10:30:00+05:30."},
        "language": {"type": "string", "description": "hi or en"},
        "episode": {"type": "integer"},
        "video": {"type": "object", "description": "{drive_file_id} or {url} of an MP4."},
        "caption": {"type": "string", "description": "Instagram caption (also the YouTube description fallback)."},
        "youtube": {"type": "object", "description": "{title, description, tags[], category_id, made_for_kids}"},
    },
}

TOOLS = {
    "queue_list": (t_queue_list, "List queued and past posts. Filter by account, status and date range (YYYY-MM-DD).", {
        "type": "object", "properties": {
            "account": {"type": "string"}, "status": {"type": "string",
                "enum": ["queued", "uploading", "scheduled", "publishing", "published", "failed", "missed", "cancelled"]},
            "from_date": {"type": "string"}, "to_date": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}),
    "queue_get": (t_queue_get, "Read one post in full, including its history and result links.", {
        "type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}),
    "queue_add": (t_queue_add, "Add up to 30 new posts to the queue. Each post becomes queue/<id>.json.", {
        "type": "object", "required": ["posts"], "properties": {"posts": {"type": "array", "items": POST_SCHEMA}}}),
    "queue_update": (t_queue_update, "Change a post that has not gone out yet (time, caption, title, video). "
                     "Changing publish_at on a failed or missed post re-queues it.", {
        "type": "object", "required": ["id", "changes"], "properties": {
            "id": {"type": "string"}, "changes": {"type": "object"}}}),
    "queue_cancel": (t_queue_cancel, "Cancel a post so it is never published.", {
        "type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}),
    "publisher_run_now": (t_run_now, "Start a publishing run immediately instead of waiting for the 15-minute schedule.", {
        "type": "object", "properties": {}}),
    "publisher_recent_runs": (t_recent_runs, "Show the latest publishing runs and whether they succeeded.", {
        "type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}}),
    "publisher_status": (t_status, "Last run summary, queue counts per account, account readiness, "
                         "latest follower/subscriber stats and token health.", {"type": "object", "properties": {}}),
}


# ---------------------------------------------------------------- MCP stdio loop
def reply(msg_id, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return  # notification (e.g. notifications/initialized)
    if method == "initialize":
        reply(mid, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "gurukul-publisher", "version": VERSION},
                    "instructions": "Manages theAIgurukul's YouTube/Instagram posting queue in GitHub. "
                                    "Times are ISO with +05:30 offset (IST)."})
    elif method == "ping":
        reply(mid, {})
    elif method == "tools/list":
        reply(mid, {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in TOOLS.items()]})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            reply(mid, error={"code": -32602, "message": f"Unknown tool {name}"})
            return
        try:
            out = TOOLS[name][0](args)
            reply(mid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
                        "isError": False})
        except GhError as exc:
            reply(mid, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # noqa: BLE001
            reply(mid, {"content": [{"type": "text", "text": f"Unexpected error: {type(exc).__name__}: {exc}"}],
                        "isError": True})
    else:
        reply(mid, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    print(f"gurukul-publisher MCP {VERSION} ready (repo={REPO or 'NOT SET'})", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply(None, error={"code": -32700, "message": "Parse error"})
            continue
        if isinstance(msg, list):
            for m in msg:
                handle(m)
        else:
            handle(msg)


if __name__ == "__main__":
    main()

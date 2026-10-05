"""LinkedIn publishing runs, token checks and statistics.

  python -m publisher linkedin-run      post whatever is due (text, image, document/carousel, video)
  python -m publisher linkedin-check    is the access token valid, and for how long? (exit 1 if not / expiring)
  python -m publisher linkedin-refresh  renew the token, only if LinkedIn gave this app a refresh token
  python -m publisher linkedin-stats    followers and per-post impressions into stats/

Why a separate run from Instagram/YouTube: LinkedIn has no scheduling API, so a post is
created at its time, and it has its own (tight) timing window that does not depend on
due_window_minutes in settings.json.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys
import tempfile

from . import google_api as g
from . import linkedin_api as li
from .common import (ROOT, ApiError, accounts, http, iso_utc, load_json, log, now_utc, save_json,
                     settings)
from .queue import Post, load_all

AUTH_STATUSES = (401, 403)


def li_settings() -> dict:
    s = settings()
    s.setdefault("linkedin_api_version", li.DEFAULT_VERSION)
    s.setdefault("linkedin_due_window_minutes", 15)     # post at most this early
    s.setdefault("linkedin_max_late_minutes", 180)      # later than this -> "missed"
    s.setdefault("linkedin_max_per_run", 3)
    s.setdefault("linkedin_token_warn_days", 10)
    return s


# ---------------------------------------------------------------- readiness
def linkedin_ready(acct: dict) -> tuple[bool, str]:
    for key in (acct.get("token_secret"), acct.get("author_secret")):
        if not key or not os.environ.get(key, "").strip():
            return False, f"secret {key} not set"
    return True, ""


def linkedin_accounts(accts: dict | None = None) -> dict:
    accts = accts if accts is not None else accounts()
    return {n: a for n, a in accts.items() if a.get("platform") == "linkedin"}


def credentials(acct: dict) -> tuple[str, str]:
    return os.environ[acct["token_secret"]].strip(), os.environ[acct["author_secret"]].strip()


# ---------------------------------------------------------------- selection
def select_due(posts: list[Post], now: dt.datetime, s: dict, accts: dict) -> list[Post]:
    out = []
    for p in posts:
        acct = accts.get(p.get("account"), {})
        if acct.get("platform") != "linkedin" or p["status"] != "queued":
            continue
        if not linkedin_ready(acct)[0]:
            continue
        at = p.publish_at
        if at < now - dt.timedelta(minutes=s["linkedin_max_late_minutes"]):
            continue
        if at - dt.timedelta(minutes=s["linkedin_due_window_minutes"]) <= now:
            out.append(p)
    return sorted(out, key=lambda p: p.publish_at)


def mark_missed(posts: list[Post], now: dt.datetime, s: dict, accts: dict) -> int:
    n = 0
    for p in posts:
        if accts.get(p.get("account"), {}).get("platform") != "linkedin" or p["status"] != "queued":
            continue
        if p.publish_at < now - dt.timedelta(minutes=s["linkedin_max_late_minutes"]):
            p["status"] = "missed"
            p.note(f"missed: more than {s['linkedin_max_late_minutes']} min past its time")
            p.save()
            n += 1
    return n


def fail(p: Post, err: Exception, s: dict) -> None:
    p["attempts"] = int(p.get("attempts", 0)) + 1
    p["last_error"] = str(err)[:500]
    retryable = getattr(err, "retryable", True)
    if p["attempts"] >= s["max_attempts"] or not retryable:
        p["status"] = "failed"
        p.note(f"failed: {str(err)[:160]}")
    else:
        p["status"] = "queued"
        p.note(f"attempt {p['attempts']} failed, will retry: {str(err)[:120]}")
    p.save()


# ---------------------------------------------------------------- media
def fetch_media(spec: dict, dest: pathlib.Path, drive_token: str | None) -> pathlib.Path:
    """Get the file for a post: a file in this repo (path), a private Drive file, or a URL."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if spec.get("path"):
        src = (ROOT / spec["path"]).resolve()
        if ROOT.resolve() not in src.parents or not src.is_file():
            raise ApiError(f"media file {spec['path']} not found in the repository", retryable=False)
        dest.write_bytes(src.read_bytes())
        return dest
    if spec.get("drive_file_id"):
        if drive_token:
            url, params, hdr = (f"{g.DRIVE}/files/{spec['drive_file_id']}",
                                {"alt": "media", "supportsAllDrives": "true"},
                                {"Authorization": f"Bearer {drive_token}"})
        else:
            url, params, hdr = ("https://drive.usercontent.google.com/download",
                                {"id": spec["drive_file_id"], "export": "download", "confirm": "t"}, {})
        resp = http("GET", url, params=params, headers=hdr, stream=True, what="Drive download")
    else:
        resp = http("GET", spec["url"], stream=True, what="media download")
    with dest.open("wb") as fh:
        for chunk in resp.iter_content(1 << 20):
            fh.write(chunk)
    if dest.stat().st_size < 1024:
        raise ApiError("downloaded media file is empty or an error page "
                       "(is the Drive folder shared with the service account?)", retryable=False)
    return dest


def drive_token_for_run() -> str | None:
    try:
        return g.service_account_token()
    except ApiError as exc:
        log(f"Drive token unavailable ({exc}); falling back to public links")
        return None


# ---------------------------------------------------------------- publish
def publish_one(p: Post, token: str, author: str, s: dict, work: pathlib.Path, drive_tok: str | None) -> None:
    version = s["linkedin_api_version"]
    kind = p.get("type", "text")
    result = p.setdefault("result", {})
    media_urn = result.get("media_urn")
    if kind != "text" and not media_urn:
        ext = {"video": "mp4", "document": "pdf", "image": "png"}[kind]
        path = fetch_media(p[kind], work / f"{p['id']}.{ext}", drive_tok)
        up = {"video": li.upload_video, "document": li.upload_document, "image": li.upload_image}[kind]
        media_urn = up(token, author, path, version)
        result["media_urn"] = media_urn
        p.note(f"{kind} uploaded to LinkedIn")
        p.save()
    if media_urn:
        li.wait_available(token, media_urn, version)
    escaped = li.escape_commentary(p["commentary"])
    body = li.build_post_body(author, escaped, media_urn, p.get("title"), escaped=True)

    urn = None
    if result.get("create_sent"):
        # an earlier run may have created the post and lost the answer: never post twice
        try:
            urn = li.find_recent_post(token, author, escaped, version)
        except ApiError as exc:
            raise ApiError("A previous attempt may already have posted this; could not verify "
                           f"({exc}). Check the page, then set status to queued or cancelled.",
                           retryable=False) from exc
        if urn:
            p.note("found the post from an earlier attempt; not posting again")
    if not urn:
        result["create_sent"] = True
        p.save()
        try:
            urn = li.create_post(token, body, version)
        except ApiError as exc:
            if exc.status and 400 <= exc.status < 500:
                result["create_sent"] = False       # definite rejection: nothing was created
            raise
    result.update({"post_urn": urn, "url": li.post_url(urn), "create_sent": False})
    p["status"] = "published"
    p.note("published on LinkedIn")
    p.save()
    if (p.get("first_comment") or "").strip():
        try_comment(p, token, author, version)


def try_comment(p: Post, token: str, author: str, version: str) -> None:
    result = p.setdefault("result", {})
    try:
        cid = li.add_comment(token, author, result["post_urn"], p["first_comment"], version)
        result.update({"comment_id": cid, "comment_pending": False})
        p.note("first comment added")
    except ApiError as exc:
        result["comment_pending"] = int(result.get("comment_tries", 0)) < 3
        result["comment_tries"] = int(result.get("comment_tries", 0)) + 1
        result["comment_error"] = str(exc)[:200]
        p.note(f"first comment failed: {str(exc)[:100]}")
    p.save()


def retry_comments(posts: list[Post], now: dt.datetime, accts: dict, s: dict) -> None:
    for p in posts:
        r = p.get("result") or {}
        acct = accts.get(p.get("account"), {})
        if p["status"] != "published" or not r.get("comment_pending") or not linkedin_ready(acct)[0]:
            continue
        if now - p.publish_at > dt.timedelta(hours=24):
            r["comment_pending"] = False
            p.save()
            continue
        token, author = credentials(acct)
        try_comment(p, token, author, s["linkedin_api_version"])


def cmd_run() -> int:
    s, accts, now = li_settings(), accounts(), now_utc()
    posts = load_all()
    missed = mark_missed(posts, now, s, accts)
    retry_comments(posts, now, accts, s)
    due = select_due(posts, now, s, accts)[: s["linkedin_max_per_run"]]
    done = errors = 0
    auth_problem = None
    with tempfile.TemporaryDirectory() as tmp:
        drive_tok = drive_token_for_run() if any(p.get("type", "text") != "text" for p in due) else None
        for p in due:
            token, author = credentials(accts[p["account"]])
            try:
                publish_one(p, token, author, s, pathlib.Path(tmp), drive_tok)
                done += 1
                log(f"LinkedIn {p['id']} -> {p['result'].get('url')}")
            except ApiError as exc:
                errors += 1
                log(f"LinkedIn {p['id']} failed: {exc}")
                if exc.status in AUTH_STATUSES:
                    # token expired / revoked / missing permission: do not burn the post's attempts
                    auth_problem = str(exc)[:300]
                    p["status"] = "queued"
                    p["last_error"] = auth_problem
                    p.note("LinkedIn refused the token; fix the connection (see SETUP-LINKEDIN.md)")
                    p.save()
                    break
                fail(p, exc, s)
    summary = {"ran_at": iso_utc(now_utc()), "missed": missed, "linkedin_done": done,
               "linkedin_errors": errors, "auth_problem": auth_problem}
    state = load_json(ROOT / "state" / "linkedin_last_run.json", {}) or {}
    state.update(summary)
    save_json(ROOT / "state" / "linkedin_last_run.json", state)
    log(f"LinkedIn run summary: {summary}")
    return 1 if auth_problem else 0


# ---------------------------------------------------------------- token health
def cmd_check() -> int:
    s, now = li_settings(), now_utc()
    cid, csec = os.environ.get("LINKEDIN_CLIENT_ID", "").strip(), os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    ready = [n for n, a in linkedin_accounts().items() if linkedin_ready(a)[0]]
    if not ready:
        log("LinkedIn: no account is set up yet (secrets missing); nothing to check")
        return 0
    if not (cid and csec):
        log("LinkedIn: LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET not set, cannot check the token expiry")
        return 0
    token, _ = credentials(linkedin_accounts()[ready[0]])
    try:
        info = li.introspect(cid, csec, token)
    except ApiError as exc:
        log(f"LinkedIn token check failed: {exc}")
        return 1
    active = bool(info.get("active"))
    exp = info.get("expires_at")
    days = round((int(exp) - now.timestamp()) / 86400, 1) if exp else None
    save_json(ROOT / "state" / "linkedin_token.json",
              {"checked_at": iso_utc(now), "active": active, "days_left": days,
               "scope": info.get("scope"), "status": info.get("status")})
    log(f"LinkedIn token active={active}, days left={days}, scope={info.get('scope')}")
    if not active:
        log("LinkedIn token is NOT active. Run tools/linkedin_auth.py again and update the LINKEDIN_TOKEN secret.")
        return 1
    if days is not None and days <= s["linkedin_token_warn_days"]:
        log(f"LinkedIn token expires in {days} days. Run tools/linkedin_auth.py and update the LINKEDIN_TOKEN secret.")
        return 1
    return 0


def cmd_refresh() -> int:
    rt = os.environ.get("LINKEDIN_REFRESH_TOKEN", "").strip()
    cid, csec = os.environ.get("LINKEDIN_CLIENT_ID", "").strip(), os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
    if not (rt and cid and csec):
        log("LinkedIn: no refresh token configured (normal for most apps). "
            "Renew by running tools/linkedin_auth.py about every 50 days.")
        return 0
    from .gh_secrets import put_secret
    try:
        body = li.refresh_access_token(cid, csec, rt)
    except ApiError as exc:
        log(f"LinkedIn refresh failed: {exc}")
        return 1
    put_secret("LINKEDIN_TOKEN", body["access_token"])
    if body.get("refresh_token") and body["refresh_token"] != rt:
        put_secret("LINKEDIN_REFRESH_TOKEN", body["refresh_token"])
    log(f"LinkedIn token refreshed, valid {round(int(body.get('expires_in', 0)) / 86400)} days")
    return 0


# ---------------------------------------------------------------- stats
def linkedin_stats(acct: dict) -> dict:
    s = li_settings()
    token, author = credentials(acct)
    accts = accounts()
    posts = [p for p in load_all()
             if p["status"] == "published" and accts.get(p.get("account"), {}).get("platform") == "linkedin"
             and (p.get("result") or {}).get("post_urn")]
    urns = [p["result"]["post_urn"] for p in posts]
    stats = li.post_statistics(token, author, urns, s["linkedin_api_version"])
    per_post = {}
    for p in posts:
        m = stats.get(p["result"]["post_urn"])
        if m:
            per_post[p["id"]] = {"url": p["result"].get("url"), "impressions": m.get("impressionCount"),
                                 "unique_impressions": m.get("uniqueImpressionsCount"),
                                 "clicks": m.get("clickCount"), "likes": m.get("likeCount"),
                                 "comments": m.get("commentCount"), "shares": m.get("shareCount"),
                                 "engagement": m.get("engagement")}
    return {"followers": li.follower_count(token, author, s["linkedin_api_version"]),
            "published_posts": len(posts), "posts": per_post}


def cmd_stats() -> int:
    now = now_utc()
    out = {"generated_at": iso_utc(now), "accounts": {}}
    for name, acct in linkedin_accounts().items():
        ready, why = linkedin_ready(acct)
        if not ready:
            out["accounts"][name] = {"status": why}
            continue
        try:
            out["accounts"][name] = linkedin_stats(acct)
        except ApiError as exc:
            out["accounts"][name] = {"error": str(exc)[:300]}
    save_json(ROOT / "stats" / "linkedin.json", out)
    log("LinkedIn stats written")
    return 0


def main(cmd: str) -> int:
    return {"linkedin-run": cmd_run, "linkedin-check": cmd_check, "linkedin-refresh": cmd_refresh,
            "linkedin-stats": cmd_stats}[cmd]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "linkedin-run"))

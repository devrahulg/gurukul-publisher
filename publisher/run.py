"""Command-line entry points used by the GitHub Actions workflows.

  python -m publisher prepare --site site   stage today's Instagram videos for GitHub Pages
  python -m publisher run --site site       upload YouTube, publish Instagram, update the queue
  python -m publisher stats                 write stats/latest.json and a dated history file
  python -m publisher refresh-ig-tokens     extend Instagram tokens and store them back as secrets
  python -m publisher validate              check config and every queue file
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import shutil
import sys
import tempfile
import time

from . import google_api as g
from . import instagram_api as ig
from .common import (IST, ROOT, ApiError, accounts, github_output, http, iso_utc, load_json, log,
                     now_utc, save_json, settings)
from .queue import Post, load_all, validate

MANIFEST = "manifest.json"


# ---------------------------------------------------------------- selection
def account_ready(name: str, acct: dict) -> tuple[bool, str]:
    if not acct.get("enabled"):
        return False, "disabled in config/accounts.json"
    key = acct.get("token_secret") if acct["platform"] == "instagram" else acct.get("refresh_secret")
    if not key or not os.environ.get(key, "").strip():
        return False, f"secret {key} not set"
    if acct["platform"] == "youtube" and not (os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET")):
        return False, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set"
    return True, ""


def select(posts: list[Post], platform: str, now: dt.datetime, s: dict, accts: dict) -> list[Post]:
    out = []
    for p in posts:
        acct = accts.get(p.get("account"), {})
        if acct.get("platform") != platform or p["status"] != "queued":
            continue
        if not account_ready(p["account"], acct)[0]:
            continue
        at = p.publish_at
        if at < now - dt.timedelta(minutes=s["max_late_minutes"]):
            continue  # handled by mark_missed
        if platform == "instagram" and at - dt.timedelta(minutes=s["due_window_minutes"]) <= now:
            out.append(p)
        if platform == "youtube" and at - dt.timedelta(hours=s["youtube_lead_hours"]) <= now:
            out.append(p)
    return sorted(out, key=lambda p: p.publish_at)


def mark_missed(posts: list[Post], now: dt.datetime, s: dict) -> int:
    n = 0
    for p in posts:
        if p["status"] == "queued" and p.publish_at < now - dt.timedelta(minutes=s["max_late_minutes"]):
            p["status"] = "missed"
            p.note(f"missed: more than {s['max_late_minutes']} min past its time")
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


def drive_token(accts: dict) -> str | None:
    """Drive API access is optional. By default videos come from their public
    ("anyone with the link") Drive URLs; set "drive_api": true in settings.json
    only if the Google token was granted the drive.readonly scope."""
    if not settings().get("drive_api", False):
        return None
    for acct in accts.values():
        if acct.get("platform") == "youtube" and account_ready("", acct)[0]:
            try:
                return g.access_token(acct["refresh_secret"])
            except ApiError as exc:
                log(f"Drive token unavailable ({exc}); falling back to public links")
    return None


def fetch_video(post: Post, dest: pathlib.Path, token: str | None) -> pathlib.Path:
    video = post["video"]
    if video.get("drive_file_id"):
        return g.drive_download(video["drive_file_id"], dest, token)
    resp = http("GET", video["url"], stream=True, what="video download")
    with dest.open("wb") as fh:
        for chunk in resp.iter_content(1 << 20):
            fh.write(chunk)
    return dest


# ---------------------------------------------------------------- prepare
def build_static(site: pathlib.Path) -> None:
    """Public pages (home, privacy, terms) that Google and Meta link to."""
    if site.exists():
        shutil.rmtree(site)
    (site / "media").mkdir(parents=True)
    (site / ".nojekyll").write_text("")
    pages = ROOT / "pages"
    for f in pages.glob("*"):
        if f.is_file():
            shutil.copy(f, site / f.name)


def cmd_prepare(site: pathlib.Path, static_only: bool = False) -> int:
    build_static(site)
    if static_only:
        log("static pages built")
        return 0
    s, accts, now = settings(), accounts(), now_utc()
    posts = load_all()
    due = select(posts, "instagram", now, s, accts)
    staged = []
    token = drive_token(accts) if due else None
    for p in due:
        dest = site / "media" / f"{p['id']}.mp4"
        try:
            fetch_video(p, dest, token)
            staged.append({"id": p["id"], "file": f"media/{p['id']}.mp4"})
            log(f"staged {p['id']} ({dest.stat().st_size // 1024} KB)")
        except ApiError as exc:
            log(f"could not stage {p['id']}: {exc}")
            fail(p, exc, s)
    save_json(site.parent / MANIFEST, {"generated_at": iso_utc(now), "items": staged})
    github_output("has_media", "true" if staged else "false")
    log(f"{len(staged)} Instagram video(s) staged for Pages")
    return 0


# ---------------------------------------------------------------- run
def media_base_url() -> str:
    base = os.environ.get("MEDIA_BASE_URL", "").strip()
    if base:
        return base.rstrip("/")
    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}"


def wait_for_url(url: str, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = http("GET", url, ok=(200, 206), retries=1, headers={"Range": "bytes=0-1023"},
                     what="media URL check", timeout=30)
            if r.status_code in (200, 206):
                return
        except ApiError:
            pass
        time.sleep(15)
    raise ApiError(f"Media URL not reachable yet: {url}", retryable=True)


def resume_open_instagram(posts: list[Post], accts: dict, s: dict) -> None:
    """A post left in 'publishing' means a previous run stopped mid-way.
    Check its container so we never publish the same Reel twice."""
    for p in posts:
        if p["status"] != "publishing":
            continue
        acct = accts.get(p["account"], {})
        if not account_ready(p["account"], acct)[0]:
            continue
        token = os.environ[acct["token_secret"]]
        cid = (p.get("result") or {}).get("container_id")
        try:
            if not cid:
                raise ApiError("no container recorded")
            status = http("GET", f"{ig._base()}/{cid}", what="Instagram container status",
                          params={"fields": "status_code", "access_token": token}).json().get("status_code")
            if status == "PUBLISHED":
                p["status"] = "published"
                p.note("confirmed published (recovered after interrupted run)")
            elif status == "FINISHED":
                uid = ig.whoami(token)["user_id"]
                mid = ig.publish(token, uid, cid)
                p["status"] = "published"
                p.setdefault("result", {}).update({"media_id": mid, "url": ig.permalink(token, mid)})
                p.note("published (resumed)")
            else:
                raise ApiError(f"container state {status}")
            p.save()
        except ApiError as exc:
            p["status"] = "queued"
            fail(p, exc, s)


def do_youtube(posts: list[Post], now: dt.datetime, s: dict, accts: dict, work: pathlib.Path) -> dict:
    done, errors = 0, 0
    due = select(posts, "youtube", now, s, accts)[: s["max_youtube_uploads_per_run"]]
    tokens: dict[str, str] = {}
    for p in due:
        acct = accts[p["account"]]
        try:
            if p["account"] not in tokens:
                tokens[p["account"]] = g.access_token(acct["refresh_secret"])
            token = tokens[p["account"]]
            p["status"] = "uploading"
            p.note("uploading to YouTube")
            p.save()
            path = fetch_video(p, work / f"{p['id']}.mp4", drive_token(accts))
            p["_publish_at"] = p.publish_at
            resource = g.build_video_resource(p, now_utc())
            video = g.youtube_upload(token, path, resource)
            vid = video.get("id")
            st = video.get("status", {})
            scheduled = bool(st.get("publishAt"))
            p["status"] = "scheduled" if scheduled else "published"
            p["result"] = {"video_id": vid, "url": f"https://www.youtube.com/shorts/{vid}",
                           "privacy": st.get("privacyStatus"), "publish_at": st.get("publishAt"),
                           "upload_status": st.get("uploadStatus")}
            if st.get("privacyStatus") == "private" and not scheduled:
                p["result"]["warning"] = ("Uploaded as private. Unverified Google Cloud projects are "
                                          "locked to private until the YouTube API audit is approved.")
            p.note(f"YouTube {'scheduled' if scheduled else 'uploaded'}: {vid}")
            p.save()
            done += 1
            log(f"YouTube {p['id']} -> {vid} ({p['status']})")
        except ApiError as exc:
            errors += 1
            log(f"YouTube {p['id']} failed: {exc}")
            if p["status"] == "uploading":
                p["status"] = "queued"
            fail(p, exc, s)
    return {"youtube_done": done, "youtube_errors": errors}


def do_instagram(posts: list[Post], manifest: dict, s: dict, accts: dict) -> dict:
    done, errors = 0, 0
    by_id = {p["id"]: p for p in posts}
    base = media_base_url()
    profiles: dict[str, str] = {}
    for item in manifest.get("items", []):
        p = by_id.get(item["id"])
        if not p or p["status"] != "queued":
            continue
        acct = accts[p["account"]]
        token = os.environ.get(acct["token_secret"], "")
        try:
            if p["account"] not in profiles:
                profiles[p["account"]] = ig.whoami(token)["user_id"]
            uid = profiles[p["account"]]
            url = f"{base}/{item['file']}"
            wait_for_url(url)
            cid = ig.create_reel_container(token, uid, url, p["caption"], p.get("share_to_feed", True))
            p["status"] = "publishing"
            p["result"] = {"container_id": cid}
            p.note("Instagram container created")
            p.save()
            ig.wait_until_ready(token, cid)
            mid = ig.publish(token, uid, cid)
            p["status"] = "published"
            p["result"].update({"media_id": mid, "url": ig.permalink(token, mid)})
            p.note("published on Instagram")
            p.save()
            done += 1
            log(f"Instagram {p['id']} -> {p['result'].get('url')}")
        except ApiError as exc:
            errors += 1
            log(f"Instagram {p['id']} failed: {exc}")
            if p["status"] == "publishing" and not getattr(exc, "retryable", False):
                p["status"] = "queued"
            if p["status"] != "publishing":
                fail(p, exc, s)
            # a 'publishing' post that timed out is left for resume_open_instagram next run
    return {"instagram_done": done, "instagram_errors": errors}


def cmd_run(site: pathlib.Path) -> int:
    s, accts, now = settings(), accounts(), now_utc()
    posts = load_all()
    missed = mark_missed(posts, now, s)
    resume_open_instagram(posts, accts, s)
    manifest = load_json(site.parent / MANIFEST, {"items": []}) or {"items": []}
    with tempfile.TemporaryDirectory() as tmp:
        yt = do_youtube(posts, now, s, accts, pathlib.Path(tmp))
    igr = do_instagram(posts, manifest, s, accts)
    summary = {"ran_at": iso_utc(now_utc()), "missed": missed, **yt, **igr,
               "accounts": {n: (account_ready(n, a)[1] or "ready") for n, a in accts.items()},
               "queue": queue_counts(load_all())}
    save_json(ROOT / "state" / "last_run.json", summary)
    log(f"run summary: {summary}")
    return 0


# ---------------------------------------------------------------- stats
def queue_counts(posts: list[Post]) -> dict:
    counts: dict = {}
    for p in posts:
        a = counts.setdefault(p.get("account", "?"), {})
        a[p["status"]] = a.get(p["status"], 0) + 1
    return counts


def cmd_stats() -> int:
    accts = accounts()
    out = {"generated_at": iso_utc(now_utc()), "accounts": {}, "queue": queue_counts(load_all())}
    for name, acct in accts.items():
        ready, why = account_ready(name, acct)
        if not ready:
            out["accounts"][name] = {"status": why}
            continue
        try:
            if acct["platform"] == "youtube":
                out["accounts"][name] = g.channel_stats(g.access_token(acct["refresh_secret"]))
            else:
                token = os.environ[acct["token_secret"]]
                me = ig.whoami(token)
                out["accounts"][name] = {"username": me.get("username"), "account_type": me.get("account_type"),
                                         "followers": me.get("followers_count"), "posts": me.get("media_count"),
                                         "publishing_quota": ig.publishing_quota(token, me["user_id"])}
        except ApiError as exc:
            out["accounts"][name] = {"error": str(exc)[:300]}
    day = now_utc().astimezone(IST).date().isoformat()
    save_json(ROOT / "stats" / "latest.json", out)
    save_json(ROOT / "stats" / "history" / f"{day}.json", out)
    log(f"stats written for {day}")
    return 0


# ---------------------------------------------------------------- tokens
def cmd_refresh_ig_tokens() -> int:
    from .gh_secrets import put_secret  # needs pynacl; imported only here
    changed = 0
    for name, acct in accounts().items():
        if acct.get("platform") != "instagram" or not account_ready(name, acct)[0]:
            continue
        old = os.environ[acct["token_secret"]]
        try:
            new, expires = ig.refresh_long_lived(old)
        except ApiError as exc:
            log(f"{name}: token refresh failed: {exc}")
            continue
        state = load_json(ROOT / "state" / "tokens.json", {}) or {}
        state[name] = {"refreshed_at": iso_utc(now_utc()), "expires_in_days": round(expires / 86400)}
        save_json(ROOT / "state" / "tokens.json", state)
        if new != old:
            put_secret(acct["token_secret"], new)
            changed += 1
        log(f"{name}: token valid for {round(expires / 86400)} more days")
    log(f"{changed} secret(s) updated")
    return 0


def cmd_validate() -> int:
    accts = accounts()
    bad = 0
    for p in load_all():
        problems = validate(p, accts)
        if problems:
            bad += 1
            print(f"{p.path.name}: {'; '.join(problems)}")
    for name, acct in accts.items():
        ready, why = account_ready(name, acct)
        print(f"account {name:6s} {acct['platform']:9s} {'ready' if ready else why}")
    print(f"{len(load_all())} queue files, {bad} with problems")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="publisher")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--site", default="site")
        if name == "prepare":
            sp.add_argument("--static-only", action="store_true")
    for name in ("stats", "refresh-ig-tokens", "validate"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(pathlib.Path(args.site), args.static_only)
    if args.cmd == "run":
        return cmd_run(pathlib.Path(args.site))
    if args.cmd == "stats":
        return cmd_stats()
    if args.cmd == "refresh-ig-tokens":
        return cmd_refresh_ig_tokens()
    return cmd_validate()


if __name__ == "__main__":
    sys.exit(main())

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from publisher import google_api as g  # noqa: E402
from publisher import instagram_api as ig  # noqa: E402
from publisher import queue as q  # noqa: E402
from publisher import run  # noqa: E402
from publisher.common import IST, parse_ts  # noqa: E402

ACCTS = {
    "hi_ig": {"platform": "instagram", "token_secret": "IG_TOKEN_HI", "enabled": True},
    "hi_yt": {"platform": "youtube", "refresh_secret": "YT_REFRESH_HI", "enabled": True},
    "en_ig": {"platform": "instagram", "token_secret": "IG_TOKEN_EN", "enabled": False},
}
S = {"due_window_minutes": 20, "max_late_minutes": 180, "youtube_lead_hours": 36, "max_attempts": 3,
     "max_youtube_uploads_per_run": 6, "ig_api_version": "v25.0"}


@pytest.fixture
def qdir(tmp_path, monkeypatch):
    d = tmp_path / "queue"
    d.mkdir()
    monkeypatch.setattr(q, "QDIR", d)
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setenv("IG_TOKEN_HI", "ig-token")
    monkeypatch.setenv("YT_REFRESH_HI", "yt-refresh")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    return d


def put(d, pid, account, at, **extra):
    post = {"id": pid, "account": account, "publish_at": at, "video": {"drive_file_id": "X"},
            "caption": "cap", "status": "queued", "attempts": 0, **extra}
    if account.endswith("yt"):
        post["youtube"] = {"title": "T", "tags": ["#a"]}
    (d / f"{pid}.json").write_text(json.dumps(post), encoding="utf-8")


def now_at(s):
    return parse_ts(s)


def test_instagram_selection_window_and_late(qdir):
    put(qdir, "a", "hi_ig", "2026-10-02T10:30:00+05:30")   # due in 10 min -> selected
    put(qdir, "b", "hi_ig", "2026-10-02T11:30:00+05:30")   # later -> not yet
    put(qdir, "c", "hi_ig", "2026-10-02T05:00:00+05:30")   # 5h20 late -> missed
    put(qdir, "d", "en_ig", "2026-10-02T10:30:00+05:30")   # account disabled
    now = now_at("2026-10-02T10:20:00+05:30")
    posts = q.load_all()
    assert [p["id"] for p in run.select(posts, "instagram", now, S, ACCTS)] == ["a"]
    assert run.mark_missed(posts, now, S) == 1
    assert json.loads((qdir / "c.json").read_text())["status"] == "missed"


def test_youtube_lead_time(qdir):
    put(qdir, "y1", "hi_yt", "2026-10-03T16:00:00+05:30")  # 30h ahead -> within 36h lead
    put(qdir, "y2", "hi_yt", "2026-10-05T16:00:00+05:30")  # too far ahead
    now = now_at("2026-10-02T10:00:00+05:30")
    assert [p["id"] for p in run.select(q.load_all(), "youtube", now, S, ACCTS)] == ["y1"]


def test_youtube_resource_scheduled_vs_immediate():
    now = dt.datetime(2026, 10, 2, 4, 0, tzinfo=dt.timezone.utc)
    post = {"youtube": {"title": "x" * 120, "tags": ["#Kiro"]}, "language": "hi",
            "_publish_at": now + dt.timedelta(hours=10)}
    r = g.build_video_resource(post, now)
    assert r["status"]["privacyStatus"] == "private" and r["status"]["publishAt"] == "2026-10-02T14:00:00Z"
    assert len(r["snippet"]["title"]) == 100 and r["snippet"]["tags"] == ["Kiro"]
    assert r["snippet"]["defaultLanguage"] == "hi" and r["snippet"]["categoryId"] == "27"
    post["_publish_at"] = now + dt.timedelta(minutes=5)
    r = g.build_video_resource(post, now)
    assert r["status"]["privacyStatus"] == "public" and "publishAt" not in r["status"]


def test_instagram_publish_happy_path(qdir, monkeypatch):
    put(qdir, "a", "hi_ig", "2026-10-02T10:30:00+05:30")
    calls = []
    monkeypatch.setattr(run, "wait_for_url", lambda url: calls.append(("url", url)))
    monkeypatch.setattr(ig, "whoami", lambda t: {"user_id": "U1"})
    monkeypatch.setattr(ig, "create_reel_container", lambda t, u, url, cap, share: calls.append(("create", u, url)) or "C1")
    monkeypatch.setattr(ig, "wait_until_ready", lambda t, c: None)
    monkeypatch.setattr(ig, "publish", lambda t, u, c: "M1")
    monkeypatch.setattr(ig, "permalink", lambda t, m: "https://instagram.com/reel/M1")
    monkeypatch.setenv("MEDIA_BASE_URL", "https://me.github.io/pub")
    res = run.do_instagram(q.load_all(), {"items": [{"id": "a", "file": "media/a.mp4"}]}, S, ACCTS)
    post = json.loads((qdir / "a.json").read_text())
    assert res == {"instagram_done": 1, "instagram_errors": 0}
    assert post["status"] == "published" and post["result"]["url"].endswith("M1")
    assert ("create", "U1", "https://me.github.io/pub/media/a.mp4") in calls


def test_instagram_failure_is_retried_then_fails(qdir, monkeypatch):
    put(qdir, "a", "hi_ig", "2026-10-02T10:30:00+05:30")
    monkeypatch.setattr(run, "wait_for_url", lambda url: None)
    monkeypatch.setattr(ig, "whoami", lambda t: {"user_id": "U1"})

    def boom(*a, **k):
        raise run.ApiError("bad video", 400, retryable=True)
    monkeypatch.setattr(ig, "create_reel_container", boom)
    manifest = {"items": [{"id": "a", "file": "media/a.mp4"}]}
    for expected_status, attempts in (("queued", 1), ("queued", 2), ("failed", 3)):
        run.do_instagram(q.load_all(), manifest, S, ACCTS)
        post = json.loads((qdir / "a.json").read_text())
        assert (post["status"], post["attempts"]) == (expected_status, attempts)


def test_interrupted_instagram_post_is_not_published_twice(qdir, monkeypatch):
    put(qdir, "a", "hi_ig", "2026-10-02T10:30:00+05:30", status="publishing", result={"container_id": "C1"})

    class R:
        def json(self):
            return {"status_code": "PUBLISHED"}
    monkeypatch.setattr(run, "http", lambda *a, **k: R())
    published = []
    monkeypatch.setattr(ig, "publish", lambda *a: published.append(a) or "M")
    run.resume_open_instagram(q.load_all(), ACCTS, S)
    assert json.loads((qdir / "a.json").read_text())["status"] == "published"
    assert published == []


def test_youtube_upload_happy_path(qdir, monkeypatch, tmp_path):
    put(qdir, "y1", "hi_yt", "2026-10-03T16:00:00+05:30")
    monkeypatch.setenv("PUBLISHER_NOW", "2026-10-02T10:00:00+05:30")
    monkeypatch.setattr(g, "access_token", lambda env: "AT")
    monkeypatch.setattr(run, "fetch_video", lambda p, dest, tok: dest)
    seen = {}
    monkeypatch.setattr(g, "youtube_upload", lambda tok, path, res: seen.update(res) or
                        {"id": "VID", "status": {"privacyStatus": "private", "publishAt": "2026-10-03T10:30:00Z"}})
    res = run.do_youtube(q.load_all(), now_at("2026-10-02T10:00:00+05:30"), S, ACCTS, tmp_path)
    post = json.loads((qdir / "y1.json").read_text())
    assert res["youtube_done"] == 1 and post["status"] == "scheduled"
    assert post["result"]["url"] == "https://www.youtube.com/shorts/VID"
    assert seen["status"]["publishAt"] == "2026-10-03T10:30:00Z"


def test_account_without_secret_is_skipped(qdir, monkeypatch):
    monkeypatch.delenv("IG_TOKEN_HI")
    put(qdir, "a", "hi_ig", "2026-10-02T10:30:00+05:30")
    assert run.select(q.load_all(), "instagram", now_at("2026-10-02T10:25:00+05:30"), S, ACCTS) == []


def test_seeded_queue_is_valid():
    accts = json.loads((ROOT / "config" / "accounts.json").read_text())
    files = sorted((ROOT / "queue").glob("*.json"))
    assert len(files) == 292
    for f in files:
        post = json.loads(f.read_text(encoding="utf-8"))
        assert q.validate(post, accts) == [], f.name
        assert post["id"] == f.stem
        assert parse_ts(post["publish_at"]).astimezone(IST).strftime("%H:%M") in ("10:30", "16:00")


def test_mcp_server_handshake_and_tools():
    env = {**os.environ, "GITHUB_TOKEN": "", "GITHUB_REPO": ""}
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "publisher_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "queue_get", "arguments": {"id": "../x"}}},
    ]
    out = subprocess.run([sys.executable, str(ROOT / "mcp_server" / "server.py")],
                         input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, env=env, timeout=20)
    replies = [json.loads(line) for line in out.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["serverInfo"]["name"] == "gurukul-publisher"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert {"queue_list", "queue_add", "queue_update", "queue_cancel", "publisher_run_now",
            "publisher_status", "publisher_recent_runs", "queue_get"} == names
    # status degrades gracefully when not configured
    assert "not configured" in json.dumps(replies[2]["result"])
    # path traversal ids are rejected
    assert replies[3]["result"]["isError"] is True

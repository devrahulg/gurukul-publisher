"""LinkedIn tests against a fake LinkedIn (no network)."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from publisher import linkedin_api as li  # noqa: E402
from publisher import linkedin_run as lr  # noqa: E402
from publisher import queue as q  # noqa: E402
from publisher.common import ApiError, parse_ts  # noqa: E402

ACCTS = {"li_page": {"platform": "linkedin", "token_secret": "LINKEDIN_TOKEN",
                     "author_secret": "LINKEDIN_AUTHOR", "enabled": True},
         "en_ig": {"platform": "instagram", "token_secret": "IG_TOKEN_EN", "enabled": True}}
AUTHOR = "urn:li:organization:777"


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body if self._body is not None else {}

    def iter_content(self, n):
        yield b"x" * 4096


class Fake:
    """Routes calls made through publisher.common.http (requests.request) to canned answers."""

    def __init__(self):
        self.calls, self.fail_next_post, self.recent, self.video_parts = [], None, [], 3
        self.posts_created = 0

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        base = url.split("?")[0]
        if base.endswith("/videos") and "initializeUpload" in url:
            size = kw["json"]["initializeUploadRequest"]["fileSizeBytes"]
            step = li.CHUNK
            parts = [{"uploadUrl": f"https://up/v{i}", "firstByte": i * step,
                      "lastByte": min(size, (i + 1) * step) - 1} for i in range((size + step - 1) // step)]
            return Resp(200, {"value": {"video": "urn:li:video:V1", "uploadInstructions": parts, "uploadToken": ""}})
        if base.endswith("/documents") and "initializeUpload" in url:
            return Resp(200, {"value": {"document": "urn:li:document:D1", "uploadUrl": "https://up/doc"}})
        if base.startswith("https://up/v"):
            return Resp(200, headers={"etag": "etag-" + base[-1]})
        if base == "https://up/doc":
            return Resp(201)
        if "/videos/" in base or "/documents/" in base:
            return Resp(200, {"status": "AVAILABLE"})
        if method == "POST" and base.endswith("/finalizeUpload") or "finalizeUpload" in url:
            return Resp(200)
        if method == "POST" and base.endswith("/posts"):
            if self.fail_next_post:
                err, self.fail_next_post = self.fail_next_post, None
                return err
            self.posts_created += 1
            return Resp(201, headers={"x-restli-id": f"urn:li:share:{self.posts_created}"})
        if method == "GET" and base.endswith("/posts"):
            return Resp(200, {"elements": self.recent})
        if "/socialActions/" in base:
            return Resp(201, headers={"x-restli-id": "c1"})
        raise AssertionError(f"unexpected call {method} {url}")


@pytest.fixture
def env(tmp_path, monkeypatch):
    qd = tmp_path / "queue"
    qd.mkdir()
    monkeypatch.setattr(q, "QDIR", qd)
    monkeypatch.setattr(lr, "ROOT", tmp_path)
    monkeypatch.setattr(lr, "accounts", lambda: ACCTS)
    monkeypatch.setenv("LINKEDIN_TOKEN", "tok")
    monkeypatch.setenv("LINKEDIN_AUTHOR", AUTHOR)
    monkeypatch.setenv("PUBLISHER_NOW", "2026-10-12T09:10:00+05:30")
    monkeypatch.setattr("time.sleep", lambda s: None)
    fake = Fake()
    monkeypatch.setattr("requests.request", fake)
    (tmp_path / "carousels").mkdir()
    (tmp_path / "carousels" / "c.pdf").write_bytes(b"%PDF" + b"0" * 3000)
    return qd, fake, tmp_path


def put(qd, pid="2026-10-12T0915_li_page_ep1", **extra):
    post = {"id": pid, "account": "li_page", "publish_at": "2026-10-12T09:15:00+05:30", "type": "text",
            "commentary": "Hello (world) #Kiro", "status": "queued", "attempts": 0, **extra}
    (qd / f"{pid}.json").write_text(json.dumps(post), encoding="utf-8")
    return pid


def get(qd, pid):
    return json.loads((qd / f"{pid}.json").read_text(encoding="utf-8"))


# ---- text format
def test_escape_commentary():
    out = li.escape_commentary("Use (a) [b] and a_b * ~ | { } @ < > \\ now #Kiro #AI-tools #1")
    assert "\\(a\\) \\[b\\]" in out and "a\\_b" in out and "\\@" in out
    assert "{hashtag|\\#|Kiro}" in out
    assert "{hashtag|\\#|AI}" in out          # stops at the hyphen
    assert "\\#1" in out                      # not a hashtag (starts with digit): escaped, stays text
    assert li.escape_commentary("x" * 5000).__len__() == li.MAX_COMMENTARY


def test_post_problems():
    assert li.post_problems({"commentary": "hi"}) == []
    assert li.post_problems({"type": "video", "commentary": "hi"}) == ["video needs drive_file_id, url or path"]
    assert li.post_problems({"type": "gif", "commentary": "hi"})
    assert li.post_problems({"type": "text"}) == ["LinkedIn post needs commentary"]


def test_queue_validation_text_post_needs_no_video():
    ok = {"id": "a", "account": "li_page", "publish_at": "2026-10-12T09:15:00+05:30", "commentary": "hi"}
    assert q.validate(ok, ACCTS) == []
    ig = {"id": "a", "account": "en_ig", "publish_at": "2026-10-12T09:15:00+05:30", "caption": "c"}
    assert "missing video" in q.validate(ig, ACCTS)


# ---- selection
def test_selection_window_and_late(env):
    qd, _, _ = env
    put(qd, "a")                                                   # due in 5 min: inside the 15 min window
    put(qd, "b", publish_at="2026-10-12T09:40:00+05:30")           # too early
    put(qd, "c", publish_at="2026-10-12T05:00:00+05:30")           # 4h10 late
    s = lr.li_settings()
    posts = q.load_all()
    now = parse_ts("2026-10-12T09:10:00+05:30")
    assert [p["id"] for p in lr.select_due(posts, now, s, ACCTS)] == ["a"]
    assert lr.mark_missed(posts, now, s, ACCTS) == 1
    assert get(qd, "c")["status"] == "missed"


def test_not_ready_without_secret(env, monkeypatch):
    qd, _, _ = env
    put(qd)
    monkeypatch.delenv("LINKEDIN_AUTHOR")
    assert lr.select_due(q.load_all(), parse_ts("2026-10-12T09:10:00+05:30"), lr.li_settings(), ACCTS) == []


# ---- publishing
def test_text_post_with_first_comment(env):
    qd, fake, _ = env
    put(qd, first_comment="Docs: https://kiro.dev")
    assert lr.cmd_run() == 0
    p = get(qd, "2026-10-12T0915_li_page_ep1")
    assert p["status"] == "published" and p["result"]["post_urn"] == "urn:li:share:1"
    assert p["result"]["url"].endswith("urn:li:share:1/") and p["result"]["comment_id"] == "c1"
    method, url, kw = next(c for c in fake.calls if c[0] == "POST" and c[1].endswith("/rest/posts"))
    body = kw["json"]
    assert body["author"] == AUTHOR and body["lifecycleState"] == "PUBLISHED" and "content" not in body
    assert "{hashtag|\\#|Kiro}" in body["commentary"] and "\\(world\\)" in body["commentary"]
    h = kw["headers"]
    assert h["Authorization"] == "Bearer tok" and h["Linkedin-Version"] == li.DEFAULT_VERSION
    assert h["X-Restli-Protocol-Version"] == "2.0.0"


def test_document_post_uploads_and_attaches(env):
    qd, fake, _ = env
    put(qd, type="document", document={"path": "carousels/c.pdf"}, title="My carousel")
    assert lr.cmd_run() == 0
    put_call = next(c for c in fake.calls if c[0] == "PUT")
    assert put_call[1] == "https://up/doc" and put_call[2]["headers"]["Authorization"] == "Bearer tok"
    body = next(c for c in fake.calls if c[0] == "POST" and c[1].endswith("/rest/posts"))[2]["json"]
    assert body["content"]["media"] == {"id": "urn:li:document:D1", "title": "My carousel"}
    assert get(qd, "2026-10-12T0915_li_page_ep1")["status"] == "published"


def test_video_multipart_upload(env, monkeypatch, tmp_path):
    qd, fake, root = env
    big = root / "v.mp4"
    big.write_bytes(bytes(range(256)) * (9 * 4096))                # ~9.4 MB -> 3 parts
    monkeypatch.setattr(lr, "fetch_media", lambda spec, dest, tok: (dest.write_bytes(big.read_bytes()), dest)[1])
    put(qd, type="video", video={"drive_file_id": "X"})
    assert lr.cmd_run() == 0
    puts = [c for c in fake.calls if c[0] == "PUT"]
    assert [c[1] for c in puts] == ["https://up/v0", "https://up/v1", "https://up/v2"]
    assert sum(len(c[2]["data"]) for c in puts) == big.stat().st_size
    assert "Authorization" not in puts[0][2]["headers"]               # upload URLs are pre-signed
    fin = next(c for c in fake.calls if "finalizeUpload" in c[1])[2]["json"]["finalizeUploadRequest"]
    assert fin["uploadedPartIds"] == ["etag-0", "etag-1", "etag-2"] and fin["video"] == "urn:li:video:V1"
    body = next(c for c in fake.calls if c[0] == "POST" and c[1].endswith("/rest/posts"))[2]["json"]
    assert body["content"]["media"]["id"] == "urn:li:video:V1"


def test_expired_token_keeps_post_queued_and_fails_run(env):
    qd, fake, _ = env
    put(qd)
    fake.fail_next_post = Resp(401, {"message": "token expired"})
    assert lr.cmd_run() == 1
    p = get(qd, "2026-10-12T0915_li_page_ep1")
    assert p["status"] == "queued" and p["attempts"] == 0 and "refused the token" in p["history"][-1]["event"]


def test_rejected_post_fails_without_retry_loop(env):
    qd, fake, _ = env
    put(qd)
    fake.fail_next_post = Resp(422, {"message": "duplicate"})
    lr.cmd_run()
    p = get(qd, "2026-10-12T0915_li_page_ep1")
    assert p["status"] == "failed" and p["result"]["create_sent"] is False


def test_never_posts_twice_after_lost_answer(env):
    qd, fake, _ = env
    pid = put(qd)
    fake.fail_next_post = Resp(503, {"message": "gateway"})
    lr.cmd_run()
    p = get(qd, pid)
    assert p["status"] == "queued" and p["result"]["create_sent"] is True       # outcome unknown
    # LinkedIn did create it after all:
    fake.recent = [{"id": "urn:li:share:99", "commentary": li.escape_commentary("Hello (world) #Kiro")}]
    created_before = fake.posts_created
    lr.cmd_run()
    p = get(qd, pid)
    assert p["status"] == "published" and p["result"]["post_urn"] == "urn:li:share:99"
    assert fake.posts_created == created_before                                   # no second post


def test_unverifiable_lost_answer_fails_safe(env, monkeypatch):
    qd, fake, _ = env
    pid = put(qd, result={"create_sent": True})
    orig = fake.__call__

    def no_listing(method, url, **kw):
        if method == "GET" and url.split("?")[0].endswith("/posts"):
            return Resp(403, {"message": "no r_organization_social"})
        return orig(method, url, **kw)
    monkeypatch.setattr("requests.request", no_listing)
    lr.cmd_run()
    p = get(qd, pid)
    assert p["status"] == "failed" and "Check the page" in p["last_error"]
    assert fake.posts_created == 0                                                # and nothing was re-posted


def test_comment_failure_is_retried_later(env, monkeypatch):
    qd, fake, _ = env
    pid = put(qd, first_comment="Docs")
    orig = fake.__call__
    state = {"n": 0}

    def flaky(method, url, **kw):
        if "/socialActions/" in url and state["n"] < 2:
            state["n"] += 1
            return Resp(500, {"message": "x"})
        return orig(method, url, **kw)
    monkeypatch.setattr("requests.request", flaky)
    lr.cmd_run()
    assert get(qd, pid)["status"] == "published" and get(qd, pid)["result"]["comment_pending"] is True
    monkeypatch.setenv("PUBLISHER_NOW", "2026-10-12T10:00:00+05:30")
    lr.cmd_run()
    assert get(qd, pid)["result"]["comment_pending"] is False and get(qd, pid)["result"]["comment_id"] == "c1"


def test_missing_repo_media_fails_cleanly(env):
    qd, _, _ = env
    pid = put(qd, type="document", document={"path": "carousels/nope.pdf"})
    lr.cmd_run()
    assert get(qd, pid)["status"] == "failed"


def test_token_check(env, monkeypatch):
    monkeypatch.setattr(lr, "linkedin_accounts", lambda *a: ACCTS and {"li_page": ACCTS["li_page"]})
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "cid")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "cs")
    now = parse_ts("2026-10-12T09:10:00+05:30").timestamp()
    monkeypatch.setattr(li, "introspect", lambda *a: {"active": True, "expires_at": now + 40 * 86400, "scope": "w_organization_social"})
    assert lr.cmd_check() == 0
    monkeypatch.setattr(li, "introspect", lambda *a: {"active": True, "expires_at": now + 5 * 86400})
    assert lr.cmd_check() == 1                                                    # expiring soon -> loud
    monkeypatch.setattr(li, "introspect", lambda *a: {"active": False})
    assert lr.cmd_check() == 1


def test_refresh_without_refresh_token_is_a_noop(monkeypatch):
    monkeypatch.delenv("LINKEDIN_REFRESH_TOKEN", raising=False)
    assert lr.cmd_refresh() == 0

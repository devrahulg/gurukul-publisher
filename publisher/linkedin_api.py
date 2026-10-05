"""LinkedIn side (official REST APIs): Posts, Videos, Documents, Images, comments,
token introspection and best-effort statistics. Nothing here scrapes or
automates the website; it uses only documented endpoints.

Needs an access token with w_organization_social (post as a Page) or
w_member_social (post as yourself).
"""
from __future__ import annotations

import pathlib
import re
import time
import urllib.parse

from .common import ApiError, http

API = "https://api.linkedin.com/rest"
OAUTH = "https://www.linkedin.com/oauth/v2"
DEFAULT_VERSION = "202609"          # YYYYMM; LinkedIn supports each version for about a year
CHUNK = 4 * 1024 * 1024             # video upload part size required by the Videos API
TYPES = ("video", "document", "image", "text")
MAX_COMMENTARY = 3000

# Characters the "little text" format treats as markup. Unescaped, LinkedIn can
# silently cut the post off at the first one (a famous trap with "(" and "[").
_RESERVED = r"\|{}@[]()<>#*_~"
_HASHTAG = re.compile(r"(?<![\w#&/])#([A-Za-z][A-Za-z0-9_]{1,48})")


def escape_commentary(text: str) -> str:
    """Turn plain post text into LinkedIn commentary: escape markup characters
    and convert #hashtags into real hashtag entities."""
    text = text.replace("\r\n", "\n").strip()
    tags: list[str] = []

    def stash(m: re.Match) -> str:
        tags.append(m.group(1))
        return f"\x00{len(tags) - 1}\x00"

    work = _HASHTAG.sub(stash, text)
    out = "".join("\\" + c if c in _RESERVED else c for c in work)
    out = re.sub(r"\x00(\d+)\x00", lambda m: "{hashtag|\\#|" + tags[int(m.group(1))] + "}", out)
    return out[:MAX_COMMENTARY]


def headers(token: str, version: str = DEFAULT_VERSION, json_body: bool = True) -> dict:
    h = {"Authorization": f"Bearer {token}", "Linkedin-Version": version,
         "X-Restli-Protocol-Version": "2.0.0"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _q(urn: str) -> str:
    return urllib.parse.quote(urn, safe="")


def post_problems(post: dict) -> list[str]:
    """Queue-file validation for a LinkedIn post (used by `publisher validate`)."""
    problems = []
    kind = post.get("type", "text")
    if kind not in TYPES:
        problems.append(f"LinkedIn type must be one of {', '.join(TYPES)}")
        return problems
    if not (post.get("commentary") or "").strip():
        problems.append("LinkedIn post needs commentary")
    if len(post.get("commentary") or "") > MAX_COMMENTARY:
        problems.append(f"commentary is over {MAX_COMMENTARY} characters")
    if kind != "text":
        spec = post.get(kind) or {}
        if not (spec.get("drive_file_id") or spec.get("url") or spec.get("path")):
            problems.append(f"{kind} needs drive_file_id, url or path")
    return problems


# ---------------------------------------------------------------- uploads
def _initialize(token: str, version: str, kind: str, owner: str, size: int | None) -> dict:
    plural = {"video": "videos", "document": "documents", "image": "images"}[kind]
    body: dict = {"owner": owner}
    if kind == "video":
        body.update({"fileSizeBytes": size, "uploadCaptions": False, "uploadThumbnail": False})
    resp = http("POST", f"{API}/{plural}?action=initializeUpload", headers=headers(token, version),
                json={"initializeUploadRequest": body}, what=f"LinkedIn {kind} init")
    value = resp.json().get("value") or {}
    if not value:
        raise ApiError(f"LinkedIn {kind} init returned no value")
    return value


def _put_bytes(url: str, data: bytes, token: str | None, what: str):
    h = {"Content-Type": "application/octet-stream"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return http("PUT", url, headers=h, data=data, ok=(200, 201), what=what, timeout=300)


def upload_video(token: str, owner: str, path: pathlib.Path, version: str = DEFAULT_VERSION) -> str:
    """Multi-part upload (4 MB parts). Returns the video URN once uploaded."""
    size = path.stat().st_size
    value = _initialize(token, version, "video", owner, size)
    urn = value.get("video")
    instructions = value.get("uploadInstructions") or []
    if not urn or not instructions:
        raise ApiError("LinkedIn video init returned no upload instructions")
    etags = []
    with path.open("rb") as fh:
        for part in instructions:
            first, last = int(part["firstByte"]), int(part["lastByte"])
            fh.seek(first)
            data = fh.read(last - first + 1)
            resp = _put_bytes(part["uploadUrl"], data, None, "LinkedIn video part")
            etag = resp.headers.get("etag") or resp.headers.get("ETag")
            if not etag:
                raise ApiError("LinkedIn video part upload returned no ETag")
            etags.append(etag)
    http("POST", f"{API}/videos?action=finalizeUpload", headers=headers(token, version),
         json={"finalizeUploadRequest": {"video": urn, "uploadToken": value.get("uploadToken", ""),
                                         "uploadedPartIds": etags}},
         ok=(200, 201, 204), what="LinkedIn video finalize")
    return urn


def upload_document(token: str, owner: str, path: pathlib.Path, version: str = DEFAULT_VERSION) -> str:
    value = _initialize(token, version, "document", owner, None)
    urn, url = value.get("document"), value.get("uploadUrl")
    if not urn or not url:
        raise ApiError("LinkedIn document init returned no upload URL")
    _put_bytes(url, path.read_bytes(), token, "LinkedIn document upload")
    return urn


def upload_image(token: str, owner: str, path: pathlib.Path, version: str = DEFAULT_VERSION) -> str:
    value = _initialize(token, version, "image", owner, None)
    urn, url = value.get("image"), value.get("uploadUrl")
    if not urn or not url:
        raise ApiError("LinkedIn image init returned no upload URL")
    _put_bytes(url, path.read_bytes(), token, "LinkedIn image upload")
    return urn


def wait_available(token: str, urn: str, version: str = DEFAULT_VERSION, timeout_s: int = 600,
                   every_s: int = 15) -> None:
    kind = urn.split(":")[2]                      # urn:li:video:XYZ -> video
    plural = {"video": "videos", "document": "documents", "image": "images"}[kind]
    deadline = time.time() + timeout_s
    status = "UNKNOWN"
    while time.time() < deadline:
        body = http("GET", f"{API}/{plural}/{_q(urn)}", headers=headers(token, version, False),
                    what=f"LinkedIn {kind} status").json()
        status = body.get("status", "UNKNOWN")
        if status == "AVAILABLE":
            return
        if status == "PROCESSING_FAILED":
            raise ApiError(f"LinkedIn could not process the {kind}")
        time.sleep(every_s)
    raise ApiError(f"LinkedIn {kind} still {status} after {timeout_s}s", retryable=True)


# ---------------------------------------------------------------- posts
def build_post_body(author: str, commentary: str, media_urn: str | None = None,
                    title: str | None = None, escaped: bool = False) -> dict:
    body: dict = {
        "author": author,
        "commentary": commentary if escaped else escape_commentary(commentary),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                         "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if media_urn:
        media: dict = {"id": media_urn}
        if title:
            media["title"] = title[:200]
        body["content"] = {"media": media}
    return body


def create_post(token: str, body: dict, version: str = DEFAULT_VERSION) -> str:
    """Create the post. retries=1 on purpose: a blind re-POST after a timeout could double-post."""
    resp = http("POST", f"{API}/posts", headers=headers(token, version), json=body, ok=(201,),
                retries=1, what="LinkedIn create post")
    urn = resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id")
    if not urn:
        raise ApiError("LinkedIn created the post but returned no id", retryable=False)
    return urn


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def find_recent_post(token: str, author: str, escaped_commentary: str,
                     version: str = DEFAULT_VERSION) -> str | None:
    """Look through the page's latest posts for one we already sent (duplicate guard)."""
    url = f"{API}/posts?author={_q(author)}&q=author&count=20&sortBy=LAST_MODIFIED"
    h = headers(token, version, False)
    h["X-RestLi-Method"] = "FINDER"
    elements = http("GET", url, headers=h, what="LinkedIn recent posts").json().get("elements", [])
    probe = escaped_commentary[:120]
    for el in elements:
        if (el.get("commentary") or "")[:120] == probe:
            return el.get("id")
    return None


def add_comment(token: str, actor: str, post_urn: str, text: str, version: str = DEFAULT_VERSION) -> str | None:
    """First comment (used for links, which reach further when kept out of the post body)."""
    resp = http("POST", f"{API}/socialActions/{_q(post_urn)}/comments", headers=headers(token, version),
                json={"actor": actor, "object": post_urn, "message": {"text": text[:1250]}},
                ok=(200, 201), retries=2, what="LinkedIn comment")
    return resp.headers.get("x-restli-id")


# ---------------------------------------------------------------- tokens & stats
def introspect(client_id: str, client_secret: str, token: str) -> dict:
    resp = http("POST", f"{OAUTH}/introspectToken", ok=(200,), what="LinkedIn token check",
                data={"client_id": client_id, "client_secret": client_secret, "token": token})
    return resp.json()


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Only works for apps LinkedIn has enabled programmatic refresh on (partners)."""
    resp = http("POST", f"{OAUTH}/accessToken", ok=(200,), what="LinkedIn token refresh",
                data={"grant_type": "refresh_token", "refresh_token": refresh_token,
                      "client_id": client_id, "client_secret": client_secret})
    body = resp.json()
    if not body.get("access_token"):
        raise ApiError("LinkedIn token refresh returned no token")
    return body


def follower_count(token: str, org_urn: str, version: str = DEFAULT_VERSION) -> int | None:
    if ":organization:" not in org_urn:
        return None
    try:
        url = f"{API}/networkSizes/{_q(org_urn)}?edgeType=COMPANY_FOLLOWED_BY_MEMBER"
        return http("GET", url, headers=headers(token, version, False), what="LinkedIn followers"
                    ).json().get("firstDegreeSize")
    except ApiError:
        return None


def post_statistics(token: str, org_urn: str, post_urns: list[str], version: str = DEFAULT_VERSION) -> dict:
    """Impressions, clicks, reactions, comments and shares per post (Page posts only).
    Best effort: returns {} if the app lacks r_organization_social."""
    if ":organization:" not in org_urn or not post_urns:
        return {}
    out: dict = {}
    for kind, key in (("share", "shares"), ("ugcPost", "ugcPosts")):
        urns = [u for u in post_urns if u.startswith(f"urn:li:{kind}:")]
        for i in range(0, len(urns), 20):
            chunk = urns[i:i + 20]
            lst = ",".join(_q(u) for u in chunk)
            url = (f"{API}/organizationalEntityShareStatistics?q=organizationalEntity"
                   f"&organizationalEntity={_q(org_urn)}&{key}=List({lst})")
            try:
                els = http("GET", url, headers=headers(token, version, False),
                           what="LinkedIn post statistics").json().get("elements", [])
            except ApiError:
                return out
            for el in els:
                urn = el.get("share") or el.get("ugcPost")
                if urn:
                    out[urn] = el.get("totalShareStatistics", {})
    return out

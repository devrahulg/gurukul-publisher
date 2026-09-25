"""Google side: OAuth refresh, Drive download, YouTube upload and channel stats.

Uses only the official REST endpoints (no paid services).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

from .common import ApiError, http, iso_utc, secret

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"
DRIVE = "https://www.googleapis.com/drive/v3"

# YouTube category 27 = Education
DEFAULT_CATEGORY = "27"
# Anything closer than this to "now" is published immediately instead of scheduled.
MIN_SCHEDULE_AHEAD = dt.timedelta(minutes=15)


def access_token(refresh_env: str) -> str:
    resp = http("POST", TOKEN_URL, what="Google token refresh", data={
        "client_id": secret("GOOGLE_CLIENT_ID"),
        "client_secret": secret("GOOGLE_CLIENT_SECRET"),
        "refresh_token": secret(refresh_env),
        "grant_type": "refresh_token",
    })
    token = resp.json().get("access_token")
    if not token:
        raise ApiError("Google token refresh returned no access token")
    return token


def _looks_like_mp4(path: pathlib.Path) -> bool:
    if not path.exists() or path.stat().st_size < 10_000:
        return False
    with path.open("rb") as fh:
        head = fh.read(12)
    return head[4:8] == b"ftyp"


def drive_download(file_id: str, dest: pathlib.Path, token: str | None = None) -> pathlib.Path:
    """Download a Drive file. Tries the Drive API (with the channel's token),
    then the public link (works when the file is shared 'anyone with the link')."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    attempts = []
    if token:
        attempts.append((f"{DRIVE}/files/{file_id}", {"alt": "media", "supportsAllDrives": "true"},
                         {"Authorization": f"Bearer {token}"}))
    attempts.append(("https://drive.usercontent.google.com/download",
                     {"id": file_id, "export": "download", "confirm": "t"}, {}))
    errors = []
    for url, params, headers in attempts:
        try:
            resp = http("GET", url, params=params, headers=headers, stream=True, what="Drive download")
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
            if _looks_like_mp4(dest):
                return dest
            errors.append("downloaded file is not an MP4 (check sharing on the Drive file)")
        except ApiError as exc:
            errors.append(str(exc))
    raise ApiError(f"Could not download Drive file {file_id}: " + " | ".join(errors))


def build_video_resource(post: dict, now: dt.datetime) -> dict:
    yt = post.get("youtube", {})
    title = (yt.get("title") or post.get("title") or "").strip()
    if not title:
        raise ApiError("YouTube post has no title")
    if len(title) > 100:
        title = title[:97].rstrip() + "..."
    publish_at = post["_publish_at"]
    status: dict = {
        "selfDeclaredMadeForKids": bool(yt.get("made_for_kids", False)),
        "embeddable": True,
        "license": "youtube",
    }
    if publish_at - now > MIN_SCHEDULE_AHEAD:
        status["privacyStatus"] = "private"
        status["publishAt"] = iso_utc(publish_at)
    else:
        status["privacyStatus"] = yt.get("privacy", "public")
    snippet = {
        "title": title,
        "description": (yt.get("description") or post.get("caption") or "")[:4900],
        "tags": [t.lstrip("#") for t in yt.get("tags", [])][:30],
        "categoryId": str(yt.get("category_id", DEFAULT_CATEGORY)),
    }
    lang = post.get("language")
    if lang:
        snippet["defaultLanguage"] = lang
        snippet["defaultAudioLanguage"] = lang
    return {"snippet": snippet, "status": status}


def youtube_upload(token: str, video: pathlib.Path, resource: dict) -> dict:
    size = video.stat().st_size
    init = http("POST", UPLOAD_URL, what="YouTube upload start",
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json; charset=UTF-8",
                         "X-Upload-Content-Type": "video/mp4",
                         "X-Upload-Content-Length": str(size)},
                data=json.dumps(resource))
    session = init.headers.get("Location")
    if not session:
        raise ApiError("YouTube did not return an upload session")
    with video.open("rb") as fh:
        resp = http("PUT", session, what="YouTube upload", ok=(200, 201), retries=2,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                             "Content-Length": str(size)},
                    data=fh, timeout=900)
    return resp.json()


def channel_stats(token: str) -> dict:
    resp = http("GET", f"{API}/channels", what="YouTube channel stats",
                params={"part": "snippet,statistics", "mine": "true"},
                headers={"Authorization": f"Bearer {token}"})
    items = resp.json().get("items", [])
    if not items:
        raise ApiError("No YouTube channel found for this token")
    ch = items[0]
    st = ch.get("statistics", {})
    return {
        "channel_id": ch.get("id"),
        "title": ch.get("snippet", {}).get("title"),
        "subscribers": int(st.get("subscriberCount", 0)) if not st.get("hiddenSubscriberCount") else None,
        "views": int(st.get("viewCount", 0)),
        "videos": int(st.get("videoCount", 0)),
    }


def env_present(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())

"""Instagram side (Instagram API with Instagram Login): Reels publishing,
account stats and long-lived token refresh. Official endpoints only."""
from __future__ import annotations

import time

from .common import ApiError, http, settings

HOST = "https://graph.instagram.com"


def _base() -> str:
    return f"{HOST}/{settings()['ig_api_version']}"


def whoami(token: str) -> dict:
    fields = "user_id,username,account_type,followers_count,media_count"
    try:
        resp = http("GET", f"{_base()}/me", what="Instagram profile",
                    params={"fields": fields, "access_token": token})
    except ApiError:
        # Some fields are not available on every account type; retry with the basics.
        resp = http("GET", f"{_base()}/me", what="Instagram profile",
                    params={"fields": "user_id,username", "access_token": token})
    data = resp.json()
    if not data.get("user_id") and data.get("id"):
        data["user_id"] = data["id"]
    return data


def create_reel_container(token: str, ig_user_id: str, video_url: str, caption: str,
                          share_to_feed: bool = True) -> str:
    resp = http("POST", f"{_base()}/{ig_user_id}/media", what="Instagram create container",
                data={"media_type": "REELS", "video_url": video_url, "caption": caption[:2200],
                      "share_to_feed": "true" if share_to_feed else "false",
                      "access_token": token})
    cid = resp.json().get("id")
    if not cid:
        raise ApiError("Instagram returned no container id")
    return cid


def wait_until_ready(token: str, container_id: str, timeout_s: int = 420, every_s: int = 20) -> None:
    deadline = time.time() + timeout_s
    last = "UNKNOWN"
    while time.time() < deadline:
        resp = http("GET", f"{_base()}/{container_id}", what="Instagram container status",
                    params={"fields": "status_code,status", "access_token": token})
        body = resp.json()
        last = body.get("status_code", "UNKNOWN")
        if last == "FINISHED":
            return
        if last in ("ERROR", "EXPIRED"):
            raise ApiError(f"Instagram could not process the video: {body.get('status', last)}")
        time.sleep(every_s)
    raise ApiError(f"Instagram video still processing after {timeout_s}s (last status {last})",
                   retryable=True)


def publish(token: str, ig_user_id: str, container_id: str) -> str:
    resp = http("POST", f"{_base()}/{ig_user_id}/media_publish", what="Instagram publish",
                data={"creation_id": container_id, "access_token": token})
    mid = resp.json().get("id")
    if not mid:
        raise ApiError("Instagram publish returned no media id")
    return mid


def permalink(token: str, media_id: str) -> str | None:
    try:
        resp = http("GET", f"{_base()}/{media_id}", what="Instagram permalink",
                    params={"fields": "permalink", "access_token": token})
        return resp.json().get("permalink")
    except ApiError:
        return None


def publishing_quota(token: str, ig_user_id: str) -> dict | None:
    try:
        resp = http("GET", f"{_base()}/{ig_user_id}/content_publishing_limit", what="Instagram quota",
                    params={"fields": "quota_usage,config", "access_token": token})
        data = resp.json().get("data", [])
        return data[0] if data else None
    except ApiError:
        return None


def refresh_long_lived(token: str) -> tuple[str, int]:
    resp = http("GET", f"{HOST}/refresh_access_token", what="Instagram token refresh",
                params={"grant_type": "ig_refresh_token", "access_token": token})
    body = resp.json()
    new = body.get("access_token")
    if not new:
        raise ApiError("Instagram token refresh returned no token")
    return new, int(body.get("expires_in", 0))

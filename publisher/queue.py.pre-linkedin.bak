"""The post queue: one JSON file per post in queue/.

Status flow
  queued -> uploading -> scheduled | published      (YouTube)
  queued -> publishing -> published                 (Instagram)
  any open post -> failed (after max_attempts) | missed (too late) | cancelled (by you)
"""
from __future__ import annotations

import datetime as dt
import pathlib

from .common import ROOT, iso_utc, load_json, now_utc, parse_ts, save_json

QDIR = ROOT / "queue"
OPEN = {"queued"}
REQUIRED = ("id", "account", "publish_at", "video")


class Post(dict):
    path: pathlib.Path

    @property
    def publish_at(self) -> dt.datetime:
        return parse_ts(self["publish_at"])

    def note(self, event: str) -> None:
        hist = self.setdefault("history", [])
        hist.append({"at": iso_utc(now_utc()), "event": event})
        del hist[:-8]  # keep the file small

    def save(self) -> None:
        self["updated_at"] = iso_utc(now_utc())
        data = {k: v for k, v in self.items() if not k.startswith("_")}
        save_json(self.path, data)


def load_all() -> list[Post]:
    posts = []
    for path in sorted(QDIR.glob("*.json")):
        raw = load_json(path, {})
        if not isinstance(raw, dict):
            continue
        post = Post(raw)
        post.path = path
        post.setdefault("status", "queued")
        post.setdefault("attempts", 0)
        posts.append(post)
    return posts


def validate(post: dict, accounts: dict) -> list[str]:
    problems = [f"missing {k}" for k in REQUIRED if not post.get(k)]
    acct = accounts.get(post.get("account", ""))
    if not acct:
        problems.append(f"unknown account {post.get('account')!r}")
    else:
        if acct["platform"] == "youtube" and not (post.get("youtube", {}).get("title") or post.get("title")):
            problems.append("YouTube post needs youtube.title")
        if acct["platform"] == "instagram" and not post.get("caption"):
            problems.append("Instagram post needs caption")
    video = post.get("video") or {}
    if not (video.get("drive_file_id") or video.get("url")):
        problems.append("video needs drive_file_id or url")
    try:
        parse_ts(post.get("publish_at", ""))
    except Exception:  # noqa: BLE001
        problems.append("publish_at is not an ISO date-time")
    return problems

"""Shared helpers: paths, time, JSON files, HTTP with retries, settings."""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import time
from typing import Any

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UTC = dt.timezone.utc


class ApiError(RuntimeError):
    """An API call failed. The message never contains tokens."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


# ---------- time ----------
def now_utc() -> dt.datetime:
    override = os.environ.get("PUBLISHER_NOW")  # tests / dry runs only
    if override:
        return parse_ts(override)
    return dt.datetime.now(UTC)


def parse_ts(value: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=IST)
    return d


def iso_utc(d: dt.datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- files ----------
def load_json(path: pathlib.Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    tmp.replace(path)


def settings() -> dict:
    s = load_json(ROOT / "config" / "settings.json", {}) or {}
    s.setdefault("due_window_minutes", 20)        # publish Instagram posts up to this early
    s.setdefault("max_late_minutes", 180)         # later than this -> "missed", never posted
    s.setdefault("youtube_lead_hours", 36)        # upload YouTube this far ahead with publishAt
    s.setdefault("max_attempts", 3)
    s.setdefault("max_youtube_uploads_per_run", 6)
    s.setdefault("ig_api_version", "v25.0")
    return s


def accounts() -> dict:
    return load_json(ROOT / "config" / "accounts.json", {}) or {}


def secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ApiError(f"Secret {name} is not set in the GitHub repository secrets.")
    return value


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(IST).strftime('%H:%M:%S')} IST] {msg}", flush=True)


# ---------- http ----------
def http(method: str, url: str, *, ok: tuple = (200, 201), retries: int = 3,
         backoff: float = 4.0, what: str = "", **kwargs) -> requests.Response:
    """HTTP call with retries on 429/5xx/network errors.

    Error messages include the API's error body (which never echoes tokens)
    but never the full URL, because Instagram tokens travel as query params.
    """
    kwargs.setdefault("timeout", 120)
    label = what or url.split("?")[0]
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:  # network trouble
            last = ApiError(f"{label}: network error ({type(exc).__name__})", retryable=True)
        else:
            if resp.status_code in ok:
                return resp
            body = resp.text[:600].replace("\n", " ")
            retryable = resp.status_code == 429 or resp.status_code >= 500
            last = ApiError(f"{label}: HTTP {resp.status_code}: {body}", resp.status_code, retryable)
            if not retryable:
                raise last
        if attempt < retries:
            time.sleep(backoff * attempt)
    assert last is not None
    raise last


def github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")

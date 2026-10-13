"""Reel scout, API mode (runs by itself on GitHub Actions).

Per series:
1. Apify hashtag scraper: recent reels for the series' hashtags.
2. The creators behind the best of those (plus the series' fixed accounts): their recent
   reels, so each reel can be compared with what that creator normally gets.
3. Outliers (see core.find_outliers).
4. Gemini watches the top outliers and breaks down the hook and structure.
5. research/reports/<date>-<series>.md and research/data/<date>-<series>.json.

Each run spends up to max_results_per_run Apify results, scouting the series that were
scouted longest ago first, so every series gets its turn inside the free $5/month.
research/last_run.json is always written, even on failure.

Secrets: APIFY_TOKEN, GEMINI_API_KEY (sent in headers only, never in URLs or files).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import sys
import time
import traceback
from typing import Any

import requests

from scout import core
from scout.core import log

APIFY = "https://api.apify.com/v2"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
STATE = core.OUT / "series_state.json"


# ------------------------------------------------------------------ Apify
def apify_run(actor: str, payload: dict, token: str, timeout_s: int = 900) -> tuple[list[dict], float]:
    auth = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{APIFY}/acts/{actor}/runs", headers=auth, json=payload, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Apify {actor} start: HTTP {r.status_code}: {r.text[:300]}")
    run = r.json()["data"]
    deadline = time.time() + timeout_s
    while run["status"] in ("READY", "RUNNING"):
        if time.time() > deadline:
            raise RuntimeError(f"Apify {actor} still running after {timeout_s}s")
        r = requests.get(f"{APIFY}/actor-runs/{run['id']}", headers=auth, params={"waitForFinish": 60}, timeout=90)
        if r.status_code >= 300:
            raise RuntimeError(f"Apify {actor} status: HTTP {r.status_code}")
        run = r.json()["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify {actor} ended {run['status']}")
    r = requests.get(f"{APIFY}/datasets/{run['defaultDatasetId']}/items", headers=auth,
                     params={"clean": "true", "format": "json"}, timeout=120)
    if r.status_code >= 300:
        raise RuntimeError(f"Apify {actor} dataset: HTTP {r.status_code}")
    items = r.json()
    cost = float(run.get("usageTotalUsd") or 0)
    log(f"Apify {actor}: {len(items)} items, ${cost:.3f}")
    return items, cost


def slim(item: dict) -> dict:
    return {
        "url": item.get("url"),
        "owner": (item.get("ownerUsername") or "").lower(),
        "views": int(item.get("videoPlayCount") or item.get("videoViewCount") or 0),
        "likes": item.get("likesCount") or 0,
        "comments": item.get("commentsCount") or 0,
        "duration_s": round(item.get("videoDuration") or 0, 1),
        "posted": core.parse_date(item.get("timestamp")),
        "caption": (item.get("caption") or "")[:600],
        "video_url": item.get("videoUrl"),
    }


def is_reel(item: dict) -> bool:
    return bool(item.get("videoUrl") or item.get("type") == "Video" or item.get("productType") == "clips")


def pick_creators(tag_reels: list[dict], cfg: dict) -> list[str]:
    exclude = {a.lower() for a in cfg["exclude"]}
    best: dict[str, int] = {}
    for r in tag_reels:
        o = r["owner"]
        if o and o not in exclude:
            best[o] = max(best.get(o, 0), r["views"])
    ranked = sorted(best, key=best.get, reverse=True)
    forced = [a.lower().lstrip("@") for a in cfg["accounts"]]
    out = forced + [o for o in ranked if o not in forced]
    return out[: max(cfg["creators_to_profile"], len(forced))]


def planned_results(cfg: dict) -> int:
    creators = max(cfg["creators_to_profile"], len(cfg["accounts"]))
    return len(cfg["hashtags"]) * cfg["per_hashtag"] + creators * cfg["reels_per_creator"]


# ------------------------------------------------------------------ Gemini
def gemini_call(parts: list[dict], key: str, models: list[str], json_out: bool) -> str:
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0.4}}
    if json_out:
        body["generationConfig"]["responseMimeType"] = "application/json"
    last = ""
    for model in models:
        for attempt in range(3):
            r = requests.post(f"{GEMINI}/models/{model}:generateContent", headers={"x-goog-api-key": key},
                              json=body, timeout=300)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{model}: HTTP {r.status_code}"
                time.sleep(20 * (attempt + 1))
                continue
            if r.status_code >= 300:
                last = f"{model}: HTTP {r.status_code}: {r.text[:200]}"
                break
            cands = r.json().get("candidates") or []
            text = "".join(p.get("text", "") for c in cands for p in c.get("content", {}).get("parts", []))
            if text:
                return text
            last = f"{model}: empty response"
            break
    raise RuntimeError(f"Gemini failed ({last})")


def download_video(url: str, max_mb: int) -> bytes | None:
    try:
        r = requests.get(url, timeout=120, stream=True)
        if r.status_code != 200:
            return None
        data = b""
        for chunk in r.iter_content(1 << 16):
            data += chunk
            if len(data) > max_mb * 1024 * 1024:
                return None
        return data
    except requests.RequestException:
        return None


def breakdown(reel: dict, cfg: dict, key: str) -> dict:
    video = download_video(reel["video_url"], cfg["max_video_mb"]) if reel.get("video_url") else None
    extra = "" if video else "(The video could not be downloaded: work from the caption only and say so in why_it_stops_the_scroll.)"
    parts: list[dict] = []
    if video:
        parts.append({"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(video).decode()}})
    parts.append({"text": core.breakdown_prompt(reel, cfg, extra)})
    text = gemini_call(parts, key, cfg["gemini_models"], json_out=True)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"raw": text[:2000]}
    data["_watched_video"] = bool(video)
    return data


# ------------------------------------------------------------------ one series
def scout_series(cfg: dict, apify_token: str, gemini_key: str, now: dt.datetime) -> dict:
    date = now.astimezone(core.IST).strftime("%Y-%m-%d")
    usd = 0.0
    tag_items, c = apify_run("apify~instagram-hashtag-scraper", {
        "hashtags": cfg["hashtags"], "resultsType": "reels", "resultsLimit": cfg["per_hashtag"]}, apify_token)
    usd += c
    tag_reels = [slim(i) for i in tag_items if is_reel(i)]
    creators = pick_creators(tag_reels, cfg)
    log(f"[{cfg['key']}] {len(tag_reels)} hashtag reels, profiling: {', '.join(creators)}")

    creator_items: list[dict] = []
    if creators:
        creator_items, c = apify_run("apify~instagram-reel-scraper", {
            "username": creators, "resultsLimit": cfg["reels_per_creator"],
            "onlyPostsNewerThan": f"{cfg['baseline_days']} days", "skipPinnedPosts": True}, apify_token)
        usd += c
    by_owner: dict[str, list[dict]] = {}
    for i in creator_items:
        r = slim(i)
        if r["owner"]:
            by_owner.setdefault(r["owner"], []).append(r)
    outliers = core.find_outliers(by_owner, cfg, now)
    log(f"[{cfg['key']}] {len(outliers)} outliers")

    top = outliers[: cfg["analyse_top"]]
    for n, r in enumerate(top, 1):
        try:
            r["breakdown"] = breakdown(r, cfg, gemini_key)
            log(f"[{cfg['key']}] breakdown {n}/{len(top)} @{r['owner']} ok")
        except Exception as exc:
            r["breakdown_error"] = str(exc)[:300]
            log(f"[{cfg['key']}] breakdown {n}/{len(top)} @{r['owner']} failed: {exc}")
        time.sleep(6)  # stay inside Gemini free-tier rate limits

    patterns = ""
    prompt = core.pattern_prompt(top, cfg)
    if prompt:
        try:
            patterns = gemini_call([{"text": prompt}], gemini_key, cfg["gemini_models"], json_out=False)
        except Exception as exc:
            log(f"[{cfg['key']}] pattern summary failed: {exc}")

    stats = {"discovered": len(tag_reels), "creators_profiled": len(by_owner),
             "creator_reels": sum(len(v) for v in by_owner.values()), "apify_usd": usd,
             "results": len(tag_items) + len(creator_items)}
    for o in outliers:
        o.pop("video_url", None)  # signed CDN links expire; don't keep them
    report = core.write_report(date, cfg, stats, outliers, patterns, mode="api")
    core.save(core.OUT / "data" / f"{date}-{cfg['key']}.json", {"stats": stats, "outliers": outliers})
    return {"report": str(report.relative_to(core.ROOT)), "outliers": len(outliers),
            "analysed": sum(1 for r in top if r.get("breakdown")), **stats}


def choose_series(cfg: dict, state: dict, only: list[str] | None) -> list[str]:
    keys = only or sorted(cfg["series"], key=lambda k: state.get(k, ""))  # never-scouted first
    picked, budget = [], cfg["max_results_per_run"]
    for k in keys:
        need = planned_results(core.series_cfg(cfg, k))
        if picked and need > budget:
            continue
        picked.append(k)
        budget -= need
    return picked


def main(argv: list[str]) -> int:
    now = dt.datetime.now(core.UTC)
    status: dict[str, Any] = {"ran_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "api", "series": {}}
    ok = True
    try:
        cfg = core.load_config()
        req = core.OUT / "request.json"
        over = json.loads(req.read_text(encoding="utf-8")) if req.exists() else {}
        only = over.pop("series", None) if isinstance(over, dict) else None
        if isinstance(only, str):
            only = [only]
        cfg.update({k: v for k, v in over.items() if k in cfg and k != "series"})
        apify_token = os.environ.get("APIFY_TOKEN", "").strip()
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        missing = [n for n, v in (("APIFY_TOKEN", apify_token), ("GEMINI_API_KEY", gemini_key)) if not v]
        if missing:
            raise RuntimeError("secret(s) not set: " + ", ".join(missing))
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        for key in choose_series(cfg, state, only):
            try:
                status["series"][key] = {"ok": True, **scout_series(core.series_cfg(cfg, key), apify_token, gemini_key, now)}
                state[key] = status["ran_at"]
                core.save(STATE, state)
            except Exception as exc:
                ok = False
                status["series"][key] = {"ok": False, "error": str(exc)[:500]}
                log(f"[{key}] FAILED: {exc}")
    except Exception as exc:
        ok = False
        status.update(error=str(exc)[:500], trace=traceback.format_exc()[-1500:])
        log(f"FAILED: {exc}")
    status["ok"] = ok
    core.save(core.OUT / "last_run.json", status)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

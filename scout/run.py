"""Reel scout.

1. Apify hashtag scraper: recent reels for each niche hashtag.
2. The creators behind the best of those (plus config accounts): their last N reels,
   so every reel can be compared with what that creator normally gets.
3. Outlier = views / that creator's median views. 3x+ means the idea or hook
   did the work, not the account size.
4. Gemini watches the top outliers and breaks down the hook and structure.
5. Writes research/reports/<date>.md, research/data/<date>.json and
   research/last_run.json (status, always written, even on failure).

Secrets: APIFY_TOKEN, GEMINI_API_KEY. Neither is ever written to a file or log.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import pathlib
import statistics
import sys
import time
import traceback
from typing import Any

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "scout.json"
OUT = ROOT / "research"
APIFY = "https://api.apify.com/v2"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def log(msg: str) -> None:
    print(f"[scout] {msg}", flush=True)


# ------------------------------------------------------------------ Apify
def apify_run(actor: str, payload: dict, token: str, timeout_s: int = 900) -> tuple[list[dict], float]:
    """Start an actor run, wait for it, return (dataset items, cost in USD).
    The token goes in a header, never the URL, so it can't leak into an error message."""
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
    r = requests.get(f"{APIFY}/datasets/{run['defaultDatasetId']}/items",
                     headers=auth, params={"clean": "true", "format": "json"}, timeout=120)
    if r.status_code >= 300:
        raise RuntimeError(f"Apify {actor} dataset: HTTP {r.status_code}")
    items = r.json()
    cost = (run.get("usageTotalUsd") or 0)
    log(f"Apify {actor}: {len(items)} items, ${cost:.3f}")
    return items, float(cost)


# ------------------------------------------------------------------ data
def views(item: dict) -> int:
    return int(item.get("videoPlayCount") or item.get("videoViewCount") or 0)


def ts(item: dict) -> dt.datetime | None:
    raw = item.get("timestamp")
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_reel(item: dict) -> bool:
    return bool(item.get("videoUrl") or item.get("type") == "Video" or item.get("productType") == "clips")


def slim(item: dict) -> dict:
    t = ts(item)
    return {
        "url": item.get("url"),
        "owner": (item.get("ownerUsername") or "").lower(),
        "views": views(item),
        "likes": item.get("likesCount") or 0,
        "comments": item.get("commentsCount") or 0,
        "duration_s": round(item.get("videoDuration") or 0, 1),
        "posted": t.isoformat() if t else None,
        "caption": (item.get("caption") or "")[:600],
        "video_url": item.get("videoUrl"),
        "hashtags": item.get("hashtags") or [],
    }


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


def find_outliers(creator_reels: dict[str, list[dict]], cfg: dict, now: dt.datetime) -> list[dict]:
    since = now - dt.timedelta(days=cfg["lookback_days"])
    out = []
    for owner, reels in creator_reels.items():
        counted = [r for r in reels if r["views"] > 0]
        if len(counted) < cfg["min_reels_for_baseline"]:
            continue
        med = statistics.median(r["views"] for r in counted)
        if med <= 0:
            continue
        for r in counted:
            posted = dt.datetime.fromisoformat(r["posted"]) if r["posted"] else None
            if posted and posted < since:
                continue
            mult = r["views"] / med
            if mult >= cfg["outlier_multiplier"] and r["views"] >= cfg["min_views"]:
                out.append({**r, "creator_median": int(med), "multiplier": round(mult, 1),
                            "creator_reels_sampled": len(counted)})
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: (r["multiplier"], r["views"]), reverse=True):
        if r["url"] not in seen:
            seen.add(r["url"])
            uniq.append(r)
    return uniq


# ------------------------------------------------------------------ Gemini
BREAKDOWN_PROMPT = """You are a short-form video strategist. Watch this Instagram reel closely,
especially the first 3 seconds, and break down why it outperformed. The creator's usual
reel gets {median:,} views; this one got {views:,} ({mult}x their normal).
Caption: {caption!r}

Our channel: {series}

Return ONLY JSON with these keys:
{{
 "hook_spoken": "exact words said in the first ~3 seconds (original language), or '' if none",
 "hook_on_screen_text": "exact on-screen text in the first ~3 seconds, or ''",
 "hook_visual": "what the viewer SEES in the first 2 seconds",
 "hook_type": "one of: curiosity-gap, bold-claim, numbered-list, before-after, pain-point, contrarian, secret-hack, live-demo-result, question, shock-stat, other",
 "why_it_stops_the_scroll": "1-2 sentences",
 "format": "talking head / screen recording / AI avatar / text-on-video / mixed ...",
 "beats": [{{"t": "0-3s", "what": "..."}}],
 "retention_tricks": ["pattern interrupts, open loops, on-screen counters, cuts every Ns, etc."],
 "cta": "the call to action, or ''",
 "pace": "cuts per 10s estimate and energy level",
 "language": "English / Hindi / Hinglish / other",
 "steal_this": "the reusable principle in one line",
 "kiro_reel_idea": {{"hook_line": "our first line, max 12 words", "on_screen_text": "...", "outline": "3 beats"}},
 "claude_reel_idea": {{"hook_line": "...", "on_screen_text": "...", "outline": "3 beats"}}
}}"""

PATTERN_PROMPT = """Below are breakdowns of the top outlier reels in the AI-tools niche on Instagram
this week. Our channel: {series}

{breakdowns}

Write a short markdown section (no top-level heading) with:
1. "### What's working this week" - 4-6 bullets of concrete patterns (hook types, formats, length, pace, on-screen text style), each citing which reels (by number) show it.
2. "### 10 hook templates for our buzz reels" - a numbered list of fill-in-the-blank hook lines (max 12 words each) we can reuse for Kiro and Claude reels, each tagged [Kiro], [Claude] or [Both]. Keep them natural English; no forced slang.
3. "### Avoid" - 2-3 bullets of things that did NOT seem to matter or would not fit us.
Be specific and brief."""


def gemini_call(parts: list[dict], key: str, models: list[str], json_out: bool) -> str:
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}],
                            "generationConfig": {"temperature": 0.4}}
    if json_out:
        body["generationConfig"]["responseMimeType"] = "application/json"
    last = ""
    for model in models:
        for attempt in range(3):
            r = requests.post(f"{GEMINI}/models/{model}:generateContent",
                              headers={"x-goog-api-key": key}, json=body, timeout=300)
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
    prompt = BREAKDOWN_PROMPT.format(median=reel["creator_median"], views=reel["views"],
                                     mult=reel["multiplier"], caption=reel["caption"][:500],
                                     series=cfg["our_series"])
    parts: list[dict] = []
    video = download_video(reel["video_url"], cfg["max_video_mb"]) if reel.get("video_url") else None
    if video:
        parts.append({"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(video).decode()}})
    else:
        prompt += "\n\n(The video could not be downloaded: work from the caption only and say so in why_it_stops_the_scroll.)"
    parts.append({"text": prompt})
    text = gemini_call(parts, key, cfg["gemini_models"], json_out=True)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"raw": text[:2000]}
    data["_watched_video"] = bool(video)
    return data


# ------------------------------------------------------------------ report
def fmt_n(n: int) -> str:
    return f"{n/1_000_000:.1f}M" if n >= 1_000_000 else f"{n/1000:.0f}K" if n >= 1000 else str(n)


def write_report(date: str, stats: dict, outliers: list[dict], patterns: str) -> pathlib.Path:
    L = [f"# Reel scout: {date}", "",
         f"Scanned {stats['hashtag_reels']} reels from {len(stats['hashtags'])} hashtags, "
         f"profiled {stats['creators_profiled']} creators ({stats['creator_reels']} reels). "
         f"Found {len(outliers)} outliers (≥{stats['multiplier']}x the creator's median, "
         f"≥{fmt_n(stats['min_views'])} views, last {stats['lookback_days']} days). "
         f"Apify cost this run: ${stats['apify_usd']:.2f}.", ""]
    if patterns:
        L += ["## Patterns", "", patterns.strip(), ""]
    if outliers:
        L += ["## Top outliers", "", "| # | Creator | Views | x normal | Length | Hook type | Link |",
              "|---|---|---|---|---|---|---|"]
        for i, r in enumerate(outliers, 1):
            b = r.get("breakdown") or {}
            L.append(f"| {i} | @{r['owner']} | {fmt_n(r['views'])} | {r['multiplier']}x | "
                     f"{r['duration_s']:.0f}s | {b.get('hook_type', '–')} | [open]({r['url']}) |")
        L.append("")
    analysed = [r for r in outliers if r.get("breakdown")]
    if analysed:
        L += ["## Breakdowns", ""]
    for i, r in enumerate(outliers, 1):
        b = r.get("breakdown")
        if not b:
            continue
        L += [f"### {i}. @{r['owner']}: {fmt_n(r['views'])} views ({r['multiplier']}x)", "", f"[Watch]({r['url']})"
              + ("" if b.get("_watched_video") else " · *caption only, video not downloadable*"), ""]
        if "raw" in b:
            L += [b["raw"], ""]
            continue
        rows = [("Hook (spoken)", b.get("hook_spoken")), ("Hook (on screen)", b.get("hook_on_screen_text")),
                ("Hook (visual)", b.get("hook_visual")), ("Why it stops the scroll", b.get("why_it_stops_the_scroll")),
                ("Format", b.get("format")), ("Pace", b.get("pace")), ("Language", b.get("language")),
                ("CTA", b.get("cta")), ("Steal this", b.get("steal_this"))]
        L += [f"- **{k}:** {v}" for k, v in rows if v]
        if b.get("beats"):
            L.append("- **Beats:** " + " → ".join(f"{x.get('t', '')} {x.get('what', '')}" for x in b["beats"]))
        if b.get("retention_tricks"):
            L.append("- **Retention:** " + "; ".join(b["retention_tricks"]))
        for label, key in (("Kiro version", "kiro_reel_idea"), ("Claude version", "claude_reel_idea")):
            idea = b.get(key) or {}
            if idea.get("hook_line"):
                L.append(f"- **{label}:** \"{idea['hook_line']}\" · on screen: {idea.get('on_screen_text', '')} · {idea.get('outline', '')}")
        L.append("")
    path = OUT / "reports" / f"{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path


def save(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ main
def scout(cfg: dict, apify_token: str, gemini_key: str, now: dt.datetime) -> dict:
    date = now.astimezone(IST).strftime("%Y-%m-%d")
    usd = 0.0

    tag_items, c = apify_run("apify~instagram-hashtag-scraper", {
        "hashtags": cfg["hashtags"], "resultsType": "reels", "resultsLimit": cfg["per_hashtag"]}, apify_token)
    usd += c
    tag_reels = [slim(i) for i in tag_items if is_reel(i)]
    creators = pick_creators(tag_reels, cfg)
    log(f"{len(tag_reels)} hashtag reels, profiling {len(creators)} creators: {', '.join(creators)}")

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
    outliers = find_outliers(by_owner, cfg, now)
    log(f"{len(outliers)} outliers")

    top = outliers[: cfg["analyse_top"]]
    for n, r in enumerate(top, 1):
        try:
            r["breakdown"] = breakdown(r, cfg, gemini_key)
            log(f"breakdown {n}/{len(top)} @{r['owner']} ok")
        except Exception as exc:  # keep going; one bad video shouldn't sink the report
            r["breakdown_error"] = str(exc)[:300]
            log(f"breakdown {n}/{len(top)} @{r['owner']} failed: {exc}")
        time.sleep(6)  # stay inside Gemini free-tier rate limits

    patterns = ""
    good = [(i, r["breakdown"]) for i, r in enumerate(top, 1) if r.get("breakdown") and "raw" not in r["breakdown"]]
    if good:
        blob = "\n\n".join(f"Reel {i} (@{top[i-1]['owner']}, {top[i-1]['multiplier']}x, {top[i-1]['duration_s']:.0f}s): "
                           + json.dumps({k: v for k, v in b.items() if not k.startswith("_")}, ensure_ascii=False)
                           for i, b in good)
        try:
            patterns = gemini_call([{"text": PATTERN_PROMPT.format(series=cfg["our_series"], breakdowns=blob)}],
                                   gemini_key, cfg["gemini_models"], json_out=False)
        except Exception as exc:
            log(f"pattern summary failed: {exc}")

    stats = {"hashtags": cfg["hashtags"], "hashtag_reels": len(tag_reels), "creators_profiled": len(by_owner),
             "creator_reels": sum(len(v) for v in by_owner.values()), "multiplier": cfg["outlier_multiplier"],
             "min_views": cfg["min_views"], "lookback_days": cfg["lookback_days"], "apify_usd": usd}
    for o in outliers:
        o.pop("video_url", None)  # signed CDN links expire; don't keep them
    report = write_report(date, stats, outliers, patterns)
    save(OUT / "data" / f"{date}.json", {"stats": stats, "outliers": outliers,
                                         "creators": {k: len(v) for k, v in by_owner.items()}})
    return {"report": str(report.relative_to(ROOT)), "outliers": len(outliers),
            "analysed": sum(1 for r in top if r.get("breakdown")), **{k: stats[k] for k in ("hashtag_reels", "creators_profiled")}}


def main(argv: list[str]) -> int:
    now = dt.datetime.now(UTC)
    status: dict[str, Any] = {"ran_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        req = OUT / "request.json"
        if req.exists():  # one-off overrides, e.g. {"hashtags": [...], "analyse_top": 5}
            over = json.loads(req.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in over.items() if k in cfg})
        apify_token = os.environ.get("APIFY_TOKEN", "").strip()
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        missing = [n for n, v in (("APIFY_TOKEN", apify_token), ("GEMINI_API_KEY", gemini_key)) if not v]
        if missing:
            raise RuntimeError("secret(s) not set: " + ", ".join(missing))
        status.update(ok=True, **scout(cfg, apify_token, gemini_key, now))
    except Exception as exc:
        status.update(ok=False, error=str(exc)[:500], trace=traceback.format_exc()[-1500:])
        log(f"FAILED: {exc}")
    save(OUT / "last_run.json", status)
    return 0 if status.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

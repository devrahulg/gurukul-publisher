"""Shared by both scout modes: settings, outlier maths, breakdown prompt, report.

Outlier = a reel's views / the median views of that creator's recent reels.
3x+ means the idea or hook did the work, not the account size.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import statistics
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "scout.json"
OUT = ROOT / "research"
UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def log(msg: str) -> None:
    print(f"[scout] {msg}", flush=True)


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def series_cfg(cfg: dict, key: str) -> dict:
    """Global settings merged with one series' settings."""
    if key not in cfg["series"]:
        raise SystemExit(f"unknown series '{key}'. Known: {', '.join(cfg['series'])}")
    merged = {k: v for k, v in cfg.items() if k != "series"}
    merged.update(cfg["series"][key])
    merged["key"] = key
    return merged


def save(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ numbers
_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "l": 100_000, "lakh": 100_000, "cr": 10_000_000}


def parse_count(value: Any) -> int:
    """'1.2M', '45.3K', '12,345', '3.4 lakh', 9876 -> int."""
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value or "").strip().lower().replace(",", "").replace("views", "").replace("plays", "").strip()
    if not s:
        return 0
    m = re.fullmatch(r"([\d.]+)\s*([a-z]*)", s)
    if not m:
        return 0
    num, suf = float(m.group(1)), m.group(2)
    return int(round(num * _SUFFIX.get(suf, 1)))


def parse_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.isoformat()


def find_outliers(creator_reels: dict[str, list[dict]], cfg: dict, now: dt.datetime) -> list[dict]:
    """creator_reels: owner -> [{url, views, posted (ISO or None), ...}]."""
    since = now - dt.timedelta(days=cfg["lookback_days"])
    exclude = {a.lower() for a in cfg.get("exclude", [])}
    out = []
    for owner, reels in creator_reels.items():
        if owner.lower() in exclude:
            continue
        counted = [r for r in reels if r.get("views", 0) > 0]
        if len(counted) < cfg["min_reels_for_baseline"]:
            continue
        med = statistics.median(r["views"] for r in counted)
        if med <= 0:
            continue
        for r in counted:
            posted = dt.datetime.fromisoformat(r["posted"]) if r.get("posted") else None
            if posted and posted < since:
                continue
            mult = r["views"] / med
            if mult >= cfg["outlier_multiplier"] and r["views"] >= cfg["min_views"]:
                out.append({**r, "owner": owner, "creator_median": int(med), "multiplier": round(mult, 1),
                            "creator_reels_sampled": len(counted)})
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: (r["multiplier"], r["views"]), reverse=True):
        if r["url"] not in seen:
            seen.add(r["url"])
            uniq.append(r)
    return uniq


# ------------------------------------------------------------------ prompts
BREAKDOWN_PROMPT = """You are a short-form video strategist. Study this Instagram reel closely,
especially the first 3 seconds, and work out why it outperformed. The creator's usual
reel gets {median:,} views; this one got {views:,} ({mult}x their normal).
Caption: {caption!r}

Our series: {about}

Return ONLY JSON with these keys:
{{
 "hook_spoken": "exact words said in the first ~3 seconds (original language), or '' if none",
 "hook_on_screen_text": "exact on-screen text in the first ~3 seconds, or ''",
 "hook_visual": "what the viewer SEES in the first 2 seconds",
 "hook_type": "one of: curiosity-gap, bold-claim, numbered-list, before-after, pain-point, contrarian, secret-hack, live-demo-result, question, shock-stat, relatable-moment, emotional-trigger, other",
 "why_it_stops_the_scroll": "1-2 sentences",
 "format": "talking head / screen recording / AI avatar / cartoon / text-on-video / mixed ...",
 "beats": [{{"t": "0-3s", "what": "..."}}],
 "retention_tricks": ["pattern interrupts, open loops, counters, cut rhythm, loop ending, etc."],
 "audio": "voice, music or trending sound, and how it is used",
 "cta": "the call to action, or ''",
 "pace": "cuts per 10s estimate and energy level",
 "language": "English / Hindi / Hinglish / other",
 "steal_this": "the reusable principle in one line",
 "our_version": "{idea}"
}}"""

PATTERN_PROMPT = """Below are breakdowns of the top outlier reels for this series. Our series: {about}

{breakdowns}

Write a short markdown section (no top-level heading) with:
1. "### What's working" - 4-6 bullets of concrete patterns (hook types, formats, length, pace, on-screen text, audio), each citing which reels (by number) show it.
2. "### 10 ready hooks for us" - a numbered list of fill-in-the-blank hooks that fit OUR series and its tone. Natural language, no forced slang.
3. "### Avoid" - 2-3 bullets of things that did not seem to matter or would not fit us.
Be specific and brief."""


def breakdown_prompt(reel: dict, cfg: dict, extra: str = "") -> str:
    p = BREAKDOWN_PROMPT.format(median=reel["creator_median"], views=reel["views"], mult=reel["multiplier"],
                                caption=(reel.get("caption") or "")[:500], about=cfg["about"], idea=cfg["idea"])
    return p + ("\n\n" + extra if extra else "")


def pattern_prompt(top: list[dict], cfg: dict) -> str | None:
    good = [(i, r) for i, r in enumerate(top, 1) if isinstance(r.get("breakdown"), dict) and "raw" not in r["breakdown"]]
    if not good:
        return None
    blob = "\n\n".join(f"Reel {i} (@{r['owner']}, {r['multiplier']}x, {r.get('duration_s') or 0:.0f}s): "
                       + json.dumps({k: v for k, v in r["breakdown"].items() if not k.startswith("_")}, ensure_ascii=False)
                       for i, r in good)
    return PATTERN_PROMPT.format(about=cfg["about"], breakdowns=blob)


# ------------------------------------------------------------------ report
def fmt_n(n: int) -> str:
    return f"{n/1_000_000:.1f}M" if n >= 1_000_000 else f"{n/1000:.0f}K" if n >= 1000 else str(n)


def _idea_text(v: Any) -> str:
    if isinstance(v, dict):
        return " · ".join(f"{k.replace('_', ' ')}: {x}" for k, x in v.items() if x)
    return str(v or "")


def write_report(date: str, cfg: dict, stats: dict, outliers: list[dict], patterns: str, mode: str) -> pathlib.Path:
    L = [f"# Reel scout: {cfg['name']}, {date}", "",
         f"*Mode: {mode}.* Scanned {stats.get('discovered', 0)} reels"
         + (f" from {len(cfg.get('hashtags', []))} hashtags" if mode == "api" else "")
         + f", profiled {stats.get('creators_profiled', 0)} creators ({stats.get('creator_reels', 0)} reels). "
         f"Found {len(outliers)} outliers (≥{cfg['outlier_multiplier']}x the creator's median, "
         f"≥{fmt_n(cfg['min_views'])} views, last {cfg['lookback_days']} days)."
         + (f" Apify cost: ${stats['apify_usd']:.2f}." if stats.get("apify_usd") else ""), ""]
    if patterns:
        L += ["## Patterns", "", patterns.strip(), ""]
    if outliers:
        L += ["## Top outliers", "", "| # | Creator | Views | x normal | Length | Hook type | Link |",
              "|---|---|---|---|---|---|---|"]
        for i, r in enumerate(outliers, 1):
            b = r.get("breakdown") if isinstance(r.get("breakdown"), dict) else {}
            dur = f"{r['duration_s']:.0f}s" if r.get("duration_s") else "–"
            L.append(f"| {i} | @{r['owner']} | {fmt_n(r['views'])} | {r['multiplier']}x | {dur} | "
                     f"{b.get('hook_type', '–')} | [open]({r['url']}) |")
        L.append("")
    if any(isinstance(r.get("breakdown"), dict) for r in outliers):
        L += ["## Breakdowns", ""]
    for i, r in enumerate(outliers, 1):
        b = r.get("breakdown")
        if not isinstance(b, dict):
            continue
        note = "" if b.get("_watched_video", True) else " · *caption only, video not available*"
        L += [f"### {i}. @{r['owner']}: {fmt_n(r['views'])} views ({r['multiplier']}x)", "", f"[Watch]({r['url']}){note}", ""]
        if "raw" in b:
            L += [str(b["raw"]), ""]
            continue
        rows = [("Hook (spoken)", b.get("hook_spoken")), ("Hook (on screen)", b.get("hook_on_screen_text")),
                ("Hook (visual)", b.get("hook_visual")), ("Why it stops the scroll", b.get("why_it_stops_the_scroll")),
                ("Format", b.get("format")), ("Audio", b.get("audio")), ("Pace", b.get("pace")),
                ("Language", b.get("language")), ("CTA", b.get("cta")), ("Steal this", b.get("steal_this"))]
        L += [f"- **{k}:** {v}" for k, v in rows if v]
        if b.get("beats"):
            L.append("- **Beats:** " + " → ".join(f"{x.get('t', '')} {x.get('what', '')}" for x in b["beats"] if isinstance(x, dict)))
        if b.get("retention_tricks"):
            L.append("- **Retention:** " + "; ".join(map(str, b["retention_tricks"])))
        if b.get("our_version"):
            L.append(f"- **Our version ({cfg['name']}):** {_idea_text(b['our_version'])}")
        L.append("")
    path = OUT / "reports" / f"{date}-{cfg['key']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path

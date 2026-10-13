"""Reel scout, no-API mode: no Apify, no Gemini, no keys.

Claude collects the numbers by browsing Instagram (a creator's Reels tab shows every reel's
view count), this tool finds the outliers, turns each outlier video into something Claude can
study (hook frames, a storyboard, cut rhythm, audio level, and a transcript from a local
open-source Whisper model), and Claude writes the breakdowns into the same report format
the API mode uses.

  python -m scout.local plan <series>
      What to look at for this series (hashtags, fixed creators, thresholds).
  python -m scout.local outliers <series> <collected.json>
      collected.json = {"discovered": 120,
                        "creators": {"someone": [{"url": "...", "views": "1.2M",
                                                  "posted": "2026-10-01", "caption": "..."}]}}
      -> research/local/<date>-<series>-outliers.json
  python -m scout.local pack <video.mp4> <out_dir> [--whisper small] [--lang hi]
      -> hook.jpg (first 3 s, 7 frames), story.jpg (whole reel), facts.json, transcript.txt
  python -m scout.local report <series> <outliers.json>
      outliers.json with a "breakdown" object filled in per reel -> research/reports/<date>-<series>.md
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys

from scout import core

LOCAL = core.OUT / "local"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _need_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found. Install it (Windows: winget install Gyan.FFmpeg).")


# ------------------------------------------------------------------ plan
def cmd_plan(series: str) -> int:
    cfg = core.series_cfg(core.load_config(), series)
    print(json.dumps({k: cfg[k] for k in ("name", "hashtags", "accounts", "exclude", "creators_to_profile",
                                         "reels_per_creator", "lookback_days", "outlier_multiplier",
                                         "min_views", "analyse_top", "about", "idea")}, indent=2, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ outliers
def cmd_outliers(series: str, collected_path: str, now: dt.datetime | None = None) -> pathlib.Path:
    cfg = core.series_cfg(core.load_config(), series)
    now = now or dt.datetime.now(core.UTC)
    raw = json.loads(pathlib.Path(collected_path).read_text(encoding="utf-8"))
    by_owner: dict[str, list[dict]] = {}
    for owner, reels in raw.get("creators", {}).items():
        owner = owner.lower().lstrip("@")
        by_owner[owner] = [{"url": r["url"], "views": core.parse_count(r.get("views")),
                            "posted": core.parse_date(r.get("posted")), "caption": (r.get("caption") or "")[:600],
                            "duration_s": r.get("duration_s")} for r in reels if r.get("url")]
    outliers = core.find_outliers(by_owner, cfg, now)
    date = now.astimezone(core.IST).strftime("%Y-%m-%d")
    out = LOCAL / f"{date}-{series}-outliers.json"
    core.save(out, {"series": series, "date": date,
                    "stats": {"discovered": raw.get("discovered", 0), "creators_profiled": len(by_owner),
                              "creator_reels": sum(len(v) for v in by_owner.values())},
                    "analyse": [r["url"] for r in outliers[: cfg["analyse_top"]]],
                    "outliers": outliers})
    core.log(f"{len(outliers)} outliers from {len(by_owner)} creators -> {out}")
    return out


# ------------------------------------------------------------------ pack
def _duration(video: str) -> float:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _has_audio(video: str) -> bool:
    r = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", video])
    return bool(r.stdout.strip())


def _cuts(video: str, threshold: float = 0.3) -> list[float]:
    r = _run(["ffmpeg", "-hide_banner", "-i", video, "-vf", f"select='gt(scene,{threshold})',showinfo",
              "-an", "-f", "null", "-"])
    return [round(float(t), 2) for t in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def _volume(video: str) -> dict:
    r = _run(["ffmpeg", "-hide_banner", "-i", video, "-af", "volumedetect", "-vn", "-f", "null", "-"])
    mean = re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr)
    peak = re.search(r"max_volume: (-?[\d.]+) dB", r.stderr)
    return {"mean_db": float(mean.group(1)) if mean else None, "peak_db": float(peak.group(1)) if peak else None}


def _sheet(video: str, times: list[float], out: pathlib.Path, cols: int, width: int = 270) -> None:
    """Grab frames at the given times and tile them into one labelled image."""
    tmp = out.parent / f".frames_{out.stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(times):
        label = f"{t:.1f}s"
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
              "-vf", f"scale={width}:-2,drawtext=text='{label}':x=8:y=8:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.6",
              str(tmp / f"{i:03d}.jpg")])
    frames = sorted(tmp.glob("*.jpg"))
    if not frames:
        shutil.rmtree(tmp, ignore_errors=True)
        return
    rows = -(-len(frames) // cols)
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-framerate", "1", "-i", str(tmp / "%03d.jpg"),
          "-vf", f"tile={cols}x{rows}:padding=4:color=white", "-frames:v", "1", str(out)])
    shutil.rmtree(tmp, ignore_errors=True)


def _transcribe(video: str, model: str, lang: str | None) -> dict | None:
    try:
        from faster_whisper import WhisperModel  # optional, pip install faster-whisper
    except ImportError:
        return None
    m = WhisperModel(model, device="cpu", compute_type="int8")
    segments, info = m.transcribe(video, language=lang, vad_filter=True)
    segs = [{"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()} for s in segments]
    return {"language": info.language, "segments": segs}


def cmd_pack(video: str, out_dir: str, whisper: str | None = None, lang: str | None = None) -> pathlib.Path:
    _need_ffmpeg()
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dur = _duration(video)
    hook_times = [t / 2 for t in range(7) if t / 2 < max(dur - 0.05, 0.1)]  # 0, 0.5 ... 3.0 s
    _sheet(video, hook_times, out / "hook.jpg", cols=4)
    n = 12 if dur > 12 else max(int(dur), 1)
    story_times = [round(dur * (i + 0.5) / n, 2) for i in range(n)]
    _sheet(video, story_times, out / "story.jpg", cols=4, width=220)
    cuts = _cuts(video)
    audio = _has_audio(video)
    facts = {"duration_s": round(dur, 1), "cuts": cuts, "cuts_per_10s": round(len(cuts) / dur * 10, 1) if dur else None,
             "first_cut_s": cuts[0] if cuts else None, "has_audio": audio, **(_volume(video) if audio else {})}
    if whisper and audio:
        tr = _transcribe(video, whisper, lang)
        if tr is None:
            facts["transcript"] = "faster-whisper not installed (pip install faster-whisper)"
        else:
            facts["transcript_language"] = tr["language"]
            (out / "transcript.txt").write_text(
                "\n".join(f"[{s['start']:>5.1f}-{s['end']:>5.1f}] {s['text']}" for s in tr["segments"]) + "\n",
                encoding="utf-8")
            facts["hook_spoken_0_3s"] = " ".join(s["text"] for s in tr["segments"] if s["start"] < 3.0)
    core.save(out / "facts.json", facts)
    core.log(f"packed {video}: {dur:.1f}s, {len(cuts)} cuts -> {out}")
    return out


# ------------------------------------------------------------------ report
def cmd_report(series: str, outliers_path: str) -> pathlib.Path:
    cfg = core.series_cfg(core.load_config(), series)
    data = json.loads(pathlib.Path(outliers_path).read_text(encoding="utf-8"))
    patterns = data.get("patterns", "")
    path = core.write_report(data["date"], cfg, data.get("stats", {}), data["outliers"], patterns,
                             mode="no-API (Claude browsing + local video analysis)")
    core.log(f"report -> {path}")
    return path


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    opts = {}
    while "--whisper" in args or "--lang" in args:
        for flag in ("--whisper", "--lang"):
            if flag in args:
                i = args.index(flag)
                opts[flag[2:]] = args[i + 1]
                del args[i:i + 2]
    if cmd == "plan":
        return cmd_plan(args[0])
    if cmd == "outliers":
        cmd_outliers(args[0], args[1])
        return 0
    if cmd == "pack":
        cmd_pack(args[0], args[1], opts.get("whisper"), opts.get("lang"))
        return 0
    if cmd == "report":
        cmd_report(args[0], args[1])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

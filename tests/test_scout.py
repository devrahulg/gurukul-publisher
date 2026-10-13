"""Offline tests of the reel scout. Apify, Gemini and video downloads are faked."""
import datetime as dt
import json
import shutil
import subprocess

import pytest

import scout.core as core
import scout.local as local
import scout.run as sr

NOW = dt.datetime(2026, 10, 13, 6, 0, tzinfo=dt.timezone.utc)


def reel(owner, views, days_ago, n):
    t = (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"url": f"https://www.instagram.com/reel/{owner}{n}/", "ownerUsername": owner, "videoPlayCount": views,
            "likesCount": views // 50, "commentsCount": 3, "videoDuration": 24.5, "timestamp": t,
            "caption": f"{owner} reel {n}", "videoUrl": f"https://cdn.example/{owner}{n}.mp4", "type": "Video"}


def fake_apify(actor, payload, token, timeout_s=900):
    assert token == "tok"
    if "hashtag" in actor:
        return [reel("alpha", 900_000, 5, 0), reel("beta", 50_000, 3, 0), reel("theai_gurukul", 2_000_000, 1, 0)], 0.5
    out = []
    for i in range(10):  # alpha: normal ~30K with one 900K hit; beta: flat ~40K
        out.append(reel("alpha", 900_000 if i == 0 else 30_000 + i * 100, 2 + i * 5, i))
        out.append(reel("beta", 40_000 + i * 100, 2 + i * 5, i))
    return out, 0.75


def fake_gemini(parts, key, models, json_out):
    assert key == "gk"
    if json_out:
        return json.dumps({"hook_spoken": "Stop using ChatGPT for this", "hook_type": "contrarian",
                           "why_it_stops_the_scroll": "x", "beats": [{"t": "0-3s", "what": "claim"}],
                           "our_version": {"hook_line": "Stop writing specs by hand", "on_screen_text": "SPEC IN 10s"}})
    return "### What's working\n- contrarian hooks (1)"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUT", tmp_path)
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "STATE", tmp_path / "series_state.json")
    monkeypatch.setattr(local, "LOCAL", tmp_path / "local")
    return tmp_path


def test_api_series_end_to_end(sandbox, monkeypatch):
    monkeypatch.setattr(sr, "apify_run", fake_apify)
    monkeypatch.setattr(sr, "gemini_call", fake_gemini)
    monkeypatch.setattr(sr, "download_video", lambda url, mb: b"\x00" * 10)
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    cfg = core.series_cfg(core.load_config(), "kiro")
    res = sr.scout_series(cfg, "tok", "gk", NOW)
    assert res["outliers"] == 1 and res["analysed"] == 1 and res["apify_usd"] == 1.25
    data = json.loads((sandbox / "data" / "2026-10-13-kiro.json").read_text())
    o = data["outliers"][0]
    assert o["owner"] == "alpha" and o["multiplier"] >= 25 and "video_url" not in o
    md = (sandbox / "reports" / "2026-10-13-kiro.md").read_text()
    assert "Kiro buzz reels" in md and "@alpha" in md and "Stop writing specs by hand" in md


def test_rotation_respects_budget_and_age():
    cfg = core.load_config()
    keys = list(cfg["series"])
    # every series scouted except the last two -> those come first
    state = {k: "2026-10-01T00:00:00Z" for k in keys[:-2]}
    picked = sr.choose_series(cfg, state, None)
    assert picked[:2] == sorted(keys[-2:], key=lambda k: state.get(k, ""))
    assert sum(sr.planned_results(core.series_cfg(cfg, k)) for k in picked) <= cfg["max_results_per_run"]
    assert sr.choose_series(cfg, state, ["mishti"]) == ["mishti"]


def test_missing_secrets_writes_status(sandbox, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert sr.main([]) == 1
    st = json.loads((sandbox / "last_run.json").read_text())
    assert st["ok"] is False and "APIFY_TOKEN" in st["error"]


def test_parse_count():
    assert core.parse_count("1.2M") == 1_200_000
    assert core.parse_count("45.3K") == 45_300
    assert core.parse_count("12,345") == 12_345
    assert core.parse_count("3.4 lakh") == 340_000
    assert core.parse_count(987) == 987 and core.parse_count("") == 0


def test_local_outliers_and_report(sandbox, tmp_path):
    reels = [{"url": f"https://www.instagram.com/reel/m{i}/", "views": v, "posted": "2026-10-05"}
             for i, v in enumerate(["2.9M", "180K", "150K", "210K", "95K", "160K"])]
    collected = tmp_path / "c.json"
    collected.write_text(json.dumps({"discovered": 40, "creators": {"@doodle.and.drama": reels}}))
    out = local.cmd_outliers("mishti", str(collected), NOW)
    data = json.loads(out.read_text())
    assert len(data["outliers"]) == 1 and data["outliers"][0]["owner"] == "doodle.and.drama"
    data["outliers"][0]["breakdown"] = {"hook_on_screen_text": "Me at 2 am", "hook_type": "relatable-moment",
                                        "our_version": {"emotion": "overthinking", "hook_text": "2 am brain"}}
    data["patterns"] = "### What's working\n- relatable moments (1)"
    out.write_text(json.dumps(data))
    md = local.cmd_report("mishti", str(out)).read_text()
    assert "Mishti emotions" in md and "no-API" in md and "2 am brain" in md and "relatable-moment" in md


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_pack_real_video(tmp_path):
    video = tmp_path / "v.mp4"
    # 8 s, three hard colour changes (scene cuts at 3 s and 6 s) and a tone
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=red:s=360x640:d=3", "-f", "lavfi", "-i", "color=c=blue:s=360x640:d=3",
                    "-f", "lavfi", "-i", "color=c=green:s=360x640:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    out = local.cmd_pack(str(video), str(tmp_path / "pack"))
    facts = json.loads((out / "facts.json").read_text())
    assert 7.5 < facts["duration_s"] < 8.5 and facts["has_audio"] and len(facts["cuts"]) == 2
    assert (out / "hook.jpg").stat().st_size > 1000 and (out / "story.jpg").stat().st_size > 1000

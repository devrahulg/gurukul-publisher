"""Offline test of the reel scout: Apify, Gemini and video downloads are faked."""
import datetime as dt
import json

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
    for i in range(10):  # alpha: normal ~30K, one 900K hit; beta: flat ~40K
        out.append(reel("alpha", 900_000 if i == 0 else 30_000 + i * 100, 2 + i * 5, i))
        out.append(reel("beta", 40_000 + i * 100, 2 + i * 5, i))
    return out, 0.75


def fake_gemini(parts, key, models, json_out):
    assert key == "gk"
    if json_out:
        return json.dumps({"hook_spoken": "Stop using ChatGPT for this", "hook_type": "contrarian",
                           "why_it_stops_the_scroll": "x", "beats": [{"t": "0-3s", "what": "claim"}],
                           "kiro_reel_idea": {"hook_line": "Stop writing specs by hand", "on_screen_text": "SPEC IN 10s",
                                              "outline": "a b c"}})
    return "### What's working this week\n- contrarian hooks (1)"


def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "OUT", tmp_path)
    monkeypatch.setattr(sr, "ROOT", tmp_path)
    monkeypatch.setattr(sr, "apify_run", fake_apify)
    monkeypatch.setattr(sr, "gemini_call", fake_gemini)
    monkeypatch.setattr(sr, "download_video", lambda url, mb: b"\x00" * 10)
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    cfg = json.loads(sr.CONFIG.read_text())
    res = sr.scout(cfg, "tok", "gk", NOW)
    assert res["outliers"] == 1 and res["analysed"] == 1
    data = json.loads((tmp_path / "data" / "2026-10-13.json").read_text())
    o = data["outliers"][0]
    assert o["owner"] == "alpha" and o["multiplier"] >= 25 and "video_url" not in o
    assert "theai_gurukul" not in data["creators"]  # our own account is excluded
    assert data["stats"]["apify_usd"] == 1.25
    md = (tmp_path / "reports" / "2026-10-13.md").read_text()
    assert "@alpha" in md and "contrarian" in md and "Stop writing specs by hand" in md


def test_missing_secrets_writes_status(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "OUT", tmp_path)
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert sr.main([]) == 1
    st = json.loads((tmp_path / "last_run.json").read_text())
    assert st["ok"] is False and "APIFY_TOKEN" in st["error"]

"""Re-time existing queue entries so every day has 4 posts per platform, 3+ hours apart.

    python tools/reslot_queue.py            # dry run: prints what would change
    python tools/reslot_queue.py --apply    # does it

New slots (IST):
  Learning HI   IG 13:30 (was 10:30)   YT 19:00 (was 16:00)
  Kiro buzz IG  even day after anchor -> 17:00, odd day -> 20:00      (YT stays 12:30)
Learning EN (IG 10:30, YT 16:00) is unchanged. Claude reels are placed by add_claude_queue.py.
Only entries whose status is "queued" are touched; scheduled/published ones are skipped and listed.
"""
import argparse, json, pathlib, re
from datetime import date, datetime, timedelta, timezone

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--anchor", default="2026-10-05")
ap.add_argument("--from-date", default=None, help="only entries on/after this date (default: today)")
a = ap.parse_args()
anchor = date.fromisoformat(a.anchor)
IST = timezone(timedelta(hours=5, minutes=30))
today = date.fromisoformat(a.from_date) if a.from_date else datetime.now(IST).date()
q = pathlib.Path("queue")
changes = skipped = 0
for f in sorted(q.glob("*.json")):
    e = json.loads(f.read_text(encoding="utf-8"))
    when = datetime.fromisoformat(e["publish_at"]); d = when.date()
    if d < today: continue
    ep = e.get("episode", 0); acct = e["account"]
    new = None
    if acct == "hi_ig" and ep < 101: new = "13:30"
    elif acct == "hi_yt" and ep < 101: new = "19:00"
    elif acct == "en_ig" and 101 <= ep <= 150: new = "17:00" if (d - anchor).days % 2 == 0 else "20:00"
    if not new or when.strftime("%H:%M") == new: continue
    if e.get("status") != "queued":
        skipped += 1; print("skip (status %s):" % e.get("status"), f.name); continue
    stamp = d.isoformat() + "T" + new.replace(":", "")
    nid = re.sub(r"^\d{4}-\d\d-\d\dT\d{4}", stamp, e["id"])
    print(f"{f.name} -> {nid}.json  ({when.strftime('%H:%M')} -> {new})")
    changes += 1
    if a.apply:
        e["id"] = nid; e["publish_at"] = f"{d.isoformat()}T{new}:00+05:30"
        (q / f"{nid}.json").write_text(json.dumps(e, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        f.unlink()
print(f"{changes} entries {'changed' if a.apply else 'would change'}; {skipped} skipped")

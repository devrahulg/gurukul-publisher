"""Add the Claude Buzz reels (episodes 201-250) to the publisher queue.

Run from the root of the gurukul-publisher repo:

    python tools/add_claude_queue.py claude_posts.json claude_drive_ids.csv --start 2026-10-07

claude_drive_ids.csv has two columns:  file_name,drive_file_id
   Claude-Buzz201-EN.mp4,1AbC...
Reels with no Drive ID yet are skipped (and listed), so you can add them in batches.
Existing queue files are never overwritten. Then run:  python -m publisher validate

Slots (IST), identical to the tracker's Settings sheet:
  day N after --anchor (default 2026-10-05) is even  -> Claude IG 20:00, Claude YT 22:00
  day N after --anchor is odd                        -> Claude IG 17:00, Claude YT 09:30
"""
import argparse, csv, json, pathlib, sys
from datetime import date, timedelta

ap = argparse.ArgumentParser()
ap.add_argument("posts"); ap.add_argument("ids")
ap.add_argument("--start", required=True, help="date reel C01 posts, YYYY-MM-DD")
ap.add_argument("--anchor", default="2026-10-05", help="slot-swap anchor date (Kiro buzz day 1)")
a = ap.parse_args()
start = date.fromisoformat(a.start); anchor = date.fromisoformat(a.anchor)
posts = json.load(open(a.posts, encoding="utf-8"))
ids = {}
with open(a.ids, encoding="utf-8-sig", newline="") as f:
    for row in csv.reader(f):
        if len(row) >= 2 and row[0].strip().lower().endswith(".mp4"):
            ids[row[0].strip()] = row[1].strip()
q = pathlib.Path("queue")
if not q.is_dir():
    sys.exit("Run this from the repo root (no queue/ folder here).")
made = skipped = 0; missing = []
for p in posts:
    fid = ids.get(p["file_name"])
    if not fid:
        missing.append(p["file_name"]); continue
    d = start + timedelta(days=p["day"] - 1)
    even = (d - anchor).days % 2 == 0
    slots = {"en_ig": "20:00" if even else "17:00", "en_yt": "22:00" if even else "09:30"}
    video = {"drive_file_id": fid, "file_name": p["file_name"]}
    for acct, hhmm in slots.items():
        when = f"{d.isoformat()}T{hhmm}:00+05:30"
        qid = f"{d.isoformat()}T{hhmm.replace(':', '')}_{acct}_ep{p['episode']:03d}"
        path = q / f"{qid}.json"
        if path.exists():
            skipped += 1; continue
        entry = {"id": qid, "account": acct, "publish_at": when, "language": "en", "episode": p["episode"], "video": video, "caption": p["ig_caption"]}
        if acct == "en_yt":
            entry["youtube"] = {"title": p["yt_title"], "description": p["yt_description"], "tags": p["yt_tags"], "category_id": "27", "made_for_kids": False}
        entry["status"] = "queued"; entry["attempts"] = 0
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        made += 1
print(f"created {made} queue files, skipped {skipped} that already existed")
if missing:
    print(f"{len(missing)} reels have no Drive ID yet:", ", ".join(missing[:8]), "..." if len(missing) > 8 else "")

"""Add the Kiro Buzz reels (ep 101-150) to the publisher queue.

Run from the root of the gurukul-publisher repo:

    python tools/add_buzz_queue.py buzz_posts.json buzz_drive_ids.csv

buzz_drive_ids.csv has two columns:  file_name,drive_file_id
   Kiro-Buzz101-EN.mp4,1AbC...
Reels with no Drive ID yet are skipped (and listed), so you can add them in batches.
Existing queue files are never overwritten. Then run:  python -m publisher validate
"""
import csv, json, pathlib, sys

posts = json.load(open(sys.argv[1], encoding="utf-8"))
ids = {}
with open(sys.argv[2], encoding="utf-8-sig", newline="") as f:
    for row in csv.reader(f):
        if len(row) >= 2 and row[0].strip().lower().endswith(".mp4"):
            ids[row[0].strip()] = row[1].strip()
q = pathlib.Path("queue")
if not q.is_dir():
    sys.exit("Run this from the repo root (no queue/ folder here).")
made = skipped = 0
missing = []
for p in posts:
    fid = ids.get(p["file_name"])
    if not fid:
        missing.append(p["file_name"]); continue
    video = {"drive_file_id": fid, "file_name": p["file_name"]}
    for acct, when, key in (("en_ig", p["ig_publish_at"], "ig"), ("en_yt", p["yt_publish_at"], "yt")):
        stamp = when[:10] + "T" + when[11:13] + when[14:16]
        qid = f"{stamp}_{acct}_ep{p['episode']:03d}"
        path = q / f"{qid}.json"
        if path.exists():
            skipped += 1; continue
        entry = {"id": qid, "account": acct, "publish_at": when, "language": "en", "episode": p["episode"], "video": video}
        if key == "ig":
            entry["caption"] = p["ig_caption"]
        else:
            entry["caption"] = p["ig_caption"]
            entry["youtube"] = {"title": p["yt_title"], "description": p["yt_description"], "tags": p["yt_tags"],
                                "category_id": "27", "made_for_kids": False}
        entry["status"] = "queued"; entry["attempts"] = 0
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        made += 1
print(f"created {made} queue files, skipped {skipped} that already existed")
if missing:
    print(f"{len(missing)} reels have no Drive ID yet:", ", ".join(missing[:8]), "..." if len(missing) > 8 else "")

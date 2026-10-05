"""One-step go-live for the 100 buzz reels (50 Kiro + 50 Claude).

1. Put the kit files in the repo's tools/ folder (go_live.py, the 3 scripts, the 2 json files,
   drive_ids.csv). Fill drive_ids.csv (file_name,drive_file_id) for the videos already in Drive.
2. From the repo root:
       python tools/go_live.py            # dry run: shows what will happen
       python tools/go_live.py --apply    # does it (add Kiro, re-slot, add Claude, validate, commit, push)

Reels without a Drive ID are skipped and listed, so you can run it again later in batches.
Claude reel C01 starts on --claude-start (default: today, IST). Kiro buzz dates come from buzz_posts.json.
"""
import argparse, subprocess, sys, pathlib, shutil, tempfile
from datetime import datetime, timedelta, timezone

ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
ap.add_argument("--claude-start", default=datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat())
ap.add_argument("--no-push", action="store_true")
a = ap.parse_args()
here = pathlib.Path(__file__).resolve().parent; root = pathlib.Path.cwd()
if not (root / "queue").is_dir(): sys.exit("Run from the repo root.")
def run(*cmd, check=True):
    print(">", " ".join(map(str, cmd))); r = subprocess.run(cmd, cwd=root)
    if check and r.returncode: sys.exit(f"failed: {cmd}")
py = sys.executable; ids = here / "drive_ids.csv"
if not a.apply:
    print("DRY RUN - nothing is written. Re-slot preview:")
    run(py, here / "reslot_queue.py", check=False)
    print("Then --apply will add Kiro buzz + Claude entries (Claude C01 on %s) and push." % a.claude_start); sys.exit()
if not a.no_push: run("git", "pull", "--rebase", "--autostash")   # start from GitHub's latest queue (publisher writes statuses there)
run(py, here / "add_buzz_queue.py", here / "buzz_posts.json", ids)
run(py, here / "reslot_queue.py", "--apply")   # after adding Kiro buzz so its IG slots swap too
run(py, here / "add_claude_queue.py", here / "claude_posts.json", ids, "--start", a.claude_start)
run(py, "-m", "publisher", "validate")
if not a.no_push:
    run("git", "add", "-A", "queue"); run("git", "commit", "-m", "Queue: 4 posts/day (learning EN+HI, Kiro buzz, Claude buzz)")
    run("git", "pull", "--rebase"); run("git", "push")
print("Done. The publisher picks entries up on its next 15-minute run.")

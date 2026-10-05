"""Build the LinkedIn queue from the reels you already have (Kiro buzz 101-150, Claude buzz 201-250).

Run from the repo root, AFTER the LinkedIn secrets are set (see SETUP-LINKEDIN.md):

    python tools/add_linkedin_queue.py --start 2026-10-12            # preview only
    python tools/add_linkedin_queue.py --start 2026-10-12 --apply    # write queue files

Plan (default, one post per weekday at 09:15 IST, 10 weeks = 50 posts), alternating weeks:
    week A   Mon video (Kiro) | Tue carousel (Claude) | Wed video (Claude) | Thu text (Kiro)   | Fri carousel (Kiro)
    week B   Mon video (Claude) | Tue carousel (Kiro) | Wed video (Kiro)   | Thu text (Claude) | Fri carousel (Claude)
Every reel is used once on LinkedIn. Videos use the Drive IDs in tools/drive_ids.csv; carousels are
PDFs in carousels/ (made here if missing; needs `pip install pillow`).
Existing queue files are never overwritten.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
from datetime import date, timedelta

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PATTERN = {
    "A": [("video", "kiro"), ("document", "claude"), ("video", "claude"), ("text", "kiro"), ("document", "kiro")],
    "B": [("video", "claude"), ("document", "kiro"), ("video", "kiro"), ("text", "claude"), ("document", "claude")],
}
DISCLAIMER = {
    "kiro": "Independent creator, not affiliated with AWS or Kiro.",
    "claude": "Independent creator, not affiliated with Anthropic. Claude is a trademark of Anthropic.",
}
NAME = {"kiro": "Kiro", "claude": "Claude"}
_STEP = re.compile(r"^(\d+)\. (.+?)(?: - (.+))?$")
DROP_TAGS = {"#explorepage", "#commentbelow", "#reels", "#fyp", "#viral"}


def parse(desc: str):
    lines = [x.strip() for x in desc.split("\n")]
    headline = lines[0]
    paras = [x.strip() for x in desc.split("\n\n")]
    summary = paras[1] if len(paras) > 1 and not _STEP.match(paras[1].split("\n")[0]) else ""
    steps = [(m.group(2), m.group(3) or "") for m in (_STEP.match(l) for l in lines) if m]
    needs = next((l[6:].strip() for l in lines if l.startswith("Needs:")), "")
    return headline, summary, steps, needs


def question(post: dict, headline: str) -> str:
    for l in post["ig_caption"].split("\n"):
        l = l.strip()
        if "?" in l and l != headline and len(l) < 120 and not l.startswith(("1", "2", "3", "4", "5", "6", "7", "8", "9")):
            return l
    return "What would you add?"


def tags(post: dict) -> str:
    t = [x for x in post["hashtags"].split() if x.lower() not in DROP_TAGS]
    if "#theAIgurukul" not in t:
        t.append("#theAIgurukul")
    return " ".join(t[:4] + (["#theAIgurukul"] if "#theAIgurukul" not in t[:4] else []))[:200]


def first_comment(post: dict) -> str:
    docs = post.get("docs")
    return f"Official docs: {docs}" if docs else ""


def commentary(post: dict, series: str, kind: str) -> str:
    headline, summary, steps, needs = parse(post["yt_description"])
    q = question(post, headline)
    parts = [headline]
    if summary and kind != "document":
        parts.append(summary)
    if kind == "document":
        parts.append(f"Swipe through the {len(steps)} steps. Save it for later.")
    else:
        lines = [f"{i}. {h}" + (f": {d}" if d else "") for i, (h, d) in enumerate(steps, 1)]
        parts.append("\n".join(lines))
    if needs:
        parts.append(f"Needs: {needs}")
    parts.append(q)
    if first_comment(post):
        parts.append("Link to the official docs is in the first comment.")
    parts.append(f"Follow theAIgurukul for a {NAME[series]} tip every working day.")
    parts.append(DISCLAIMER[series])
    parts.append(tags(post))
    return "\n\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="first post date (a Monday), YYYY-MM-DD")
    ap.add_argument("--weeks", type=int, default=10)
    ap.add_argument("--time", default="09:15", help="IST, HH:MM")
    ap.add_argument("--account", default="li_page")
    ap.add_argument("--buzz", default=str(HERE / "buzz_posts.json"))
    ap.add_argument("--claude", default=str(HERE / "claude_posts.json"))
    ap.add_argument("--ids", default=str(HERE / "drive_ids.csv"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    start = date.fromisoformat(a.start)
    if start.weekday() != 0:
        sys.exit(f"--start {a.start} is not a Monday. Use the Monday the first week begins.")
    root = HERE.parent
    q = root / "queue"
    if not q.is_dir():
        sys.exit("Run this from the repo root (no queue/ folder here).")
    pools = {"kiro": json.load(open(a.buzz, encoding="utf-8")), "claude": json.load(open(a.claude, encoding="utf-8"))}
    pools = {k: sorted(v, key=lambda p: p["episode"]) for k, v in pools.items()}
    ids = {}
    with open(a.ids, encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2 and row[0].strip().lower().endswith(".mp4"):
                ids[row[0].strip()] = row[1].strip()
    used = {"kiro": 0, "claude": 0}
    hhmm = a.time.replace(":", "")
    made = skipped = 0
    problems = []
    rows = []
    for w in range(a.weeks):
        plan = PATTERN["A" if w % 2 == 0 else "B"]
        for dow, (kind, series) in enumerate(plan):
            d = start + timedelta(weeks=w, days=dow)
            pool = pools[series]
            if used[series] >= len(pool):
                problems.append(f"{d}: no more {series} reels"); continue
            post = pool[used[series]]; used[series] += 1
            ep = post["episode"]
            qid = f"{d.isoformat()}T{hhmm}_{a.account}_ep{ep:03d}"
            headline = parse(post["yt_description"])[0]
            entry = {"id": qid, "account": a.account, "publish_at": f"{d.isoformat()}T{a.time}:00+05:30",
                     "type": kind, "language": "en", "episode": ep, "series": series,
                     "title": headline[:150], "commentary": commentary(post, series, kind)}
            fc = first_comment(post)
            if fc:
                entry["first_comment"] = fc
            if kind == "video":
                fid = ids.get(post["file_name"])
                if not fid:
                    problems.append(f"{d}: no Drive ID for {post['file_name']}"); continue
                entry["video"] = {"drive_file_id": fid, "file_name": post["file_name"]}
            elif kind == "document":
                rel = f"carousels/linkedin-{series}-{ep}.pdf"
                pdf = root / rel
                if not pdf.exists() and a.apply:
                    from make_carousel import build_carousel, spec_from_post
                    build_carousel(spec_from_post(post, series), pdf)
                entry["document"] = {"path": rel}
            entry["status"] = "queued"; entry["attempts"] = 0
            path = q / f"{qid}.json"
            rows.append((d, kind, series, ep, headline))
            if path.exists():
                skipped += 1; continue
            if a.apply:
                path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            made += 1
    for d, kind, series, ep, headline in rows:
        print(f"{d} {d.strftime('%a')} {a.time}  {kind:8s} {series:6s} ep{ep}  {headline[:70]}")
    print(f"\n{'created' if a.apply else 'would create'} {made} queue files, skipped {skipped} that already existed")
    for p in problems:
        print("PROBLEM:", p)
    if not a.apply:
        print("Nothing written. Add --apply to write the files.")


if __name__ == "__main__":
    main()

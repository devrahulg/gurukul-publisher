"""Tiny pre-check for the workflow (standard library only, runs in about a second):
prints due=true when some LinkedIn post is inside its posting window or a comment is waiting,
so the heavy steps (install packages, call LinkedIn) are skipped on the other runs."""
import datetime as dt, json, os, pathlib

root = pathlib.Path(__file__).resolve().parent.parent
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
now = dt.datetime.now(dt.timezone.utc)
try:
    s = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
except Exception:
    s = {}
early = int(s.get("linkedin_due_window_minutes", 15))
late = int(s.get("linkedin_max_late_minutes", 180))
try:
    accts = json.loads((root / "config" / "accounts.json").read_text(encoding="utf-8"))
except Exception:
    accts = {}
li = {n for n, a in accts.items() if a.get("platform") == "linkedin" and a.get("enabled")}
due = False
for f in (root / "queue").glob("*.json"):
    try:
        p = json.loads(f.read_text(encoding="utf-8"))
        if p.get("account") not in li:
            continue
        at = dt.datetime.fromisoformat(p["publish_at"].replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=IST)
        r = p.get("result") or {}
        if p.get("status", "queued") == "queued" and at - dt.timedelta(minutes=early) <= now <= at + dt.timedelta(minutes=late + 30):
            due = True
        if p.get("status") == "published" and r.get("comment_pending"):
            due = True
    except Exception:
        continue
out = os.environ.get("GITHUB_OUTPUT")
line = f"due={'true' if due else 'false'}"
print(line)
if out:
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")

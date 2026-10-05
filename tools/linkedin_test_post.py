"""Queue ONE small LinkedIn text post that is due right now, to prove the connection works.

    python tools/linkedin_test_post.py

The post is PUBLIC on your Page. Delete it on LinkedIn afterwards if you do not want to keep it.
Then commit and push, and run Actions > linkedin > Run workflow.
"""
import datetime as dt
import json
import pathlib

root = pathlib.Path(__file__).resolve().parent.parent
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
now = dt.datetime.now(IST).replace(microsecond=0)
qid = f"{now:%Y-%m-%dT%H%M}_li_page_test"
entry = {
    "id": qid, "account": "li_page", "publish_at": now.isoformat(), "type": "text", "language": "en",
    "commentary": "Testing the theAIgurukul publisher (this post can be deleted). #theAIgurukul",
    "status": "queued", "attempts": 0,
}
path = root / "queue" / f"{qid}.json"
path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {path.relative_to(root)}\nNext: git add queue; git commit -m \"linkedin test\"; git push; "
      "then GitHub > Actions > linkedin > Run workflow.\nThe post must go out within 3 hours of its time or it is marked missed.")

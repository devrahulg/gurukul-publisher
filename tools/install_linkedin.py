"""Add LinkedIn support to the publisher. Safe to run more than once.

Run from the repo root, after the LinkedIn files have been copied in:

    python tools/install_linkedin.py

It makes four small edits (each backed up as <file>.pre-linkedin.bak, handles Windows
line endings) and refuses to touch anything if an expected line is not found:
  publisher/queue.py   LinkedIn text posts have no video, so "video" is only required for IG/YT
  publisher/run.py     account check for LinkedIn + the linkedin-* commands
  config/accounts.json adds the li_page account (switches on by itself once secrets exist)
and copies the two workflow files from _workflows/ into .github/workflows/.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path.cwd()

QUEUE_EDITS = [
    ("# linkedin-patch-1",
     'REQUIRED = ("id", "account", "publish_at", "video")',
     'REQUIRED = ("id", "account", "publish_at")  # linkedin-patch-1 (video is checked below, per platform)'),
    ("# linkedin-patch-2",
     '    video = post.get("video") or {}\n'
     '    if not (video.get("drive_file_id") or video.get("url")):\n'
     '        problems.append("video needs drive_file_id or url")\n',
     '    if acct and acct["platform"] == "linkedin":  # linkedin-patch-2\n'
     '        from .linkedin_api import post_problems\n'
     '        problems.extend(post_problems(post))\n'
     '    else:\n'
     '        video = post.get("video") or {}\n'
     '        if not video:\n'
     '            problems.append("missing video")\n'
     '        elif not (video.get("drive_file_id") or video.get("url")):\n'
     '            problems.append("video needs drive_file_id or url")\n'),
]

RUN_EDITS = [
    ("# linkedin-patch-3",
     '    if not acct.get("enabled"):\n        return False, "disabled in config/accounts.json"\n',
     '    if not acct.get("enabled"):\n        return False, "disabled in config/accounts.json"\n'
     '    if acct["platform"] == "linkedin":  # linkedin-patch-3\n'
     '        from .linkedin_run import linkedin_ready\n'
     '        return linkedin_ready(acct)\n'),
    ("# linkedin-patch-4",
     '    for name in ("stats", "refresh-ig-tokens", "validate"):\n        sub.add_parser(name)\n',
     '    for name in ("stats", "refresh-ig-tokens", "validate", "linkedin-run", "linkedin-check",  # linkedin-patch-4\n'
     '                 "linkedin-refresh", "linkedin-stats"):\n        sub.add_parser(name)\n'),
    ("# linkedin-patch-5",
     '    if args.cmd == "stats":\n        return cmd_stats()\n',
     '    if args.cmd.startswith("linkedin-"):  # linkedin-patch-5\n'
     '        from .linkedin_run import main as linkedin_main\n'
     '        return linkedin_main(args.cmd)\n'
     '    if args.cmd == "stats":\n        return cmd_stats()\n'),
]

ACCOUNT = {
    "platform": "linkedin",
    "label": "LinkedIn page theAIgurukul",
    "token_secret": "LINKEDIN_TOKEN",
    "author_secret": "LINKEDIN_AUTHOR",
    "enabled": True,
}


def patch_text(path: pathlib.Path, edits: list[tuple[str, str, str]]) -> str:
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    done = 0
    for marker, old, new in edits:
        if marker in text:
            continue
        if text.count(old) != 1:
            raise SystemExit(f"STOP: {path} does not look like the expected version "
                             f"(could not find the line for {marker}). Nothing was changed. "
                             "Send this message to Claude.")
        text = text.replace(old, new)
        done += 1
    if not done:
        return f"{path.name}: already patched"
    backup = path.with_name(path.name + ".pre-linkedin.bak")
    if not backup.exists():
        shutil.copy(path, backup)
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    return f"{path.name}: {done} edit(s) applied"


def patch_accounts(path: pathlib.Path) -> str:
    raw = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    data = json.loads(raw)
    if "li_page" in data:
        return "accounts.json: li_page already there"
    backup = path.with_name(path.name + ".pre-linkedin.bak")
    if not backup.exists():
        shutil.copy(path, backup)
    data["li_page"] = ACCOUNT
    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_bytes((out.replace("\n", "\r\n") if crlf else out).encode("utf-8"))
    return "accounts.json: li_page added"


def main() -> None:
    need = [ROOT / "publisher" / "run.py", ROOT / "publisher" / "queue.py",
            ROOT / "publisher" / "linkedin_api.py", ROOT / "publisher" / "linkedin_run.py",
            ROOT / "config" / "accounts.json"]
    missing = [str(p.relative_to(ROOT)) for p in need if not p.exists()]
    if missing:
        sys.exit("Run this from the repo root after copying the LinkedIn files in. Missing: " + ", ".join(missing))
    # check every edit applies before writing anything
    for path, edits in ((ROOT / "publisher" / "queue.py", QUEUE_EDITS), (ROOT / "publisher" / "run.py", RUN_EDITS)):
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        for marker, old, _ in edits:
            if marker not in text and text.count(old) != 1:
                sys.exit(f"STOP: {path.name} is not the expected version (line for {marker} not found). "
                         "Nothing was changed. Send this message to Claude.")
    for line in (patch_text(ROOT / "publisher" / "queue.py", QUEUE_EDITS),
                 patch_text(ROOT / "publisher" / "run.py", RUN_EDITS),
                 patch_accounts(ROOT / "config" / "accounts.json")):
        print(line)
    wf_src, wf_dst = ROOT / "_workflows", ROOT / ".github" / "workflows"
    for name in ("linkedin.yml", "linkedin-health.yml"):
        src = wf_src / name
        if src.exists():
            wf_dst.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, wf_dst / name)
            print(f".github/workflows/{name}: copied from _workflows")
        elif not (wf_dst / name).exists():
            print(f"NOTE: {name} not found in _workflows/ or .github/workflows/")
    print("Done. Next: python -m publisher validate   (the li_page line should say it needs secrets)")


if __name__ == "__main__":
    main()

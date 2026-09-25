# Gurukul Publisher

Free, self-hosted posting for theAIgurukul. It publishes YouTube Shorts and Instagram Reels through the official YouTube Data API and Instagram API. There are no paid schedulers and no per-month post cap.

## How it works

```
queue/*.json  ──►  GitHub Actions (every 15 min, free)  ──►  YouTube Data API   (upload ahead, YouTube releases at the set time)
   ▲                     │                               ──►  Instagram API     (publishes the Reel when it is due)
   │                     └─ GitHub Pages hosts that day's Reel video for Instagram to fetch
   │
Claude (connector in mcp_server/)  reads and edits the queue, starts runs, reads stats
```

- **One file per post** in `queue/`, named `<date>T<time>_<account>_ep<NNN>.json`. The publisher updates each file's `status` (`queued → scheduled/published`, or `failed`, `missed`, `cancelled`), the live URL and a short history.
- **Videos come from Google Drive** (the "theAIgurukul - Kiro Episode Videos" folder) through the Drive API, so nothing large is stored in this repository.
- **Secrets** (Google and Instagram tokens) live only in the repository's Actions secrets, never in files.
- **Stats**: every morning at 08:00 IST the `stats` workflow writes followers, subscribers and views to `stats/latest.json`. On Sundays it also extends the Instagram tokens.

## What's in the queue now

The Hindi track is queued from Oct 1 to Dec 12, 2026: 146 posts. Instagram posts go out at 10:30 IST and YouTube at 16:00 IST. Each post uses the correct episode video and the master Hindi script's title, hook and hashtags. The English accounts are still on Metricool and stay switched off in `config/accounts.json` until you move them.

## Accounts

| Key | Where | Secret |
|---|---|---|
| `hi_ig` | Hindi Instagram | `IG_TOKEN_HI` |
| `hi_yt` | Hindi YouTube channel | `YT_REFRESH_HI` |
| `en_ig` | @theai_gurukul (off for now) | `IG_TOKEN_EN` |
| `en_yt` | English YouTube channel (off for now) | `YT_REFRESH_EN` |

Also required: `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. `GH_ADMIN_TOKEN` is needed for automatic Instagram token renewal.

## Limits to know

- **YouTube:** until Google approves the API audit, uploads from a new Google Cloud project stay **private**. Apply once (see SETUP.md, step 4). Until then you can make the uploaded videos public in bulk from YouTube Studio.
- **Instagram:** at most 100 API posts per account per 24 hours. You need a Professional (Business or Creator) account.
- **Timing:** GitHub's 15-minute schedule can run a few minutes late. Posts more than 3 hours late are marked `missed` rather than posted out of order. Change this in `config/settings.json`.

## Commands (run by the workflows)

```
python -m publisher validate            # check config and every queue file
python -m publisher prepare --site site # stage due Instagram videos for Pages
python -m publisher run --site site     # upload / publish, update the queue
python -m publisher stats               # write stats/latest.json
python -m publisher refresh-ig-tokens   # extend Instagram tokens
```

Tests: `pip install pytest && python -m pytest -q tests`

Setup steps: **[SETUP.md](SETUP.md)**.

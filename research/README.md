# Reel scout

Finds Instagram reels in our niche (Claude, Kiro, AI coding, AI tools) that did far better than their creator's normal, then has Gemini watch the top ones and break down the hook, structure and pace, with a Kiro and a Claude version of each idea.

- **Reports:** `research/reports/<date>.md`
- **Raw numbers:** `research/data/<date>.json`
- **Last run status:** `research/last_run.json`
- **Settings:** `config/scout.json` (hashtags, accounts to always include, thresholds, budget)

**How "outlier" is measured:** a reel's views divided by the median views of that creator's last 20 reels. 3x or more means the idea or hook did the work, not the account size.

**Runs:** on the 1st and 15th of each month at 08:15 IST, from Actions → scout → Run workflow, or whenever `research/request.json` changes. That file can also override settings for a single run, e.g. `{"hashtags": ["claudecode"], "analyse_top": 5}`.

**Cost:** about 700 Apify results per run (roughly $1.80), so the free $5 monthly Apify credit covers about two runs a month. Gemini runs on the free API tier.

**Secrets (Settings → Secrets and variables → Actions):** `APIFY_TOKEN` and `GEMINI_API_KEY`.

**Note:** this repo is public, so the reports are public too. They contain only public reels and our own notes.

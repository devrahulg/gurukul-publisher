# Reel scout

Finds Instagram reels that did far better than their creator's normal, for every series in The AI Gurukul project, then breaks down why: the hook (spoken, on screen, visual), structure, pace, audio and call to action. Each breakdown includes a version for our own series.

**Series** (settings in `config/scout.json`):

| Key | Series | Looks at |
|---|---|---|
| `kiro` | Kiro buzz reels | #kiro #kiroide #aicoding #vibecoding #cursorai #agenticai |
| `claude` | Claude buzz reels | #claudeai #claude #anthropic #claudecode #aitools #chatgpttips |
| `vidya` | Vidya daily reels | #aitips #aihacks #aiforbeginners #chatgpthindi #techtipshindi #productivityhacks |
| `mira` | WTF Claude (Mira) | #aihumor #aimemes #techhumor #aiexplained #chatgptfunny #claudeai |
| `mishti` | Mishti emotions | #relatablereels #cartoonreels #moodreels #relatablememes #doodleart #girlymood, plus @mittsy_tiny_world and @doodle.and.drama |

**How "outlier" is measured:** a reel's views divided by the median views of that creator's recent reels. 3x or more (and at least 10K views, in the last 60 days) means the idea or hook did the work, not the account size.

## Two ways to run it

**1. No-API mode (free, no keys).** Claude does the scouting in a session: it browses Instagram in your browser (a creator's Reels tab shows every reel's view count), then runs `python -m scout.local` to pick the outliers, turn each video into hook frames, a storyboard, cut rhythm and a local Whisper transcript, and writes the breakdowns itself. It needs your PC online with the Claude desktop app, and ffmpeg plus `pip install -r requirements-local.txt` on the PC. Ask: "run the reel scout for mishti".

**2. API mode (automatic).** GitHub Actions runs on the 1st and 15th at 08:15 IST, on demand (Actions → scout → Run workflow), or when `research/request.json` changes (e.g. `{"series": ["mishti"]}`). It uses Apify (free $5/month credit) and Gemini (free API tier). Each run spends up to 900 Apify results, about 3 series, starting with the ones scouted longest ago, so all five rotate through within the free credit. Needs the repo secrets `APIFY_TOKEN` and `GEMINI_API_KEY`.

**Output:** `research/reports/<date>-<series>.md` (read this), `research/data/` and `research/local/` (numbers), `research/last_run.json` (API run status).

**Note:** this repo is public, so the reports are public too. They contain only public reels and our own notes.

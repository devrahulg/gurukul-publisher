# Setup (about 1 hour, one time)

Tokens and passwords go only into Google, Meta and GitHub's own pages. Never paste them into chat. Once step 8 is done, tell Claude, and it will check everything end to end.

---

## 1. GitHub repository (10 min)

1. Create a free GitHub account if you don't have one.
2. Create a new **public** repository named `gurukul-publisher` with no README.
   *Why public:* GitHub Actions minutes are unlimited on public repos, and GitHub Pages (which hosts each day's Reel file for Instagram to fetch) is free only on public repos. No secrets are ever stored in the files. Captions become visible a few days early, and that's the trade-off.
3. Move the workflow files into place. Claude couldn't write into `.github` on your computer, so they are in `_workflows`:
   ```
   cd C:\Code\Claude\gurukul-publisher
   mkdir .github\workflows
   move _workflows\*.yml .github\workflows\
   rmdir /s /q _workflows
   ```
   (The zip file Claude sent in chat already has them in the right place.)
4. Upload the folder:
   ```
   git init -b main
   git add .
   git commit -m "Gurukul Publisher"
   git remote add origin https://github.com/<you>/gurukul-publisher.git
   git push -u origin main
   ```
   If git isn't available, drag the folder's contents into the repo's **Add file → Upload files** page, in two batches if needed.

## 2. Turn on Pages (1 min)

Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.

## 3. Google / YouTube (15 min)

1. Go to <https://console.cloud.google.com>, create a project called `gurukul-publisher`.
2. **APIs & Services → Library**: enable **YouTube Data API v3** and **Google Drive API**.
3. **OAuth consent screen**: set the user type to External and the app name to "Gurukul Publisher", and add your email. Add scopes `youtube.upload`, `youtube.readonly` and `drive.readonly`. Then click **Publish app** to move it to *In production*.
   *Important:* in "Testing" mode Google expires the tokens after 7 days. When you sign in, Google shows an "unverified app" warning. That's expected for your own app: click *Advanced → Go to Gurukul Publisher*.
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON file. Keep it private and don't put it in the repo.
5. On any computer with Python 3, run:
   ```
   python tools/google_auth.py C:\path\to\client_secret.json
   ```
   Sign in and pick the **English channel** when Google asks which account or channel to use. The script prints the connected channel's name and a refresh token.
   Then run the same command a second time and pick the **Hindi channel**.
6. In the GitHub repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` (from the JSON file)
   - `YT_REFRESH_EN` (the token printed for the English channel)
   - `YT_REFRESH_HI` (the token printed for the Hindi channel)

## 4. Ask YouTube to allow public uploads (5 min, then wait)

New Google Cloud projects can upload **only as private** until YouTube audits them. Fill in the **YouTube API Services – Audit and Quota Extension form** (search that name, or use <https://support.google.com/youtube/contact/yt_api_form>). Describe the use as: *"Internal tool that uploads and schedules my own educational Shorts to my own two channels (English and Hindi)."*

Until it's approved, the publisher still uploads every video on time, in both languages, as private. In YouTube Studio, select them and set each to Public or Scheduled. It takes about 2 minutes a week.

## 5. Instagram (20 min)

1. In the Instagram app, make sure both accounts are **Professional** (Business or Creator). @theai_gurukul already is, because Metricool needs that. Switch the Hindi account too.
2. Go to <https://developers.facebook.com> → **My Apps → Create app**. Pick the use case **Manage messaging & content on Instagram**.
3. In the app, open **Instagram → API setup with Instagram login**.
   - Under **Generate access tokens**, click **Add account** and sign in with @theai_gurukul. Then do it again with the Hindi account. If asked to accept a tester invite, open Instagram → **Settings → Website permissions → Apps and websites → Tester invites** and accept.
   - Make sure the permissions include `instagram_business_basic` and `instagram_business_content_publish`.
   - Click **Generate token** next to each account and copy it. Each token lasts 60 days, and the publisher renews them every Sunday.
4. Add them as the GitHub secrets `IG_TOKEN_EN` (@theai_gurukul) and `IG_TOKEN_HI` (Hindi account).

Leave the app in **Development** mode. Your own accounts don't need Meta's App Review.

## 6. Automatic Instagram token renewal (3 min)

GitHub → your avatar → **Settings → Developer settings → Fine-grained tokens → Generate new token**
- Repository access: only `gurukul-publisher`
- Permissions: **Secrets: Read and write**

Save it as the repo secret `GH_ADMIN_TOKEN`.

## 7. Connect it to Claude (5 min)

1. Create a second fine-grained token for the same repository only, with **Contents: Read and write** and **Actions: Read and write**. Metadata read is added automatically.
2. In the Claude desktop app, go to **Settings → Developer → Edit Config**. That opens `claude_desktop_config.json`. Add this block, merging it into any existing `mcpServers`:
   ```json
   {
     "mcpServers": {
       "gurukul-publisher": {
         "command": "python",
         "args": ["C:\\Code\\Claude\\gurukul-publisher\\mcp_server\\server.py"],
         "env": {
           "GITHUB_REPO": "<you>/gurukul-publisher",
           "GITHUB_TOKEN": "<the token from step 7.1>"
         }
       }
     }
   }
   ```
   If `python` isn't found, use the full path to `python.exe`. The connector needs only Python 3's standard library, with nothing to install.
3. Quit Claude completely and reopen it.

## 8. First run

1. Repo **Actions** tab → enable workflows if GitHub asks.
2. Run **stats → Run workflow**. `stats/latest.json` should show all four accounts: two YouTube channels and two Instagram accounts.
3. Run **publish → Run workflow**. The log lists what it uploaded or published. Posts dated before today are marked `missed`, which is expected.
4. Tell Claude: *"check the publisher"*.

## Switching off Metricool

Metricool still has the same episodes scheduled through Dec 12. Its English posts go to your English accounts, and its Hindi copies also go to the English accounts. As soon as the publisher's first post succeeds, ask Claude to turn every remaining Metricool post into a draft. That stops duplicates on both languages. After that, Metricool is only needed for its analytics, which stay free.

---

### Troubleshooting

| What you see | Fix |
|---|---|
| `secret YT_REFRESH_HI not set` (or `_EN`) in the run summary | Add the secret (step 3.6) |
| Google `invalid_grant` | The consent screen was still in Testing. Publish it (3.3) and run `google_auth.py` again |
| YouTube `uploadLimitExceeded` / `quotaExceeded` | Wait a day. The default quota is plenty for 2 videos a day |
| Instagram `Media URL not reachable` | Pages isn't enabled (step 2). The post retries next run |
| Instagram token error / code 190 | Generate a new token (5.3) and replace `IG_TOKEN_EN` or `IG_TOKEN_HI` |
| Everything shows `missed` | The workflow was disabled or never ran. Check the Actions tab |

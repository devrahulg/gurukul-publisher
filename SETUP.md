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

## 2. Turn on Pages (2 min)

1. Repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. **Actions → site → Run workflow**. This publishes your home, privacy policy and terms pages at `https://devrahulg.github.io/gurukul-publisher/`. Google and Meta ask for these links.

## 3. Google / YouTube (15 min)

1. Go to <https://console.cloud.google.com> and create a project called `gurukul-publisher`.
2. **APIs & Services → Library**: enable **YouTube Data API v3** and **Google Drive API**.
3. **Google Auth Platform → Branding** (the "OAuth consent screen"):
   - App name: `Gurukul Publisher`. Support email and developer contact: your Gmail.
   - **App domain** (these pages go live when you run the `site` workflow in step 2b):
     - Application home page: `https://devrahulg.github.io/gurukul-publisher/`
     - Application privacy policy link: `https://devrahulg.github.io/gurukul-publisher/privacy.html`
     - Application Terms of Service link: `https://devrahulg.github.io/gurukul-publisher/terms.html`
     - Authorised domain: `devrahulg.github.io`. If Google refuses it, leave this field empty. It's only required for full verification.
4. **Data access → Add or remove scopes**: add only `.../auth/youtube.upload` and `.../auth/youtube.readonly`. Both are "sensitive" scopes, not "restricted" ones. Drive access goes through the service account in step 3b instead, so it needs no scope on this screen.
5. **Audience → Publish app → Confirm**, so the status reads *In production*.
   - Google may say the app "needs verification". You don't have to submit it. An unverified app still works for up to 100 users, and you are the only one. You'll see an "unverified app" warning when signing in: click **Advanced → Go to Gurukul Publisher (unsafe)**.
   - *Why this matters:* in **Testing** mode, Google expires the sign-in after 7 days and publishing would stop every week. If you do leave it in Testing for now, add your Gmail under **Audience → Test users**. Then re-run step 3.7 every 7 days until you publish.
6. **Clients → Create client → Desktop app**, twice: one client per channel.
   - Name the first one `Gurukul Publisher EN` and download its JSON as `client_en.json`.
   - Name the second one `Gurukul Publisher HI` and download its JSON as `client_hi.json`.
   - Keep both files private and don't put them in the repo.
7. On any computer with Python 3, get one refresh token per channel:
   ```
   python tools/google_auth.py C:\path\to\client_en.json
   ```
   Sign in and pick the **English channel** when Google asks which account or channel to use. The script prints the connected channel's name, so you can confirm it's the right one, and then the refresh token. Then run:
   ```
   python tools/google_auth.py C:\path\to\client_hi.json
   ```
   This time pick the **Hindi channel**. If the Hindi channel lives under a different Google login, sign in with that login. Both clients sit in the same Cloud project, so this works either way.
8. In the GitHub repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add six secrets:

   | Secret | Value |
   |---|---|
   | `GOOGLE_CLIENT_ID_EN` | `client_id` from `client_en.json` |
   | `GOOGLE_CLIENT_SECRET_EN` | `client_secret` from `client_en.json` |
   | `YT_REFRESH_EN` | token printed for the English channel |
   | `GOOGLE_CLIENT_ID_HI` | `client_id` from `client_hi.json` |
   | `GOOGLE_CLIENT_SECRET_HI` | `client_secret` from `client_hi.json` |
   | `YT_REFRESH_HI` | token printed for the Hindi channel |

   Then delete both JSON files from your computer.

   *Simpler alternative:* one client can serve both channels. Run the script twice with the same JSON, pick a different channel each time, and save the client as `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. The publisher uses a channel's own client when one is set, and otherwise falls back to the shared one.

## 3b. Keep the Drive folder private (10 min)

The publisher reads your videos through a **service account**: a robot Google identity that sees only what you share with it. After this step nobody else can open the folder, even with a link.

1. In the same Cloud project, go to **IAM & Admin → Service Accounts → Create service account**.
   - Name: `gurukul-drive-reader`. Skip the optional "grant access" steps. It needs no project roles.
2. Open the new account and copy its email. It looks like `gurukul-drive-reader@<project-id>.iam.gserviceaccount.com`.
3. Open the account's **Keys** tab → **Add key → Create new key → JSON**. A `.json` file downloads.
4. GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `GOOGLE_SA_KEY`
   - Value: open the JSON file in Notepad, select all, copy, and paste the whole text.
   - Then delete the downloaded JSON file from your computer.
5. In Google Drive, on your phone if Drive is blocked on the work laptop, open the folder **theAIgurukul - Kiro Episode Videos**:
   - **Share** → add the service account email as **Viewer**. Turn off "Notify people".
   - **General access** → **Restricted**.
6. Tell Claude "check the Drive folder is private". It will confirm that no file in the folder is still shared "anyone with the link". Files shared on their own keep that sharing until it's switched off, and Claude can list any that still have it.

*If Google says "Service account key creation is disabled":* that policy applies only to company-managed Google accounts. A personal Gmail project allows keys.

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
| `secret YT_REFRESH_HI not set` (or `_EN`) in the run summary | Add the secret (step 3.8) |
| Google `invalid_grant` | The app was still in Testing, so the token expired after 7 days. Publish it (3.5) and run `google_auth.py` again |
| YouTube `uploadLimitExceeded` / `quotaExceeded` | Wait a day. The default quota is plenty for 2 videos a day |
| Instagram `Media URL not reachable` | Pages isn't enabled (step 2). The post retries next run |
| `not an MP4 (is the folder shared with the service account?)` | Share the Drive folder with the service account email as Viewer (step 3b.5) |
| `Service account sign-in failed` | `GOOGLE_SA_KEY` must hold the whole JSON file, from `{` to `}` |
| Instagram token error / code 190 | Generate a new token (5.3) and replace `IG_TOKEN_EN` or `IG_TOKEN_HI` |
| Everything shows `missed` | The workflow was disabled or never ran. Check the Actions tab |

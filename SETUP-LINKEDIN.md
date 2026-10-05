# LinkedIn publishing: setup

What you get: the publisher posts to the **theAIgurukul LinkedIn Page** on weekdays at 09:15 IST: reels as native video, "carousels" as swipeable PDFs, and plain text posts, each with the official docs link as the first comment. It uses only LinkedIn's official API, runs on GitHub Actions (free), and costs nothing.

LinkedIn has no scheduling API, so the publisher posts at the time (checked every 20 minutes, weekdays 07:30 to 18:30 IST) instead of uploading ahead like YouTube.

## Part A: create the LinkedIn app (about 20 minutes, on your PC)

1. Open <https://www.linkedin.com/developers/apps> and click **Create app**.
   - App name: `theAIgurukul Publisher`
   - LinkedIn Page: pick **theAIgurukul** (you must be a Super admin of the Page)
   - Privacy policy URL: `https://devrahulg.github.io/gurukul-publisher/privacy.html`
   - Logo: your theAIgurukul logo. Tick the legal box and create.
   - Make this a **new app that has no other products added**. LinkedIn only gives Community Management API access to a fresh app.
2. On the app's **Settings** tab click **Verify** next to the Page. It gives you a link; open it as the Page's Super admin and approve.
3. **Products** tab: find **Community Management API** and click **Request access**. In the form say, in your own words, that you publish your own Page's content (videos, documents and text) and read its own post statistics, for one Page you own, and no third-party data. This gives the *Development tier*, which is enough for one post a day. If LinkedIn later asks for a screencast for the Standard tier, you only need it if you hit rate limits.
   - Approval time is not published; plan for days. Do parts B and E while you wait.
4. **Auth** tab: copy the **Client ID** and **Primary Client Secret**. Under **Authorized redirect URLs** add exactly `http://localhost:8765/callback`.

If approval is refused or takes too long, there is a fallback that works the same day: add the **Share on LinkedIn** product (self-serve, instant) to a *separate* app, and use `--personal` in Part C. Posts then go out from your own profile instead of the Page.

## Part B: install the code (5 minutes)

The files are already in your repo folder. Then, in the repo folder:

```
python tools/install_linkedin.py
pip install pytest
python -m pytest tests/test_linkedin.py
python -m publisher validate
git add -A
git commit -m "Add LinkedIn publishing"
git push
```

`install_linkedin.py` also copies the two LinkedIn workflow files from the `_workflows` folder into `.github/workflows`. It backs up the two files it edits (`*.pre-linkedin.bak`) and stops without changing anything if a file is not what it expects. `validate` should end with `0 with problems` and show `account li_page linkedin secret LINKEDIN_TOKEN not set`; that is correct until Part C.

## Part C: connect (after LinkedIn approves the app)

```
python tools/linkedin_auth.py --client-id YOUR_CLIENT_ID
```

Paste the Client Secret when asked (hidden). Your browser opens; sign in as the Page's Super admin and click **Allow**. The script lists your Pages, you pick theAIgurukul, and it prints the values. Add them under GitHub > repo > Settings > Secrets and variables > Actions > **New repository secret**:

| Secret | Value |
|---|---|
| `LINKEDIN_TOKEN` | the access token printed (valid about 60 days) |
| `LINKEDIN_AUTHOR` | e.g. `urn:li:organization:12345678` |
| `LINKEDIN_CLIENT_ID` | your Client ID |
| `LINKEDIN_CLIENT_SECRET` | your Client Secret |

`GOOGLE_SA_KEY` and `GH_ADMIN_TOKEN` are already set from the Instagram and YouTube setup; the LinkedIn videos come from the same private Drive folder.

If the script cannot list your Pages, find the number in the Page admin URL (`linkedin.com/company/NUMBER/admin`) and add `--org-id NUMBER`.

## Part D: test with one real post

```
python tools/linkedin_test_post.py
git add queue
git commit -m "linkedin test"
git push
```

Then GitHub > Actions > **linkedin** > **Run workflow**. Within a minute the Page shows "Testing the theAIgurukul publisher". Open the queue file in the repo: `status` should be `published` with a `url`. Delete the test post on LinkedIn.

## Part E: queue the real plan

Pick the Monday you want to start (it must be after Part D works), then:

```
python tools/add_linkedin_queue.py --start 2026-10-19            # preview
python tools/add_linkedin_queue.py --start 2026-10-19 --apply    # write 50 queue files
python -m publisher validate
git add -A
git commit -m "Queue LinkedIn posts"
git push
```

Do this only **after** Part C: a queued post whose time passes by more than 3 hours is marked `missed`, even if LinkedIn was not connected yet.

The plan alternates two weeks: video (Kiro), carousel (Claude), video (Claude), text (Kiro), carousel (Kiro), then Kiro and Claude swapped. 50 posts over 10 weeks, each reel used once. The 20 carousel PDFs are in `carousels/`. Change time with `--time 12:30` or the length with `--weeks`.

## Keeping it running

- **Token renewal, about every 55 days.** The daily `linkedin-health` run fails and GitHub emails you 10 days before the token expires. Then run `python tools/linkedin_auth.py --client-id ...` again and replace the `LINKEDIN_TOKEN` secret. Put a calendar reminder 50 days after each renewal as well. (If LinkedIn ever gives your app a refresh token, the script prints it; add it as `LINKEDIN_REFRESH_TOKEN` and renewal becomes automatic.)
- **A post that fails** shows `failed` with `last_error` in its queue file; fix the cause and set `status` back to `queued` (and `attempts` to 0) within 3 hours, or it becomes `missed`.
- **If LinkedIn refuses the token**, the run fails without using up your posts' retries, so after you replace the token they go out on the next run if still within 3 hours.
- **Pause**: set a post's `status` to `cancelled`. Do not just switch the account off, because the existing runner would mark the queued posts as missed after 3 hours.
- **Numbers**: `stats/linkedin.json` is written daily (followers, impressions, clicks, reactions, comments, shares per post) when your app has the read scope.
- **Timing settings** (optional, in `config/settings.json`): `linkedin_due_window_minutes` (default 15), `linkedin_max_late_minutes` (default 180), `linkedin_max_per_run` (default 3).

## Notes

- LinkedIn video: MP4, 75 KB to 500 MB. Your 9:16 reels work; LinkedIn shows them in a vertical player.
- Posts are always PUBLIC on the Page's feed. LinkedIn rejects an exact duplicate of a recent post; the publisher never re-posts after an uncertain failure, it checks the Page's latest posts first and stops if it cannot verify.
- Official docs links are posted as the first comment, which tends to reach further than a link in the post body.
- Do not use browser automation or scraping on LinkedIn; the pipeline deliberately uses only the official API.

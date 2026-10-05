"""One-time helper: get a LinkedIn access token for the publisher.

Run it on your own computer (standard Python 3, no packages needed):

    python tools/linkedin_auth.py --client-id <CLIENT_ID>              (posting as the Page)
    python tools/linkedin_auth.py --client-id <CLIENT_ID> --personal   (posting as yourself)

You are asked for the Client Secret (typing is hidden). A browser opens; sign in as the
person who is a Super admin of the theAIgurukul Page and click Allow. The script then
prints the two values to store as GitHub secrets:

    LINKEDIN_TOKEN    the access token (valid 60 days; run this script again to renew)
    LINKEDIN_AUTHOR   who posts, e.g. urn:li:organization:12345678

Your Client ID must have http://localhost:8765/callback listed under "Authorized redirect
URLs" in the LinkedIn Developer Portal (Auth tab). Everything is printed only to your own
terminal; nothing is sent anywhere except LinkedIn.
"""
from __future__ import annotations

import argparse
import getpass
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

AUTH = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
API = "https://api.linkedin.com"
VERSION = "202609"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/callback"
ORG_SCOPES = "w_organization_social r_organization_social rw_organization_admin"
PERSONAL_SCOPES = "openid profile w_member_social"


def call(url: str, token: str | None = None, data: dict | None = None, rest: bool = True) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if rest:
        headers.update({"Linkedin-Version": VERSION, "X-Restli-Protocol-Version": "2.0.0"})
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None


def wait_for_code(state: str) -> str:
    box: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            box["q"] = {k: v[0] for k, v in q.items()}
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h3>You can close this tab and go back to the terminal.</h3>")

        def log_message(self, *a):  # silence
            pass

    server = http.server.HTTPServer(("localhost", PORT), Handler)
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    t.join(timeout=300)
    server.server_close()
    q = box.get("q")
    if not q:
        sys.exit("No answer from the browser within 5 minutes. Run the script again.")
    if q.get("state") != state:
        sys.exit("Security check failed (state mismatch). Run the script again.")
    if "error" in q:
        sys.exit(f"LinkedIn said: {q.get('error')}: {q.get('error_description', '')}")
    return q["code"]


def find_pages(token: str) -> list[tuple[str, str]]:
    out = []
    url = (f"{API}/rest/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED&count=50")
    for el in call(url, token).get("elements", []):
        urn = el.get("organization")
        if not urn:
            continue
        name = ""
        try:
            org = call(f"{API}/rest/organizations/{urn.split(':')[-1]}", token)
            name = org.get("localizedName", "")
        except RuntimeError:
            pass
        out.append((urn, name))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--personal", action="store_true", help="post as your own profile instead of the Page")
    ap.add_argument("--org-id", help="Page number, if the lookup of your Pages does not work")
    ap.add_argument("--scopes", help="override the scopes")
    a = ap.parse_args()
    secret = os.environ.get("LINKEDIN_CLIENT_SECRET") or getpass.getpass(
        "Paste the Client Secret (hidden, press Enter): ").strip()
    if not secret:
        sys.exit("No client secret entered.")
    scopes = a.scopes or (PERSONAL_SCOPES if a.personal else ORG_SCOPES)
    state = secrets.token_urlsafe(16)
    url = AUTH + "?" + urllib.parse.urlencode({"response_type": "code", "client_id": a.client_id,
                                               "redirect_uri": REDIRECT, "state": state, "scope": scopes})
    print("Opening your browser. If it does not open, paste this link into it:\n" + url + "\n")
    webbrowser.open(url)
    code = wait_for_code(state)
    tok = call(TOKEN, data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                            "client_id": a.client_id, "client_secret": secret}, rest=False)
    access = tok["access_token"]
    days = round(int(tok.get("expires_in", 0)) / 86400)
    print(f"Connected. Token valid for about {days} days. Scope granted: {tok.get('scope')}\n")

    if a.personal:
        me = call(f"{API}/v2/userinfo", access, rest=False)
        author = f"urn:li:person:{me['sub']}"
        print(f"Posting as: {me.get('name', '')}")
    elif a.org_id:
        author = f"urn:li:organization:{a.org_id}"
    else:
        try:
            pages = find_pages(access)
        except RuntimeError as exc:
            sys.exit(f"Could not list your Pages ({exc}).\nFind the number in your Page admin URL "
                     "(linkedin.com/company/<NUMBER>/admin) and run again with --org-id <NUMBER>.")
        if not pages:
            sys.exit("You are not an administrator of any Page with this login. Sign in as the Page's Super admin.")
        for i, (urn, name) in enumerate(pages, 1):
            print(f"  {i}. {name or '(name hidden)'}   {urn}")
        pick = 1 if len(pages) == 1 else int(input("Which Page should the publisher post as? Number: "))
        author = pages[pick - 1][0]

    print("\nAdd these as GitHub repository secrets (Settings > Secrets and variables > Actions):\n")
    print(f"  LINKEDIN_AUTHOR = {author}")
    print(f"  LINKEDIN_TOKEN  = {access}")
    print(f"  LINKEDIN_CLIENT_ID     = {a.client_id}   (for the daily token check)")
    print("  LINKEDIN_CLIENT_SECRET = (the secret you just typed)")
    if tok.get("refresh_token"):
        print(f"  LINKEDIN_REFRESH_TOKEN = {tok['refresh_token']}   (LinkedIn gave your app a refresh token)")
    print(f"\nThe token stops working in about {days} days. The daily check emails you 10 days before.")


if __name__ == "__main__":
    main()

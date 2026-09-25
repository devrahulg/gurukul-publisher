"""One-time helper: get a YouTube refresh token for ONE channel.

Run it on your own computer (standard Python 3, no packages needed):

    python tools/google_auth.py path\\to\\client_secret.json

A browser opens. Sign in, pick the channel (Hindi or English) when Google asks,
and allow access. The script then prints which channel it connected and the
refresh token. Paste that token into the GitHub secret named on screen
(YT_REFRESH_HI or YT_REFRESH_EN). Run it once per channel.

The token is printed only to your own terminal. It is never sent anywhere else.
"""
from __future__ import annotations

import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
# Drive access is not requested: the episode videos are shared "anyone with the link",
# so the publisher downloads them without a Google login. That keeps the app on
# YouTube's "sensitive" scopes only (Drive read access is a "restricted" scope, which
# needs a paid security assessment to verify).
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"


def load_client(path: str) -> tuple[str, str]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    block = data.get("installed") or data.get("web") or {}
    if not block.get("client_id"):
        sys.exit("That file is not an OAuth client JSON. Download it from Google Cloud > Credentials.")
    return block["client_id"], block["client_secret"]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    client_id, client_secret = load_client(sys.argv[1])
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Done. You can close this tab and go back to the terminal.</h2>".encode())

        def log_message(self, *args):  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent select_account",
        "state": state, "include_granted_scopes": "true",
    })
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print("Opening your browser. If it does not open, paste this address into it:\n")
    print(url + "\n")
    webbrowser.open(url)
    thread.join(timeout=600)
    server.server_close()

    if result.get("state") != state or "code" not in result:
        sys.exit(f"Sign-in did not finish: {result.get('error', 'no response')}")

    body = urllib.parse.urlencode({
        "code": result["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN, data=body)) as resp:
        tokens = json.load(resp)
    refresh = tokens.get("refresh_token")
    if not refresh:
        sys.exit("Google returned no refresh token. Remove the app's access at "
                 "myaccount.google.com/permissions and run this again.")

    req = urllib.request.Request(
        "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
        headers={"Authorization": f"Bearer {tokens['access_token']}"})
    with urllib.request.urlopen(req) as resp:
        items = json.load(resp).get("items", [])
    channel = items[0]["snippet"]["title"] if items else "(no channel on this login)"

    print("=" * 70)
    print(f"Connected channel: {channel}")
    print("Save the value below as a GitHub secret:")
    print("  Hindi channel   -> YT_REFRESH_HI")
    print("  English channel -> YT_REFRESH_EN")
    print("=" * 70)
    print(refresh)
    print("=" * 70)


if __name__ == "__main__":
    main()

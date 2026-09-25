"""Write a refreshed token back into this repository's Actions secrets.

Needs GH_ADMIN_TOKEN: a fine-grained personal access token limited to this
repository with "Secrets: Read and write" permission.
"""
from __future__ import annotations

import base64
import os

from nacl import encoding, public

from .common import ApiError, http, secret

API = "https://api.github.com"


def put_secret(name: str, value: str) -> None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise ApiError("GITHUB_REPOSITORY is not set")
    headers = {"Authorization": f"Bearer {secret('GH_ADMIN_TOKEN')}",
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    key = http("GET", f"{API}/repos/{repo}/actions/secrets/public-key", headers=headers,
               what="GitHub secrets key").json()
    sealed = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    encrypted = base64.b64encode(sealed.encrypt(value.encode())).decode()
    http("PUT", f"{API}/repos/{repo}/actions/secrets/{name}", headers=headers, ok=(201, 204),
         json={"encrypted_value": encrypted, "key_id": key["key_id"]}, what="GitHub secret update")

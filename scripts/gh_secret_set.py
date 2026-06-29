#!/usr/bin/env python3
"""设置/更新 GitHub Actions repo secret（用 fine-grained PAT + libsodium 加密）

Usage:
    python3 scripts/gh_secret_set.py OWNER/REPO SECRET_NAME "value"
    python3 scripts/gh_secret_set.py OWNER/REPO SECRET_NAME --file path/to/file
    GH_PAT env var 必须设置
"""
import base64
import json
import os
import sys

import requests
from nacl import encoding, public


def encrypt(public_key_b64: str, secret_value: str) -> str:
    public_key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    repo, name = sys.argv[1], sys.argv[2]
    if sys.argv[3] == "--file":
        value = open(sys.argv[4], encoding="utf-8").read()
    else:
        value = sys.argv[3]

    token = os.environ["GH_PAT"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    pk_resp = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=headers, timeout=15,
    )
    pk_resp.raise_for_status()
    pk = pk_resp.json()

    encrypted_value = encrypt(pk["key"], value)

    put_resp = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
        headers=headers,
        json={"encrypted_value": encrypted_value, "key_id": pk["key_id"]},
        timeout=15,
    )
    put_resp.raise_for_status()
    print(f"✓ secret {name} set (status {put_resp.status_code})")


if __name__ == "__main__":
    main()

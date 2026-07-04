#!/usr/bin/env python3
"""Minimal cloud smoke test for WeChat access.

Checks only:
- token.json is present and not expired according to local metadata
- one configured WeChat account can return at least one article metadata item

It intentionally does not fetch article content, call AI, write state, or send
notifications. Use this before any full workflow test.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from auth import is_token_valid, load_token
import fetcher


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal WeChat fetch smoke test.")
    parser.add_argument("--account", default="晚点再听LaterCast", help="公众号名称")
    parser.add_argument("--hours", type=int, default=24 * 14, help="Lookback window for one article")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    token_data = load_token()
    if not token_data:
        print("FAIL: token.json missing or unreadable")
        return 1
    expiry = token_data.get("expiry_timestamp", 0)
    expiry_text = datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat() if expiry else "unknown"
    if not is_token_valid(token_data):
        print(f"FAIL: token metadata expired or near expiry. expiry_utc={expiry_text}")
        return 1

    fakeid = fetcher.get_fakeid(args.account)
    if not fakeid:
        print(f"FAIL: cannot resolve fakeid for account={args.account}")
        return 1

    articles = fetcher.get_recent_articles(fakeid, args.account, hours=args.hours)
    if not articles:
        print(f"FAIL: no article metadata returned for account={args.account} within {args.hours}h")
        return 1

    first = articles[0]
    print(json.dumps({
        "ok": True,
        "account": args.account,
        "article_count": len(articles),
        "sample_title": first.get("title", ""),
        "sample_url_prefix": (first.get("url", "")[:80]),
        "token_expiry_utc": expiry_text,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

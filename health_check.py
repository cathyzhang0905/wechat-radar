#!/usr/bin/env python3
"""Preflight verification for wechat-radar.

Default mode checks local configuration without expensive external calls.
Use --wechat-smoke to verify that the current token can fetch article metadata
from one WeChat account. It does not fetch article bodies, call AI, or send.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from auth import is_token_valid, load_token
import fetcher


class CheckReport:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "", critical: bool = True) -> None:
        status = "pass" if ok else "fail"
        self.rows.append({"name": name, "status": status, "detail": detail, "critical": critical})
        marker = "PASS" if ok else "FAIL"
        crit = "critical" if critical else "optional"
        print(f"[{marker}] {name} ({crit}) {detail}".rstrip())

    def exit_code(self) -> int:
        return 1 if any(r["status"] == "fail" and r["critical"] for r in self.rows) else 0


def _load_config(report: CheckReport) -> dict:
    config_path = ROOT / "config.yaml.local"
    if not config_path.exists():
        config_path = ROOT / "config.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        report.add("config_load", False, str(exc))
        return {}
    accounts = config.get("accounts") or []
    report.add("config_load", True, f"{config_path.name}, accounts={len(accounts)}")
    report.add("config_accounts", bool(accounts), f"accounts={len(accounts)}")
    scoring = config.get("scoring") or {}
    report.add("config_scoring", bool(scoring.get("dimensions")), "scoring dimensions present")
    return config


def _check_env(report: CheckReport) -> None:
    provider = (os.getenv("AI_PROVIDER") or "").lower()
    ai_key = os.getenv("AI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    report.add("env_ai_provider", bool(provider), f"AI_PROVIDER={provider or 'missing'}")
    report.add("env_ai_key", bool(ai_key), "AI key present" if ai_key else "AI key missing")

    channels = {
        "feishu": bool(os.getenv("FEISHU_WEBHOOK")),
        "email": bool(os.getenv("EMAIL_USER") and os.getenv("EMAIL_PASSWORD") and os.getenv("EMAIL_TO")),
        "dingtalk": bool(os.getenv("DINGTALK_WEBHOOK")),
        "wecom": bool(os.getenv("WECOM_WEBHOOK")),
        "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
        "bark": bool(os.getenv("BARK_URL")),
        "serverchan": bool(os.getenv("SERVERCHAN_KEY")),
        "pushplus": bool(os.getenv("PUSHPLUS_TOKEN")),
    }
    enabled = [name for name, ok in channels.items() if ok]
    report.add("env_push_channel", bool(enabled), f"enabled={','.join(enabled) if enabled else 'none'}")
    report.add("env_email_config", channels["email"], "EMAIL_USER/PASSWORD/TO present", critical=False)


def _check_token(report: CheckReport) -> dict | None:
    token = load_token()
    if not token:
        report.add("wechat_token_file", False, "token.json missing or invalid JSON")
        return None
    expiry = token.get("expiry_timestamp", 0)
    expiry_text = datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat() if expiry else "unknown"
    valid = is_token_valid(token)
    report.add("wechat_token_valid", valid, f"expiry_utc={expiry_text}")
    return token if valid else None


def _check_state_files(report: CheckReport) -> None:
    writable = True
    try:
        probe = ROOT / ".health_check_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        writable = False
        detail = str(exc)
    else:
        detail = "project directory writable"
    report.add("state_store", writable, detail)


def _check_newsletter_generate(report: CheckReport) -> None:
    try:
        from notifier import _build_feishu_card

        card = _build_feishu_card([
            {
                "title": "health check sample",
                "account_name": "sample",
                "url": "https://example.com",
                "summary": "sample summary",
                "reason": "sample reason",
                "tags": ["sample"],
                "category": "其他",
                "scores": {"relevance": 5},
                "final_score": 5,
            }
        ])
        ok = card.get("msg_type") == "interactive" and bool(card.get("card"))
        report.add("newsletter_generate", ok, "sample Feishu card built without AI")
    except Exception as exc:
        report.add("newsletter_generate", False, str(exc))


def _check_wechat_smoke(report: CheckReport, config: dict, account: str | None, hours: int) -> None:
    target = account or ((config.get("accounts") or [""])[0])
    if not target:
        report.add("wechat_source_access", False, "no account available")
        return
    try:
        fakeid = fetcher.get_fakeid(target)
        if not fakeid:
            report.add("wechat_source_access", False, f"cannot resolve fakeid for {target}")
            return
        articles = fetcher.get_recent_articles(fakeid, target, hours=hours)
        ok = bool(articles)
        detail = f"account={target}, articles={len(articles)}"
        if articles:
            detail += f", sample={articles[0].get('title', '')[:40]}"
        report.add("wechat_source_access", ok, detail)
    except Exception as exc:
        report.add("wechat_source_access", False, str(exc))


def _alert_token_status() -> None:
    """发送 token 过期/临期提醒（best-effort，不影响健康检查退出码）。

    CI 里 main.py 只在健康检查通过后才会运行，因此提醒必须由这里发出，
    否则 token 过期时用户只能看到 GitHub 的通用失败通知。
    临期阈值可用环境变量 TOKEN_WARN_HOURS 调整（默认 24 小时）。
    """
    try:
        from notifier import notify_token_expired, notify_token_expiring_soon
        token_data = load_token()
        if not is_token_valid(token_data):
            notify_token_expired()
            return
        expiry = token_data.get("expiry_timestamp", 0)
        remaining_hours = (expiry - time.time()) / 3600
        warn_hours = float(os.getenv("TOKEN_WARN_HOURS", "24"))
        if remaining_hours < warn_hours:
            notify_token_expiring_soon(remaining_hours)
    except Exception as exc:
        print(f"[warn] token alert skipped: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify wechat-radar health.")
    parser.add_argument("--wechat-smoke", action="store_true", help="Fetch one account's article metadata from WeChat")
    parser.add_argument("--account", default="", help="Account to use for --wechat-smoke")
    parser.add_argument("--hours", type=int, default=24 * 14, help="Lookback for --wechat-smoke")
    parser.add_argument("--json", action="store_true", help="Also print machine-readable JSON")
    args = parser.parse_args()

    report = CheckReport()
    config = _load_config(report)
    _check_env(report)
    token = _check_token(report)
    _alert_token_status()
    _check_state_files(report)
    _check_newsletter_generate(report)

    if args.wechat_smoke:
        if token:
            _check_wechat_smoke(report, config, args.account or None, args.hours)
        else:
            report.add("wechat_source_access", False, "skipped because token is invalid")

    if args.json:
        print(json.dumps({"checks": report.rows}, ensure_ascii=False, indent=2))
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())

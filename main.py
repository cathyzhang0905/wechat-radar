"""
main.py - 微信公众号 AI 筛选推送系统入口

用法:
  python3 main.py              # 正常运行
  python3 main.py --login      # 手动扫码登录/续期
  python3 main.py --test       # 测试模式：每个公众号取1篇
  python3 main.py --dry-run    # 只拉取和筛选，不推送
  python3 main.py --setup-cron # 根据 config.yaml 自动配置 crontab
  python3 main.py --remove-cron # 移除本项目的 crontab
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 加载 .env
_script_dir = Path(__file__).parent
load_dotenv(_script_dir / ".env")

from auth import load_token, is_token_valid, login
from fetcher import get_fakeid, get_recent_articles, get_article_content, TokenExpiredError
from filter import filter_article, generate_intro
from prefilter import should_skip
from dedup import deduplicate
from notifier import (
    send_feishu, send_dingtalk, send_wecom,
    send_email, send_telegram, send_bark,
    send_serverchan, send_pushplus,
)

# ──────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

STATE_FILE = _script_dir / "state.json"
CONFIG_LOCAL = _script_dir / "config.yaml.local"
CONFIG_FILE = CONFIG_LOCAL if CONFIG_LOCAL.exists() else _script_dir / "config.yaml"
LOG_DIR = _script_dir / "logs"


# ──────────────────────────────────────────────
# State 管理
# ──────────────────────────────────────────────

def load_state() -> set:
    """加载已处理文章 URL 集合"""
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("processed_urls", []))
    except Exception as e:
        logger.warning(f"Cannot load state: {e}")
        return set()


def save_state(processed_urls: set):
    """保存已处理文章 URL（保留最近 30 天的记录，防止文件无限增长）"""
    data = {"processed_urls": list(processed_urls), "updated_at": _now_cst()}
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_scoring_log(all_results: list[dict]):
    """保存评分日志（含所有文章，包括未推荐的）"""
    LOG_DIR.mkdir(exist_ok=True)
    cst = timezone(timedelta(hours=8))
    date_str = datetime.now(cst).strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"scoring_log_{date_str}.json"

    log_data = {
        "date": date_str,
        "total_articles": len(all_results),
        "articles": all_results,
    }
    log_file.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Scoring log saved: {log_file}")


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

def run(test_mode: bool = False, dry_run: bool = False):
    logger.info(f"Starting wechat-digest [test={test_mode}, dry_run={dry_run}]")

    # 0. 检查 token
    token_data = load_token()
    if not is_token_valid(token_data):
        logger.warning("Token missing or expired. Sending Feishu notification...")
        _notify_token_expired()
        logger.error("请运行 python3 main.py --login 重新扫码登录")
        sys.exit(1)

    # 1. 读配置
    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    accounts = config.get("accounts", [])
    scoring_config = config.get("scoring", {})
    min_score = scoring_config.get("min_score", 5)
    top_n = scoring_config.get("top_n", 10)
    fetch_hours = config.get("fetch", {}).get("hours", 24)

    if not accounts:
        logger.error("No accounts in config.yaml")
        sys.exit(1)

    logger.info(f"Accounts: {len(accounts)} | fetch_hours={fetch_hours} | min_score={min_score} | top_n={top_n}")

    # 2. 加载已处理记录
    processed_urls = load_state()
    logger.info(f"Already processed: {len(processed_urls)} articles")

    # 3. 拉取文章
    all_articles = []
    newly_processed = set()

    for account_name in accounts:
        logger.info(f"\n── Fetching: {account_name} ──")

        fakeid = get_fakeid(account_name)
        if not fakeid:
            logger.warning(f"Skipping {account_name}: cannot find fakeid")
            continue

        articles = get_recent_articles(fakeid, account_name, hours=fetch_hours)

        if test_mode:
            articles = articles[:1]

        for art in articles:
            url = art["url"]
            if not url:
                continue

            if url in processed_urls:
                logger.info(f"  Skip (already processed): {art['title'][:40]}")
                continue

            # Step 0: 规则预过滤（标题级）
            if should_skip(art["title"], ""):
                logger.info(f"  Skip (prefilter/title): {art['title'][:40]}")
                newly_processed.add(url)
                continue

            logger.info(f"  Fetching content: {art['title'][:50]}")
            try:
                content_data = get_article_content(url)
            except TokenExpiredError:
                _notify_token_expired()
                logger.error("Token expired mid-run. 请运行 python3 main.py --login 重新扫码")
                sys.exit(1)

            text = content_data.get("text", "")
            images = content_data.get("images", [])

            # Step 0: 规则预过滤（标题+正文）
            if should_skip(art["title"], text):
                logger.info(f"  Skip (prefilter/content): {art['title'][:40]}")
                newly_processed.add(url)
                continue

            all_articles.append({
                **art,
                "text": text,
                "images": images,
                "cover": art.get("cover") or (images[0] if images else ""),
            })
            newly_processed.add(url)

    logger.info(f"\nAfter prefilter: {len(all_articles)} articles")

    # Step 1: 跨源去重
    all_articles = deduplicate(all_articles)
    logger.info(f"After dedup: {len(all_articles)} articles")

    # Step 2: AI 评分
    all_results = []  # 评分日志（含所有文章）

    for art in all_articles:
        # Bug 4: 正文为空或极短时跳过 AI 评分
        if len((art.get("text") or "").strip()) < 50:
            logger.info(f"  Skip AI scoring (content too short): {art['title'][:50]}")
            all_results.append({
                "title": art["title"],
                "account_name": art.get("account_name", ""),
                "url": art.get("url", ""),
                "is_ad": False,
                "scores": {"relevance": 0, "depth": 0, "info_density": 0,
                            "actionability": 0},
                "summary": "正文抓取失败",
                "reason": "",
                "tags": [],
                "category": "其他",
                "final_score": 0.0,
            })
            continue

        logger.info(f"  AI scoring: {art['title'][:50]}")
        result = filter_article(art["title"], art["text"], config)

        final_score = result["final_score"]
        logger.info(
            f"  → score={final_score:.1f} | ad={result['is_ad']} | "
            f"cat={result['category']} | {result.get('reason', '')[:40]}"
        )

        all_results.append({
            "title": art["title"],
            "account_name": art.get("account_name", ""),
            "url": art.get("url", ""),
            **result,
        })

    # Step 3: 排序 + Top-N
    # 过滤广告和低分文章，按综合分降序
    qualified = [
        r for r in all_results
        if not r["is_ad"] and r["final_score"] >= min_score
    ]
    qualified.sort(key=lambda r: r["final_score"], reverse=True)
    recommended = qualified[:top_n]

    logger.info(
        f"\n── Results: {len(recommended)}/{len(all_results)} recommended "
        f"(min_score={min_score}, top_n={top_n}) ──"
    )

    # 构建推送数据
    push_articles = []
    for r in recommended:
        # 找到对应原始文章获取 images/cover
        original = next((a for a in all_articles if a.get("url") == r.get("url")), {})
        push_articles.append({
            "title": r["title"],
            "account_name": r["account_name"],
            "url": r["url"],
            "summary": r["summary"],
            "reason": r["reason"],
            "tags": r.get("tags", []),
            "category": r.get("category", ""),
            "scores": r.get("scores", {}),
            "final_score": r["final_score"],
            "images": original.get("images", []),
            "cover": original.get("cover", ""),
        })

    # Step 4: 生成开场白
    intro = ""
    if push_articles:
        logger.info("Generating newsletter intro...")
        intro = generate_intro(push_articles)
        if intro:
            logger.info(f"Intro: {intro[:80]}...")

    # Step 5: 推送
    if not dry_run and push_articles:
        send_feishu(push_articles, intro=intro)
        send_dingtalk(push_articles, intro=intro)
        send_wecom(push_articles, intro=intro)
        send_email(push_articles, intro=intro)
        send_telegram(push_articles, intro=intro)
        send_bark(push_articles, intro=intro)
        send_serverchan(push_articles, intro=intro)
        send_pushplus(push_articles, intro=intro)
    elif dry_run:
        logger.info("[dry-run] Skipping push")
        if intro:
            logger.info(f"  Intro: {intro}")
        for art in push_articles:
            logger.info(
                f"  ✓ [{art['category']}] {art['account_name']} | "
                f"{art['title'][:50]} (score={art['final_score']:.1f})"
            )
            logger.info(f"    {art['summary'][:80]}")
            logger.info(f"    Scores: {art['scores']}")
    else:
        logger.info("No recommended articles today")

    # Step 6: 保存评分日志
    if all_results:
        save_scoring_log(all_results)

    # 更新 state（测试模式不写）
    if not test_mode and not dry_run and newly_processed:
        save_state(processed_urls | newly_processed)
        logger.info(f"State updated: +{len(newly_processed)} articles")

    logger.info("Done.")
    return push_articles


def _now_cst() -> str:
    cst = timezone(timedelta(hours=8))
    return datetime.now(cst).isoformat()


def _notify_token_expired():
    """发飞书通知提示重新登录"""
    webhook = os.environ.get("FEISHU_WEBHOOK", "")
    if not webhook:
        return
    try:
        import requests as _req
        msg = {
            "msg_type": "text",
            "content": {"text": "⚠️ 微信登录已过期，请运行 python3 main.py --login 重新扫码登录"},
        }
        _req.post(webhook, json=msg, timeout=10)
    except Exception as e:
        logger.warning(f"Failed to send Feishu expiry notice: {e}")


# ──────────────────────────────────────────────
# Cron 管理
# ──────────────────────────────────────────────

_CRON_TAG = "# wechat-radar"


def setup_cron():
    """根据 config.yaml 中的 schedule.cron 自动配置 crontab"""
    import subprocess

    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    cron_list = config.get("schedule", {}).get("cron", [])

    if not cron_list:
        logger.error("config.yaml 中没有配置 schedule.cron")
        sys.exit(1)

    python_path = sys.executable
    project_dir = str(_script_dir)
    log_file = f"/tmp/wechat-radar.log"

    # 读取现有 crontab（排除本项目的行）
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing = result.stdout if result.returncode == 0 else ""
    except Exception:
        existing = ""

    other_lines = [
        line for line in existing.splitlines()
        if _CRON_TAG not in line and line.strip()
    ]

    # 生成新的 cron 行
    new_lines = []
    for expr in cron_list:
        expr = expr.strip()
        cmd = f"{expr} cd {project_dir} && {python_path} main.py >> {log_file} 2>&1 {_CRON_TAG}"
        new_lines.append(cmd)

    all_lines = other_lines + new_lines
    crontab_content = "\n".join(all_lines) + "\n"

    # 写入 crontab
    proc = subprocess.run(
        ["crontab", "-"], input=crontab_content, text=True, capture_output=True
    )
    if proc.returncode == 0:
        logger.info("Crontab 已更新：")
        for line in new_lines:
            logger.info(f"  {line}")
    else:
        logger.error(f"Crontab 写入失败: {proc.stderr}")
        sys.exit(1)


def remove_cron():
    """移除本项目在 crontab 中的所有条目"""
    import subprocess

    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing = result.stdout if result.returncode == 0 else ""
    except Exception:
        existing = ""

    other_lines = [
        line for line in existing.splitlines()
        if _CRON_TAG not in line and line.strip()
    ]

    if other_lines:
        crontab_content = "\n".join(other_lines) + "\n"
        subprocess.run(["crontab", "-"], input=crontab_content, text=True)
    else:
        subprocess.run(["crontab", "-r"], capture_output=True)

    logger.info("已移除 wechat-radar 的 crontab 条目")


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WeChat Radar - AI 公众号筛选推送")
    parser.add_argument("--login", action="store_true", help="手动扫码登录/续期")
    parser.add_argument("--test", action="store_true", help="测试模式：每个公众号取1篇")
    parser.add_argument("--dry-run", action="store_true", help="只筛选不推送")
    parser.add_argument("--setup-cron", action="store_true", help="根据 config.yaml 自动配置 crontab")
    parser.add_argument("--remove-cron", action="store_true", help="移除本项目的 crontab")
    args = parser.parse_args()

    if args.login:
        success = login()
        sys.exit(0 if success else 1)

    if args.setup_cron:
        setup_cron()
        sys.exit(0)

    if args.remove_cron:
        remove_cron()
        sys.exit(0)

    run(test_mode=args.test, dry_run=args.dry_run)

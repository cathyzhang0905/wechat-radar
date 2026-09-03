"""
notifier.py - 多渠道推送（飞书/钉钉/企业微信/邮件/Telegram/Bark/Server酱/PushPlus）
"""
import logging
import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_DEFAULT_TITLE = "微信公众号日报"
_DEFAULT_FOOTER = "由 wechat-radar 自动生成"


def _brand_title(branding: dict = None) -> str:
    return (branding or {}).get("title", _DEFAULT_TITLE)


def _brand_footer(branding: dict = None) -> str:
    return (branding or {}).get("footer", _DEFAULT_FOOTER)


# ──────────────────────────────────────────────
# 飞书推送
# ──────────────────────────────────────────────

def send_feishu(articles: list[dict], intro: str = "", branding: dict = None) -> bool:
    """发送飞书卡片消息（批量，一条消息汇总所有推荐文章）"""
    webhook = os.getenv("FEISHU_WEBHOOK", "")
    if not webhook:
        logger.warning("FEISHU_WEBHOOK not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send to Feishu")
        return True

    card = _build_feishu_card(articles, intro, branding)
    try:
        resp = requests.post(webhook, json=card, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            logger.info(f"Feishu sent: {len(articles)} articles")
            return True
        else:
            logger.error(f"Feishu API error: {result}")
            return False
    except Exception as e:
        logger.error(f"Feishu send failed: {e}")
        return False


def _build_feishu_card(articles: list[dict], intro: str = "", branding: dict = None) -> dict:
    """构建飞书消息卡片（interactive card 格式）"""
    date_str = _today_str()
    brand = _brand_title(branding)
    elements = []

    # 标题
    elements.append({
        "tag": "markdown",
        "content": f"**📰 {brand} · {date_str}**\n共 {len(articles)} 篇推荐"
    })

    # 开场白
    if intro:
        elements.append({
            "tag": "markdown",
            "content": f"\n{intro}"
        })

    elements.append({"tag": "hr"})

    for i, art in enumerate(articles, 1):
        # 封面图暂不支持（飞书需要先上传获取 img_key，后续实现）

        # 文章内容块
        summary = art.get("summary", "")
        reason = art.get("reason", "")
        account = art.get("account_name", "")
        url = art.get("url", "")
        category = art.get("category", "")
        tags = art.get("tags", [])
        scores = art.get("scores", {})
        final_score = art.get("final_score", 0)

        content_lines = [
            f"**{i}. [{category}] {art['title']}**" if category else f"**{i}. {art['title']}**",
            f"来源：{account}" + (f"　|　综合分：{final_score:.1f}" if final_score else ""),
        ]
        if tags:
            content_lines.append(f"标签：{'、'.join(tags)}")
        if summary:
            content_lines.append(f"\n{summary}")
        if reason:
            content_lines.append(f"\n💡 {reason}")
        if scores:
            score_text = " | ".join(
                f"{k}:{v}" for k, v in scores.items()
            )
            content_lines.append(f"\n<font color='grey'>📊 {score_text}</font>")

        elements.append({
            "tag": "markdown",
            "content": "\n".join(content_lines)
        })

        if url:
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "阅读原文"},
                    "url": url,
                    "type": "default",
                }]
            })

        if i < len(articles):
            elements.append({"tag": "hr"})

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"{brand} · {date_str}"},
                "template": "blue"
            },
            "elements": elements
        }
    }


# ──────────────────────────────────────────────
# 钉钉推送
# ──────────────────────────────────────────────

def send_dingtalk(articles: list[dict], intro: str = "", branding: dict = None) -> bool:
    """发送钉钉机器人 Markdown 消息"""
    webhook = os.getenv("DINGTALK_WEBHOOK", "")
    if not webhook:
        logger.warning("DINGTALK_WEBHOOK not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send to DingTalk")
        return True

    date_str = _today_str()
    brand = _brand_title(branding)
    text = _build_markdown_text(articles, intro, branding)

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"{brand} · {date_str}",
            "text": text
        }
    }

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info(f"DingTalk sent: {len(articles)} articles")
            return True
        else:
            logger.error(f"DingTalk API error: {result}")
            return False
    except Exception as e:
        logger.error(f"DingTalk send failed: {e}")
        return False


# ──────────────────────────────────────────────
# 企业微信推送
# ──────────────────────────────────────────────

def send_wecom(articles: list[dict], intro: str = "", branding: dict = None) -> bool:
    """发送企业微信群机器人 Markdown 消息"""
    webhook = os.getenv("WECOM_WEBHOOK", "")
    if not webhook:
        logger.warning("WECOM_WEBHOOK not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send to WeCom")
        return True

    text = _build_markdown_text(articles, intro, branding)

    payload = {
        "msgtype": "markdown",
        "markdown": {"content": text}
    }

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info(f"WeCom sent: {len(articles)} articles")
            return True
        else:
            logger.error(f"WeCom API error: {result}")
            return False
    except Exception as e:
        logger.error(f"WeCom send failed: {e}")
        return False


# ──────────────────────────────────────────────
# Telegram Bot 推送
# ──────────────────────────────────────────────

def send_telegram(articles: list[dict], intro: str = "", branding: dict = None) -> bool:
    """发送 Telegram Bot 消息"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send to Telegram")
        return True

    text = _build_markdown_text(articles, intro, branding)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("ok"):
            logger.info(f"Telegram sent: {len(articles)} articles")
            return True
        else:
            logger.error(f"Telegram API error: {result}")
            return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# ──────────────────────────────────────────────
# Bark 推送（iOS）
# ──────────────────────────────────────────────

def send_bark(articles: list[dict], intro: str = "", branding: dict = None) -> bool:
    """发送 Bark 推送（iOS 通知）"""
    bark_url = os.getenv("BARK_URL", "")  # e.g. https://api.day.app/yourkey
    if not bark_url:
        logger.warning("BARK_URL not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send to Bark")
        return True

    bark_url = bark_url.rstrip("/")
    date_str = _today_str()
    brand = _brand_title(branding)
    title = f"{brand} · {date_str}"

    # Bark 单条通知，拼摘要
    lines = []
    if intro:
        lines.append(intro)
        lines.append("")
    for i, art in enumerate(articles[:10], 1):
        score = art.get("final_score", 0)
        lines.append(f"{i}. {art['title']}（{score:.1f}分）")
    if len(articles) > 10:
        lines.append(f"...共 {len(articles)} 篇")

    body = "\n".join(lines)

    try:
        resp = requests.post(f"{bark_url}/{title}/{body}", timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 200:
            logger.info(f"Bark sent: {len(articles)} articles")
            return True
        else:
            logger.error(f"Bark API error: {result}")
            return False
    except Exception as e:
        logger.error(f"Bark send failed: {e}")
        return False


# ──────────────────────────────────────────────
# Server酱推送
# ──────────────────────────────────────────────

def send_serverchan(articles: list[dict], intro: str = "", branding: dict = None) -> bool:
    """通过 Server酱（SCT）推送到微信"""
    send_key = os.getenv("SERVERCHAN_KEY", "")
    if not send_key:
        logger.warning("SERVERCHAN_KEY not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send to ServerChan")
        return True

    date_str = _today_str()
    brand = _brand_title(branding)
    title = f"{brand} · {date_str}（{len(articles)} 篇）"
    desp = _build_markdown_text(articles, intro, branding)

    url = f"https://sctapi.ftqq.com/{send_key}.send"
    payload = {"title": title, "desp": desp}

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            logger.info(f"ServerChan sent: {len(articles)} articles")
            return True
        else:
            logger.error(f"ServerChan API error: {result}")
            return False
    except Exception as e:
        logger.error(f"ServerChan send failed: {e}")
        return False


# ──────────────────────────────────────────────
# PushPlus 推送
# ──────────────────────────────────────────────

def send_pushplus(articles: list[dict], intro: str = "", branding: dict = None) -> bool:
    """通过 PushPlus 推送到微信"""
    token = os.getenv("PUSHPLUS_TOKEN", "")
    if not token:
        logger.warning("PUSHPLUS_TOKEN not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send to PushPlus")
        return True

    date_str = _today_str()
    brand = _brand_title(branding)
    title = f"{brand} · {date_str}（{len(articles)} 篇）"
    content = _build_markdown_text(articles, intro, branding)

    url = "https://www.pushplus.plus/send"
    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 200:
            logger.info(f"PushPlus sent: {len(articles)} articles")
            return True
        else:
            logger.error(f"PushPlus API error: {result}")
            return False
    except Exception as e:
        logger.error(f"PushPlus send failed: {e}")
        return False


# ──────────────────────────────────────────────
# 通用 Markdown 文本构建（企业微信/钉钉/Telegram/Server酱/PushPlus 复用）
# ──────────────────────────────────────────────

def _build_markdown_text(articles: list[dict], intro: str = "", branding: dict = None) -> str:
    """构建通用 Markdown 格式的日报文本"""
    date_str = _today_str()
    brand = _brand_title(branding)
    lines = [f"## {brand} · {date_str}", f"共 {len(articles)} 篇推荐", ""]
    if intro:
        lines.append(f"> {intro}")
        lines.append("")
    lines.append("---")
    lines.append("")

    for i, art in enumerate(articles, 1):
        category = art.get("category", "")
        title = art["title"]
        url = art.get("url", "")
        account = art.get("account_name", "")
        summary = art.get("summary", "")
        reason = art.get("reason", "")
        final_score = art.get("final_score", 0)

        prefix = f"**{i}. [{category}]** " if category else f"**{i}.** "
        lines.append(f"{prefix}[{title}]({url})" if url else f"{prefix}{title}")
        lines.append(f"来源：{account}" + (f"　|　综合分：{final_score:.1f}" if final_score else ""))
        if summary:
            lines.append(f"\n{summary}")
        if reason:
            lines.append(f"\n💡 {reason}")
        lines.append("\n---\n")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 邮件推送（通用 SMTP，支持 Gmail / QQ / 163 / Outlook 等）
# ──────────────────────────────────────────────

# 常见邮箱 SMTP 配置（host, port, use_ssl）
_SMTP_PRESETS = {
    "gmail.com":      ("smtp.gmail.com",      465, True),
    "qq.com":         ("smtp.qq.com",          465, True),
    "foxmail.com":    ("smtp.qq.com",          465, True),
    "163.com":        ("smtp.163.com",         465, True),
    "126.com":        ("smtp.126.com",         465, True),
    "yeah.net":       ("smtp.yeah.net",        465, True),
    "outlook.com":    ("smtp-mail.outlook.com", 587, False),
    "hotmail.com":    ("smtp-mail.outlook.com", 587, False),
}


def _get_smtp_config(email: str) -> tuple[str, int, bool]:
    """根据邮箱域名自动匹配 SMTP 配置，支持环境变量覆盖"""
    host = os.getenv("SMTP_HOST", "")
    port = os.getenv("SMTP_PORT", "")
    if host and port:
        return host, int(port), int(port) == 465

    domain = email.split("@")[-1].lower()
    if domain in _SMTP_PRESETS:
        return _SMTP_PRESETS[domain]

    logger.warning(f"Unknown email domain '{domain}', trying SSL on port 465")
    return f"smtp.{domain}", 465, True


def send_email(articles: list[dict], intro: str = "", branding: dict = None) -> bool:
    """通过 SMTP 发送 HTML 日报（自动识别邮箱类型）"""
    email_user = os.getenv("EMAIL_USER", "") or os.getenv("GMAIL_USER", "")
    email_pass = os.getenv("EMAIL_PASSWORD", "") or os.getenv("GMAIL_APP_PASSWORD", "")
    email_to = os.getenv("EMAIL_TO", "") or os.getenv("GMAIL_TO", email_user)

    if not email_user or not email_pass:
        logger.warning("EMAIL_USER/EMAIL_PASSWORD not set, skipping email")
        return False

    if not articles:
        logger.info("No articles to send via email")
        return True

    smtp_host, smtp_port, use_ssl = _get_smtp_config(email_user)

    date_str = _today_str()
    brand = _brand_title(branding)
    subject = f"{brand} · {date_str}（{len(articles)} 篇推荐）"
    html_body = _build_gmail_html(articles, date_str, intro, branding)

    recipients = [addr.strip() for addr in email_to.split(",") if addr.strip()]

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = email_user
    msg["To"] = ", ".join(recipients)

    msg_alt = MIMEMultipart("alternative")
    msg_alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(msg_alt)

    # 内嵌 banner 图片（支持 branding.banner 自定义路径，默认 assets/banner.png）
    custom_banner = (branding or {}).get("banner", "")
    if custom_banner:
        banner_path = Path(custom_banner) if Path(custom_banner).is_absolute() else Path(__file__).parent / custom_banner
    else:
        banner_path = Path(__file__).parent / "assets" / "banner.png"
    if banner_path.exists():
        with open(banner_path, "rb") as f:
            banner_img = MIMEImage(f.read(), _subtype="png")
            banner_img.add_header("Content-ID", "<banner>")
            banner_img.add_header("Content-Disposition", "inline", filename="banner.png")
            msg.attach(banner_img)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                server.login(email_user, email_pass)
                server.sendmail(email_user, recipients, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(email_user, email_pass)
                server.sendmail(email_user, recipients, msg.as_string())
        logger.info(f"Email sent via {smtp_host} to {recipients}: {len(articles)} articles")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


# 向后兼容
send_gmail = send_email


def _build_gmail_html(articles: list[dict], date_str: str, intro: str = "", branding: dict = None) -> str:
    # 卷首语
    intro_html = ""
    if intro:
        intro_html = f"""
        <div style="margin:32px 0;padding:20px 24px;border-left:3px solid #333;background:#fafafa;">
            <p style="margin:0;color:#333;font-size:15px;line-height:1.9;">{intro}</p>
        </div>
        """

    # 本期概要（按 category 分组列标题）
    from collections import OrderedDict
    toc_groups: dict[str, list] = OrderedDict()
    for art in articles:
        cat = art.get("category", "其他")
        toc_groups.setdefault(cat, []).append(art["title"])

    toc_items = ""
    for cat, titles in toc_groups.items():
        toc_items += f"<li style='margin-bottom:4px;color:#666;font-size:13px;'><strong>{cat}</strong>：{'、'.join(t[:20] for t in titles)}</li>"

    toc_html = f"""
    <div style="margin:24px 0 32px;padding:16px 20px;background:#f8f9fa;border-radius:6px;">
        <p style="margin:0 0 8px;font-size:14px;font-weight:bold;color:#333;">本期概要</p>
        <ul style="margin:0;padding-left:20px;list-style:disc;">{toc_items}</ul>
    </div>
    """

    # 按 category 分区展示文章
    cat_groups: dict[str, list] = OrderedDict()
    for art in articles:
        cat = art.get("category", "其他")
        cat_groups.setdefault(cat, []).append(art)

    items_html = ""
    article_idx = 0
    for cat, cat_articles in cat_groups.items():
        items_html += f"""
        <h2 style="margin:36px 0 16px;padding-bottom:8px;border-bottom:1px solid #ddd;
                    font-size:18px;color:#333;font-weight:bold;">{cat}</h2>
        """
        for art in cat_articles:
            article_idx += 1
            summary = art.get("summary", "")
            reason = art.get("reason", "")
            url = art.get("url", "#")
            account = art.get("account_name", "")
            tags = art.get("tags", [])
            scores = art.get("scores", {})
            final_score = art.get("final_score", 0)

            tags_html = " ".join(
                f'<span style="background:#f1f3f4;color:#555;padding:2px 6px;border-radius:3px;font-size:11px;">#{t}</span>'
                for t in tags
            ) if tags else ""

            scores_html = ""
            if scores:
                score_items = " · ".join(f"{k}:{v}" for k, v in scores.items())
                scores_html = f'<p style="margin:4px 0 0;color:#999;font-size:11px;">📊 {score_items}</p>'

            cover = art.get("cover", "")
            thumb_cell = f'<td style="width:60px;vertical-align:top;padding-left:12px;"><img src="{cover}" style="width:60px;height:60px;object-fit:cover;border-radius:6px;display:block;" /></td>' if cover and cover.startswith("http") else '<td style="width:60px;"></td>'

            items_html += f"""
            <div style="margin-bottom:32px;">
                <table style="width:100%;border-collapse:collapse;"><tr>
                    <td style="vertical-align:top;">
                        <h3 style="margin:0 0 6px;font-size:17px;line-height:1.5;">
                            <a href="{url}" style="color:#1a73e8;text-decoration:none;">{art['title']}</a>
                        </h3>
                    </td>
                    {thumb_cell}
                </tr></table>
                <p style="margin:0 0 8px;color:#999;font-size:12px;">
                    来源：{account}{'　|　综合分：%.1f' % final_score if final_score else ''}
                </p>
                {f'<p style="margin:0 0 8px;">{tags_html}</p>' if tags_html else ''}
                {"<p style='margin:0 0 8px;color:#333;font-size:14px;line-height:1.8;'>" + summary + "</p>" if summary else ""}
                {"<p style='margin:0 0 4px;color:#555;font-size:13px;line-height:1.8;'>💡 " + reason + "</p>" if reason else ""}
                {scores_html}
            </div>
            """

    footer = _brand_footer(branding)
    return f"""
    <html><body style="font-family:-apple-system,'Helvetica Neue',sans-serif;max-width:620px;margin:0 auto;padding:40px 20px;color:#333;">
        <div style="text-align:center;margin-bottom:24px;">
            <img src="cid:banner" style="max-width:100%;border-radius:8px;" alt="AI 日报" />
        </div>
        <p style="text-align:center;color:#999;font-size:14px;margin-bottom:8px;">{date_str}　·　共 {len(articles)} 篇推荐</p>
        {intro_html}
        {toc_html}
        {items_html}
        <hr style="border:none;border-top:1px solid #eee;margin:40px 0 16px;" />
        <p style="text-align:center;color:#bbb;font-size:12px;">{footer}</p>
    </body></html>
    """


def _get_hero_image(articles: list[dict]) -> str:
    """取第一篇有封面图的文章作为整期 newsletter 的头图"""
    for art in articles:
        cover = art.get("cover", "")
        if cover and cover.startswith("http"):
            return f'<img src="{cover}" style="max-width:100%;border-radius:8px;margin-bottom:24px;display:block;" />'
    return ""


def _today_str() -> str:
    from datetime import datetime, timezone, timedelta
    cst = timezone(timedelta(hours=8))
    return datetime.now(cst).strftime("%Y-%m-%d")



# ──────────────────────────────────────────────
# Token 过期/临期提醒（main.py 与 CI 健康检查共用）
# ──────────────────────────────────────────────

def send_alert(alert_text: str):
    """通过所有已配置的推送渠道发送提醒"""
    sent = False

    # 飞书
    feishu_webhook = os.environ.get("FEISHU_WEBHOOK", "")
    if feishu_webhook:
        try:
            import requests as _req
            _req.post(feishu_webhook, json={"msg_type": "text", "content": {"text": alert_text}}, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"Feishu alert failed: {e}")

    # 钉钉
    dingtalk_webhook = os.environ.get("DINGTALK_WEBHOOK", "")
    if dingtalk_webhook:
        try:
            import requests as _req
            _req.post(dingtalk_webhook, json={"msgtype": "text", "text": {"content": alert_text}}, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"DingTalk alert failed: {e}")

    # 企业微信
    wecom_webhook = os.environ.get("WECOM_WEBHOOK", "")
    if wecom_webhook:
        try:
            import requests as _req
            _req.post(wecom_webhook, json={"msgtype": "text", "text": {"content": alert_text}}, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"WeCom alert failed: {e}")

    # Telegram
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if tg_token and tg_chat:
        try:
            import requests as _req
            _req.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                      json={"chat_id": tg_chat, "text": alert_text}, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

    # Bark
    bark_url = os.environ.get("BARK_URL", "")
    if bark_url:
        try:
            import requests as _req
            url = f"{bark_url.rstrip('/')}/Token提醒/{alert_text}"
            _req.get(url, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"Bark alert failed: {e}")

    # Server酱
    sc_key = os.environ.get("SERVERCHAN_KEY", "")
    if sc_key:
        try:
            import requests as _req
            _req.post(f"https://sctapi.ftqq.com/{sc_key}.send",
                      data={"title": "Token提醒", "desp": alert_text}, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"ServerChan alert failed: {e}")

    # PushPlus
    pp_token = os.environ.get("PUSHPLUS_TOKEN", "")
    if pp_token:
        try:
            import requests as _req
            _req.post("http://www.pushplus.plus/send",
                      json={"token": pp_token, "title": "Token提醒", "content": alert_text}, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"PushPlus alert failed: {e}")

    # 邮件（仅发给发件人自己，不打扰其他收件人）
    email_user = os.environ.get("EMAIL_USER") or os.environ.get("GMAIL_USER", "")
    email_pass = os.environ.get("EMAIL_PASSWORD") or os.environ.get("GMAIL_APP_PASSWORD", "")
    if email_user and email_pass:
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText(alert_text, "plain", "utf-8")
            msg["Subject"] = "🔔 wechat-radar Token 提醒"
            msg["From"] = email_user
            msg["To"] = email_user
            host = "smtp.gmail.com" if "gmail" in email_user else "smtp.qq.com"
            with smtplib.SMTP_SSL(host, 465, timeout=15) as s:
                s.login(email_user, email_pass)
                s.sendmail(email_user, [email_user], msg.as_string())
            sent = True
        except Exception as e:
            logger.warning(f"Email alert failed: {e}")

    if sent:
        logger.info("Alert sent via configured channels")
    else:
        logger.warning("No push channels configured — cannot send alert")


def notify_token_expiring_soon(remaining_hours: float):
    """Token 即将过期，提前提醒"""
    alert_text = f"⏰ 微信 Token 将在 {remaining_hours:.1f} 小时后过期，请尽快运行 python3 main.py --login 重新扫码续期"
    send_alert(alert_text)


def notify_token_expired():
    """通过所有已配置的推送渠道通知 token 过期"""
    send_alert("⚠️ 微信登录已过期，请运行 python3 main.py --login 重新扫码登录")

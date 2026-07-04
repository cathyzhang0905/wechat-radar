"""
fetcher.py - 从微信公众号官方 API 拉取文章
使用 mp.weixin.qq.com 官方接口（searchbiz + appmsgpublish）
"""
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

from auth import load_token

logger = logging.getLogger(__name__)

BASE = "https://mp.weixin.qq.com"
REQUEST_TIMEOUT = 15
RETRY_DELAY = 2
API_INTERVAL = 1.5  # 每次 API 请求间隔（秒）
_last_request_time = 0.0

FAKEID_CACHE_FILE = Path(__file__).parent / "fakeid_cache.json"
ARTICLE_CACHE_FILE = Path(__file__).parent / "article_cache.json"


def _make_headers() -> dict:
    token_data = load_token()
    cookies = token_data.get("cookies", "") if token_data else ""
    return {
        "Cookie": cookies,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://mp.weixin.qq.com/",
        "X-Requested-With": "XMLHttpRequest",
    }


def _rate_limit():
    """确保两次 API 请求之间有足够间隔"""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < API_INTERVAL:
        time.sleep(API_INTERVAL - elapsed)
    _last_request_time = time.time()


def _get(url: str, params: dict = None, retries: int = 2) -> Optional[dict]:
    headers = _make_headers()
    for attempt in range(retries + 1):
        _rate_limit()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            # 检查微信 API 错误码
            base_resp = data.get("base_resp", {})
            ret = base_resp.get("ret", 0)
            if ret != 0:
                logger.error(f"WeChat API error: ret={ret}, errmsg={base_resp.get('err_msg', '')}, url={url}")
                if ret in (200003, 200013, 200014, -1):  # token/session 过期相关错误码
                    raise TokenExpiredError(f"Token expired (ret={ret})")
                return None
            return data
        except TokenExpiredError:
            raise
        except requests.RequestException as e:
            if attempt < retries:
                logger.warning(f"Request failed ({attempt+1}/{retries+1}): {e}, retrying...")
                time.sleep(RETRY_DELAY)
            else:
                logger.error(f"Request failed after {retries+1} attempts: {url} - {e}")
                return None


class TokenExpiredError(Exception):
    pass


def _load_fakeid_cache() -> dict:
    if FAKEID_CACHE_FILE.exists():
        try:
            return json.loads(FAKEID_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_fakeid_cache(cache: dict):
    FAKEID_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def get_fakeid(account_name: str) -> Optional[str]:
    """根据公众号名称搜索 fakeid（优先读缓存，命中则跳过 API 调用）"""
    # 先查缓存
    cache = _load_fakeid_cache()
    if account_name in cache:
        fakeid = cache[account_name]
        logger.info(f"Cache hit for '{account_name}': fakeid={fakeid}")
        return fakeid

    token_data = load_token()
    if not token_data:
        logger.error("No token available, please run: python3 main.py --login")
        return None
    token = token_data.get("token", "")

    data = _get(
        f"{BASE}/cgi-bin/searchbiz",
        params={
            "action": "search_biz",
            "query": account_name,
            "count": 5,
            "begin": 0,
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        },
    )
    if not data:
        return None

    items = data.get("list", [])
    if not items:
        logger.warning(f"No account found for: {account_name}")
        return None

    # 优先精确匹配 nickname
    for item in items:
        if item.get("nickname") == account_name:
            fakeid = item.get("fakeid")
            logger.info(f"Found exact match for '{account_name}': fakeid={fakeid}")
            cache[account_name] = fakeid
            _save_fakeid_cache(cache)
            return fakeid

    # 回退：取第一条
    fakeid = items[0].get("fakeid")
    logger.info(f"No exact match for '{account_name}', using first result: fakeid={fakeid}")
    cache[account_name] = fakeid
    _save_fakeid_cache(cache)
    return fakeid


def _load_article_cache() -> dict:
    if ARTICLE_CACHE_FILE.exists():
        try:
            return json.loads(ARTICLE_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_article_cache(cache: dict):
    ARTICLE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def get_recent_articles(fakeid: str, account_name: str, hours: int = 24) -> list[dict]:
    """拉取最新文章列表，过滤出指定小时内的文章"""
    token_data = load_token()
    if not token_data:
        logger.error("No token available")
        return []
    token = token_data.get("token", "")

    data = _get(
        f"{BASE}/cgi-bin/appmsgpublish",
        params={
            "sub": "list",
            "sub_action": "list_ex",
            "fakeid": fakeid,
            "begin": 0,
            "count": 20,
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        },
    )
    if not data:
        return []

    # 三层嵌套解析
    articles = _parse_publish_list(data)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []

    for art in articles:
        pub_dt = _parse_time(art.get("create_time"))
        if pub_dt is None:
            logger.warning(f"Cannot parse create_time for: {art.get('title')}")
            continue
        if pub_dt >= cutoff:
            recent.append({
                "title": art.get("title", ""),
                "url": art.get("link", ""),
                "publish_time": pub_dt.isoformat(),
                "account_name": account_name,
                "fakeid": fakeid,
                "cover": art.get("cover", ""),
            })

    logger.info(f"'{account_name}': {len(recent)}/{len(articles)} articles in last {hours}h")
    return recent


def _parse_publish_list(data: dict) -> list[dict]:
    """解析 appmsgpublish 三层嵌套结构"""
    articles = []
    try:
        publish_page_raw = data.get("publish_page", "")
        if not publish_page_raw:
            return []

        publish_page = json.loads(publish_page_raw)  # 第二层
        for item in publish_page.get("publish_list", []):
            publish_info_raw = item.get("publish_info", "")
            if not publish_info_raw:
                continue
            publish_info = json.loads(publish_info_raw)  # 第三层
            for art in publish_info.get("appmsgex", []):
                articles.append(art)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"Failed to parse publish_list: {e}")
    return articles


def _parse_time(raw) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))  # CST
                return dt
            except ValueError:
                continue
    return None


def get_article_content(url: str) -> dict:
    """直接 GET 文章原始 URL，提取正文文字和图片列表。优先读缓存，避免测试阶段重复请求。"""
    # 查缓存
    cache = _load_article_cache()
    if url in cache:
        logger.info(f"  Article cache hit: {url[:60]}")
        return cache[url]

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        result = _parse_html(resp.text)

        # 写缓存（只缓存有内容的）
        if result.get("text", "").strip():
            cache[url] = result
            _save_article_cache(cache)

        return result
    except Exception as e:
        logger.error(f"Failed to fetch article content: {url} - {e}")
        return {"text": "", "images": []}


def _parse_html(html: str) -> dict:
    """从 HTML 提取纯文字和图片 URL"""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")

        # 移除 script/style
        for tag in soup(["script", "style", "meta", "link"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        clean_text = "\n".join(lines)

        images = []
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src", "")
            if src and src.startswith("http"):
                images.append(src)

        return {"text": clean_text[:8000], "images": images[:5]}
    except Exception as e:
        logger.error(f"HTML parse error: {e}")
        return {"text": html[:3000], "images": []}

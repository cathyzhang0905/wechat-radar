"""
cubox_client.py - 抓取 Cubox 收藏 + 标注过的文章（纯 HTTP，不依赖浏览器）

数据源（探针 2026-06-24 验证）：
  - 收藏：       GET /c/api/norm/card/query        （page 分页）
  - 标注过的文章：GET /c/api/norm/card/marked/query （lastCardId 游标分页）
鉴权：请求头 `Authorization: <token>`（裸 token，非 Bearer）。token 取自 Cubox 网页版
      localStorage['token']，配置在 .env 的 CUBOX_TOKEN；失效时（API code -1006）需重新获取。

合并规则：两个源按 cardId 去重；source 标 collected / marked / both；markCount 作权重信号。
落地：cubox_favorites.json（增量合并，历史正样本不丢）。
"""
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

BASE = "https://cubox.pro/c/api"
REQUEST_TIMEOUT = 15
API_INTERVAL = 1.0  # 请求间隔（秒），避免频率限制
_last_request_time = 0.0

FAVORITES_FILE = Path(__file__).parent / "cubox_favorites.json"
BIZ_CACHE_FILE = Path(__file__).parent / "cubox_biz_cache.json"  # __biz -> 公众号名缓存

_JS_NAME_RE = re.compile(r'id="js_name"[^>]*>\s*([^<]+?)\s*<')
_BIZ_RE = re.compile(r'__biz=([^&]+)')

# Cubox token 形如 UUID；优先环境变量，方便后台无浏览器运行
TOKEN = os.getenv("CUBOX_TOKEN", "").strip()

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


class CuboxTokenError(RuntimeError):
    """token 缺失或失效（API code -1006）。需重新从 Cubox 网页版 localStorage 取 CUBOX_TOKEN。"""


def _rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < API_INTERVAL:
        time.sleep(API_INTERVAL - elapsed)
    _last_request_time = time.time()


def _get(path: str, params: dict, retries: int = 2) -> dict:
    if not TOKEN:
        raise CuboxTokenError("CUBOX_TOKEN 未配置（.env）")
    headers = {**_HEADERS, "Authorization": TOKEN}
    url = f"{BASE}/{path}"
    last_err = None
    for attempt in range(retries + 1):
        _rate_limit()
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            j = resp.json()
            code = j.get("code")
            if code == -1006:
                raise CuboxTokenError("Cubox token 失效（-1006），请更新 .env 的 CUBOX_TOKEN")
            if code != 200:
                raise RuntimeError(f"Cubox API {path} 返回 code={code} msg={j.get('message')}")
            return j.get("data") or {}
        except CuboxTokenError:
            raise
        except Exception as e:
            last_err = e
            logger.warning(f"Cubox {path} 第 {attempt + 1} 次失败: {e}")
            time.sleep(2)
    raise RuntimeError(f"Cubox API {path} 重试耗尽: {last_err}")


def _normalize(item: dict, source: str) -> dict:
    """把 Cubox card 条目标准化成统一 schema。"""
    return {
        "cardId": item.get("cardId"),
        "title": (item.get("title") or "").strip(),
        "description": (item.get("description") or item.get("summary") or "").strip(),
        "url": item.get("url") or "",
        "domain": item.get("domain") or "",
        "createTime": item.get("createTime") or "",
        "tags": item.get("tags") or [],
        "groupName": item.get("groupName") or "",
        "markCount": item.get("markCount") or 0,
        "source": source,  # collected | marked | both
        "marks": [],        # [{text 划线原文, noteText 你的批注, colorType}]，由 fetch_marks 挂载
        "account": "",      # 来源公众号名，由 resolve_account 懒解析
    }


def fetch_collected() -> list[dict]:
    """抓「所有收藏」（page 分页）。"""
    out, page = [], 1
    while True:
        data = _get("norm/card/query", {
            "page": page, "orderType": 4, "asc": "false",
            "isArticle": "false", "archiving": "false",
        })
        lst = data.get("list") or []
        out.extend(_normalize(it, "collected") for it in lst)
        if page >= (data.get("pageCount") or 1) or not lst:
            break
        page += 1
    logger.info(f"Cubox 收藏: {len(out)} 篇")
    return out


def fetch_marked() -> list[dict]:
    """抓「标注过的文章」（lastCardId 游标分页）。"""
    out, last_card_id = [], ""
    while True:
        data = _get("norm/card/marked/query", {
            "orderType": 4, "asc": "false", "filters": "",
            "isArticle": "false", "colorTypes": "", "pageSize": 30,
            "lastCardId": last_card_id,
        })
        lst = data.get("list") if isinstance(data, dict) else data
        lst = lst or []
        out.extend(_normalize(it, "marked") for it in lst)
        if len(lst) < 30:
            break
        last_card_id = lst[-1].get("cardId") or ""
        if not last_card_id:
            break
    logger.info(f"Cubox 标注过的文章: {len(out)} 篇")
    return out


def fetch_marks() -> dict:
    """抓所有划线/批注（/norm/mark/list，lastMarkId 游标，一次 50 条），按 cardId 分组。"""
    by_card: dict[str, list[dict]] = {}
    last_mark_id, total = "", 0
    while True:
        data = _get("norm/mark/list", {
            "orderType": 4, "asc": "false", "filters": "",
            "isArticle": "false", "colorTypes": "", "lastMarkId": last_mark_id,
        })
        lst = data.get("list") if isinstance(data, dict) else data
        lst = lst or []
        for m in lst:
            cid = m.get("cardId")
            if not cid:
                continue
            by_card.setdefault(cid, []).append({
                "text": (m.get("text") or "").strip(),
                "noteText": (m.get("noteText") or "").strip(),
                "colorType": m.get("colorType"),
            })
        total += len(lst)
        if len(lst) < 50:
            break
        last_mark_id = lst[-1].get("markId") or ""
        if not last_mark_id:
            break
    logger.info(f"Cubox 划线/批注: {total} 条，分布在 {len(by_card)} 篇")
    return by_card


def _load_biz_cache() -> dict:
    if BIZ_CACHE_FILE.exists():
        try:
            return json.loads(BIZ_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def resolve_account(url: str) -> str:
    """从 mp.weixin 文章 url 解析公众号名（页面 id="js_name"）；按 __biz 缓存，避免重复抓。失败返回 ''。"""
    if not url or "mp.weixin" not in url:
        return ""
    m = _BIZ_RE.search(url)
    biz = m.group(1) if m else url
    cache = _load_biz_cache()
    if biz in cache:
        return cache[biz]
    name = ""
    try:
        _rate_limit()
        html = requests.get(url, headers={"User-Agent": _HEADERS["User-Agent"]}, timeout=REQUEST_TIMEOUT).text
        mm = _JS_NAME_RE.search(html)
        if mm:
            name = mm.group(1).strip()
    except Exception as e:
        logger.warning(f"解析公众号名失败 {url[:50]}: {e}")
    if name:
        cache[biz] = name
        BIZ_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return name


def _merge(records: list[dict]) -> dict:
    """按 cardId 去重合并；同时出现在收藏和标注里的标 both，markCount 取较大。"""
    merged: dict[str, dict] = {}
    for r in records:
        cid = r.get("cardId")
        if not cid:
            continue
        if cid in merged:
            ex = merged[cid]
            if ex["source"] != r["source"]:
                ex["source"] = "both"
            ex["markCount"] = max(ex.get("markCount", 0), r.get("markCount", 0))
        else:
            merged[cid] = r
    return merged


def get_favorites(refresh: bool = True) -> list[dict]:
    """
    返回合并去重后的正样本列表（收藏 ∪ 标注过）。
    refresh=True 时从 Cubox 拉新数据并与本地 json 增量合并后落地。
    """
    if refresh:
        cards = _merge(fetch_collected() + fetch_marked())
        marks = fetch_marks()
        for cid, rec in cards.items():
            rec["marks"] = marks.get(cid, [])
        existing = _load_local_map()
        existing.update(cards)  # 新数据覆盖/更新，历史保留（一旦见过的正样本不丢）
        _save_local_map(existing)
        records = list(existing.values())
    else:
        records = list(_load_local_map().values())
    # 标注过的优先、再按收藏时间倒序
    records.sort(key=lambda r: (r.get("markCount", 0) > 0, r.get("createTime", "")), reverse=True)
    return records


def _load_local_map() -> dict:
    if FAVORITES_FILE.exists():
        try:
            return json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取 {FAVORITES_FILE.name} 失败: {e}")
    return {}


def _save_local_map(m: dict):
    FAVORITES_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"已写入 {FAVORITES_FILE.name}（{len(m)} 篇正样本）")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    favs = get_favorites(refresh=True)
    n_mark = sum(1 for r in favs if r.get("markCount", 0) > 0)
    n_both = sum(1 for r in favs if r.get("source") == "both")
    n_marks = sum(1 for r in favs if r.get("marks"))
    n_notes = sum(1 for r in favs for m in r.get("marks", []) if m.get("noteText"))
    print(f"\n合计正样本 {len(favs)} 篇（标注过 {n_mark}，both {n_both}，带划线内容 {n_marks}，含你的批注 {n_notes} 条）")
    print("\n最强信号 Top 5:")
    for r in favs[:5]:
        ms = r.get("marks") or []
        print(f"  [{r['source']:9}|mark{r['markCount']:>2}|划线{len(ms)}] {r['title'][:34]}")
        if ms and ms[0].get("text"):
            print(f"       划线例：{ms[0]['text'][:36]}")

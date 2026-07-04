"""
source_evolve.py - 信源自进化（机制 ③）

逻辑：统计 Cubox 收藏来自哪些公众号 → 某号「不在 config.yaml 的 list」且「累计收藏 ≥ MIN_COLLECT 篇」
      → 自动加进 config.yaml 的 accounts，并发飞书通知（可随时删）。

来源号靠 cubox_client.resolve_account(url)（解析文章页 js_name，按 __biz 缓存）。
默认 dry-run（只预览不改）；加 --apply 才真正写 config + 通知。runner 用 --apply。
"""
import logging
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

import cubox_client

logger = logging.getLogger(__name__)
load_dotenv()

CONFIG = Path(__file__).parent / "config.yaml"
MIN_COLLECT = 3
FEISHU = os.getenv("FEISHU_WEBHOOK", "").strip()


def _current_accounts(text: str) -> set:
    """解析 config.yaml 的 accounts 段，返回当前监控的号名集合。"""
    accts, in_acc = set(), False
    for line in text.split("\n"):
        if line.startswith("accounts:"):
            in_acc = True
            continue
        if in_acc:
            # 下一个顶层 key（非缩进、非空、非注释）→ accounts 段结束
            if line.strip() and not line[0].isspace() and not line.lstrip().startswith("#"):
                break
            m = re.match(r"\s*-\s*(.+?)\s*$", line)
            if m:
                accts.add(m.group(1).strip())
    return accts


def _count_sources() -> Counter:
    """统计每个来源公众号的收藏篇数（只统计能解析出号名的 mp.weixin）。"""
    favs = cubox_client.get_favorites(refresh=False)
    c = Counter()
    for r in favs:
        name = cubox_client.resolve_account(r.get("url", ""))
        if name:
            c[name] += 1
    return c


def _add_to_config(text: str, names: list[str]) -> str:
    """在 accounts 段末尾（profile: 前）追加新号，带自动新增注释。"""
    lines = text.split("\n")
    pi = next(i for i, l in enumerate(lines) if l.startswith("profile:"))
    at = pi
    while at - 1 >= 0 and lines[at - 1].strip() == "":
        at -= 1
    block = [f"  # ── 自动新增（信源自进化 {date.today()}）──"] + [f"  - {n}" for n in names]
    return "\n".join(lines[:at] + block + lines[at:])


def _notify(added: list[tuple]):
    if not FEISHU:
        logger.warning("FEISHU_WEBHOOK 未配置，跳过通知")
        return
    msg = ["📡 信源自进化：本次新增监控公众号"]
    for n, cnt in added:
        msg.append(f"· {n}（你已收藏 {cnt} 篇）")
    msg.append("已加入雷达监控 list；如不想要，可在 config.yaml 删除。")
    try:
        requests.post(FEISHU, json={"msg_type": "text", "content": {"text": "\n".join(msg)}}, timeout=10)
    except Exception as e:
        logger.warning(f"飞书通知失败: {e}")


def run(apply: bool = False) -> list[tuple]:
    text = CONFIG.read_text(encoding="utf-8")
    current = _current_accounts(text)
    counts = _count_sources()
    added = sorted([(n, c) for n, c in counts.items() if c >= MIN_COLLECT and n not in current],
                   key=lambda x: -x[1])

    logger.info(f"当前 list {len(current)} 个号；收藏来源 {len(counts)} 个号；达标新增候选 {len(added)} 个")
    for n, c in added:
        logger.info(f"  [候选] {n}（收藏 {c} 篇）")

    if added and apply:
        CONFIG.write_text(_add_to_config(text, [n for n, _ in added]), encoding="utf-8")
        logger.info(f"已写入 config.yaml，新增 {[n for n, _ in added]}")
        _notify(added)
    elif added:
        logger.info("（dry-run，未写入。加 --apply 才真正加入并通知）")
    return added


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run(apply="--apply" in sys.argv)

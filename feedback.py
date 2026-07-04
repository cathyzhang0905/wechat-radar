"""
feedback.py - 把 Cubox 正样本（收藏 ∪ 标注）转成评分用的 few-shot 正例文本

偏好闭环的"作用回评分"那一步：评分时在 prompt 里塞几篇用户真喜欢的文章当高分锚点，
让 AI 对照打分——主题/深度/风格越接近这些，相关度与价值应给得越高。

来源数据：cubox_favorites.json（由 cubox_client.py 抓取落地）。
选取规则（已拍板 2026-06-24）：标注过的优先 → 优先 mp.weixin 公众号 → 按收藏时间倒序 → 取最近 N 篇；
每条只给 标题 + 来源 + 摘要(截断)，不塞全文。
"""
import logging

import cubox_preferences
import cubox_client

logger = logging.getLogger(__name__)

DESC_MAX = 90  # 每条摘要截断长度


def _pick(records: list[dict], n: int, prefer_domain: str) -> list[dict]:
    """优先 prefer_domain 的条目，不足 n 再用其余补足。records 已按(标注过,时间)倒序。"""
    preferred = [r for r in records if prefer_domain in (r.get("domain") or "")]
    others = [r for r in records if prefer_domain not in (r.get("domain") or "")]
    picked = (preferred + others)[:n]
    return picked


def build_positive_examples(n: int = 10, prefer_domain: str = "weixin") -> str:
    """生成可直接拼进评分 system prompt 的正例文本块；无数据时返回空串（优雅降级）。"""
    try:
        records = cubox_client.get_favorites(refresh=False)
    except Exception as e:
        logger.warning(f"读取 Cubox 正样本失败，跳过 few-shot 正例: {e}")
        return ""
    if not records:
        return ""

    picked = _pick(records, n, prefer_domain)
    if not picked:
        return ""

    learned = cubox_preferences.build_preference_block()
    lines = []
    if learned:
        lines.extend([learned, ""])

    lines.extend([
        "以下是用户**主动收藏 / 标注划线过**的文章，代表 ta 真正想读、会给高分的内容。",
        "评分时把它们当高分锚点：新文章在主题、深度、风格上越接近这些（尤其接近用户的**划线**与**批注**），",
        "relevance / actionability 应给得越高；来自用户常收藏的同类来源也可适当加分。",
        "",
    ])
    for i, r in enumerate(picked, 1):
        title = r.get("title", "").strip()
        acct = cubox_client.resolve_account(r.get("url", "")) or r.get("domain", "")
        mark = r.get("markCount", 0)
        tag = f"［标注 {mark} 处］" if mark else ""
        lines.append(f"{i}.《{title}》（来源：{acct}）{tag}")

        desc = (r.get("description") or "").strip().replace("\n", " ")
        if desc:
            if len(desc) > DESC_MAX:
                desc = desc[:DESC_MAX] + "…"
            lines.append(f"   摘要（文章内容）：{desc}")

        # 划线 / 批注：带批注的优先，最多 3 条
        marks = sorted(r.get("marks") or [], key=lambda m: bool(m.get("noteText")), reverse=True)[:3]
        hl = [m["text"] for m in marks if m.get("text")]
        notes = [m["noteText"] for m in marks if m.get("noteText")]
        if hl:
            lines.append("   ta 划线：" + " / ".join(f"「{t[:50]}」" for t in hl))
        if notes:
            lines.append("   ta 批注（用户自己的话）：" + " / ".join(f"「{nt[:60]}」" for nt in notes))
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(build_positive_examples())

"""
cubox_preferences.py - 从 Cubox 收藏/划线中学习动态偏好画像

这层不是训练模型，也不直接改分数；它把真实行为信号压缩成一段短 prompt：
- 划线/批注权重最高
- 标注过的文章高于普通收藏
- 标题和摘要作为主题背景
- 来源公众号和 Cubox 标签作为辅助信号
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
FAVORITES_FILE = ROOT / "cubox_favorites.json"
BIZ_CACHE_FILE = ROOT / "cubox_biz_cache.json"

THEMES = {
    "Agent 产品与工作流": [
        "agent", "Agent", "智能体", "workflow", "Workflow", "工作流", "自动化",
        "浏览器", "RPA", "GUI", "人机协作", "执行", "工具调用", "operator",
    ],
    "AI Native 组织与协同": [
        "AI Native", "组织", "协同", "管理", "团队", "公司", "中层", "一人公司",
        "企业", "工作方式", "生产力", "AI时代",
    ],
    "Coding Agent / Skill / Context": [
        "Claude Code", "Codex", "Claude", "Skill", "skill", "MCP", "context",
        "Context", "上下文", "memory", "记忆", "prompt", "Prompt", "提示词",
        "vibe coding", "工程", "代码",
    ],
    "AI 产品设计与 PM 判断": [
        "产品", "PM", "用户", "交互", "界面", "设计", "需求", "场景",
        "体验", "产品经理", "产品判断",
    ],
    "创业、商业化与公司分析": [
        "创业", "商业化", "SaaS", "客户", "收入", "定价", "融资", "估值",
        "ToB", "ToC", "市场", "竞争", "增长", "公司",
    ],
    "深度方法论与第一性原理": [
        "第一性原理", "框架", "方法论", "复盘", "本质", "机制", "系统",
        "判断", "长期", "认知", "逻辑",
    ],
}

STOP_TAGS = {"", "帮助文档 & 样例"}


def _load_records() -> list[dict]:
    if not FAVORITES_FILE.exists():
        return []
    try:
        return list(json.loads(FAVORITES_FILE.read_text(encoding="utf-8")).values())
    except Exception:
        return []


def _load_biz_cache() -> dict:
    if not BIZ_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(BIZ_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _biz_from_url(url: str) -> str:
    m = re.search(r"[?&]__biz=([^&]+)", url or "")
    return m.group(1) if m else ""


def _tag_names(tags) -> list[str]:
    out = []
    for tag in tags or []:
        if isinstance(tag, dict):
            name = tag.get("name", "")
        else:
            name = str(tag)
        name = name.strip()
        if name and name not in STOP_TAGS:
            out.append(name)
    return out


def _weighted_text_parts(record: dict) -> list[tuple[str, float]]:
    """Return text snippets with behavior-based weights."""
    parts = []
    title = (record.get("title") or "").strip()
    desc = (record.get("description") or "").strip()
    if title:
        parts.append((title, 1.5))
    if desc:
        parts.append((desc, 0.6))
    if record.get("markCount", 0) > 0:
        # Marked articles are stronger even when marks were not fetched.
        if title:
            parts.append((title, 1.0))
    for mark in record.get("marks") or []:
        text = (mark.get("text") or "").strip()
        note = (mark.get("noteText") or "").strip()
        if text:
            parts.append((text, 3.0))
        if note:
            parts.append((note, 5.0))
    return parts


def analyze_preferences(records: list[dict] | None = None) -> dict:
    records = records if records is not None else _load_records()
    biz_cache = _load_biz_cache()

    theme_scores = defaultdict(float)
    theme_evidence: dict[str, Counter] = {name: Counter() for name in THEMES}
    account_counts = Counter()
    account_strong_counts = Counter()
    tag_counts = Counter()
    tag_strong_counts = Counter()

    for record in records:
        marks = record.get("marks") or []
        is_strong = record.get("markCount", 0) > 0 or bool(marks)
        biz = _biz_from_url(record.get("url", ""))
        account = record.get("account") or biz_cache.get(biz, "")
        if account:
            account_counts[account] += 1
            if is_strong:
                account_strong_counts[account] += 1

        for tag in _tag_names(record.get("tags")):
            tag_counts[tag] += 1
            if is_strong:
                tag_strong_counts[tag] += 1

        for text, weight in _weighted_text_parts(record):
            for theme, keywords in THEMES.items():
                for kw in keywords:
                    count = text.count(kw)
                    if count:
                        theme_scores[theme] += count * weight
                        theme_evidence[theme][kw] += count

    themes = []
    for theme, score in theme_scores.items():
        if score <= 0:
            continue
        evidence = [kw for kw, _ in theme_evidence[theme].most_common(5)]
        themes.append({"name": theme, "score": round(score, 1), "evidence": evidence})
    themes.sort(key=lambda x: x["score"], reverse=True)

    accounts = []
    for name, count in account_counts.most_common(10):
        accounts.append({
            "name": name,
            "count": count,
            "strong_count": account_strong_counts[name],
        })

    tags = []
    for name, count in tag_counts.most_common(12):
        tags.append({
            "name": name,
            "count": count,
            "strong_count": tag_strong_counts[name],
        })

    return {
        "total_records": len(records),
        "marked_records": sum(1 for r in records if r.get("markCount", 0) > 0 or r.get("marks")),
        "note_count": sum(1 for r in records for m in r.get("marks") or [] if m.get("noteText")),
        "themes": themes,
        "accounts": accounts,
        "tags": tags,
    }


def build_preference_block(max_themes: int = 6, max_accounts: int = 6, max_tags: int = 8) -> str:
    data = analyze_preferences()
    if not data["total_records"]:
        return ""

    lines = [
        "## 从 Cubox 行为持续学习到的偏好",
        f"信号来源：{data['total_records']} 篇收藏，其中 {data['marked_records']} 篇带划线/标注，{data['note_count']} 条用户批注。",
        "使用方式：这不是硬规则，而是个性化校准。匹配多个高权重主题、且有具体产品机制/一手案例/方法论的文章，应提高 relevance 和 actionability；泛泛 AI 新闻、情绪化热点、融资通稿即使命中关键词也不要高估。",
        "",
    ]

    if data["themes"]:
        lines.append("高权重主题：")
        for item in data["themes"][:max_themes]:
            ev = "、".join(item["evidence"])
            lines.append(f"- {item['name']}（信号强度 {item['score']}；证据词：{ev}）")
        lines.append("")

    if data["accounts"]:
        lines.append("常见正反馈来源：")
        for item in data["accounts"][:max_accounts]:
            strong = f"，强信号 {item['strong_count']}" if item["strong_count"] else ""
            lines.append(f"- {item['name']}（收藏 {item['count']}{strong}）")
        lines.append("")

    if data["tags"]:
        lines.append("用户主动打过的 Cubox 标签：")
        for item in data["tags"][:max_tags]:
            strong = f"，强信号 {item['strong_count']}" if item["strong_count"] else ""
            lines.append(f"- {item['name']}（{item['count']}{strong}）")

    return "\n".join(lines).strip()


def build_markdown_report() -> str:
    data = analyze_preferences()
    lines = [
        "# Cubox Preference Profile",
        "",
        f"- 收藏样本：{data['total_records']}",
        f"- 带划线/标注：{data['marked_records']}",
        f"- 用户批注：{data['note_count']}",
        "",
        "## 高权重主题",
        "",
        "| 主题 | 信号强度 | 证据词 |",
        "|---|---:|---|",
    ]
    for item in data["themes"]:
        lines.append(f"| {item['name']} | {item['score']} | {'、'.join(item['evidence'])} |")

    lines += ["", "## 正反馈来源", "", "| 来源 | 收藏 | 强信号 |", "|---|---:|---:|"]
    for item in data["accounts"]:
        lines.append(f"| {item['name']} | {item['count']} | {item['strong_count']} |")

    lines += ["", "## Cubox 标签", "", "| 标签 | 次数 | 强信号 |", "|---|---:|---:|"]
    for item in data["tags"]:
        lines.append(f"| {item['name']} | {item['count']} | {item['strong_count']} |")

    lines += ["", "## Prompt 注入块", "", "```text", build_preference_block(), "```"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(build_markdown_report())

#!/usr/bin/env python3
"""Analyze the Cubox -> wechat-radar feedback loop.

This is the measurement layer for the loop:

    scored articles -> recommendations -> Cubox collects/marks -> weekly review

It intentionally does not change ranking logic. It gives us the evidence needed
before adjusting prompts, weights, accounts, or thresholds.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
FAVORITES_FILE = ROOT / "cubox_favorites.json"
CONFIG_FILE = ROOT / "config.yaml.local" if (ROOT / "config.yaml.local").exists() else ROOT / "config.yaml"
REPORT_DIR = ROOT / "reports"

CST = timezone(timedelta(hours=8))


@dataclass
class Favorite:
    title: str
    url: str
    create_time: datetime | None
    source: str
    mark_count: int
    marks_count: int
    note_count: int
    account: str
    domain: str

    @property
    def is_strong(self) -> bool:
        return self.mark_count > 0 or self.marks_count > 0 or self.note_count > 0


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", (title or "").strip().lower())


def normalize_url(url: str) -> str:
    """Normalize enough for matching without overfitting to WeChat share params."""
    url = (url or "").strip()
    if not url:
        return ""
    if "mp.weixin.qq.com" not in url:
        return url.split("#", 1)[0]
    # For normal /s/<id> URLs, query params are just tracking noise.
    if "/s/" in url:
        return url.split("?", 1)[0].split("#", 1)[0]
    # For long WeChat URLs, __biz + mid + idx + sn is the stable identity.
    parts = []
    for key in ("__biz", "mid", "idx", "sn"):
        m = re.search(rf"[?&]{key}=([^&]+)", url)
        if m:
            parts.append(f"{key}={m.group(1)}")
    return "mp.weixin:" + "&".join(parts) if parts else url.split("#", 1)[0]


def parse_cubox_time(value: str) -> datetime | None:
    """Parse Cubox timestamps like 2026-06-24T13:14:33:676+08:00."""
    if not value:
        return None
    fixed = re.sub(r"T(\d{2}:\d{2}:\d{2}):(\d{3})([+-]\d{2}:\d{2})$", r"T\1.\2\3", value)
    try:
        return datetime.fromisoformat(fixed)
    except ValueError:
        return None


def load_config() -> dict[str, Any]:
    text = CONFIG_FILE.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}

    # Minimal fallback so the report can run with system Python. The production
    # app still uses PyYAML; this only reads the two knobs needed for analysis.
    scoring: dict[str, Any] = {}
    in_scoring = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("scoring:"):
            in_scoring = True
            continue
        if in_scoring and line and not line[0].isspace():
            break
        if in_scoring:
            m = re.match(r"\s{2}(min_score|top_n):\s*([0-9.]+)", line)
            if m:
                value = float(m.group(2)) if "." in m.group(2) else int(m.group(2))
                scoring[m.group(1)] = value
    return {"scoring": scoring}


def load_favorites() -> tuple[dict[str, Favorite], dict[str, Favorite]]:
    if not FAVORITES_FILE.exists():
        raise FileNotFoundError(f"Missing {FAVORITES_FILE}. Run cubox_client.py first.")
    raw = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
    by_url: dict[str, Favorite] = {}
    by_title: dict[str, Favorite] = {}
    for item in raw.values():
        marks = item.get("marks") or []
        fav = Favorite(
            title=(item.get("title") or "").strip(),
            url=item.get("url") or "",
            create_time=parse_cubox_time(item.get("createTime") or ""),
            source=item.get("source") or "",
            mark_count=int(item.get("markCount") or 0),
            marks_count=len(marks),
            note_count=sum(1 for m in marks if m.get("noteText")),
            account=item.get("account") or "",
            domain=item.get("domain") or "",
        )
        nurl = normalize_url(fav.url)
        if nurl:
            by_url[nurl] = fav
        ntitle = normalize_title(fav.title)
        if ntitle:
            by_title[ntitle] = fav
    return by_url, by_title


def load_logs(days: int | None) -> list[dict[str, Any]]:
    paths = sorted(LOG_DIR.glob("scoring_log_*.json"))
    if days:
        cutoff = datetime.now(CST).date() - timedelta(days=days - 1)
        paths = [p for p in paths if _date_from_log_path(p) >= cutoff]
    logs = []
    for path in paths:
        try:
            logs.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"Skip unreadable log {path.name}: {exc}")
    return logs


def _date_from_log_path(path: Path) -> date:
    return datetime.strptime(path.stem.replace("scoring_log_", ""), "%Y-%m-%d").date()


def match_favorite(article: dict[str, Any], by_url: dict[str, Favorite], by_title: dict[str, Favorite]) -> Favorite | None:
    return by_url.get(normalize_url(article.get("url", ""))) or by_title.get(normalize_title(article.get("title", "")))


def score_articles(logs: list[dict[str, Any]], config: dict[str, Any], by_url: dict[str, Favorite], by_title: dict[str, Favorite]) -> list[dict[str, Any]]:
    scoring = config.get("scoring", {})
    min_score = scoring.get("min_score", 5)
    top_n = scoring.get("top_n", 20)

    rows: list[dict[str, Any]] = []
    for log in logs:
        log_date = datetime.strptime(log["date"], "%Y-%m-%d").date()
        scored = []
        for article in log.get("articles") or []:
            fav = match_favorite(article, by_url, by_title)
            row = {
                **article,
                "date": log["date"],
                "log_date": log_date,
                "favorite": fav,
                "is_positive": fav is not None,
                "is_strong_positive": bool(fav and fav.is_strong),
                "is_later_positive": bool(
                    fav and fav.create_time and fav.create_time >= datetime.combine(log_date, time.min, tzinfo=CST)
                ),
            }
            scored.append(row)

        qualified = [r for r in scored if not r.get("is_ad") and r.get("final_score", 0) >= min_score]
        qualified.sort(key=lambda r: r.get("final_score", 0), reverse=True)
        recommended_urls = {r.get("url") for r in qualified[:top_n]}
        for row in scored:
            row["is_recommended"] = row.get("url") in recommended_urls
            row["rank"] = next((i + 1 for i, r in enumerate(qualified) if r.get("url") == row.get("url")), None)
        rows.extend(scored)
    return rows


def pct(num: float, den: float) -> str:
    return "0.0%" if not den else f"{num / den * 100:.1f}%"


def avg(values: list[float]) -> float:
    return mean(values) if values else 0.0


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rec = [r for r in rows if r["is_recommended"]]
    nonrec = [r for r in rows if not r["is_recommended"]]
    pos = [r for r in rows if r["is_positive"]]
    rec_pos = [r for r in rec if r["is_positive"]]
    nonrec_pos = [r for r in nonrec if r["is_positive"]]
    strong_pos = [r for r in rows if r["is_strong_positive"]]

    day_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        day_rows[row["date"]].append(row)

    by_day = []
    for day, items in sorted(day_rows.items()):
        drec = [r for r in items if r["is_recommended"]]
        dpos = [r for r in items if r["is_positive"]]
        drec_pos = [r for r in drec if r["is_positive"]]
        dnonrec_pos = [r for r in items if not r["is_recommended"] and r["is_positive"]]
        by_day.append({
            "date": day,
            "scored": len(items),
            "recommended": len(drec),
            "positives": len(dpos),
            "recommended_hits": len(drec_pos),
            "missed_hits": len(dnonrec_pos),
            "precision": pct(len(drec_pos), len(drec)),
            "capture": pct(len(drec_pos), len(dpos)),
            "avg_positive_score": avg([r.get("final_score", 0) for r in dpos]),
            "avg_other_score": avg([r.get("final_score", 0) for r in items if not r["is_positive"]]),
        })

    return {
        "total_scored": len(rows),
        "recommended": len(rec),
        "positives": len(pos),
        "strong_positives": len(strong_pos),
        "recommended_hits": len(rec_pos),
        "missed_hits": len(nonrec_pos),
        "precision": pct(len(rec_pos), len(rec)),
        "capture": pct(len(rec_pos), len(pos)),
        "strong_capture": pct(len([r for r in rec if r["is_strong_positive"]]), len(strong_pos)),
        "avg_positive_score": avg([r.get("final_score", 0) for r in pos]),
        "avg_other_score": avg([r.get("final_score", 0) for r in rows if not r["is_positive"]]),
        "by_day": by_day,
    }


def top_counter(rows: list[dict[str, Any]], field: str, positive_only: bool = False, limit: int = 12) -> list[tuple[str, int, int, str]]:
    bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        values = row.get(field) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            if value:
                bucket[str(value)].append(row)
    out = []
    for key, items in bucket.items():
        if positive_only:
            count = sum(1 for r in items if r["is_positive"])
        else:
            count = len(items)
        if count:
            hits = sum(1 for r in items if r["is_positive"])
            out.append((key, count, len(items), pct(hits, len(items))))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out[:limit]


def dimension_deltas(rows: list[dict[str, Any]]) -> list[tuple[str, float, float, float]]:
    positives = [r for r in rows if r["is_positive"]]
    others = [r for r in rows if not r["is_positive"]]
    dims = sorted({k for r in rows for k in (r.get("scores") or {}).keys()})
    out = []
    for dim in dims:
        p = avg([r.get("scores", {}).get(dim, 0) for r in positives])
        o = avg([r.get("scores", {}).get(dim, 0) for r in others])
        out.append((dim, p, o, p - o))
    out.sort(key=lambda x: -abs(x[3]))
    return out


def make_report(rows: list[dict[str, Any]], summary: dict[str, Any], days: int | None) -> str:
    title_days = f"最近 {days} 天" if days else "全部日志"
    lines = [
        f"# WeChat Radar Feedback Loop Report",
        "",
        f"- 范围：{title_days}",
        f"- 评分文章：{summary['total_scored']}",
        f"- 推荐文章：{summary['recommended']}",
        f"- Cubox 命中：{summary['positives']}（其中强信号 {summary['strong_positives']}）",
        f"- 推荐命中：{summary['recommended_hits']}，漏报命中：{summary['missed_hits']}",
        f"- 推荐命中率：{summary['precision']}，正样本召回：{summary['capture']}，强信号召回：{summary['strong_capture']}",
        f"- 命中文章平均分：{summary['avg_positive_score']:.2f}，其他文章平均分：{summary['avg_other_score']:.2f}",
        "",
        "## Daily Trend",
        "",
        "| 日期 | 评分 | 推荐 | Cubox命中 | 推荐命中 | 漏报 | 推荐命中率 | 正样本召回 | 命中均分 | 其他均分 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for d in summary["by_day"]:
        lines.append(
            f"| {d['date']} | {d['scored']} | {d['recommended']} | {d['positives']} | "
            f"{d['recommended_hits']} | {d['missed_hits']} | {d['precision']} | {d['capture']} | "
            f"{d['avg_positive_score']:.2f} | {d['avg_other_score']:.2f} |"
        )

    missed = sorted(
        [r for r in rows if r["is_positive"] and not r["is_recommended"]],
        key=lambda r: r.get("final_score", 0),
        reverse=True,
    )[:20]
    lines += ["", "## Missed Positives", ""]
    if missed:
        lines.append("| 日期 | 分数 | 账号 | 标题 | 信号 |")
        lines.append("|---|---:|---|---|---|")
        for r in missed:
            fav = r["favorite"]
            signal = "强信号" if fav and fav.is_strong else "收藏"
            lines.append(
                f"| {r['date']} | {r.get('final_score', 0):.1f} | {r.get('account_name', '')} | "
                f"{r.get('title', '')[:42]} | {signal} |"
            )
    else:
        lines.append("暂无漏报正样本。")

    hits = sorted(
        [r for r in rows if r["is_positive"] and r["is_recommended"]],
        key=lambda r: (r["date"], -(r.get("final_score", 0))),
    )[:20]
    lines += ["", "## Recommended Hits", ""]
    if hits:
        lines.append("| 日期 | Rank | 分数 | 账号 | 标题 |")
        lines.append("|---|---:|---:|---|---|")
        for r in hits:
            lines.append(
                f"| {r['date']} | {r.get('rank') or ''} | {r.get('final_score', 0):.1f} | "
                f"{r.get('account_name', '')} | {r.get('title', '')[:48]} |"
            )
    else:
        lines.append("暂无推荐命中。")

    lines += ["", "## Signals", "", "### Accounts With Positive Hits", ""]
    lines.append("| 账号 | 命中数 | 该账号评分数 | 命中占比 |")
    lines.append("|---|---:|---:|---:|")
    for account, hit_count, total, hit_rate in top_counter(rows, "account_name", positive_only=True):
        lines.append(f"| {account} | {hit_count} | {total} | {hit_rate} |")

    lines += ["", "### Tags With Positive Hits", ""]
    lines.append("| 标签 | 命中数 | 出现数 | 命中占比 |")
    lines.append("|---|---:|---:|---:|")
    for tag, hit_count, total, hit_rate in top_counter(rows, "tags", positive_only=True):
        lines.append(f"| {tag} | {hit_count} | {total} | {hit_rate} |")

    lines += ["", "### Score Dimension Gap", ""]
    lines.append("| 维度 | 命中均分 | 其他均分 | 差值 |")
    lines.append("|---|---:|---:|---:|")
    for dim, p, o, delta in dimension_deltas(rows):
        lines.append(f"| {dim} | {p:.2f} | {o:.2f} | {delta:+.2f} |")

    lines += [
        "",
        "## Loop Readout",
        "",
        "- 推荐命中率回答：推给你的文章里，有多少后来被你收藏/标注。",
        "- 正样本召回答案：你后来收藏/标注的文章里，有多少当时被 radar 推出来。",
        "- 漏报列表最值得看：它告诉我们下一轮要补哪类来源、标签或评分维度。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze wechat-radar feedback loop.")
    parser.add_argument("--days", type=int, default=None, help="Only include recent N days of scoring logs.")
    parser.add_argument("--write", action="store_true", help="Write markdown report under reports/.")
    args = parser.parse_args()

    config = load_config()
    by_url, by_title = load_favorites()
    logs = load_logs(args.days)
    rows = score_articles(logs, config, by_url, by_title)
    summary = summarize(rows)
    report = make_report(rows, summary, args.days)

    print(report)
    if args.write:
        REPORT_DIR.mkdir(exist_ok=True)
        suffix = f"{args.days}d" if args.days else "all"
        out = REPORT_DIR / f"feedback-loop-{suffix}.md"
        out.write_text(report, encoding="utf-8")
        print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

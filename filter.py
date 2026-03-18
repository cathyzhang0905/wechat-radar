"""
filter.py - AI 多维度评分筛选，支持从 memory 文件或自定义描述读取偏好
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Pydantic 模型
# ──────────────────────────────────────────────

# 默认维度（config 未配置时的 fallback）
_DEFAULT_DIMENSIONS = {
    "relevance": {"description": "与用户兴趣的相关度（1=完全无关，10=高度匹配）", "weight": 2},
    "depth": {"description": "思考深度（1=标题党/无内容，10=有独到洞察、商业框架、一手经验或深度调研）", "weight": 1},
    "info_density": {"description": "信息密度（1=水文/大量废话，10=全篇干货）", "weight": 1},
    "actionability": {"description": "可行动性（1=纯闲聊/纯资讯，10=读完能影响决策、启发行动方向）", "weight": 2},
}


def _get_dimensions(config: dict = None) -> dict:
    """从 config 获取维度定义，fallback 到默认"""
    dims = (config or {}).get("scoring", {}).get("dimensions")
    return dims if dims else _DEFAULT_DIMENSIONS


class ArticleEvaluation(BaseModel):
    is_ad: bool
    scores: dict[str, int]
    summary: str
    reason: str
    tags: list[str]
    category: str  # 深度分析/行业观察/工具推荐/观点/活动资讯/...


# ──────────────────────────────────────────────
# 偏好提取（保留原有逻辑）
# ──────────────────────────────────────────────

PREFERENCE_KEYWORDS = [
    "关注", "感兴趣", "不感兴趣", "偏好", "目标", "喜欢", "不喜欢",
    "想看", "不想看", "关心", "专注", "聚焦", "背景", "身份",
    "interest", "prefer", "focus", "goal",
]
MAX_MEMORY_LINES = 60


def extract_preferences_from_memory(file_path: str) -> str:
    """从 memory 文件中提取偏好相关段落"""
    path = Path(file_path).expanduser()
    if not path.exists():
        logger.warning(f"Memory file not found: {path}")
        return ""

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Cannot read memory file {path}: {e}")
        return ""

    lines = content.splitlines()
    extracted = []
    in_relevant_section = False
    section_lines = 0

    for line in lines:
        line_lower = line.lower()

        if line.startswith("#"):
            in_relevant_section = any(kw in line_lower for kw in PREFERENCE_KEYWORDS)
            section_lines = 0
            if in_relevant_section:
                extracted.append(line)
            continue

        if in_relevant_section:
            extracted.append(line)
            section_lines += 1
            if section_lines >= 20:
                in_relevant_section = False
        else:
            if any(kw in line_lower for kw in PREFERENCE_KEYWORDS) and line.strip():
                extracted.append(line)

    result = "\n".join(extracted[:MAX_MEMORY_LINES]).strip()
    logger.info(f"Extracted {len(result)} chars from memory: {path.name}")
    return result


def build_preferences_text(preferences_config: dict) -> str:
    """根据 config 中的 preferences 配置，构建最终偏好文本"""
    merge_mode = preferences_config.get("merge_mode", "custom_only")
    memory_files = preferences_config.get("memory_files", [])
    custom = preferences_config.get("custom", "").strip()

    parts = []

    if merge_mode in ("memory_only", "memory+custom"):
        for f in memory_files:
            extracted = extract_preferences_from_memory(f)
            if extracted:
                parts.append(f"[来自个人背景档案]\n{extracted}")

    if merge_mode in ("custom_only", "memory+custom"):
        if custom and not custom.startswith("#"):
            parts.append(f"[自定义偏好]\n{custom}")
        elif custom:
            non_comment = "\n".join(
                l for l in custom.splitlines() if not l.strip().startswith("#")
            ).strip()
            if non_comment:
                parts.append(f"[自定义偏好]\n{non_comment}")

    if not parts:
        logger.warning("No preferences found, using default fallback")
        return "关注 AI、科技、创业相关内容，不感兴趣娱乐八卦和广告软文。"

    return "\n\n".join(parts)


# ──────────────────────────────────────────────
# 结构化用户画像
# ──────────────────────────────────────────────

def build_profile_text(config: dict) -> str:
    """从 config 构建结构化用户画像文本"""
    profile = config.get("profile", {})
    preferences_config = config.get("preferences", {})

    sections = []

    # 结构化画像
    if profile:
        if profile.get("background"):
            sections.append(f"背景：{profile['background']}")
        if profile.get("expertise_level"):
            sections.append(f"专业水平：{profile['expertise_level']}")
        if profile.get("interests"):
            sections.append("兴趣领域：" + "、".join(profile["interests"]))
        prefs = profile.get("preferences", {})
        if prefs:
            pref_lines = []
            if prefs.get("prefer_practical"):
                pref_lines.append("偏好实用性强的内容")
            if prefs.get("avoid_marketing_hype"):
                pref_lines.append("避免营销炒作内容")
            if pref_lines:
                sections.append("偏好：" + "；".join(pref_lines))
        if profile.get("custom"):
            sections.append(f"补充：{profile['custom'].strip()}")

    # 兼容旧版 preferences 配置
    if preferences_config:
        free_text = build_preferences_text(preferences_config)
        if free_text:
            sections.append(free_text)

    if not sections:
        return "关注 AI、科技、创业相关内容，不感兴趣娱乐八卦和广告软文。"

    return "\n\n".join(sections)


# ──────────────────────────────────────────────
# Prompt 构建
# ──────────────────────────────────────────────

_SCORING_SYSTEM_TEMPLATE = """你是一个专业的信息筛选与评分助手。根据用户画像对文章进行多维度评分。

## 用户画像
{profile_text}

## 评分维度（每个维度 1-10 分）
{dimensions_text}

## 打分校准（重要）
请严格打分，不要倾向于给高分。参考以下校准标准：
- 1-3 分：差，明显不符合该维度要求
- 4-5 分：一般，大多数普通文章应在此区间
- 6-7 分：良好，有亮点但不突出
- 8-9 分：优秀，只有真正高质量的文章才配得上
- 10 分：极少出现，几乎完美
每个维度独立评判，不要所有维度给相近的分数。

## 输出要求
返回严格 JSON（不加 markdown 代码块），格式如下：
{{"is_ad": bool, "scores": {{{scores_format}}}, "summary": "100字以内的中文摘要", "reason": "50-80字的推荐理由", "tags": ["标签1", "标签2"], "category": "从以下选一个：深度分析、行业观察、工具推荐、观点、活动资讯、其他"}}

注意：
- 广告软文 is_ad 标记为 true，同时各维度如实打分
- 创业营、hackathon、AI meetup、技术活动、开源发布等不算广告，即使包含报名链接
- summary 用简洁中文概括核心内容
- reason 用编辑推荐口吻，说明读者为什么应该读这篇。要求：
  - 不要复述文章内容（禁止"文章介绍了/探讨了/分析了"开头）
  - 禁止使用"对正在构建XX的读者有参考价值"、"值得关注"、"有一定参考价值"等万能句式
  - 点明这篇的独特之处：它提供了什么别处看不到的信息、视角或方法？
  - 每篇理由的句式和角度必须不同
  - 好的例子：「傅盛把一人公司从概念拉到了执行层，尤其是他用 AI 替代中层管理的思路，对正在搭团队的创业者很有参考价值」
  - 好的例子：「难得看到有人拿 80 万条真实数据说话，而不是又一篇"AI 赋能医疗"的空谈」
  - 坏的例子：「本文从创业者角度深入分析了 AI 对企业管理的影响，值得关注」
  - 坏的例子：「文章探讨了当前软件产品逐渐向 Agent 技术转型的趋势，分析了背后的原因」
- tags 提取 2-4 个关键词标签
- category 只选一个，不要用 | 或其他符号组合多个类别"""


def _build_scoring_prompt(profile_text: str, dimensions: dict) -> str:
    """根据维度配置动态生成评分 prompt"""
    dims_lines = []
    for name, cfg in dimensions.items():
        desc = cfg.get("description", name)
        dims_lines.append(f"- {name}: {desc}")
    dimensions_text = "\n".join(dims_lines)

    scores_format = ", ".join(f'"{name}": int' for name in dimensions)

    return _SCORING_SYSTEM_TEMPLATE.format(
        profile_text=profile_text,
        dimensions_text=dimensions_text,
        scores_format=scores_format,
    )


def build_prompt(title: str, content: str, profile_text: str, max_content_length: int = 4000, dimensions: dict = None) -> list[dict]:
    """构建 AI 评分的 messages"""
    dims = dimensions or _DEFAULT_DIMENSIONS
    system = _build_scoring_prompt(profile_text, dims)
    user = f"""文章标题：{title}

文章正文（节选）：
{content[:max_content_length]}"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ──────────────────────────────────────────────
# AI 调用（复用原有逻辑）
# ──────────────────────────────────────────────

def call_ai(messages: list[dict], temperature: float = 0.3) -> Optional[dict]:
    """调用 AI API，返回解析后的 JSON 结果"""
    provider = os.getenv("AI_PROVIDER", "anthropic").lower()

    try:
        if provider == "anthropic":
            return _call_anthropic(messages)
        else:
            return _call_openai_compatible(messages, temperature=temperature)
    except Exception as e:
        logger.error(f"AI call failed: {e}")
        return None


def _call_anthropic(messages: list[dict]) -> Optional[dict]:
    import anthropic

    system_content = ""
    user_messages = []
    for m in messages:
        if m["role"] == "system":
            system_content = m["content"]
        else:
            user_messages.append(m)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    response = client.messages.create(
        model=model,
        max_tokens=800,
        system=system_content,
        messages=user_messages,
    )
    raw = response.content[0].text.strip()
    return _parse_evaluation(raw)


def _call_openai_compatible(messages: list[dict], temperature: float = 0.3) -> Optional[dict]:
    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("AI_API_KEY"),
        base_url=os.getenv("AI_BASE_URL", "https://api.openai.com/v1"),
    )
    model = os.getenv("AI_MODEL", "gpt-4o-mini")

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=800,
        temperature=temperature,
    )
    raw = response.choices[0].message.content.strip()
    return _parse_evaluation(raw)


def _parse_evaluation(raw: str) -> Optional[dict]:
    """解析 AI 返回的 JSON 为 ArticleEvaluation"""
    # 去掉可能的 ```json ... ``` 包裹
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    try:
        data = json.loads(raw.strip())
        # Bug 3 兜底：category 可能返回多个值，只取第一个
        if "category" in data and isinstance(data["category"], str):
            data["category"] = re.split(r"[|/、，,]", data["category"])[0].strip()
        evaluation = ArticleEvaluation(**data)
        return evaluation.model_dump()
    except Exception as e:
        logger.error(f"Evaluation parse failed: {e}\nRaw: {raw[:300]}")
        return None


# ──────────────────────────────────────────────
# 综合分计算
# ──────────────────────────────────────────────

def calc_final_score(scores: dict, dimensions: dict = None) -> float:
    """根据维度配置计算加权综合分。"""
    dims = dimensions or _DEFAULT_DIMENSIONS
    total_weight = 0
    weighted_sum = 0
    for name, cfg in dims.items():
        w = cfg.get("weight", 1)
        total_weight += w
        weighted_sum += scores.get(name, 0) * w
    return weighted_sum / total_weight if total_weight else 0.0


# ──────────────────────────────────────────────
# Newsletter 开场白
# ──────────────────────────────────────────────

INTRO_PROMPT = """你是一个私人 Newsletter 编辑。根据今天推荐的文章列表，写 2-3 句中文开场白。

风格要求：
- 像朋友微信私聊分享，有你自己的判断和态度，不是新闻联播
- 从今天的文章里挑一个你觉得最有意思的点展开，不要试图概括所有文章
- 可以提出一个问题、一个反直觉的发现、或者两篇文章之间的矛盾/呼应
- 禁止使用 emoji
- 禁止"亲爱的/尊敬的/让我们一起/为您精选"等套话
- 禁止"聚焦于/涵盖了/涉及到"这类概括性表达
- 不超过 100 字

好的开场白示例：
- 「今天有个有意思的碰撞——傅盛说一人公司靠 AI 就够了，但 OpenClaw 的拆解恰好说明 Agent 还远没到能替人的程度。真相大概在中间。」
- 「80 万条病历数据告诉我们 AI 正在污染医疗数据，这可能是今年最值得警惕的 AI 风险之一。」
- 「黄仁勋说 Token 成本决定生死，但我更好奇的是：当成本趋近于零的时候，什么才是真正的护城河？」

坏的开场白（绝对不要写成这样）：
- 「今天的内容聚焦于 AI 技术的最新进展与应用，从医疗领域到工业级大模型...」
- 「本期推荐涵盖了深度分析、行业观察、工具推荐等多个维度的优质内容。」

今日推荐文章：
{article_list}"""


def generate_intro(articles: list[dict], config: dict = None) -> str:
    """生成 Newsletter 开场白"""
    if not articles:
        return ""

    ai_config = (config or {}).get("ai", {})
    intro_temperature = ai_config.get("intro_temperature", 0.5)
    intro_style = ai_config.get("intro_style", "")

    article_list = "\n".join(
        f"- {art['title']}：{art.get('summary', '')[:50]}"
        for art in articles[:10]
    )

    # 如果用户自定义了开场白风格，替换默认 prompt 中的风格部分
    prompt = INTRO_PROMPT
    if intro_style and intro_style.strip():
        prompt = f"""你是一个私人 Newsletter 编辑。根据今天推荐的文章列表，写 2-3 句中文开场白。

风格要求：
{intro_style.strip()}
- 不超过 100 字

今日推荐文章：
{{article_list}}"""

    messages = [
        {"role": "system", "content": "你是一个简洁的 Newsletter 编辑。"},
        {"role": "user", "content": prompt.format(article_list=article_list)},
    ]

    return _call_ai_raw(messages, temperature=intro_temperature)


def _call_ai_raw(messages: list[dict], temperature: float = 0.5) -> str:
    """直接调用 AI 获取原始文本（不做 JSON 解析）"""
    provider = os.getenv("AI_PROVIDER", "anthropic").lower()
    try:
        if provider == "anthropic":
            import anthropic
            system_content = ""
            user_messages = []
            for m in messages:
                if m["role"] == "system":
                    system_content = m["content"]
                else:
                    user_messages.append(m)
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
            response = client.messages.create(
                model=model, max_tokens=200, system=system_content, messages=user_messages,
            )
            return response.content[0].text.strip()
        else:
            from openai import OpenAI
            client = OpenAI(
                api_key=os.getenv("AI_API_KEY"),
                base_url=os.getenv("AI_BASE_URL", "https://api.openai.com/v1"),
            )
            model = os.getenv("AI_MODEL", "gpt-4o-mini")
            response = client.chat.completions.create(
                model=model, messages=messages, max_tokens=200, temperature=temperature,
            )
            return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Intro generation failed: {e}")
        return ""


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def filter_article(title: str, content: str, config: dict) -> dict:
    """
    主入口：对单篇文章做 AI 多维度评分。
    config: 完整的 config dict（含 profile, preferences, scoring）

    返回: ArticleEvaluation dict + final_score，或失败时的默认值
    """
    ai_config = config.get("ai", {})
    max_content_length = ai_config.get("max_content_length", 4000)
    temperature = ai_config.get("temperature", 0.3)
    dimensions = _get_dimensions(config)

    profile_text = build_profile_text(config)
    messages = build_prompt(title, content, profile_text, max_content_length=max_content_length, dimensions=dimensions)
    result = call_ai(messages, temperature=temperature)

    if result is None:
        zero_scores = {name: 0 for name in dimensions}
        return {
            "is_ad": False,
            "scores": zero_scores,
            "summary": "",
            "reason": "AI 评分失败",
            "tags": [],
            "category": "其他",
            "final_score": 0.0,
        }

    # 计算综合分
    result["final_score"] = calc_final_score(result["scores"], dimensions)

    return result

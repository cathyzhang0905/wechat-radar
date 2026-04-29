# wechat-radar

AI 驱动的微信公众号智能日报 — 自动抓取、多维度评分、个性化推荐、定时推送。

> 每天从几十个公众号中，帮你筛出最值得读的几篇。

## 功能

- **公众号监控** — 自动抓取新文章（默认 24 小时内）
- **智能过滤** — 规则预过滤广告 + 跨源去重 + AI 多维度评分
- **个性化推荐** — 基于你的背景和兴趣定制评分排序
- **多渠道推送** — 飞书 / 钉钉 / 企业微信 / 邮件 / Telegram / Bark / Server酱 / PushPlus
- **多模型支持** — Anthropic / OpenAI / DeepSeek / 通义千问 / 硅基流动 / Ollama 等 12+
- **评分日志** — 完整保留所有文章评分，供持续调优

## 快速开始

所有配置都有合理默认值，一键脚本引导你完成全部配置。

### 方式一：一键安装（推荐）

```bash
git clone https://github.com/cathyzhang0905/wechat-radar.git
cd wechat-radar
./setup.sh
```

脚本会自动：安装依赖 → 引导选择 AI 模型 → 配置推送渠道 → 扫码登录 → 测试运行。

### 方式二：手动安装

```bash
# 1. 安装依赖
git clone https://github.com/cathyzhang0905/wechat-radar.git
cd wechat-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置（最少只需填 AI Key + 推送渠道）
cp .env.example .env
# 编辑 .env，填写 AI 模型 API Key 和推送渠道

# 3. 扫码登录微信公众号平台
python3 main.py --login

# 4. 运行
python3 main.py --dry-run  # 试运行（不推送）
python3 main.py            # 正式运行
```

> **关于微信登录**：需要有一个微信公众号（免费的个人订阅号即可）。前往 [微信公众平台](https://mp.weixin.qq.com/) 注册，用个人微信即可完成，无需企业资质。token 有效期约 3 天（微信服务端控制），过期后系统会自动通过已配置的推送渠道提醒你重新扫码。

**就这些！** 默认配置已内置 48 个精选 AI / 科技 / 创投公众号、4 个评分维度、广告过滤规则，开箱即用。

---

## 进阶配置

以下配置都是**可选的**，有合理默认值，按需调整即可。所有配置集中在 `config.yaml`。

### 个性化

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `accounts` | 监控的公众号列表 | 48 个精选账号（可直接使用或增删） |
| `profile` | 你的背景和兴趣（AI 据此个性化评分） | 示例模板 |

### 评分

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `scoring.dimensions` | 评分维度、描述、权重 | 4 个核心维度（见下方） |
| `scoring.min_score` | 推送最低分（1-10） | 5 |
| `scoring.top_n` | 每次最多推送篇数 | 20 |

默认评分维度：

| 维度 | 权重 | 含义 |
|------|-----|------|
| relevance | ×2 | 与用户兴趣的相关度 |
| depth | ×1 | 思考深度（独到洞察、一手经验） |
| info_density | ×1 | 信息密度（干货占比） |
| actionability | ×2 | 可行动性（能影响决策或行动） |

还有 4 个可选维度（originality / timeliness / credibility / entertainment），在 `config.yaml` 中取消注释即可启用。也支持自定义全新维度。

### 品牌 & 外观

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `branding.title` | 日报标题 | "微信公众号日报" |
| `branding.footer` | 邮件页脚署名 | "由 wechat-radar 自动生成" |
| `branding.banner` | 邮件 banner 头图 | `assets/banner.png` |

### AI 参数

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `ai.temperature` | 评分温度（越低越稳定） | 0.3 |
| `ai.intro_temperature` | 开场白温度（越高越有创意） | 0.5 |
| `ai.max_content_length` | 送入 AI 的正文最大字符数 | 4000 |
| `ai.min_content_length` | 低于此字数跳过评分 | 50 |
| `ai.intro_style` | 自定义开场白风格 | 内置 prompt |

### 过滤 & 去重

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `dedup.title_threshold` | 去重相似度阈值（0-1） | 0.5 |
| `prefilter.tech_whitelist` | 技术关键词白名单 | AI/技术词列表 |
| `prefilter.ad_rules` | 广告过滤规则 | 限时促销 + 免费领引流 |

### 抓取 & 定时

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `fetch.hours` | 抓取最近 N 小时内的文章 | 24 |
| `fetch.request_interval` | API 请求间隔（秒） | 1.5 |
| `schedule.cron` | 定时运行表达式 | 09:00 + 18:00 |
| `schedule.log_file` | Cron 日志路径 | /tmp/wechat-radar.log |

定时任务一键配置：

```bash
python3 main.py --setup-cron   # 写入 crontab
python3 main.py --remove-cron  # 移除
```

---

## 推送渠道

配置在 `.env` 中，选一个或多个均可：

| 渠道 | 环境变量 | 消息格式 |
|------|---------|---------|
| 飞书 | `FEISHU_WEBHOOK` | 交互式卡片，带「阅读原文」按钮 |
| 钉钉 | `DINGTALK_WEBHOOK` | Markdown 摘要 + 链接 |
| 企业微信 | `WECOM_WEBHOOK` | Markdown 摘要 + 链接 |
| 邮件 | `EMAIL_USER` / `EMAIL_PASSWORD` / `EMAIL_TO` | HTML Newsletter（banner + 卷首语 + 分类导览） |
| Telegram | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Markdown 摘要 + 链接 |
| Bark | `BARK_URL` | iOS 原生推送，点击跳转 |
| Server酱 | `SERVERCHAN_KEY` | 推送到微信 |
| PushPlus | `PUSHPLUS_TOKEN` | 推送到微信 |

> 邮件支持 Gmail / QQ邮箱 / 163邮箱 / Outlook 自动识别，详见 `.env.example`。

## AI 模型

支持 12+ 服务商，任选其一：

- **国际**：Anthropic Claude / OpenAI
- **国内**：DeepSeek / 通义千问 / 硅基流动 / 月之暗面 / 智谱 GLM / MiniMax / 百度文心 / 零一万物 / 百川 / 讯飞星火
- **本地**：Ollama
- **自定义**：任何 OpenAI 兼容接口（配置 `OPENAI_BASE_URL`）

## 数据流

```
公众号列表 → 规则预过滤 → 跨源去重 → AI 多维度评分
  → 排序 Top N → 生成开场白 → 推送 → 保存评分日志
```

## 项目结构

```
wechat-radar/
├── main.py          主入口（--test / --dry-run / --login / --setup-cron）
├── config.yaml      所有可配置项（均有默认值）
├── fetcher.py       微信 API 拉取 + 缓存
├── filter.py        AI 多维度评分 + 开场白生成
├── prefilter.py     规则预过滤
├── dedup.py         跨源去重
├── notifier.py      多渠道推送（8 种）
├── auth.py          微信扫码登录 / token 管理
├── setup.sh         一键安装配置脚本
├── assets/          邮件 banner 等静态资源
├── .env.example     环境变量模板（含详细配置说明）
└── requirements.txt 依赖列表
```

## Roadmap

- [ ] 用户反馈闭环（推送文章支持"有用/没用"反馈，数据驱动评分调优）
- [ ] 周报汇总（攒一周评分日志，周末出精选）
- [ ] 更多信息源（RSS、Product Hunt、Hacker News，从公众号工具升级为信息雷达）
- [ ] Claude Code / OpenClaw Skills 集成

## License

MIT

---

## About the builder

Built by **Cathy** ([@cathyzhang0905](https://github.com/cathyzhang0905)) — AI-native PM. Senior PM at DiDi, pivoting to AI-native product roles. I build tools at the intersection of [agent oversight](https://github.com/cathyzhang0905/agent-cockpit) and [personal information processing](https://github.com/cathyzhang0905).

Interested in **agent collaboration / vertical agents / AI PM workflows / eval methodology**? Open an issue or reach me at [@cathyzhang0905](https://github.com/cathyzhang0905).

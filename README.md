# wechat-radar

AI 驱动的微信公众号智能日报 — 自动抓取、多维度评分、个性化推荐、定时推送。

> 每天从几十个公众号中，帮你筛出最值得读的几篇。

## 功能

- **公众号监控** — 自动抓取 24 小时内新文章
- **规则预过滤** — 关键词组合过滤广告软文，技术文章白名单放行
- **跨源去重** — bigram Jaccard 相似度，同一事件只保留最优
- **AI 多维度评分** — 4 个维度加权打分，Pydantic 结构化输出
- **个性化用户画像** — 基于你的背景、兴趣、偏好定制评分
- **8 种推送渠道** — 飞书 / 钉钉 / 企业微信 / 邮件 / Telegram / Bark / Server酱 / PushPlus
- **评分日志** — 完整保留所有文章评分，供持续调优

## 数据流

```
公众号列表（config.yaml）
  → [Step 0] 规则预过滤（零成本，极保守策略）
  → [Step 1] 跨源去重（bigram Jaccard + 正文相似度）
  → [Step 2] AI 4 维度评分（LLM + Pydantic 结构化输出）
  → [Step 3] 排序 Top N（综合分 < 5 不推送）
  → [Step 4] 生成开场白（LLM 额外调用）
  → [Step 5] 推送（8 种渠道按需配置）
  → [Step 6] 保存 state + 评分日志
```

## 评分维度

| 维度 | 权重 | 含义 |
|------|------|------|
| relevance | ×2 | 与用户兴趣的相关度 |
| depth | ×1 | 思考深度（独到洞察、商业框架、一手经验） |
| info_density | ×1 | 信息密度（干货占比） |
| actionability | ×2 | 可行动性（读完能影响决策或行动） |

综合分 1-10，< 5 不推送，取 Top 20。

## 快速开始

### 1. 安装依赖

```bash
git clone https://github.com/cathyzhang0905/wechat-radar.git
cd wechat-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置

复制环境变量模板并填写：

```bash
cp .env.example .env
```

需要配置：
- AI 评分模型（任选其一）：Anthropic Claude / OpenAI / DeepSeek / 硅基流动 / 通义千问 / 月之暗面 / 智谱 GLM / Ollama 本地模型等（任何 OpenAI 兼容接口均可）
- 推送渠道（至少选一个）：
  - `FEISHU_WEBHOOK` — 飞书群机器人
  - `DINGTALK_WEBHOOK` — 钉钉群机器人
  - `WECOM_WEBHOOK` — 企业微信群机器人
  - `EMAIL_USER` / `EMAIL_PASSWORD` / `EMAIL_TO` — 邮件（自动识别 Gmail / QQ邮箱 / 163邮箱 / Outlook）
  - `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — Telegram Bot
  - `BARK_URL` — Bark（iOS 推送）
  - `SERVERCHAN_KEY` — Server酱（推送到微信）
  - `PUSHPLUS_TOKEN` — PushPlus（推送到微信）

编辑 `config.yaml`：
- `accounts` — 已内置 48 个精选 AI / 科技 / 创投方向优质公众号，可直接使用或自行增删
- `profile` — 你的背景和兴趣（用于个性化评分）
- `scoring` — 评分权重和阈值

### 3. 登录微信

```bash
python3 main.py --login
```

扫码登录后 token 会保存到 `token.json`（有效期约 3 天）。

### 4. 运行

```bash
# 测试模式（每个公众号取 1 篇，实际推送）
python3 main.py --test

# 试运行（完整流程但不推送）
python3 main.py --dry-run

# 正式运行
python3 main.py
```

### 5. 定时任务

在 `config.yaml` 中配置运行时间：

```yaml
schedule:
  cron:
    - "0 9 * * *"    # 每天 09:00
    - "0 18 * * *"   # 每天 18:00
```

然后一键写入 crontab：

```bash
python3 main.py --setup-cron   # 自动配置定时任务
python3 main.py --remove-cron  # 移除定时任务
```

## 项目结构

```
wechat-radar/
├── main.py          主入口（--test / --dry-run / --login）
├── config.yaml      公众号列表 + 用户画像 + 评分权重
├── fetcher.py       微信官方 API 拉取 + fakeid/文章内容缓存
├── filter.py        AI 多维度评分 + 开场白生成
├── prefilter.py     规则预过滤（关键词组合 + 技术白名单）
├── dedup.py         跨源去重（bigram Jaccard + 正文相似度）
├── notifier.py      多渠道推送（8 种渠道）
├── auth.py          微信扫码登录 / token 管理
├── assets/          Newsletter 头图等静态资源
├── .env.example     环境变量模板
└── requirements.txt 依赖列表
```

## 推送效果

### 邮件 Newsletter

- 固定品牌 banner 头图
- AI 生成的卷首语（每日不同）
- 本期概要（按分类导览）
- 按 category 分区展示（深度分析 / 行业观察 / 工具推荐 / 活动资讯）
- 每篇文章：标题 + 缩略图 + 摘要 + 推荐理由 + 维度评分

### IM / 推送通知

- **飞书**：交互式卡片消息，每篇文章带"阅读原文"按钮
- **钉钉 / 企业微信 / Telegram**：Markdown 格式消息
- **Server酱 / PushPlus**：推送到微信，适合个人使用
- **Bark**：iOS 原生推送通知

## 技术栈

- **AI 评分**：支持 Anthropic Claude / OpenAI / DeepSeek / 通义千问 / 硅基流动 / Ollama 等任何 OpenAI 兼容接口
- **结构化输出**：Pydantic v2
- **微信 API**：mp.weixin.qq.com 官方接口（searchbiz + appmsgpublish）
- **正文提取**：BeautifulSoup + lxml
- **去重**：中文 bigram tokenization + Jaccard 相似度 + Union-Find

## Roadmap

- [ ] 飞书卡片模板升级（跟 Gmail 对齐）
- [ ] 周报汇总（攒一周评分日志，周末出精选）
- [ ] 更多信息源（RSS、Product Hunt、Twitter）
- [ ] 两阶段过滤（embedding 粗筛 + LLM 精排）
- [ ] Web UI

## License

MIT

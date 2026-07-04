# wechat-radar · 设计与路线图

> 本文件是这个项目的单一真相源：记录当前部署现状、闭环设计、后续 TODO 与关键决策。
> 最近更新：2026-06-24

---

## 一、当前现状（2026-06-24 已完成）

本地部署，按"唤醒电脑就跑"的方式运行（不走死定时）。

| 项 | 状态 |
|----|------|
| 部署位置 | `~/Projects/wechat-radar`，独立 venv（system python 3.9） |
| 采点模型 | DeepSeek（`deepseek-chat`，OpenAI 兼容），key 在 `.env`（已 gitignore） |
| 推送渠道 | 飞书自定义机器人 webhook（复用原 OpenClaw 那个，已验证 success） |
| 微信登录 | 已扫码，token 约 3 天失效，失效后飞书提醒重扫 |
| 公众号 list | `config.yaml` 共 62 个，已校正 |
| 个性化 profile | 已按本人视角填写（PM、ToB Agent、AI native 协同、要深度），评分已个性化 |
| 推送量 | `top_n=12`，从 Top20 收窄为 Top12，进入两周控噪实验 |
| 自动运行 | launchd `com.cathy.wechat-radar`，每天 09:00 / 睡着则唤醒后补跑一次；runner 有当天去重 |
| 闭环复盘 | 主流程成功后自动生成 `reports/feedback-loop-all.md`；也可手动跑 `python3 scripts/analyze_feedback_loop.py --days 7 --write` |
| Cubox 偏好画像 | `cubox_preferences.py` 从收藏/划线/批注/来源/标签提炼动态偏好，注入评分 prompt，并生成 `reports/cubox-preference-profile.md` |

运行机制与本地 `daily-product-radar`（`com.cathy.wiki.daily-radar`）同构：`StartCalendarInterval` + `RunAtLoad=false`，错过会在唤醒/开机后补跑。

相关文件：
- 项目代码：`~/Projects/wechat-radar/`
- runner：`~/.claude/scheduled-tasks/run-wechat-radar.sh`
- 日志：`~/.claude/scheduled-tasks/wechat-radar/`
- launchd plist：`~/Library/LaunchAgents/com.cathy.wechat-radar.plist`

---

## 二、闭环设计：Cubox 驱动的两层系统

核心思路：用 Cubox 收藏（你真实关注什么的强正信号）作共享上游，喂给两个下游消费者。

```
        Cubox 收藏（原始关注信号）
                │
   ┌────────────┴────────────┐
   ▼                         ▼
 ① radar 闭环             ② 个人 context 层
  （窄：优化推送）          （宽：让所有 Claude 日常更懂我）
```

### 🔗 共享地基 — `cubox_client.py`（已实现）
- 职责：**纯 Python HTTP** 调 Cubox 内部 API 抓收藏+标注 → 标准化 → 落地 `cubox_favorites.json`（增量合并，历史正样本不丢）。**不开 Chrome**；token 在 `.env` 的 `CUBOX_TOKEN`（取自网页版 localStorage，失效报 -1006 时刷新）。
- 数据源（探针已验证）：
  - 收藏：`GET /c/api/norm/card/query?page=N&orderType=4&asc=false&isArticle=false&archiving=false`（page 分页）
  - 标注过的文章：`GET /c/api/norm/card/marked/query?...&pageSize=30&lastCardId=游标`
  - 单条划线+批注：`GET /c/api/norm/mark/list?...&lastMarkId=游标`（一次 50 条，含 `text`/`noteText`）
  - 鉴权：header `Authorization: <裸 token>`（非 Bearer、非 cookie）。详见 web-access 站点经验 `cubox.pro.md`。
- 对外接口：`get_favorites() -> list[dict]`。
- **抓一次，A 和 B 都从这里读**。

### 📦 标准原料 schema（A / B / ③ 共用输入，2026-06-24 定）
从 Cubox 沉淀的每条原料都打「类型」标签，AI 才能区分"你的话"和"别人的字"：

**文章级**（每篇收藏）：标题 / 原文 url / 文章摘要(`description`，**别人的字**) / 你的标签(`tags`) / 收藏夹(`groupName`) / 收藏时间(`createTime`) / 标注密度(`markCount`) / 来源公众号(从 url `__biz` 解析)
**标注级**（每条划线，最高价值）：划线原文(`text`) / 你的批注(`noteText`，**你自己的话**) / 颜色(`colorType`) / 所属文章

用途分配：
- **A（打分正例）**：标题 + 划线原文 + **你的批注**(`noteText`) + 文章摘要 + 类型 + **来源公众号**（同类来源可加分）→ 当高分锚点
- **B（蒸馏关于你）**：划线原文 + 批注(**核心，你的话**) + 标题 + **文章摘要**(定位 = 主题背景/佐证，打标「文章内容」，权重低于你的划线批注，不单独成结论) + 收藏时间(看兴趣偏移) + 收藏夹/标签(看你的分类认知)
- **③（信源自进化）**：来源公众号 + 收藏频次

> 文章摘要给 B 的用法：与你的划线+批注**配合**——划线/批注=你在意什么、摘要=这篇讲什么，合起来蒸馏才准；摘要是"别人的字"，只作背景，**不可单独当你的观点**（防过度归纳）。

### ① 子系统 A — radar 偏好闭环（窄、纯自动）
不动现有评分链路，只在评分前插一步：把真实收藏过的文章当 few-shot 正例塞进 DeepSeek 打分 prompt，让它对照打分。

数据流（每天唤醒，runner 触发）：
1. 抓 Cubox 收藏 → 更新 `cubox_favorites.json`
2. 评分时，profile 之后追加动态偏好块 + 正例块：
   - 动态偏好块：从 Cubox 收藏/划线/批注中学习高权重主题、常见正反馈来源、主动标签。
   - 正例块：最近代表收藏/标注文章，作为高分参照。
3. 筛选 Top N → 推送飞书（不变）
4. （可选）命中分析：今天 `scoring_log` 与收藏对照，算「你收藏的文章系统平均打了几分」，写进日志，看闭环有没有变准

当前 runner 实际链路：

```
launchd 09:00 / 唤醒补跑
  → run-wechat-radar.sh 当天去重
  → cubox_client.py 刷新收藏/划线正样本
  → analyze_cubox_preferences.py 更新 Cubox 偏好画像报告
  → source_evolve.py --apply 自动补信源
  → main.py 抓公众号、评分、推送、保存 scoring_log
  → analyze_feedback_loop.py --write 生成闭环复盘报告
```

可靠性约束：
- 微信接口返回 `ret=200003 invalid session` 时视为 token/session 失效，主流程应失败并提醒扫码，避免把「0 篇文章」当成功运行。
- 只有 `main.py` exit 0 才写 `.last-run-date`，失败留待下次唤醒重试。

组件：
| 文件 | 职责 | 对外接口 |
|------|------|---------|
| `cubox_client.py`（新） | 抓收藏、落地 | `get_favorites()` |
| `feedback.py`（新） | 挑代表正例、生成 few-shot 文本 | `build_positive_examples(n) -> str` |
| `filter.py`（小改） | 评分 prompt 注入正例（profile 之后） | — |
| runner（改） | 唤醒时序加「先抓 Cubox」 | — |

**已拍板的细节（2026-06-24）：**
- **正样本信号层**：收藏 ∪ 标注过(`markCount>0`) 的并集；标注过的权重更高（更深的投入信号）。星标不用（未使用）。
  - 实现备注：收藏（`/c/api/norm/card/query`）+ 标注过的文章（`/c/api/norm/card/marked/query`）两个 endpoint 都已在 `cubox_client.py` 实现并验证。
- 正例选取：起步从上述并集里取**最近 10 篇**（按时间倒序，标注过的优先）。够用再升级成"按类目均衡选"。
- 范围：优先取 `mp.weixin` 的公众号；不足 10 篇放宽到全部。
- 每条给：标题 + 来源 + 摘要/你的标注，**不塞全文**（省 token）。

### ② 子系统 B — 个人 context 层（宽、人在环）
把 Cubox 收藏蒸馏成「关于你的结论」，进 Claude memory，影响你所有 Claude 日常。

- 载体：Claude memory（`~/.claude/.../memory/`）+ CLAUDE.md。
- 关键原则：**不把收藏原样喂 AI**（那是别人的文章、是素材不是结论）；中间必须有"蒸馏"工序，把收藏提炼成"你在反复关注什么、兴趣往哪偏、认同/存疑哪类判断"。
- **蒸馏原料（2026-06-24 验证）**：除收藏文章元数据外，**最高价值的是你的划线+批注**——`/c/api/norm/mark/list`（游标 `lastMarkId`，一次 50 条）能拿到每条划线原文(`text`)、你的批注(`noteText`)、颜色(`colorType`)、所属文章(`card`)。划线/批注是"直接关于你"的信号（你亲手标的重点、你自己写的话），蒸馏时应作为核心输入，远胜从文章反推。
- 形式（已定）：**生成摘要给你过目，你勾选才入 memory** —— 每周两次（频率可调），蒸馏 agent 读新增收藏 → 生成「近期关注摘要」候选清单（每条标来源+置信度）→ 写入「待勾选」文件 `pending-memory-review.md` → **下次你打开 Claude Code 时，SessionStart hook 检测到该文件并提醒你**（不走飞书）→ 你和 Claude 一起过、勾选 → 只把勾中的写进 memory。
- 通知方式：**Claude 内提醒**（pending 文件 + SessionStart hook），不用飞书。理由：勾选本来就只能在 Claude 里做，通知即处理、同地闭环；飞书的"实时推送到手机"对这种要坐下来做的事没价值。（radar 每天的日报仍走飞书，互不冲突。）
- 为什么人在环：radar 打错分顶多漏一篇；memory 写错会持续污染之后所有对话。你是最终把关人，兜住"过度归纳"风险。

### ③ 信源自进化（新增，让 list 自己生长）
不只优化"排序"，也优化"信源覆盖"本身。
- 逻辑：统计收藏来自哪些公众号 → 某号「不在 `config.yaml` list」且「累计收藏 ≥ 3 篇」（阈值可调）→ 自动加进 list 开始监控。
- 把关（已定）：**自动加入 + 当天飞书日报附「本次新增监控：XXX（你收藏了它 N 篇）」+ 可随时删**。你始终知情、能反悔。
- 技术点（实现时验证）：收藏 url 只带 `__biz`（公众号加密 id），不含中文名，而 list 用中文名 → 需从文章页解析出公众号名（像探针那样先确认能稳定拿到）。

### 📍 落地顺序（渐进，不一次性全做）
```
第0步  Cubox 抓取探针 ← 验证能否稳定抓到收藏+url（已完成）
  ①   radar 闭环（接现有评分链路，纯自动，先见效）（已完成）
  ②   context 层（人在环，慢工，影响所有 Claude 日常）
  ③   信源自进化（让 list 自己生长）（已完成）
```

---

## 三、TODO / Roadmap

### 待实现（按顺序）
- [x] **第0步：Cubox 抓取探针**（2026-06-24 完成）— 验证可行：Cubox 有内部 API，能稳定拿到收藏列表 + 原文 url + 元数据。方案见上方「共享地基」。信号层已定（见子系统 A）。
- [x] **子系统 A：radar 闭环**（2026-06-24 完成）— `cubox_client.py`(纯 HTTP 抓收藏+标注) + `feedback.py`(few-shot 正例) + `filter.py` 注入「高分参照」段 + runner 加抓取步。已端到端 `--dry-run` 验证：链路稳定、排序贴合画像。**纯 Python 调 Cubox API，不开 Chrome**。
- [x] **子系统 B：context 层**（2026-06-24 完成并验证）— `distill-SKILL.md` + `run-distill.sh` + launchd `com.cathy.wechat-radar-distill`（周三/周日 9:30，已 load）+ SessionStart hook（检测 `pending-memory-review.md` 提醒勾选，在 `~/.claude/settings.json`）。蒸馏用 **headless claude**，读划线+批注+现有 memory → 候选清单。
  - 端到端验证通过：首跑蒸馏出 5 条候选，质量高（**读现有 memory 去重**、严格区分"你的话 vs 别人的字"、谨慎标置信度）。Cathy 勾选 3 条入 memory（更新 `user-profile.md` + 新建 `views-positions.md`）。处理后清空 pending 文件。
- [x] **机制 ③：信源自进化**（2026-06-24 完成）— `source_evolve.py`：统计来源号(`resolve_account`)，不在 list 且累计 ≥3 篇 → 自动加进 `config.yaml` + 飞书通知；已集成进 radar runner（`--apply`，每天检查）。dry-run 验证逻辑跑通：当前收藏来自 16 个号、均未达 3 篇阈值，暂无新增（随收藏积累会触发）。阈值 `MIN_COLLECT=3` 可调。

### 升级项（later，先用简单版跑起来再说）
- [ ] 子系统 A 正例选取：**最近 10 篇 → 按类目均衡选**（避免某一类收藏刷屏正例）
- [x] 子系统 A 正例升级（2026-06-24 完成）：正例已含 **划线(`mark.text`) + 批注(`noteText`) + 来源公众号**（解析文章页 `js_name`，按 `__biz` 缓存）。`cubox_client` 增 `fetch_marks`/`resolve_account`，`feedback` 输出富正例，已验证。
- [x] 子系统 A **命中分析模块**（2026-07 完成）— `scripts/analyze_feedback_loop.py` 对照 scoring logs 与 Cubox 收藏/划线，输出推荐命中率、正样本召回、漏报、命中来源/标签、维度差异；兼容旧入口 `scripts/validate_loop.py`。
- [x] 子系统 A **Cubox 偏好画像**（2026-07 完成）— `cubox_preferences.py` 按行为权重提炼偏好：划线/批注 > 标注文章 > 普通收藏；输出高权重主题、正反馈来源、Cubox 标签，并注入评分 prompt。
- [ ] 子系统 A **噪音控制实验**：当前 Top20 召回高但噪音偏多。历史日志基线：Top20 正样本召回 100%、命中率 11.2%；Top12 召回 88.9%、命中率 16.7%，推荐量少 40%。已切到 `top_n=12`，观察两周后复盘。
- [ ] 子系统 B 蒸馏**频率/触发调优**（每周是否合适、要不要按收藏量触发）
- [ ] 取数方式：若网页抓取不稳，迁移到 **Readwise/Notion 中转**（全自动更稳，但需账号）
- [ ] context 层：除 Cubox 外，接入更多信号源（如收藏时的高亮/标注、其它稍后读）

### 下一阶段计划（2026-07：让 loop 真正变准）

目标：不继续堆功能，先验证「Cubox 正反馈 + Top12 控噪 + 动态偏好画像」是否真的让每日推荐更轻、更准。

#### 1. Top12 两周观察
- [ ] 连续跑两周 `top_n=12`，不要频繁改阈值。
- [ ] 每周看一次 `reports/feedback-loop-all.md` / `reports/feedback-loop-7d.md`。
- [ ] 关键指标：
  - 推荐命中率：推给我的文章里，有多少后来被 Cubox 收藏/划线。
  - 正样本召回：我后来收藏/划线的文章里，有多少当时被 radar 推出来。
  - 强信号召回：带划线/批注的文章是否被推出来。
  - 主观体感：每天 Top12 里真正愿意点开的有几篇。
- [ ] 成功标准：
  - 推荐命中率高于 Top20 基线 11.2%。
  - 强信号召回保持接近 100%。
  - 每天推荐量明显更轻，不再像信息流。

#### 2. 漏报优先级规则
- [ ] 每周检查 `Missed Positives`。
- [ ] 如果漏掉的是普通收藏，先接受，不急着调。
- [ ] 如果漏掉的是强信号（划线/批注），必须分析原因：
  - 分数是否低于 `min_score`？
  - 是否排在 Top12 外但分数很高？
  - 是来源没被识别，还是主题被低估？
  - 是 prompt 没理解，还是权重不合适？
- [ ] 只有连续出现强信号漏报时，才考虑调 prompt、权重或 TopN。

#### 3. Cubox 偏好画像巡检
- [ ] 每周看一次 `reports/cubox-preference-profile.md`。
- [ ] 检查高权重主题是否仍符合真实偏好：
  - Agent 产品与工作流
  - AI Native 组织与协同
  - AI 产品设计与 PM 判断
  - Coding Agent / Skill / Context
  - 深度方法论与第一性原理
  - 创业、商业化与公司分析
- [ ] 如果画像漂移，例如把泛 AI 新闻、融资通稿、情绪热点也当成偏好，补充负向规则。

#### 4. 补弱负反馈
- [ ] 先做报告，不直接自动降权。
- [ ] 定义弱负反馈候选：
  - 被推荐但 7 天内没有收藏、没有划线、没有批注。
  - 多次推荐但长期没有正反馈的来源。
  - 高频出现但从未命中的标签/类别。
- [ ] 在 feedback report 里新增：
  - `Likely Noise Accounts`
  - `Likely Noise Tags`
  - `Recommended But Never Saved`
- [ ] 观察两周后，再决定是否把弱负反馈注入 prompt。

#### 5. 周报复盘
- [ ] 每周自动生成一个简短周报：
  - 本周推荐是否变准？
  - 哪些来源贡献最大？
  - 哪些主题/标签最命中？
  - 哪些来源/标签可能是噪音？
  - 本周是否需要调整 TopN、prompt、信源或偏好画像？
- [ ] 周报先写入 `reports/weekly-loop-review-YYYY-MM-DD.md`，不推送；稳定后再考虑飞书推送。

#### 6. 暂时不做
- [ ] 暂不训练模型。
- [ ] 暂不自动改评分权重。
- [ ] 暂不根据弱负反馈直接降权。
- [ ] 暂不继续扩大公众号 list，除非 source_evolve 达到阈值自动加入。

### 运维 / 待办
- [ ] **Gmail 推送渠道**待配（`.env` 里注释着，要用补应用专用密码）
- [ ] 留意「**无界社区 mixlab**」仍是模糊匹配（指向稳定但显示名对不上，拿到确切名再替换）
- [ ] profile 先按现版跑几天，看命中率/排序效果再决定是否调
- [ ] 微信 token 约 3 天失效（首次到 2026-06-28），飞书会提醒重新 `python main.py --login`
- [ ] **Cubox token 失效**：抓取报 `-1006` 时，从 Cubox 网页版 `localStorage['token']` 取新值更新 `.env` 的 `CUBOX_TOKEN`。runner 已容错：抓取失败不阻断主流程，退用本地旧正样本。（token 失效频率未知，先观察）

---

## 四、关键决策记录（为什么这么选）

| 决策 | 选了什么 | 理由 |
|------|---------|------|
| 运行方式 | launchd 唤醒时跑，不走 cron | 笔记本不常开；launchd 错过会唤醒补跑，cron 会直接跳过 |
| 反馈信号 | 只用 Cubox 收藏起步 | 强正信号、本来就在做、零额外负担；先不引入要每天点的按钮 |
| Cubox 取数 | 网页抓取 | 全自动、0 成本、已登录；官方 API 只能写不能读 |
| 学习机制 | few-shot 正例注入 | 比"压缩成文字画像"更具体，仍零训练；比 embedding 打分器轻 |
| context 层载体 | Claude memory | 直接进每次会话 context，最影响日常 |
| 蒸馏入库方式 | 生成摘要、人工勾选 | memory 写错会持续污染，准确性门槛高，人在环兜底 |
| 摘要通知方式 | Claude 内提醒（pending 文件 + SessionStart hook），不用飞书 | 勾选本来就在 Claude 里做，通知即处理、同地闭环；实时推送对这种事没价值 |
| 蒸馏频率 | 每周两次（可调） | 比每周一次更跟得上关注变化，又不至于太碎 |
| 沉淀原料 | 划线`text`+批注`noteText`+标题+摘要+收藏时间+标签/收藏夹+来源号 | 划线/批注是"直接关于你"的最高价值信号；每条打类型标签让 AI 区分你的话 vs 别人的字 |
| 文章摘要给 B 的定位 | 当主题背景/佐证，权重低于你的划线批注，不单独成结论 | 摘要是别人的字，单独喂会把"文章讲什么"误当"你的观点"，过度归纳 |
| 来源号→A、批注→A | 来源号让同类信源加分；批注是最精准的"你为什么觉得好" | 都是更贴近你真实偏好的强信号 |
| 信源自进化把关 | 自动加 list + 飞书通知 + 可删 | 让 list 自己生长又不失控；你始终知情、能反悔 |

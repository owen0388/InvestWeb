# 项目复现与理解文档

> **适用读者**：未参与本项目开发，需要理解设计并独立完成部署、运行与二次开发的人。
> **最后核对**：2026-11-21（基于实际代码与配置校验，非凭印象）
> **实测规模**：28 张数据表 / 66 个 HTTP 接口 / 15 个定时任务 / 103 个自动化测试用例（全部通过）

---

## 目录

1. [项目定位与核心价值](#1-项目定位与核心价值)
2. [功能清单与用户流程](#2-功能清单与用户流程)
3. [整体架构与模块划分](#3-整体架构与模块划分)
4. [关键技术选型及原因](#4-关键技术选型及原因)
5. [核心业务流程与数据处理逻辑](#5-核心业务流程与数据处理逻辑)
6. [主要接口、数据结构与配置](#6-主要接口数据结构与配置)
7. [依赖与运行环境](#7-依赖与运行环境)
8. [安装启动步骤](#8-安装启动步骤)
9. [构建、部署与验证](#9-构建部署与验证)
10. [常见问题与注意事项](#10-常见问题与注意事项)

---

## 1. 项目定位与核心价值

### 1.1 它是什么

一套面向**个人投资者**的**研究与模拟**工作台：把市场行情、财经新闻、宏观数据采集入库，做事件识别与资产评分，再给出每日机会雷达与组合权重建议，并支持回测与交易日志归因。

**它不是交易系统**：不对接券商、不执行委托、不产生真实收益。所有产出都是研究参考，页脚与接口都带免责声明。

### 1.2 解决什么问题

个人投资者自己做研究时的三个痛点：

| 痛点 | 本项目的做法 |
| --- | --- |
| 信息过载，不知道哪些数据该看 | 把"候选池准入 → 质量分 → 趋势状态 → 机会分"做成层层筛选的漏斗，每天只输出 0–10 条 |
| 信号无法验证，分不清策略差还是没执行 | 交易日志强制记录 `decision`（照做 / 没做 / 自主），归因时分组对比 |
| 明明数据过期了却看不出来 | 数据新鲜度分层可见，过期标黄；不补足数量、不编造证据 |

### 1.3 明确不做什么

- 不做自动化交易、不对接下单接口
- 不承诺收益，不输出"必涨"类判断
- 数据源无数据时如实显示"未配置"，不用 0 或旧值冒充
- 样本不足时输出"只作记录，不能据此判断策略有效"，不给结论
- 不采集实时行情（用的是延迟/收盘数据，页面已标注）

---

## 2. 功能清单与用户流程

### 2.1 功能域一览

| 功能域 | 主要页面 | 核心能力 |
| --- | --- | --- |
| **市场总览** | `/` | 61 只精管品种报价、风险状态（6 维加权）、近期事件、资产类别表现 |
| **机会雷达** | `/radar` | 每日 0–10 条候选 + 20 项研究卡片 + 数据新鲜度 + 手动刷新 |
| **新闻与事件** | `/events` | 169 篇原始新闻、去重聚类、91 个事件、证据链、事件→ETF 映射 |
| **资产目录** | `/assets`、`/assets/:symbol` | 12 个一级类别、61 个登记品种、单品分析（评分/价格/证据） |
| **ETF 候选池** | `/universe` | 全市场 1,680 只 ETF 的准入筛选（约 250 只候选）、质量分、主题归类 |
| **资产评分** | `/opportunities` | 51 个品种的六维评分与 7 种建议动作 |
| **宏观与风险** | `/macro` | 中国/美国/全球 × 5 指标 × 6 年序列、风险状态分项 |
| **资产配置** | `/allocation` | 投资者画像、持仓 CRUD + CSV 导入、战术倾斜、约束投影、风险分析 |
| **交易日志** | `/journal` | 录入/平仓/归因，样本不足不给结论 |
| **策略回测** | `/backtest` | 单策略回测 + 五策略对比、样本内外拆分、参数敏感性 |
| **数据源管理** | `/sources` | 11 个数据源的健康状态、运行记录、手动触发 |
| **系统设置** | `/settings` | 预算、保留策略、调度器状态 |

### 2.2 三条典型用户流程

**流程 A：每天盘后看机会**

```
打开雷达页 → 看顶部数据新鲜度（是否过期）
  → 点「更新数据」选「全部」（约 8 分钟）或「仅重算雷达」（10 秒）
  → 看漏斗统计（候选池 → 参与评分 → 达标 → 今日输出）
  → 点开某张卡片：驱动/风险/相反证据/失效条件/建议动作/权重区间
  → 若决定操作 → 到交易日志页录入一笔，填失效条件
```

**流程 B：验证信号是否有用**

```
积累若干笔交易日志（尤其要记「信号出现但没做」）
  → 打开交易日志页看归因四宫格（样本数/胜率/盈亏比/期望）
  → 样本 <20 时页面明确标注"数字仅供参考"，不给结论
  → 对照「照做 vs 没做」的期望差，判断执行纪律
  → 或到回测页跑五策略对比，看信号策略是否优于买入持有
```

**流程 C：从新闻看到 ETF**

```
打开事件页 → 选一个重要事件
  → 查看证据链（带原文链接）
  → 点「事件→ETF 映射」：两级匹配（资产类别 + 主题族）
  → 每族最多 2 只，附信息差因子（覆盖度差/关注度低/传导时滞）
```

### 2.3 数据流总览

```
外部数据源（新浪/东财/腾讯/世界银行/FRED/Tushare）
      ↓ 定时任务 + 手动触发
  market_observations / raw_documents / macro_observations
      ↓ 规则引擎（去重→聚类→分类→证据→传导）
  events + event_evidence + event_impacts
      ↓ 质量分 + 趋势特征 + 状态机
  etf_universe（候选池） + asset_signals（评分）
      ↓ 四道过滤 + 主题族去重 + 类别配额
  radar_snapshots + 研究卡片（20 项）
      ↓
  trade_journal（人工录入） → 归因统计
      ↓
  回测（五策略对比） → 策略可信度
```

---

## 3. 整体架构与模块划分

### 3.1 三层结构

```
┌──────────────────────────────────────────────┐
│  前端 Vue 3 SPA（vite 构建 → dist，静态托管）    │
│  13 个页面 + ECharts + 响应式（桌面侧栏/手机抽屉）│
└───────────────────┬──────────────────────────┘
                    │ HTTP（同源，开发期跨域）
┌───────────────────▼──────────────────────────┐
│  API 层 FastAPI（66 个路由，10 个模块）          │
│  参数校验(pydantic) / Basic 认证(可选) / 审计    │
└───────────────────┬──────────────────────────┘
┌───────────────────▼──────────────────────────┐
│  服务层（业务逻辑，纯 Python，可独立测试）        │
│  collector / news_pipeline / event_engine /    │
│  scoring / allocation / backtest /             │
│  opportunity(_radar) / etf_history / journal / │
│  refresh / backup / notification / regime      │
└───────────────────┬──────────────────────────┘
┌───────────────────▼──────────────────────────┐
│  存储层 SQLite（data/invest.db）+ 配置 YAML     │
│  28 张表 / APScheduler 15 个定时任务            │
└──────────────────────────────────────────────┘
```

### 3.2 目录结构

```
InvestWeb/
├── backend/
│   ├── app/
│   │   ├── main.py                 FastAPI 入口、CORS、SPA 托管、异常处理、可选认证
│   │   ├── core/
│   │   │   ├── config.py           Settings（pydantic-settings，读 .env）
│   │   │   ├── db.py               引擎/会话/init_db（create_all）
│   │   │   ├── auth.py             HTTP Basic 中间件（未设密码时跳过）
│   │   │   ├── timeutil.py         中国时区
│   │   │   └── jsonutil.py         NaN/Inf 清洗（SQLite 不支持非有限浮点）
│   │   ├── models/entities.py      28 张表的 ORM 模型
│   │   ├── providers/              数据源适配器（base + sina/eastmoney/macro/optional）
│   │   ├── services/               业务服务（14 个模块，见下表）
│   │   ├── jobs/scheduler.py       15 个定时任务 + 任务记录
│   │   ├── api/                    10 个路由模块，共 66 个接口
│   │   └── ai/                     模型适配层（未配置密钥时跳过）
│   ├── tests/                      12 个测试文件，103 个用例
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── api/                    endpoints.ts（接口封装）
│   │   ├── pages/                  13 个页面
│   │   ├── layouts/AppLayout.vue   侧栏 + 顶栏 + 移动端抽屉
│   │   ├── components/             EChart、DataFreshness 等
│   │   ├── stores/app.ts           全局状态（regime、loading）
│   │   └── styles/main.css         设计系统 + 响应式断点
│   └── dist/                       构建产物（生产部署时由后端托管）
├── config/                         7 个 YAML 配置（见第 6 节）
├── data/
│   ├── invest.db                   SQLite 数据库
│   └── backups/                    每日自动备份（保留 7 份）
├── scripts/
│   └── screenshot-mobile.js        CDP 移动端截图工具
├── docs/                           架构、部署、执行计划、截图
├── start.ps1                       Windows 一键启动（UTF-8 with BOM）
├── Dockerfile / docker-compose.yml
└── .env.example                    环境变量模板
```

### 3.3 服务层模块职责

| 模块 | 职责 |
| --- | --- |
| `collector_service` | 采集编排、数据源运行记录、幂等写入、限频退避 |
| `news_pipeline` | 新闻清洗、哈希去重、入库 |
| `event_engine` | 事件聚类、分类、重要性、证据链、传导、版本快照 |
| `event_etf_service` | 事件→ETF 两级映射（资产类别 + 主题族），信息差因子 |
| `scoring_service` | 资产评分（模块化权重、缺失处理、数据质量门槛） |
| `regime_service` | 市场风险状态（6 维加权） |
| `allocation_service` | 战术倾斜 + 有界单纯形投影 + 风险分析 |
| `backtest_service` | 单策略回测 + 五策略对比（通用 `_simulate` 引擎） |
| `universe_service` | ETF 全市场目录、分类、准入门槛、主题归类 |
| `etf_history_service` | 候选池日线补全、净值/折溢价、质量分计算 |
| `opportunity_service` | 趋势特征 + 7 状态机 + 机会分 + 失效条件 |
| `opportunity_radar_service` | 每日候选汇总、四道过滤、快照、20 项卡片 |
| `radar_card.py` | 研究卡片生成（20 项字段，含相反证据与建议动作） |
| `journal_service` | 交易日志归因（胜率/盈亏比/期望/样本保护） |
| `refresh_service` | 四层数据新鲜度 + 统一手动刷新 + 并发互斥 |
| `backup_service` | SQLite backup API 备份、告警汇总 |
| `notification_service` | 站内通知 + Webhook（企业微信格式自动适配） |

---

## 4. 关键技术选型及原因

| 选型 | 原因 | 取舍 |
| --- | --- | --- |
| **FastAPI + pydantic** | 自带 OpenAPI 文档（`/docs`），参数校验声明式 | 无 |
| **SQLAlchemy 2.0 + SQLite** | 个人单机使用，零运维；单文件便于备份迁移 | 并发写受限，故 uvicorn 单进程（多 worker 反而锁冲突） |
| **不用 Alembic** | 表结构仍在演进，用 `init_db` 的 `create_all` + 列补齐策略 | 适合前期，正式多人协作前应建 baseline 迁移 |
| **pandas / numpy** | 日线窗口指标（MA、动量、波动、回撤）用向量运算；单机数据量 9 万行内可接受 | 回测单次约 5 秒 |
| **APScheduler** | 15 个定时任务内嵌在进程里，无外部依赖 | 进程挂了任务也停，故 systemd `Restart=always` |
| **Vue 3 + Vite** | 构建快、TS 友好；SPA 被后端静态托管，部署只有 1 个端口 | 无 SSR |
| **ECharts** | 金融图表（净值曲线、K 线、分项条形）现成组件 | 包较大（1MB+ gzip 344KB） |
| **纯 CSS 设计系统** | 不引 UI 框架，样式集中在 `main.css`，控制体积 | 组件需手写 |
| **SSH + nginx 反代** | 生产复用现有 nginx，按 Host 分流，不动其他站点 | 需懂虚拟主机 |
| **Let's Encrypt + certbot** | 免费、自动续期 | 子域名需单独签发，不随主域名证书 |

**明确不引入**：Redis、Celery、消息队列、微服务、ORM 迁移框架。个人使用规模下它们只会增加部署负担。

---

## 5. 核心业务流程与数据处理逻辑

### 5.1 数据采集链

```
定时/手动触发
  ↓
run_market_quotes()      → 61 只精管品种快照（每 10 分钟）
run_market_history()     → 日线（交易日 16:20）
  ↓ 自动回退：东方财富 → 腾讯 → 新浪
  （东财短时高频会断连，一次运行内连续失败 2 次即熔断该源）
  ↓
market_observations      （带 source_code、trade_date，幂等去重）
```

**关键原则**：每条观测记录来源与时间；重复采集不会重复写入；数据源错误写入 `data_source_runs` 并在 `/api/health` 与数据源页显示。

### 5.2 ETF 候选池（全市场 → 约 250 只）

```
全市场 1,680 只 ETF
  ↓ global_exclude：货币、杠杆、商品（非黄金）、港币货币、QDII 部分
  ↓ amount_basis：成交额（近 5 日均值）
  ↓ classes：按类别阈值筛（宽基/行业/跨境/债券/商品）
  ↓ dedupe：同跟踪指数只留成交额最大一只
  ↓ supplement：主题覆盖补充（确保关键主题至少有代表）
  ↓
候选池（约 250 只）+ 分类（broad/sector/cross_border/bond/commodity/money）
```

**分类关键词**：`SECTOR_STRONG_KEYWORDS` 优先于宽基前缀（"创业板软件"是行业不是宽基）；
主题族 `etf_theme_groups.yaml`（12 个族）用于去重与事件映射。

### 5.3 质量分（0–100）

对候选池每只 ETF 计算 4 项指标（可算多少算多少，缺失时置信度下降）：

| 维度 | 权重 | 说明 |
| --- | --- | --- |
| 流动性 | 40 | 成交额（近 5 日均值） |
| 规模 | 30 | 最新资产净值（亿元） |
| 折溢价稳定性 | 20 | 价格与净值偏离的波动 |
| 历史长度 | 10 | 日线根数 |

**门槛**：质量分 < 40 的不进入机会列表（质量分是门槛不是加分项）。

### 5.4 趋势特征与 7 状态机

对每只候选的日线（≥ 260 根）计算：

```
dev20 / dev60      收盘价相对 MA20/MA60 的偏离
ret20 / ret60      20/60 日收益率
vol20              20 日年化波动
drawdown_250       距 250 日高点回撤
ma_order           均线多头/空头排列
bars               样本长度
```

状态机（`opportunity_service.decide_state`）：

```
NO_DATA   数据不足（< 260 根）
SCAN      趋势未修复
WATCH     趋势刚确认，待观察
CANDIDATE 可研究（主信号）
WAIT      追高或波动过大，等待
TRIM      趋势走坏，考虑降低
EXIT      失效，应退出
```

**不追高规则**：`ret20 > 15%` 或 `ret60 > 35%` 直接降级为 WAIT。

### 5.5 机会分与四道过滤（雷达的核心）

```
机会分 = 0.6 × 趋势强度 + 0.4 × 同类相对强弱分位
        趋势强度：多头排列 +35 + dev20/20%×35 + ret60/30%×30（上限 100）
        相对强弱：按资产类别分组，样本 <5 不排名
```

每日输出的四道过滤（缺一条就会退化成"每天 10 条废话"）：

| 过滤 | 规则 | 实测拦截 |
| --- | --- | --- |
| 1. 质量门槛 | quality_score < 40 不入 | 77 只 |
| 2. 机会分门槛 | score < 40 不入（不凑数） | 4 只 |
| 3. 同主题族上限 | `MAX_PER_THEME = 2`（酒/消费/消费50 同属 consumer 族） | 7 只 |
| 4. 类别配额 | `MAX_PER_CATEGORY = 3`（不做配额时曾出现 10 条里 9 条是债券） | — |
| 附加 | 货币类直接排除 | 4 只 |

**输出上限 10 是上限不是目标**：今天 14 条达标、去重后 6 条就是 6 条，不补足。

### 5.6 研究卡片（20 项字段）

每条候选生成一张卡片，包含：标题、资产、指标变化、首次发现时间、原始证据、主要逻辑、上涨驱动、下跌风险、传导机制、影响期限、信号强度、证据置信度、**是否已提前反应**、**相反证据**、失效条件、关注触发条件、建议动作（7 种之一）、建议权重区间、相比上期变化、更新时间。

**两条硬规则**：
- 没有关联新闻事件时明确写"本卡基于价量，未关联具体新闻事件"，**不编造证据**
- 相反证据找不到也写"未发现明显矛盾"，**不静默省略**

### 5.7 事件流水线

```
原始新闻（169 篇）
  ↓ 哈希去重 + 标题相似聚类（cluster_similarity_threshold）
  ↓ 规则分类（monetary_policy / trade_tariff / technology / military …）
  ↓ 重要性 0–100（来源数加成、关键词加成）
  ↓ 证据链（event_evidence，带原文链接）
  ↓ 传导机制（event_impacts：对 12 个一级类别的方向/强度/置信度）
  ↓ 结论版本快照（event_analysis_versions）
事件（91 个）
```

**事件→ETF 映射**（`event_etf_service`）：必须主题命中（类别只作辅助），每族最多 2 只。
信息差因子只实现 3 类可算的（覆盖度差、关注度低、传导时滞），**预期分歧因无一致预期数据明确不计算**。

### 5.8 组合配置与约束投影

```
基础权重（示例组合 8 类）
  + 战术倾斜（评分与基准的偏差 × max_tactical_tilt）
  ↓
有界单纯形投影（对偶二分求解）
  约束：单品种 ≤20%、单行业 ≤35%、类别上下界、换手 ≤12%、最小交易阈值 0.5%
  ↓
目标权重版本（allocation_targets，带 version）
```

**为什么用投影不用归一化**：简单归一化会破坏上下限，投影能在满足约束的前提下最接近目标。

### 5.9 回测（五策略对比）

通用引擎 `_simulate(price, codes, weight_fn, rebalance_days, cost_bps)`：

| 策略 | 权重函数 |
| --- | --- |
| `buy_hold` | 初始权重后不调仓（weight_fn=None） |
| `rebalance` | 每 N 日回到基准权重 |
| `score_tilt` | 评分倾斜（现有主策略） |
| `trend` | 只在 CANDIDATE 状态持权重，其余转现金 |
| `rotation` | 每期持有 20 日动量前 3，等权 |

**防前视**：每个调仓日只用该日之前的收盘价；不回填宏观数据（vintage 未接入）；样本内外 70/30 拆分。

**必须有的一条**：权重每日随价格漂移（`weights * (1+ret)` 归一）。曾经漏掉这一步，导致"买入持有"和"定期再平衡"跑出完全相同结果。

**样本保护**：260 天样本下轮动 +12% 不能当作结论；文档中明确标注"样本仅 260 天"。

### 5.10 交易日志与归因

表 `trade_journal` 的关键设计：

| 字段 | 作用 |
| --- | --- |
| `decision` | `followed`（照做）/ `skipped`（信号出现但没做）/ `manual`（自主决定）——没有它就分不清是策略差还是没执行 |
| `invalidation` | **建仓时必填**（API 422 拦截），事后补等于没写 |
| `signal_score` / `signal_state` | 下单当时的信号快照，事后不可改 |
| `pnl` / `pnl_pct` / `holding_days` | 平仓自动计算，扣双边费用 |

归因输出：胜率、平均盈利/亏损、盈亏比、期望、profit factor、平均持有天数；按 decision 与资产类别分组。
**样本 <20 笔时 `reliable=false` 并警告"只作记录"**。

---

## 6. 主要接口、数据结构与配置

### 6.1 接口分组（66 个）

| 模块 | 数量 | 代表接口 |
| --- | --- | --- |
| `ops` | 18 | `GET /api/health`、`GET /api/system/info`、`GET/POST /api/sources`、`GET /api/jobs`、`POST /api/ops/backup`、`POST /api/notifications/test` |
| `portfolio` | 16 | `GET/PUT /api/portfolios`、持仓 CRUD、`POST .../holdings/import`（CSV）、风险分析 |
| `news_events` | 7 | `GET /api/events`、`GET /api/events/{id}`、`GET /api/events/{id}/etfs`、`GET /api/opportunities` |
| `journal` | 5 | `GET/POST /api/journal`、`PUT /api/journal/{id}/close`、`GET /api/journal/stats` |
| `universe` | 5 | `GET /api/universe`、`GET /api/universe/summary`、`POST /api/universe/refresh`、`POST /api/universe/quality` |
| `catalog` | 4 | `GET /api/asset-classes`、`GET /api/instruments`、`PATCH /api/instruments/{symbol}` |
| `market` | 4 | `GET /api/market/overview`、`GET /api/market/regime`、`GET /api/market/quotes` |
| `backtest` | 4 | `POST /api/backtests`、`POST /api/backtests/comparison`、`GET /api/backtests/{id}` |
| `refresh` | 2 | `GET /api/refresh/status`、`POST /api/refresh`（scope: quotes/universe/quality/radar/all） |
| `opportunity_radar` | 1 | `GET /api/opportunity-radar/daily?limit=10` |

**通用约定**：错误返回 `{"detail": "..."}`；列表接口返回 `{"items": [...], "total": n}`；
`/api/health` 永远免认证（探活用），其余接口在设置 `AUTH_PASSWORD` 后受 Basic 认证保护。

### 6.2 关键接口示例

```http
GET /api/opportunity-radar/daily?limit=10
→ {
  "as_of": "2026-11-05",
  "message": "今日 6 条候选（共 14 条达标，按机会分取前 10；已跳过：8 只同主题重复）",
  "universe": 237, "evaluated": 143, "qualified": 14,
  "buckets": { "CANDIDATE": 18, "WATCH": 18, "SCAN": 106, ... },
  "skipped_same_theme": 8, "below_min_score": 4, "min_score": 40,
  "card_fields": 20,
  "items": [
    {
      "code": "sh512690", "name": "酒ETF鹏华", "theme": "family:consumer",
      "title": "消费 趋势转强 · 酒ETF鹏华", "action": "积极研究",
      "score": 73.0, "rs_score": 98, "quality_score": 68,
      "first_seen": "2026-11-05",
      "change": { "dev20": 0.072, "ret60": 0.097, "vol20": 0.31, "drawdown_250": -0.07 },
      "logic": "价格在 MA20 上方，且 MA20 > MA60",
      "drivers": ["均线多头排列…", "价格站上 MA20 约 7.2%", ...],
      "risks": ["价量层面未发现突出风险；不代表无风险"],
      "counter_evidence": ["已检查价量与相对强弱，未发现明显矛盾证据"],
      "priced_in": "未见明显提前反应：20 日变动 +6.9%",
      "transmission": "主题（消费）资金流入 → 跟踪指数上行 → 酒ETF鹏华 跟随；无杠杆",
      "horizon": "中期（约 20–60 个交易日）",
      "invalidation": "收盘跌破 MA20（约 0.421）或距 250 日高点回撤超过 25%",
      "watch_triggers": ["收盘价跌破 MA20（约 0.421）", ...],
      "weight_range": { "range": "1%–3%", "note": "先小仓试探…" },
      "delta": { "prev_score": 66.5, "score_change": 7.26, "prev_state": "WATCH", "state_changed": true },
      "updated_at": "2026-11-05 15:35:10"
    }
  ]
}
```

```http
POST /api/journal
{
  "symbol": "sh512690", "name": "酒ETF鹏华", "decision": "followed",
  "actual_price": 0.421, "quantity": 10000, "fee": 5,
  "thesis": "消费修复 + 趋势确认",
  "invalidation": "收盘跌破 MA20（必填）"
}
→ 200 或 422（缺失效条件时）

PUT /api/journal/{id}/close
{ "exit_price": 0.45, "exit_reason": "target", "fee": 5 }
→ 自动计算 pnl=280.00、pnl_pct=6.65%、holding_days
```

### 6.3 数据表（28 张）

| 分组 | 表名 |
| --- | --- |
| 目录 | `asset_classes`, `instrument_master`, `instrument_aliases` |
| 采集 | `data_sources`, `data_source_runs`, `market_observations`, `macro_observations`, `raw_documents` |
| 事件 | `events`, `event_evidence`, `event_impacts`, `event_analysis_versions` |
| 信号 | `asset_signals`, `etf_universe`, `radar_snapshots` |
| 组合 | `portfolios`, `portfolio_holdings`, `allocation_targets`, `risk_constraints`, `investor_profiles`, `strategy_configs` |
| 交易 | `trade_journal` |
| 回测 | `backtest_runs` |
| 运行 | `model_usage`, `notifications`, `system_settings`, `audit_logs`, `job_runs` |

### 6.4 配置文件（`config/`，全部 YAML）

| 文件 | 作用 | 关键项 |
| --- | --- | --- |
| `asset_classes.yaml` | 12 个一级类别、36 个节点 | 每类的 `module`（对应专属分析模块） |
| `instruments.yaml` | 61 个登记品种 | symbol、类别、数据源、是否跟踪 |
| `data_sources.yaml` | 11 个数据源 | code、适配器、频率、是否需密钥 |
| `signal_rules.yaml` | 评分与事件规则 | `event_scoring`、`scoring.modules`、`regime.components`、`ai.escalation` |
| `risk_limits.yaml` | 组合约束 | `portfolio`（单品种 20%、换手 12%、成本 8bps）、`class_limits`、`profiles` |
| `etf_universe_rules.yaml` | 候选池准入 | `amount_basis`、`global_exclude`、`classes`、`dedupe`、`supplement` |
| `etf_theme_groups.yaml` | 主题族 | 12 个族 + 中文标签，用于去重与事件映射 |

**环境变量（`.env`，参考 `.env.example`）**：

```ini
APP_ENV=prod                 # dev / prod
DATABASE_URL=sqlite:////opt/investweb/data/invest.db
AUTH_USER=admin              # 为空则关闭认证
AUTH_PASSWORD=               # 填了就启用 Basic 认证（/api/health 例外）
SCHEDULER_ENABLED=1          # 0 关闭定时任务
TIMEZONE=Asia/Shanghai
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:8000
# AI（可选，不填则规则引擎独立工作）
LLM_ENABLED=0  LLM_API_KEY=  LLM_BASE_URL=  LLM_MODEL_CHEAP=  LLM_MODEL_STRONG=
LLM_DAILY_BUDGET_USD=0.5     # 预算耗尽自动停调用
# 通知（可选）
WEBHOOK_URL=                 # 企业微信群机器人会自动适配 msgtype:text
FRED_API_KEY=                # 宏观（可选）
TUSHARE_TOKEN=               # 数据补充（可选）
```

---

## 7. 依赖与运行环境

### 7.1 版本要求

| 项 | 版本 | 说明 |
| --- | --- | --- |
| Python | **3.11+**（实测 3.12.3） | 项目用了 `list[dict]` 等现代语法 |
| Node.js | 18+（实测 22.x） | 仅前端构建需要 |
| 数据库 | SQLite（Python 自带） | 无需单独安装服务 |
| 操作系统 | Windows 10/11、Ubuntu 22.04/24.04 | 均已验证 |
| 内存 | 建议 ≥ 4G | 候选池全量刷新时约 1.5G |
| 磁盘 | ≥ 5G | 数据库 + 备份 + 构建产物约 300M |

### 7.2 Python 依赖（`backend/requirements.txt`）

```
fastapi>=0.115    uvicorn>=0.30      SQLAlchemy>=2.0
pydantic>=2.7     pydantic-settings>=2.3
httpx>=0.27       pandas>=2.2        numpy>=1.26
APScheduler>=3.10 PyYAML>=6.0        python-multipart>=0.0.9
pytest>=8.0
```

### 7.3 前端依赖（`frontend/package.json`）

- 运行时：`vue`、`vue-router`、`echarts`
- 构建时：`vite`、`@vitejs/plugin-vue`、`typescript`、`vue-tsc`
- 脚本：`dev`（开发服务器）、`build`（类型检查 + 构建）、`preview`

---

## 8. 安装启动步骤

### 8.1 Windows（推荐方式）

```powershell
# 1. 确认环境
python --version     # 必须 3.11+，注意别是 2.7
node --version       # 18+

# 2. 一键启动（会自动建虚拟环境、装依赖、生成 .env、启动前后端）
cd D:\InvestWeb
.\start.ps1          # 开发模式：后端 8000 + 前端 5173（热更新）
.\start.ps1 -Prod    # 生产模式：构建前端后仅 8000（单端口）
.\start.ps1 -SkipInstall   # 跳过依赖安装，快速启动
```

若提示「禁止运行脚本」：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

### 8.2 手动启动（任何平台，最稳）

> 下文命令为 bash/zsh 语法。Windows 自带 PowerShell 7 可直接使用；
> Windows PowerShell 5.1 里 `curl` 是 `Invoke-WebRequest` 的别名，请改用 `curl.exe`，
> 并把 `$API` 变量写法换成实际地址。

```bash
# 后端
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt      # Windows
# .venv/bin/pip install -r backend/requirements.txt        # Linux/macOS
cp .env.example .env

.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
# → 打开 http://127.0.0.1:8000  （SPA 由后端托管，含接口文档 /docs）

# 前端开发模式（可选，另一个终端）
cd frontend && npm install && npm run dev
# → http://localhost:5173
```

### 8.3 首次启动后的数据初始化

服务启动会自动：创建数据库 → 写入资产目录/数据源/示例组合 → 启动 15 个定时任务。

数据要有内容需按顺序触发（可点「数据源管理」或用接口）：

```bash
# 顺序执行，每步可观察结果。POST 请求必须带 Content-Type 和 -d，否则接口收不到参数。
API=http://127.0.0.1:8000

curl -X POST $API/api/refresh -H "Content-Type: application/json" -d '{"scope":"quotes"}'      # 行情
curl -X POST $API/api/jobs/market_history/run                                             # 日线
curl -X POST $API/api/refresh -H "Content-Type: application/json" -d '{"scope":"universe"}'    # 候选池（约 5 分钟）
curl -X POST $API/api/refresh -H "Content-Type: application/json" -d '{"scope":"quality"}'     # 质量分（约 3 分钟）
curl -X POST $API/api/refresh -H "Content-Type: application/json" -d '{"scope":"radar"}'       # 雷达

# 或一次跑完（约 8 分钟）：
curl -X POST $API/api/refresh -H "Content-Type: application/json" -d '{"scope":"all"}'

# 查看进度与数据新鲜度
curl $API/api/refresh/status
```

### 8.4 运行测试

```bash
cd backend
../.venv/Scripts/python -m pytest -q      # 期望：103 passed
```

---

## 9. 构建、部署与验证

### 9.1 本地生产构建

```bash
cd frontend && npm run build              # 产出 frontend/dist
cd ..
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
curl http://127.0.0.1:8000/api/health     # 200 = 正常
```

`build` 脚本会先跑 `vue-tsc --noEmit`（类型检查），失败则不产出。

### 9.2 Docker

```bash
docker compose up -d --build
# 后端 8000，前端构建在镜像内，单端口访问
```

### 9.3 生产部署（已验证：Ubuntu 24.04 + nginx）

完整记录见 `docs/DEPLOYMENT_BAIDU.md`。要点：

```bash
# 1. 上传代码与数据库（排除 .venv/node_modules）
tar czf app.tgz backend config frontend/dist data/invest.db docs README.md Dockerfile docker-compose.yml
scp app.tgz root@server:/tmp/

# 2. 解压 + 装依赖
ssh root@server
mkdir -p /opt/investweb && tar xzf /tmp/app.tgz -C /opt/investweb
cd /opt/investweb && python3 -m venv .venv
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r backend/requirements.txt

# 3. 配置 .env（生产务必考虑 AUTH_PASSWORD 或限制来源）
cat > .env <<'EOF'
APP_ENV=prod
DATABASE_URL=sqlite:////opt/investweb/data/invest.db
SCHEDULER_ENABLED=1
TIMEZONE=Asia/Shanghai
EOF

# 4. systemd 常驻
cat > /etc/systemd/system/investweb.service <<'EOF'
[Unit]
Description=InvestWeb
After=network.target
[Service]
User=root
WorkingDirectory=/opt/investweb
Environment=PATH=/opt/investweb/.venv/bin:/usr/bin:/bin
ExecStart=/opt/investweb/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now investweb

# 5. nginx 反代（按域名分流，不影响同机其他站点）
# 见 docs/DEPLOYMENT_BAIDU.md 的完整 server 块

# 6. HTTPS（子域名需单独签发证书，与主域名证书互不覆盖）
certbot certonly --webroot -w /var/www/certbot -d invest.example.com \
  --non-interactive --agree-tos --register-unsafely-without-email
```

### 9.4 部署后验证清单

| 验证项 | 命令 / 方式 | 期望 |
| --- | --- | --- |
| 服务存活 | `systemctl status investweb` | `active` |
| 健康检查 | `curl -s http://127.0.0.1:8000/api/health` | 200，含 counts |
| 数据量 | 看 health 的 `counts` | `market_obs` 在增长 |
| 定时任务 | 日志 `grep 调度器已启动` | 共 15 个任务 |
| 认证 | 无密码访问 `/api/portfolio` | 设了密码则 401，否则 200 |
| 雷达 | `GET /api/opportunity-radar/daily` | 200，`items` 数量 ≤ 10 |
| 备份 | `POST /api/ops/backup` | 返回 `size_mb` |
| 前端 | 手机浏览器打开 | 汉堡菜单、KPI 两列、无横向溢出 |
| 同机其他站点 | 原域名访问 | 不受影响 |

---

## 10. 常见问题与注意事项

### 10.1 环境类

**Q1：`start.ps1` 报 `ParserError: Unexpected token 'Add'`**
脚本是 UTF-8 with BOM；若被编辑器改存为无 BOM 的 UTF-8，PowerShell 5.1 会按 GBK 解码，中文提示里的字符被当成字符串结束符。
**解**：编辑器另存为「UTF-8 with BOM」，或直接用 8.2 节的手动启动命令。

**Q2：`python` 指向 2.7**
部分 Windows 机器 PATH 里有 Python 2.7。`start.ps1` 会校验版本（≥3.11），但手动 `python -m venv` 不会。
**解**：用 `py -3.11` 或显式路径；建好 `.venv` 后始终用 `.venv\Scripts\python`。

**Q3：中文输出乱码（Windows 控制台）**
`start.ps1` 已设 `[Console]::OutputEncoding = UTF8`；手动启动遇到乱码可先执行：
`chcp 65001`。

**Q4：依赖装不上（pip 网络问题）**
换镜像：
`.venv\Scripts\python -m pip install -r backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

### 10.2 数据类

**Q5：机会雷达显示"今日无候选"**
这**是正常结果**，不是故障。页面会说明原因：多少只趋势未修复、多少只追高、多少只质量不达标。宁可空列表也不用凑数的 10 条。

**Q6：候选池/质量分数据过期**
看雷达页顶部的数据状态条（红/黄标）。候选池刷新约 5 分钟、质量分约 3 分钟，用 `POST /api/refresh` 手动触发。
**注意**：`market_history` 只覆盖 20 只精管品种，候选池 237 只的日线由 `etf_quality` 任务（交易日 16:35）补，不在前者的范围内。

**Q7：回测结果和我预期不一致**
先检查是否区分了"买入持有"与"定期再平衡"——如果两者完全相同，说明权重没随价格漂移（历史 bug，已修）。
其次看样本长度：260 天样本不能用来判断策略优劣。

**Q8：LLM 相关接口显示未配置**
`LLM_ENABLED=0` 时规则引擎独立工作，这是设计如此。填 `LLM_API_KEY` + `LLM_ENABLED=1` 即可启用；有日预算与缓存。

### 10.3 部署类

**Q9：8000 端口从外网访问不通**
常见两层拦截：`ufw`（`ufw allow 8000/tcp`）和云安全组（控制台放行）。
**更优做法**：不开 8000，走 nginx 80/443 反代（见 9.3）。

**Q10：多个域名指向同一 IP，会不会串站点？**
不会。nginx 依据 HTTP 的 Host 头与 TLS 的 SNI 匹配 `server_name` 分流。
实测：`Host: www.xxx` → 站点 A；`Host: invest.xxx` → 站点 B，互不影响。

**Q11：子域名 HTTPS 证书不匹配**
证书按域名签发。主域名证书只含 `www` 与裸域时，子域名会报错。
**解**：`certbot certonly --webroot ...` 给子域名单独签发（免费），并配 `certbot.timer` 自动续期。

**Q12：想公开给所有人看，安全吗？**
公开前需检查：`trade_journal` 是否有真实交易、`portfolio_holdings` 是否真实持仓、`system_settings` 是否含密钥。
当前系统默认带免责声明；`AUTH_PASSWORD` 为空即完全公开，填上则启用 Basic 认证（`/api/health` 例外）。
**遗留风险**：公开后 `POST /api/refresh` 任何人可调（有并发锁返回 409，但可反复触发采集）。若担心滥用，可对非 GET 请求加来源限制。

### 10.4 开发与维护

**Q13：改动数据库表结构**
现在是 `init_db` 的 `create_all` + 列补齐。改 `entities.py` 后重启即可新增字段；改动已有字段需手动迁移或重建。
**正式多人协作前建议引入 Alembic 建 baseline。**

**Q14：截图页面显示"右侧被裁切"**
不是布局 bug。`chrome --headless --window-size=390` 在 Windows 上有约 485px 的最小窗口宽度，会截出被裁的假象。
**解**：用 `node scripts/screenshot-mobile.js <url> <out> 390 844`（走 CDP 真机模拟，并打印 `innerWidth/scrollWidth`）。

**Q15：如何升级前端？**
```bash
cd frontend && npm run build
# 生产环境再同步 dist 到服务器，无需重启后端（静态托管）
```

**Q16：如何备份/恢复？**
- 自动：每日 02:30 任务，`data/backups/`，保留 7 份
- 手动：`POST /api/ops/backup`（用 sqlite backup API，不是 cp，避免 WAL 不一致）
- 恢复：停服 → 覆盖 `data/invest.db` → 启动

### 10.5 业务理解上的坑

- **质量分是门槛不是加分**：低于 40 的产品即便趋势再好也不入选（流动性不足，纸面收益拿不到）。
- **机会分最高不代表最该买**：分数含趋势强度，往往意味着"已经涨了很多"。决策时先看「同类分位」（为什么是它）、「距 MA20」（是否追高）、「波动」（拿不拿得住）。
- **交易日志里最该记的是「没做的那笔」**：只记成交的，归因时永远分不清是信号差还是没执行。
- **信息差里的"预期分歧"当前不计算**：没有市场一致预期数据源，系统明确标注不计算，而不是返回 0 冒充。

---

## 附：文档地图

| 文档 | 内容 |
| --- | --- |
| `README.md` | 快速上手、启动命令 |
| `docs/ARCHITECTURE_V4.md` | 架构设计与演进 |
| `docs/EXECUTION_PLAN.md` | 功能执行计划与实测记录 |
| `docs/IMPLEMENTATION_STATUS.md` | 实施状态、数据量、文件清单 |
| `docs/DATABASE.md` | 表结构与迁移建议 |
| `docs/DEPLOYMENT_BAIDU.md` | 生产部署实录（含回滚） |
| `docs/DATA_SOURCES.md` | 数据源实测与权限状态 |
| `docs/KNOWN_LIMITATIONS.md` | 已知限制 |
| `docs/TEST_REPORT.md` | 测试报告 |
| `信息差.md` | 原始任务书（需求来源） |

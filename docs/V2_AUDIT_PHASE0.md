# InvestWeb V2 阶段 0 审计报告

> **审计对象**：InvestWeb 当前生产版本（本机 `D:\InvestWeb`，线上 `https://invest.mengrengjiu.com`）
> **审计日期**：2026-11-21
> **审计方式**：只读。逐项检查源码、SQLite 实际数据、YAML 配置、路由注册、前端页面与测试文件。
> **约束**：本阶段未修改任何代码、数据库表结构或生产配置。
> **证据规则**：每条判断标注来源（文件:行号 / SQL 查询 / 配置文件）。无法验证的项明确标注 **【尚未核验】**，不做假设。

---

## 目录

- [〇、结论摘要](#〇结论摘要)
- [一、审计方法与范围](#一审计方法与范围)
- [二、现有评分链路（第 1 项）](#二现有评分链路第-1-项)
- [三、28 张表的复用评估（第 2 项）](#三28-张表的复用评估第-2-项)
- [四、行业—ETF—主题映射与行情覆盖（第 3 项）](#四行业etf主题映射与行情覆盖第-3-项)
- [五、技术指标能力边界（第 4 项）](#五技术指标能力边界第-4-项)
- [六、事件关联能力（第 5 项）](#六事件关联能力第-5-项)
- [七、组合分析能力（第 6 项）](#七组合分析能力第-6-项)
- [八、回测可靠性（第 7 项）](#八回测可靠性第-7-项)
- [九、四分类清单（第 8 项）](#九四分类清单第-8-项)
- [十、修改清单（第 9 项）](#十修改清单第-9-项)
- [十一、阶段 1 最小可行方案与回滚（第 10 项）](#十一阶段-1-最小可行方案与回滚第-10-项)
- [十二、风险清单](#十二风险清单)
- [十三、尚未核验事项](#十三尚未核验事项)

---

## 〇、结论摘要

**一句话**：现有系统的"价格→趋势→机会"链路是完整且可用的，但**完全没有行业层**——没有行业目录、没有行业指数、没有行业指标、没有周期阶段。V2 的核心增量不是改造现有评分，而是**新建一个与价格无关的行业研究层**，再把两者分层输出。

六条关键结论（均有证据，见后文）：

| # | 结论 | 影响 |
| --- | --- | --- |
| 1 | 机会分 = 趋势强度 60% + 同类相对强弱 40%，**两项都由涨幅驱动**，越涨分越高 | 与路线图判断一致，是"追涨"结构性问题 |
| 2 | 评分配置中 `valuation`(15%) + `fundamental`(10%) 对权益类是**永久缺失**：代码只有 cash 模块实现估值，完全没有 fundamental 特征 | 25% 权重名义存在、实际不参与，覆盖率被拉低 |
| 3 | **代码中不存在任何 industry / cycle_stage / early_turn 概念**（全库 grep 零命中） | V2 行业层需从零建，无历史包袱也无现成资产 |
| 4 | 事件表的 `industries` 是**自由文本**（36/137 有值，如 `["半导体","医药"]`），无行业代码、无法关联行业指数或指标 | 事件→行业需要新建映射，不能直接复用 |
| 5 | **无任何成分股/穿透表**（`constituent` / `holding_map` 均不存在），组合相关性只到 7 个资产类别 | 行业重叠、底层暴露分析需新增数据源与表 |
| 6 | 行情覆盖 94,662 行 / 284 标的，但 **226 个标的只有 260 根**（约 1 年），最长 785 根 | 250 日回撤可用；120 日 RS 斜率勉强；多年周期研究不足 |

**建议**：按路线图阶段 1（行业目录 + 基准行情 + RS）小步切入，不动现有评分与前端接口。

---

## 一、审计方法与范围

### 1.1 实际检查的对象

| 类别 | 范围 | 方式 |
| --- | --- | --- |
| 后端服务 | `backend/app/services/` 17 个模块 | 逐文件读关键函数 |
| 数据模型 | `backend/app/models/entities.py` 28 张表 | 提取 `__tablename__` 与字段 |
| 配置 | `config/*.yaml` 7 个文件 | YAML 解析后打印实际值 |
| 接口 | `backend/app/api/*.py` 10 个模块 | 正则提取 66 个路由 |
| 前端 | `frontend/src/pages/` 13 个页面 + `router.ts` | 目录与路由比对 |
| 数据库 | `data/invest.db` | 直接 SQL 查询行数、字段、覆盖范围 |
| 测试 | `backend/tests/` 12 个文件 | 提取用例名；实跑 `pytest -q` |

### 1.2 验证记录

```
$ cd backend && python -m pytest -q
103 passed, 1 warning in 5.10s
```

### 1.3 本次未做的事

- 未修改任何文件、表结构、生产配置
- 未执行数据库备份（遵守"禁止修改数据库"约束；建议在阶段 1 开工前手动备份，见第十一节）
- 未验证外部数据源能力（东财是否提供中证行业指数、是否有成分股接口）→ 见第十三节

---

## 二、现有评分链路（第 1 项）

### 2.1 全景调用关系

```
market_observations（行情）
      │
      ├──► regime_service.compute_regime()            市场风险状态（6 维）
      │         └─ 写入 system_settings: last_regime / regime_history
      │
      ├──► scoring_service.score_instrument()          资产评分（6 特征，5 个实现）
      │         └─ 写入 asset_signals（51 条）
      │
      ├──► etf_history_service.refresh_quality()       候选池质量分（4 项）
      │         └─ 写入 etf_universe.quality_score
      │
      └──► opportunity_radar_service.build_daily()     机会雷达
                ├─ opportunity_service.trend_features()    趋势特征（11 个字段）
                ├─ opportunity_service.decide_state()      7 状态机
                ├─ opportunity_service.relative_strength_scores()  同类分位
                ├─ opportunity_service.opportunity_score() 机会分
                ├─ radar_card.build_card()                 20 项研究卡片
                └─ 写入 radar_snapshots（309 条）
```

### 2.2 市场风险评分（`regime_service.compute_regime`）

**文件**：`backend/app/services/regime_service.py:36-180`
**权重配置**：`config/signal_rules.yaml` → `regime.components`

| 维度 | 权重 | 计算公式（实际代码） | 数据来源 |
| --- | ---: | --- | --- |
| 权益趋势 `equity_trend` | 0.30 | `clamp(50 + dev × 400)`，dev = 沪深300 相对 MA60 偏离 | `sh000300` 日线 |
| 权益动量 `equity_momentum` | 0.20 | `clamp(50 + ret20 × 320)` | `sh000300` 20 日收益 |
| 波动率 `volatility` | 0.20 | `clamp(100 − max(0, vol−0.08) × 250)`，vol 为 20 日年化 | `sh000300` |
| 市场广度 `breadth` | 0.15 | `位于 MA60 上方的指数数 ÷ 有效指数数 × 100`（6 个宽基） | 6 个指数日线 |
| 避险资产 `haven` | 0.10 | `clamp(50 − 黄金/国债平均 ret20 × 300)` | `sh518880`、`sh511010` |
| 事件风险 `event_risk` | 0.05 | `clamp(100 − Σ(重要性/100 × 置信度 × 方向权重 × 12))`，窗口 72h | `events` 表 |

**判定阈值**（`signal_rules.yaml` → `regime.thresholds`）：

```
risk_on   ≥ 62   → 风险偏好上升
risk_off  ≤ 38   → 风险偏好下降
极端事件  ：最高重要性 ≥ 88 且 volatility 分 < 35 → 极端事件风险（优先于上两者）
数据门槛  ：可用权重覆盖 < 0.45 → "数据不足"，不输出分数（regime_service.py:150-158）
```

**当前实测状态**：`score=29.5`、`state=风险偏好下降`（来自线上页面与 `/api/market/regime`）。

**审计判断**：
- 6 维均为**价格与事件派生**，无宏观维度（PMI、社融、利率）参与 → 与"市场环境层"目标只部分吻合。
- 广度只用 6 个宽基指数，不是全市场个股广度（`CN_EQUITY_INDICES` 定义于 `regime_service.py:26`）。
- 事件风险把"事件数量"直接换算为分数下降，方向权重只有 bearish/mixed=1.0、其他=0.45，粒度粗。

### 2.3 资产评分（`scoring_service.score_instrument`）

**文件**：`backend/app/services/scoring_service.py:170-330`
**权重配置**：`signal_rules.yaml` → `scoring.modules.<module>.weights`

权益类（`equity_cn`）实际配置：

```
trend 0.30 | valuation 0.15 | fundamental 0.10 | macro 0.15 | catalyst 0.20 | liquidity 0.10
```

**各特征实现情况**：

| 特征 | 是否实现 | 公式 | 证据 |
| --- | --- | --- | --- |
| `trend` | ✅ | `clamp(50 + dev60×350 + dev20×150)` | scoring_service.py:216 |
| `risk` | ✅（未计入权重） | `clamp(vol_score + dd×100)` | :228 |
| `liquidity` | ✅ | 20 日均成交额在同类中的分位 → `20 + rank×70` | :100-112 |
| `macro` | ✅（年度数据） | 世界银行 GDP/CPI → `50 ± (值−基准)×系数`，按 `staleness_factor` 向 50 收缩 | :57-80 |
| `catalyst` | ✅ | `clamp(50 + Σ(方向×幅度×置信×重要性/100) × 45)`，窗口 30 天 | :83-97 |
| `valuation` | ⚠️ **仅 cash** | cash：`clamp(50 + (年化收益−1.5%)×2000)`；其余返回 `None` + "估值/收益率数据未接入（不猜测）" | :115-123 |
| `fundamental` | ❌ **未实现** | 代码中无对应函数；配置里却有 0.05–0.10 权重 | 全文 grep 无 `fundamental` 实现 |

**动作映射**（`_action_for`，:118-135）：

```
数据质量 < 0.40 或 置信度 < 0.35 → 暂不判断
≥72 考虑提高权重 | ≥62 积极研究 | ≥45 维持配置 | ≥35 观察等待 | ≥25 考虑降低权重 | <25 降低风险
```

**硬门槛**：价格序列 < 60 条直接返回 `total_score=None`，不评分（:180-198）。

**审计判断**：
- 权益类名义权重 25% 落在未实现的 `valuation` + `fundamental` 上，实际由剩余 75% 归一，覆盖率会反映在 `data_quality` 与 `coverage` 字段中，但**UI 上容易被忽略**。
- 无任何行业维度的投入（无行业景气、无行业利润、无供需）。

### 2.4 ETF 质量分（`quality_service.compute_quality`）

**文件**：`backend/app/services/quality_service.py:69-160`
**权重**：`DEFAULT_WEIGHTS`（同文件 :18）

| 维度 | 权重 | 计算方式 |
| --- | ---: | --- |
| 流动性 `liquidity` | 0.40 | `log10(20 日均成交额)` → 全池 rank 归一 |
| 规模 `size` | 0.30 | `log10(最新资产净值)` → rank |
| 折溢价稳定性 `premium_stability` | 0.20 | 价格/净值偏离中位数 → rank（越小越好） |
| 历史长度 `history_depth` | 0.10 | 日线根数 → rank |

**覆盖率保护**（:104-108）：某维度覆盖率 < 0.5 且非 liquidity → 整维禁用（权重归零），避免用少数样本给全池排序。
**门槛**：机会雷达要求 `quality_score ≥ 40`（`opportunity_radar_service.MIN_QUALITY`）。

**审计判断**：这是"产品可交易性"评分，与"行业是否值得投资"无关——路线图要求二者不混用，现有设计**已经分开**（质量分不进机会分，只作门槛），这点可保留。

### 2.5 机会分（`opportunity_service`）

**文件**：`backend/app/services/opportunity_service.py`

**趋势强度**（:246-258）：

```python
trend_strength = (35 if ma20 > ma60 else 0)
               + min(max(dev20, 0) / 0.20, 1.0) × 35
               + min(max(ret60, 0) / 0.30, 1.0) × 30
# 上限 100
```

**相对强弱**（:216-243）：按 `category` 分组，组内按 `ret20×0.5 + ret60×0.5` 排序，`(排名/(n−1))×100`；**组内样本 < 5 不排名**。

**机会分**（:261-273）：

```python
opportunity_score = trend_strength × 0.6 + rs_score × 0.4
# rs_score 缺失时直接返回 trend_strength（不补默认值）
```

**审计判断（关键）**：
三个加项全部是"涨得越多分越高"，`dev20` 与 `ret60` 本质是高相关动量。路线图指出"容易偏向已走强资产"——**成立**。
且 `dev20` 与 `ret60` 同属价格动量，存在重复计权（路线图 6.4 明确要求避免）。

### 2.6 七状态机（`decide_state`）

**文件**：`opportunity_service.py:86-160`；**参数**：`DEFAULTS`（:38-44）

```
min_bars 60 | chase_ret20 0.15 | chase_ret60 0.35 | max_vol 0.55 | exit_drawdown −0.25
```

**判定顺序**（优先级从高到低）：

| 顺序 | 条件 | 输出（非持仓 / 持仓） |
| ---: | --- | --- |
| 1 | `drawdown_250 ≤ −25%` | SCAN / EXIT |
| 2 | `price < MA60` | SCAN / EXIT |
| 3 | `ret20 > 15%` 或 `ret60 > 35%` | WAIT（追高） |
| 4 | `vol20 > 55%` | WATCH / TRIM |
| 5 | `price > MA20` 且 `MA20 > MA60` | **CANDIDATE** / HOLD |
| 6 | `price < MA20` 但 `> MA60` | WATCH / TRIM |
| 7 | 其他 | WATCH |

**状态转移表**（`ALLOWED_TRANSITIONS`，:26-34）显式定义，非法转移被 `apply_transition` 拒绝（:163-172）。

**审计判断**：
- 状态机只看 4 个量：价格与两条均线的关系、两个动量、波动、回撤。**没有任何"钝化/低点抬高/底部结构"判断**（详见第五节）。
- 状态语义是"机会/操作状态"，不是"周期阶段"——与路线图建议一致（应新增 `cycle_stage` 而非改造 `state`）。

### 2.7 机会雷达的四道过滤

**文件**：`backend/app/services/opportunity_radar_service.py`

| 常量 | 值 | 作用 |
| --- | ---: | --- |
| `MIN_QUALITY` | 40 | 质量门槛 |
| `MIN_SCORE` | 40 | 机会分门槛（不凑数） |
| `MAX_PER_THEME` | 2 | 同主题族上限 |
| `MAX_PER_CATEGORY` | 3 | 类别配额 |
| `EXCLUDED_CATEGORIES` | `{"money"}` | 货币类排除 |
| `DEFAULT_LIMIT` | 10 | 输出上限（是上限不是目标） |

**主题族**：`config/etf_theme_groups.yaml`，12 个族（consumer/tech/healthcare/finance/new_energy/military/cyclical/dividend/realestate/infrastructure/rate_bond/credit_bond/broad_cn）。

**审计判断**：主题族是**粗粒度标签**（"医药"一个族包含创新药、医疗器械、中药），**不是行业层级**。V2 要的"行业→细分主题→指数→ETF"多对多映射，现有配置无法表达。

---

## 三、28 张表的复用评估（第 2 项）

### 3.1 全表清单与实际行数

| # | 表名 | 行数 | V2 复用判断 |
| ---: | --- | ---: | --- |
| 1 | `asset_classes` | 36 | ✅ 直接复用（一级/二级分类） |
| 2 | `instrument_master` | 61 | ✅ 复用（可扩展行业指数标的） |
| 3 | `instrument_aliases` | — | ✅ 复用（别名匹配） |
| 4 | `data_sources` | 11 | ✅ 复用 |
| 5 | `data_source_runs` | — | ✅ 复用 |
| 6 | `market_observations` | 94,662 | ✅ 复用（可承载行业指数日线） |
| 7 | `macro_observations` | 90 | ⚠️ 结构可复用，**数据不足**（仅世界银行年度 5 指标） |
| 8 | `raw_documents` | 248 | ✅ 复用 |
| 9 | `events` | 137 | ✅ 复用（时间字段齐全） |
| 10 | `event_evidence` | 152 | ✅ 复用 |
| 11 | `event_impacts` | 570 | ⚠️ 只能到资产类别级，需扩展行业维度 |
| 12 | `event_analysis_versions` | — | ✅ 复用 |
| 13 | `asset_signals` | 51 | ⚠️ 结构可复用，但**分项缺行业维度** |
| 14 | `portfolios` | 1 | ✅ 复用 |
| 15 | `portfolio_holdings` | 7 | ✅ 复用 |
| 16 | `allocation_targets` | 76 | ✅ 复用 |
| 17 | `risk_constraints` | — | ✅ 复用 |
| 18 | `investor_profiles` | 1 | ✅ 复用 |
| 19 | `strategy_configs` | — | ✅ 复用（可存模型版本与权重） |
| 20 | `backtest_runs` | 3 | ✅ 复用 |
| 21 | `model_usage` | 0 | ✅ 复用（AI 未启用） |
| 22 | `notifications` | — | ✅ 复用 |
| 23 | `system_settings` | — | ✅ 复用（regime 快照存在此表） |
| 24 | `audit_logs` | — | ✅ 复用 |
| 25 | `job_runs` | — | ✅ 复用 |
| 26 | `etf_universe` | 1,680 | ✅ 复用（候选 235 只） |
| 27 | `trade_journal` | 0 | ✅ 复用 |
| 28 | `radar_snapshots` | 309 | ✅ 复用 |

### 3.2 现有表能否承载 V2 行业层

**结论：不能。需要新增，但只需 3–4 张，不必重造。**

| V2 需求 | 现有表能否承载 | 缺口 |
| --- | --- | --- |
| 行业目录（代码、父级、基准指数、模块） | ❌ | `asset_classes` 是资产分类（12 类），粒度是"股票/债券/黄金"，不是"半导体/化工" |
| 行业↔指数/ETF 多对多映射（含权重、生效日期） | ❌ | `instrument_aliases` 是别名，无映射语义；`etf_universe.category` 是 6 个粗分类 |
| 行业指标观测（观察日/发布日/采集日/频率/修订号） | ❌ | `macro_observations` 有 `period_date`/`fetched_at`，但绑定宏观指标，无 `industry_code`、无 `revision_no` |
| 行业每日/每周因子快照 | ❌ | `asset_signals` 按 `instrument_id` 组织；`radar_snapshots` 只有 code/state/score |
| 评分分项版本化 | ✅ 可复用 | `asset_signals` 实际字段：`fundamental` `valuation` `trend` `macro` `liquidity` `catalyst` `risk` `components(JSON)` `missing_inputs` `version` `data_quality`——**分项列齐全且带 version**，直接扩展 JSON 即可，无需新建评分快照表 |

**建议新增最小集合**（与路线图 4.2 一致，但按现有代码习惯调整命名）：

1. `industry_master`（行业目录，约 8–12 行起）
2. `industry_instrument_map`（行业↔指数/ETF，多对多，带 `effective_from` / `effective_to` / `weight` / `source`）
3. `industry_observations`（行业指标观测，必须含 `observation_date` / `published_at` / `collected_at` / `frequency` / `revision_no`）
4. `industry_signal_snapshots`（每日因子与阶段，含 `model_version`）

**不需要新增**：`score_snapshots`。现有 `asset_signals` 已有 `as_of + version` 唯一约束，可直接扩展 JSON 分项字段；新建通用评分表会与它职责重叠。

---

## 四、行业—ETF—主题映射与行情覆盖（第 3 项）

### 4.1 现有映射层次

```
asset_classes（12 个一级 / 36 个节点，如 CN_EQ → CN_EQ_INDUSTRY）
      ↓（仅分类归属，无权重无生效日期）
etf_universe（1,680 只，6 个粗分类：broad 37 / sector 129 / cross_border 52 / bond 20 / commodity 7 / money 4）
      ↓（名称关键词匹配）
etf_theme_groups.yaml（12 个主题族，如 consumer 含"消费/白酒/酒/食品/家电/旅游…"）
```

**判断**：
- 有"ETF→主题族"的**一对多打标**（`opportunity_radar_service.theme_key`），但**没有"行业"这一层**。
- 主题族是关键词匹配，非人工维护的映射表，**无生效日期、无权重、无映射来源**。
- 一个 ETF 只会落进一个族（按最长关键词优先），**无法表达"一只 ETF 覆盖多个细分行业"**。

### 4.2 行业 ETF 实际覆盖（候选池 129 只 sector）

| 关键词 | 只数 | 关键词 | 只数 |
| --- | ---: | --- | ---: |
| 芯片 / 半导体 | 9 | 消费 | 8 |
| 医药 | 8 | 通信 | 6 |
| 新能源 | 6 | 证券 | 5 |
| 有色 | 5 | 军工 / 光伏 / 银行 / 地产 / 机器人 / 传媒 | 各 2 |
| 化工 / 煤炭 / 计算机 / 酒 | 各 1 | | |

**结论**：路线图建议的 8 个试点行业中，ETF 端**基本都有代表产品**（半导体、AI硬件/通信、化工、锂电/新能源、电力设备、黄金/有色、创新药、工业自动化）。

### 4.3 行业指数（基准）缺失

`instrument_master` 中 `CN_EQ_INDEX` 只有 **7 个宽基**：

```
sh000001 上证指数 | sh000300 沪深300 | sh000905 中证500 | sh000852 中证1000
sz399001 深证成指 | sz399006 创业板指 | sh000688 科创50
```

**没有任何中证/国证行业指数**（如中证半导体 931865、中证医药 000933）。

**影响**：
- 做行业 RS 只能用**行业 ETF 价格**代替行业指数 → 引入跟踪误差、折溢价、流动性噪音。
- 路线图阶段 1 要求"为行业基准指数补齐日线历史"，这是**新增数据**而非改造。

### 4.4 历史行情覆盖

| 指标 | 实际值 |
| --- | --- |
| 总行数 | 94,662 |
| 不同标的 | 284 |
| 日期范围 | 2025-09-23 ~ 2026-11-21（约 14 个月） |
| 来源分布 | 东财 70,686 / 新浪 12,002 / 腾讯 11,761 / 新浪快照 213 |

**每标的日线根数分布**：

| 根数 | 标的数 | 含义 |
| ---: | ---: | --- |
| 785 | 26 | 约 3 年（2023-11 起） |
| 784 | 14 | 同上 |
| 272 / 271 / 260 / 253 | 9 / 2 / **226** / 1 | 约 1 年 |
| **≥250 根** | **278** | 可算 250 日回撤 |

**审计判断**：

| 分析需求 | 所需长度 | 是否满足 |
| --- | --- | --- |
| 250 日回撤 | 250 | ✅ 278 个标的满足 |
| MA250 与斜率 | 250+ | ✅ 勉强（260 根可算 250 日均线，但斜率样本极短） |
| 120 日 RS 斜率 | 120+ | ✅ |
| 250 日区间振幅/低点抬高 | 250+ | ⚠️ 仅 1 个区间，统计意义弱 |
| 多年周期对比（牛熊分组） | 3 年+ | ❌ 仅 40 个标的有 3 年，且不是行业指数 |

---

## 五、技术指标能力边界（第 4 项）

### 5.1 现有特征全清单

`opportunity_service.trend_features()` 返回 **11 个字段**（:60-84）：

```
price, ma20, ma60, dev20, dev60, ma_order, ret20, ret60, vol20, drawdown_250, bars
```

`scoring_service` 额外计算：`max_drawdown(近 120 日)`、`annualized_vol`、`series_quality`。

### 5.2 逐项对照路线图 5.1 要求

| 路线图要求 | 现状 | 判断 |
| --- | --- | --- |
| MA20/60/120/250 数值 | 只有 MA20、MA60 | ⚠️ 缺 120/250 |
| 均线**斜率**与斜率变化 | 无 | ❌ **未实现** |
| 价格偏离均线 | ✅ dev20/dev60 | ✅ |
| 行业指数相对沪深300 的 20/60/120 日比值与斜率 | 无 | ❌ **未实现** |
| 行业在行业池内的横截面排名及**排名变化** | 有同类分位（rs_score），**无排名变化** | ⚠️ 部分 |
| **下跌钝化**：新低频率、距前低距离、下跌/上涨日幅度比、利空后相对跌幅 | 无 | ❌ **未实现** |
| **底部结构**：区间持续时间、振幅、低点抬高、回踩深度、平台上沿、突破后守住 | 无 | ❌ **未实现** |
| **成交量**：涨跌量比、突破量比、回调缩量 | 无（`amount` 只用于流动性分位） | ❌ **未实现** |
| 波动率收敛、上下行波动差异 | 只有 vol20 | ❌ 未实现 |
| 动量变化率（20/60/120 收益及其变化） | 只有 ret20/ret60 绝对值 | ❌ 缺"变化率" |

### 5.3 能否识别五类目标形态

| 目标 | 现有能力 | 结论 |
| --- | --- | --- |
| **下跌钝化** | 无新低频率、无跌幅对比、无事件后相对表现 | ❌ **完全无法识别** |
| **RS 改善** | 有当日同类分位，但无历史分位序列、无斜率、无排名变化 | ❌ 只能看静态水平，看不出"改善" |
| **低点抬高** | 无局部极值检测 | ❌ **无法识别** |
| **底部区间** | 只有 `drawdown_250`（距高点距离），无区间判定 | ❌ **无法识别** |
| **早期拐点** | 状态机要求 `price > MA20 && MA20 > MA60`，即**已经确认转势**后才给 CANDIDATE | ❌ 结构上不可能早于趋势确认 |

**结论（重要）**：现有系统**在结构上就不可能识别早期拐点**——`CANDIDATE` 的判定条件是"价格站上 MA20 且 MA20 已上穿 MA60"，这是趋势**已确认**的时点，不是拐点。要识别早期拐点必须新增特征与独立阶段，不能靠调阈值实现。

---

## 六、事件关联能力（第 5 项）

### 6.1 事件表时间字段（完备）

`events` 表实际字段（SQLite `pragma table_info`）：

```
occurred_at     事件发生时间
published_at    首次公开时间
collected_at    系统采集时间
+ priced_in（是否已反应）、counter_evidence（相反证据）、invalidation（失效条件）
+ what_happened / what_uncertain / what_changed / watch_items
+ source_count、evidence_count、reliability、novelty、direction、confidence
```

**判断**：路线图 7 要求的"发生/发布/采集三时间"与"证据链、新颖度、相反证据"**已具备**，无需新建表。

**但 `status` 一列名不符实**：实际查询 137 条事件，`status` 全部为 `'auto'`，含义是**分析引擎来源**（auto / manual / ai），**不是"事件验证状态"**（未验证 / 部分验证 / 已证实 / 被证伪）。路线图要求的验证状态需要**新增独立字段**（如 `verification_status` + `verified_at` + `verification_note`），不能复用 `status`。

### 6.2 能否关联到具体行业指标

| 环节 | 现状 | 证据 |
| --- | --- | --- |
| 事件 → 行业 | `events.industries` 是**自由文本 JSON 数组**，36/137 有值，样例 `["半导体","医药","有色"]` | SQL 查询 |
| 行业文本 → 行业代码 | ❌ 无 `industry_master`，文本未归一 | 全库无 industry 表 |
| 事件 → 资产类别 | ✅ `event_impacts.asset_class_code`（570 条，10 个类别） | SQL |
| 事件 → 具体行业指标 | ❌ 无行业指标表 | — |
| 事件 → ETF | ✅ 两级映射（类别 + 主题族，每族 ≤2） | `event_etf_service.py` |

**结论**：事件能到"资产类别"和"ETF"，**到不了"行业"和"行业指标"**。`industries` 自由文本是唯一线索，但未归一、无代码、无映射，不能直接用于评分。

### 6.3 能否识别"市场反应"

| 需求 | 现状 |
| --- | --- |
| 事件前后行业相对收益 | ❌ 无（无行业序列、无事件窗口计算） |
| 事件前后成交变化 | ❌ 无 |
| 已反应程度（`priced_in`） | ⚠️ 字段存在，但雷达卡片里的 `priced_in` 是用 **ret20 大小**推断的（`radar_card._priced_in`），**不是事件窗口反应** |

**判断**：无法衡量"事件发生后市场是否已反应"，现有 `priced_in` 名不符实。

---

## 七、组合分析能力（第 6 项）

### 7.1 现有能力（`allocation_service.analyze_risk`，:401-470+）

| 能力 | 实现 | 粒度 |
| --- | --- | --- |
| 组合年化波动 | `√(wᵀΣw)`，Σ 来自 250 日收益协方差 | **7 个资产类别代表品种** |
| 相关性矩阵 | `matrix.corr()` | **7×7 类别级** |
| 集中度 HHI | `Σw²` | 类别级 |
| 低流动性权重占比 | 按 `liquidity_tier` 汇总 | 类别级 |
| 压力测试 | 4 个硬编码情景（权益−20%、利率+100bp、原油+30%、人民币−5%） | 线性近似 |
| 最大回撤（组合） | 用类别权重回放 | 类别级 |

**覆盖保护**：可计算权重 < 55% 时不输出波动率（:419-421）。

### 7.2 逐项对照路线图第八节

| V2 需求 | 现状 | 判断 |
| --- | --- | --- |
| ETF 成分股穿透，计算真实行业暴露 | 无成分表、无成分数据 | ❌ **完全不具备** |
| 同指数/同主题/同产业链 ETF 重叠度 | 有主题族标签，但无权重、无成分，算不出真实重叠 | ❌ 不具备 |
| 资产收益相关性 | ✅ 但只到 7 个类别代表品种 | ⚠️ 粒度不够 |
| 波动率与风险贡献 | 有组合波动，**无单资产风险贡献分解** | ⚠️ 部分 |
| 市场风险状态约束总风险预算 | 有 `regime.state`，但未参与仓位上限计算 | ❌ 未打通 |
| 行业机会高但风险高时压上限 | 无行业层 | ❌ 不具备 |
| "现金/等待"作为明确目标 | 有 `CASH` 类别（min 5% / max 50%），但无"主动不配置"语义 | ⚠️ 部分 |

**结论**：组合层是**资产类别级**，比 V2 要求低两个层级（缺"行业"与"成分"）。要做穿透，必须有成分股数据源（当前无）→ 见第十三节未核验项。

---

## 八、回测可靠性（第 7 项）

### 8.1 现有防护（做得对的部分）

| 项目 | 状态 | 证据 |
| --- | --- | --- |
| 前视偏差（价格） | ✅ 每个调仓日只用 `price.iloc[:i+1]` | `backtest_service._simulate` |
| 宏观数据回填 | ✅ **主动排除**：假设里写明"仅使用趋势/动量/波动，不含宏观与事件" | `backtest_runs.assumptions` |
| 交易成本 | ✅ 单边 8bps，按换手计费 | `risk_limits.yaml` |
| 换手约束 | ✅ 组合层 ≤12%/次 | 同上 |
| 样本内外拆分 | ✅ 70/30 | `metrics.in_sample` / `out_of_sample` |
| 异常数据保护 | ✅ 单日收益 >40% 直接中止回测 | `backtest_service.py` |
| 权重漂移 | ✅ 已修（每日 `w×(1+r)` 归一） | 见代码注释 |
| 多策略基准 | ✅ buy_hold / rebalance / score_tilt / trend / rotation | `STRATEGY_LABELS` |

### 8.2 现有缺陷

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| **幸存者偏差** | ❌ 未处理 | 代表品种固定为当前目录中的 6 只（`REPRESENTATIVE`），退市/清盘产品不在池中；`assumptions` 自己也承认 |
| **宏观数据修订版（vintage）** | ❌ 未接入，且**结构上无处存放** | 全库扫描确认无任何 `revision_no` / `vintage` 字段，只有 `period_date` 与 `published_at`。年度世界银行数据是修订后的值；当前靠"不使用宏观"回避，**V2 引入行业基本面后这个问题会正面暴露** |
| **ETF 历史池** | ❌ 无 | 只用当前候选池，无法还原"当时符合准入条件的产品" |
| **行业指数成分还原** | ❌ 无 | 无成分数据 |
| **滚动 / Walk-forward** | ❌ 无 | 只有一次 70/30 切分 |
| **分市场环境验证** | ❌ 无 | 无牛熊/震荡分组 |
| **滑点与折溢价** | ⚠️ 部分 | 有 bps 成本，无滑点模型、无折溢价损耗、无停牌处理 |
| **回撤恢复时间、置信区间** | ❌ 无 | 指标只有收益/年化/波动/回撤/Sharpe/换手 |
| **参数敏感性** | ✅ 单参数扫描 | `_sensitivity_point`（max_tilt / rebalance_days / cost_bps） |
| **影子运行** | ❌ 无 | 无新旧模型并行记录 |

### 8.3 样本长度问题（重要）

当前唯一一次成功回测：`data_range = 2025-09-24 ~ 2026-10-27`，共 **260 天**。

在此样本上跑出的五策略结果（趋势 +1.48%、轮动 +12.45%）**不具备统计意义**——1 年样本、单一市场环境、无滚动验证。`backtest_service` 的 `warnings` 已写明"样本有限，不能据此判断策略优劣"。

**结论**：V2 若要验证"早期拐点"模型，必须先解决**样本长度与滚动验证**，否则新旧模型对比无意义。

---

## 九、四分类清单（第 8 项）

### 9.1 可以直接复用（不动）

| 功能 | 理由 |
| --- | --- |
| 数据采集层（11 源、回退链、熔断、幂等） | 稳定运行，来源可追溯 |
| 数据新鲜度与手动刷新（`refresh_service`） | 四层可见、有并发锁 |
| 新闻清洗去重聚类（`news_pipeline` / `event_engine`） | 137 事件、152 证据，可用 |
| 事件三时间字段与证据链 | 字段完备 |
| ETF 全市场准入（`etf_universe_rules.yaml`） | 1,680→235，规则可调 |
| 质量分（`quality_service`） | 定位正确（产品门槛），与机会分已分离 |
| 主题族去重 + 类别配额 | 解决过"债券霸屏"问题，实测有效 |
| 研究卡片 20 项结构（`radar_card`） | 含相反证据、失效条件，表达完整 |
| 组合约束投影（`bounded_simplex_projection`） | 数学正确，有测试 |
| 交易日志与归因（`journal_service`） | 样本保护、decision 分组 |
| 回测防前视骨架 + 五策略引擎 | 通用 `_simulate` 可扩展 |
| 认证、备份、告警、systemd/nginx 部署 | 已上线验证 |

### 9.2 需要改造（保留接口，扩展内部）

| 功能 | 改造点 | 风险 |
| --- | --- | --- |
| `regime_service` | 增加宏观维度（PMI/社融/利率），广度改为全市场个股 | 中：会改变现有 state 判定，需保留旧分项 |
| `scoring_service` | 补 `valuation`/`fundamental` 实现，或**显式标注"未接入"并在 UI 隐藏该项** | 低-中 |
| `opportunity_service` | **不要改** `state` 语义；新增 `cycle_stage` 与两个新分数 | 低（增量字段） |
| `opportunity_radar_service` | 卡片增加行业、阶段、早期分、数据覆盖率 | 低 |
| `event_etf_service` | 映射增加"行业"中间层 | 低 |
| `allocation_service` | 相关性从类别级下沉到持仓级；压力测试改为可配置 | 中 |
| `backtest_service` | 增加滚动验证、分环境统计、回撤恢复时间 | 中-高 |
| `etf_theme_groups.yaml` | 保留，作为"主题标签"，**不再承担行业职责** | 低 |

### 9.3 需要新增

| 新增项 | 优先级 | 说明 |
| --- | --- | --- |
| `industry_master` 表 | P0 | 8–12 个试点行业 |
| `industry_instrument_map` 表 | P0 | 行业↔指数/ETF，多对多，带生效期 |
| 行业指数日线（数据） | P0 | 补 8–12 条行业基准序列 |
| 行业 RS / 排名变化计算 | P0 | 相对沪深300 与中证全指 |
| 技术特征扩展（钝化/低点/区间/量能） | P1 | 见第五节缺口 |
| `cycle_stage` 八阶段状态机 | P1 | 独立于现有 `state` |
| `early_turn_score` / `trend_confirmation_score` | P1 | 两条独立评分 |
| `industry_observations` 表 | P2 | 行业基本面指标（需人工/CSV 导入先跑通） |
| `industry_signal_snapshots` 表 | P2 | 因子与阶段快照 |
| `/industries`、`/industries/:code` 页面 | P1/P2 | 先做最小版 |
| 事件→行业映射与验证状态 | P2 | 依赖行业目录 |

### 9.4 暂时不应开发（明确排除）

| 项 | 理由 |
| --- | --- |
| ETF 成分穿透与行业重叠 | **无成分股数据源**（未核验是否可得），且工作量大、收益不确定；等数据源确认后再做 |
| 多因子机器学习/黑盒权重 | 路线图明确要求"数值评分由确定性程序计算"，样本量也不足以训练 |
| LLM 参与打分 | 同上，且当前 `LLM_ENABLED=0` |
| 全行业覆盖（30+ 行业） | 路线图明确"先 8–12 个试点" |
| 实时行情 | 现有都是延迟/收盘数据，接入实时需重新评估数据源成本 |
| 衍生品、REITs、银行理财模块 | 现有配置中 `DERIVATIVE`/`REIT` 的 `class_limits` 上限为 0，本就未启用 |
| 移动端原生 App / 小程序 | 小程序金融类目对个人主体不开放（已确认），H5 已满足 |

---

## 十、修改清单（第 9 项）

### 10.1 数据库

**新增表（4 张）**

```sql
-- 1. 行业目录
industry_master(
  id, industry_code UNIQUE, name, parent_code,
  analysis_module, benchmark_index_symbol,
  level INT, enabled BOOL, notes,
  created_at, updated_at
)

-- 2. 行业↔指数/ETF 映射（多对多 + 生效期）
industry_instrument_map(
  id, industry_code, instrument_symbol, instrument_type,  -- index / etf / stock
  weight REAL,                    -- 该标的对行业的代表权重（可空）
  effective_from DATE, effective_to DATE,
  source_code,                    -- 映射来源（人工/规则）
  confidence REAL, notes,
  UNIQUE(industry_code, instrument_symbol, effective_from)
)

-- 3. 行业指标观测
industry_observations(
  id, industry_code, metric_code,
  value REAL, unit,
  observation_date DATE,          -- 指标对应期间
  published_at DATETIME,          -- 外部发布时间（回测可见性依据）
  collected_at DATETIME,          -- 系统采集时间
  frequency VARCHAR(8),           -- D/W/M/Q/IRREGULAR
  source_code, source_url,
  quality REAL, confidence REAL,
  is_estimated BOOL, revision_no INT,
  UNIQUE(industry_code, metric_code, observation_date, revision_no)
)

-- 4. 行业信号快照
industry_signal_snapshots(
  id, as_of DATE, industry_code,
  cycle_stage VARCHAR(20),
  early_turn_score REAL, trend_confirmation_score REAL,
  industry_opportunity_score REAL, risk_score REAL, confidence_score REAL,
  factors JSON,                   -- 原始值/标准化值/贡献度
  data_coverage REAL,
  model_version VARCHAR(20),
  UNIQUE(as_of, industry_code, model_version)
)
```

**不新增**：`score_snapshots`（`asset_signals` 已具备 version 机制）。

**索引**：`industry_observations(industry_code, metric_code, observation_date)`、`industry_signal_snapshots(as_of)`。

### 10.2 后端 API（新增，不改现有签名）

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/industries` | GET | 行业列表 + 最新阶段 + RS 排名 |
| `/api/industries/{code}` | GET | 行业详情：基准序列、RS、阶段、指标、ETF 映射 |
| `/api/industries/{code}/history` | GET | 阶段变化历史（可解释触发因子） |
| `/api/industries/{code}/metrics` | GET | 行业指标观测（含观察日/发布日/来源） |
| `/api/industries/{code}/metrics/import` | POST | CSV 导入（人工录入通道） |
| `/api/industries/ranking` | GET | 横截面排名与排名变化 |
| `/api/model/versions` | GET | 模型版本与因子配置（影子运行用） |

**改造（仅增字段，不破坏）**：

- `GET /api/opportunity-radar/daily`：卡片增加 `industry_code`、`cycle_stage`、`early_turn_score`、`data_coverage`（旧字段全部保留）
- `GET /api/events/{id}/etfs`：返回增加 `industry` 层级

### 10.3 前端

**新增 2 页**（复用现有设计系统与组件）：

| 路由 | 内容 |
| --- | --- |
| `/industries` | 行业雷达：阶段分布、RS 排名与变化、覆盖率、数据新鲜度 |
| `/industries/:code` | 行业详情：K 线 + 相对强度曲线 + 阶段带 + 指标卡片（标注观察日/发布日/缺失）+ ETF 映射 + 新闻证据 |

**改造 2 页**：

- `/radar`：增加"早期拐点 / 趋势确认 / 过热"三个筛选视图；卡片显示阶段与数据覆盖率
- `/opportunities`：展示分项评分与覆盖率，**对未接入的 valuation/fundamental 显式标注"未接入"，不留空白**

**不改**：`/`、`/events`、`/assets`、`/universe`、`/macro`、`/allocation`、`/backtest`、`/journal`、`/sources`、`/settings`（本阶段）。

### 10.4 测试

| 类型 | 新增用例 |
| --- | --- |
| 行业目录与映射 | 多对多映射生效期过滤；同 ETF 归属多行业；失效映射不参与计算 |
| 行业指标 | 观察日/发布日/采集日三者不可混用；回测只取 `published_at ≤ 决策日`；缺失不补零 |
| 技术特征 | 低点抬高判定；下跌钝化（新低频率下降）；区间识别；量能比；**边界：单调下跌不得判为底部** |
| 阶段机 | 迟滞生效（连续 N 日确认才切换）；非法转移被拒；`cycle_stage` 与 `state` 互不影响 |
| RS | 相对沪深300 比值与斜率；横截面排名变化；样本 <5 不排名 |
| 兼容性 | 旧接口字段全部保留；旧页面在新数据下不报错 |
| 回测 | 滚动验证可跑；分环境统计；可见性控制（未来数据不泄漏） |

**目标**：现有 103 个用例全部通过 + 新增 ≥30 个。

---

## 十一、阶段 1 最小可行方案与回滚（第 10 项）

### 11.1 阶段 1 范围（严格限定）

**做**：
1. 建 8 个试点行业的 `industry_master`（半导体、通信设备、化工、锂电、电力设备、有色、创新药、工业自动化）
2. 建 `industry_instrument_map`，人工填入行业↔ETF（可先不用指数）
3. 为每个行业补 **1 条基准指数日线**（新增 `instrument_master` 条目 + 采集）
4. 计算行业 RS（相对沪深300 的 20/60/120 日比值 + 60 日斜率 + 横截面排名变化）
5. 输出 `industry_signal_snapshots`（只存 RS 与覆盖率，不含阶段）
6. 前端最小版 `/industries` 列表页

**不做**（留给阶段 2+）：
- `cycle_stage` 八阶段
- `early_turn_score` / `trend_confirmation_score`
- 行业基本面指标（`industry_observations`）
- 事件→行业映射
- 组合穿透

### 11.2 实施步骤（建议顺序）

| 步骤 | 产出 | 预估 |
| ---: | --- | --- |
| 1 | 备份数据库 + 导出雷达与回测基线 | 10 分钟 |
| 2 | `industry_master` + `industry_instrument_map` 表与模型 | 半天 |
| 3 | 8 个行业 × 各 1 条指数，采集日线（复用现有 provider） | 半天 |
| 4 | RS 计算模块 + 单元测试 | 1 天 |
| 5 | `industry_signal_snapshots` 写入 + 每日任务 | 半天 |
| 6 | `/api/industries` 列表与详情接口 | 半天 |
| 7 | `/industries` 最小页面 | 1 天 |
| 8 | 全量测试 + 线上灰度（新路由不加入主导航，先直接访问 URL） | 半天 |

### 11.3 回滚计划

**设计原则**：本阶段全部是"新增"，不改现有表、不改现有接口签名、不改现有页面。因此回滚 = 删除新增物。

```bash
# 1. 停服务
systemctl stop investweb

# 2. 恢复数据库（回到阶段1开始前的备份）
cp /opt/investweb/data/backups/invest-<开工前时间戳>.db /opt/investweb/data/invest.db

# 3. 回滚代码（若用 git）
cd /opt/investweb && git checkout <phase1-start-tag>

# 4. 或手工移除新增文件
rm -f backend/app/services/industry_*.py
rm -f backend/app/api/industries.py
rm -f backend/app/models/industry_entities.py
rm -rf frontend/src/pages/Industries*.vue

# 5. 前端回到上一版 dist
rm -rf frontend/dist && mv frontend/dist.bak frontend/dist

# 6. 启动
systemctl start investweb
```

**风险点**：若步骤 2（新增表）误改了 `entities.py` 中已有表定义，`create_all` 不会删列但可能影响 ORM 映射 → **新增表必须放在独立文件**，由 `init_db` 显式导入，避免触碰现有 28 张表。

### 11.4 阶段 1 验收标准（可测）

| 项 | 标准 |
| --- | --- |
| 数据 | 8 个行业各有 ≥250 根基准日线，来源可追溯 |
| 映射 | 每个行业至少 1 只 ETF，映射带 `effective_from` |
| RS | 每个行业可算出 20/60/120 日相对强度与 60 日斜率 |
| 排名 | 8 个行业内可排横截面名次并给出与上一期的变化 |
| 缺失处理 | 任一行业数据不足时，接口返回 `data_coverage` 并在页面标注，**不显示假分数** |
| 兼容 | 现有 103 个测试全绿；旧接口字段无变化 |
| 页面 | `/industries` 可用，手机端无横向溢出 |

---

## 十二、风险清单

| # | 风险 | 等级 | 应对 |
| ---: | --- | --- | --- |
| 1 | **行业指数数据源不可得**（东财是否提供中证行业指数历史，未验证） | 高 | 阶段 1 第 3 步先做可行性验证；不可得则退化用行业 ETF 并标注跟踪误差 |
| 2 | 样本长度不足，RS 与阶段模型无法验证 | 高 | 先补 3 年行业指数历史；不足则只做"观察级"输出，不上仓位 |
| 3 | 现有 `state` 与新增 `cycle_stage` 语义混淆 | 中 | 前端分区展示、API 字段名严格区分、文档写明 |
| 4 | 宏观/行业数据修订导致回测泄漏 | 高 | `published_at` 参与回测过滤；`revision_no` 保留版本；测试强制校验 |
| 5 | 行业指标靠人工录入，数据质量无保证 | 中 | 每个观测强制填来源+日期；覆盖率进置信度；缺失不补零 |
| 6 | 新增表误改现有 28 表结构 | 中 | 新表放独立模块；`init_db` 显式导入；回滚脚本先删新表 |
| 7 | 线上是公开访问（无认证），新增接口可能被滥用 | 中 | 新接口默认只读；写接口（CSV 导入）加来源限制 |
| 8 | 前端新增 2 页拖慢首屏 | 低 | 路由懒加载（现有已用 `() => import`） |

---

## 十三、尚未核验事项

以下项目本次**没有验证**，实施前必须单独确认，不得假设可用：

| # | 事项 | 为什么重要 | 建议验证方式 |
| ---: | --- | --- | --- |
| 1 | 东财/腾讯是否提供**中证行业指数**历史（如 931865 中证半导体） | 决定 RS 用指数还是 ETF | 直接调 provider 取 1 条试 |
| 2 | 是否可获取 **ETF 成分股与权重**（免费源） | 决定穿透分析能否做 | 查东财/天天基金接口 |
| 3 | 行业基本面指标（库存、开工率、产能）是否有稳定 API | 决定阶段 3 能否自动化 | 逐家核查；否则只能人工/CSV |
| 4 | 生产服务器 `/opt/investweb` 的 git 状态 | 影响回滚方式（无 git 则只能文件级回滚） | `ssh` 检查 `.git` 是否存在 |
| 5 | 8 个试点行业各自"最合适的基准指数"具体代码 | 影响目录准确性 | 与路线图 4.3 指标模板逐业核对 |
| 6 | 现有 235 只候选 ETF 的历史存续状态（是否有已清盘但仍留在池中的） | 影响回测幸存者偏差评估 | 抽查 20 只，比对规模与最后交易日 |

> 已在本次审计中**核实消除**的项：
> - `asset_signals` 分项字段名 → 确认为 `fundamental/valuation/trend/macro/liquidity/catalyst/risk + components(JSON) + version`
> - `events.status` 语义 → 确认为"分析引擎来源"（全为 `auto`），非验证状态，需新增字段
> - 是否存在 revision/vintage 字段 → 全库扫描结论：**不存在**。仅有 `macro_observations.period/period_date` 与 `*.published_at`，无 `revision_no`

---

## 附录 A：本次审计执行的关键查询

```sql
-- 行情覆盖
SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(trade_date), MAX(trade_date)
FROM market_observations;
-- → 94662 | 284 | 2025-09-23 | 2026-11-21

-- 日线深度分布
SELECT cnt, COUNT(*) FROM (
  SELECT symbol, COUNT(*) cnt FROM market_observations
  WHERE close IS NOT NULL GROUP BY symbol
) GROUP BY cnt ORDER BY cnt DESC;
-- → 785根:26, 784根:14, 260根:226 …

-- 事件行业字段
SELECT COUNT(*) FROM events
WHERE industries IS NOT NULL AND industries NOT IN ('[]','');
-- → 36 / 137

-- 穿透相关表
SELECT name FROM sqlite_master WHERE type='table'
AND (name LIKE '%constitu%' OR name LIKE '%industry%' OR name LIKE '%map%');
-- → 空
```

## 附录 B：代码中的关键常量位置

| 常量 | 值 | 位置 |
| --- | --- | --- |
| 状态机参数 | min_bars 60 / chase 15%·35% / max_vol 55% / exit −25% | `opportunity_service.py:38-44` |
| 状态转移表 | 7 状态 | `opportunity_service.py:26-34` |
| 机会分权重 | trend 0.6 / rs 0.4 | `opportunity_service.py:273` |
| 趋势强度构成 | 35 / 35 / 30 | `opportunity_service.py:255-258` |
| 质量分权重 | 40/30/20/10 | `quality_service.py:18` |
| 雷达四道过滤 | 40 / 40 / 2 / 3 | `opportunity_radar_service.py` |
| 评分配置 | 各模块 6 特征权重 | `signal_rules.yaml:scoring.modules` |
| 风险状态权重 | 0.30/0.20/0.20/0.15/0.10/0.05 | `signal_rules.yaml:regime.components` |
| 回测成本 | 8 bps | `risk_limits.yaml:portfolio` |

---

**审计完成。本阶段未修改任何代码、数据库或生产配置。等待下一阶段开发指令。**

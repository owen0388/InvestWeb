# 数据库设计

开发环境默认 SQLite（`data/invest.db`，WAL 模式），部署可切换 PostgreSQL（改 `DATABASE_URL` 即可，模型层不含 SQLite 专有语法）。

## 1. 表清单（25 张）

### 资产目录

| 表 | 说明 | 关键字段 / 约束 |
| --- | --- | --- |
| `asset_classes` | 一级/二级资产类别 | `code` 唯一、`level`、`parent_code`、`analysis_module`、默认权重上下限 |
| `instrument_master` | 品种主表 | `symbol` 唯一、类别、市场、币种、`data_status`、数据源、`last_data_at`、`enabled`、`favorite` |
| `instrument_aliases` | 代码/名称/关键词映射 | 唯一 `(instrument_id, alias, source)` |

### 数据源与采集

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `data_sources` | 数据源登记与健康状态 | `code` 唯一、认证方式、频率、`status`、`last_success_at`、`last_error` |
| `data_source_runs` | 单次采集记录 | 状态、耗时、成功/失败/跳过数、错误、触发方式 |
| `market_observations` | 行情快照与日线 | 唯一 `(symbol, trade_date, source_code)`、`data_type`（close/realtime/delayed/manual）、`observed_at` |
| `macro_observations` | 宏观观测 | 唯一 `(indicator_code, period, source_code)`、`period_date`、`release_at`、`fetched_at`、`revised` |

### 新闻与事件

| 表 | 说明 | 关键字段 |
| --- | --- | --- |
| `raw_documents` | 原始新闻元数据 | 唯一 `(source_code, external_id)`、`title_hash`、`content_hash`、`cluster_key`、`event_id` |
| `events` | 事件主表 | `event_key` 唯一、类别、重要性、新颖度、方向、置信度、`analysis_version`、`analysis_engine` |
| `event_evidence` | 证据与原文链接 | 唯一 `(event_id, document_id)`、立场、来源、可靠度 |
| `event_impacts` | 事件→资产影响路径 | 唯一 `(event_id, instrument_symbol)`、方向、机制、期限、强度 |
| `event_analysis_versions` | 结论版本快照 | 唯一 `(event_id, version)`、引擎、变更原因 |
| `asset_signals` | 资产评分历史 | 唯一 `(instrument_id, as_of, version)`、各分项、总分、置信度、动作、缺失项 |

### 组合与配置

| 表 | 说明 |
| --- | --- |
| `portfolios` | 组合（示例/个人），`is_example` 明确区分 |
| `portfolio_holdings` | 持仓，唯一 `(portfolio_id, instrument_id)` |
| `allocation_targets` | 目标权重历史，按 `version` 保留每次计算 |
| `risk_constraints` | 类别上下限、单品种/行业上限、换手、最小置信度 |
| `investor_profiles` | 风险偏好、期限、回撤、流动性、准入、禁止项、`confirmed` |
| `strategy_configs` | 策略参数版本 |
| `backtest_runs` | 回测参数、指标、净值曲线、调仓、假设、告警 |

### 运维

| 表 | 说明 |
| --- | --- |
| `model_usage` | 模型、任务、prompt/completion tokens、费用、缓存、失败原因 |
| `notifications` | 通知去重键、渠道、级别、状态 |
| `system_settings` | 键值配置（JSON），含最新风险状态与历史 |
| `audit_logs` | 关键写操作审计（持仓、权重、设置、采集触发、回测） |
| `job_runs` | 调度任务执行记录 |

## 2. 设计要点

1. **幂等**：所有采集表都有业务唯一键，重复执行只更新不重复插入。
2. **可追溯**：行情/宏观/新闻均保留来源代码、原始链接、采集时间；事件保留证据与版本。
3. **不覆盖历史**：`asset_signals` 按日 + 版本保存；`event_analysis_versions` 保存每次结论；`allocation_targets` 每次计算递增版本。
4. **区分“期间”与“时间”**：`macro_observations.period` 是数据所属期间，`release_at` 是发布时间，`fetched_at` 是采集时间。
5. **人工维护可见**：`data_status=manual` 与 `source_code=manual_entry` 在 API 与前端均显式标记。

## 3. 索引

- 行情：`(symbol, trade_date)`、`trade_date`。
- 宏观：`(indicator_code, period)`。
- 新闻：`content_hash`、`title_hash`、`published_at`、`cluster_key`、`event_id`。
- 事件：`published_at`、`(category, importance)`。
- 评分：`(instrument_id, as_of)`、`as_of`。
- 通知：`dedupe_key` 唯一。

## 4. 初始化与迁移

首期使用 SQLAlchemy `create_all` 在启动时建表（幂等），配合 YAML 目录初始化。原因是个人单机部署、表结构仍在快速演进，引入迁移链的成本高于收益。

**升级到需保留历史数据的正式部署时**，建议：

```bash
pip install alembic
alembic init migrations         # 在 backend/ 下执行
# 修改 migrations/env.py 指向 app.models.entities 的 Base.metadata
alembic revision --autogenerate -m "baseline"
alembic upgrade head
```

在此之前请先备份 `data/invest.db`（SQLite 可直接复制文件，需在服务停止时操作）。

## 5. 备份与清理

```bash
# SQLite 备份（服务停止时）
copy data\invest.db data\backup\invest-20261027.db

# PostgreSQL
pg_dump -Fc invest > invest-20261027.dump
```

数据保留建议（尚未实现自动清理）：行情 900 天、新闻 180 天、采集/任务记录 60 天。清理时按 `trade_date`、`published_at`、`started_at` 删除，并先备份。

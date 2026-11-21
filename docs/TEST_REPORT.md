# 测试报告

> 测试时间：2026-10-27 23:50（Asia/Shanghai）
> 环境：Windows 11 / Python 3.13.14 / Node 22.22.2 / SQLite（WAL）/ 单进程 uvicorn
> 数据：真实公开接口采集，未使用任何预置行情或伪造信号

## 1. 自动化测试

```bash
cd backend
..\.venv\Scripts\python.exe -m pytest -q
# 31 passed, 1 warning in 2.36s
```

| 文件 | 覆盖内容 | 结果 |
| --- | --- | --- |
| `tests/test_indicators.py` | 收益、均线、年化波动、最大回撤、数据质量、陈旧度、约束投影（含不可行回退） | 8 passed |
| `tests/test_events.py` | 标题规范化/哈希、重复入库幂等、分类、聚类、事件流水线幂等 | 6 passed |
| `tests/test_scoring_allocation.py` | 无行情时拒绝评分、配置总和/上下限、约束读取、换手校验 | 5 passed |
| `tests/test_api.py` | 健康检查、目录、分页与过滤、单品详情 404、参数校验、总览、新闻事件、数据源、组合配置、设置不泄露密钥、回测失败提示、未知 API 404 | 12 passed |

关键断言示例：

- `bounded_simplex_projection` 输出满足 `Σw = 1`、`l ≤ w ≤ u`；不可行时残差标记为 1.0。
- 无价格序列时 `score_instrument` 返回 `total_score=None`、`action="暂不判断"`，不会用流动性分层拼出一个分数。
- 同一批新闻第二次入库 `created=0, duplicate=1`；事件流水线第二次运行 `events_created=0`。
- `/api/settings` 响应不包含任何密钥明文。

## 2. 前端构建与类型检查

```bash
cd frontend
npx vue-tsc --noEmit     # 0 error
npm run build            # ✓ built in 6.99s
```

产物：`frontend/dist`，主包 108.95 kB（gzip 42.62 kB），ECharts 分包 1,035.89 kB（gzip 343.91 kB），10 个页面按路由懒加载。

## 3. 真实数据采集验证

| 数据源 | 操作 | 结果 | 耗时 |
| --- | --- | --- | --- |
| 世界银行 | 手动触发 | 90 条年度观测，状态 healthy | 28.2s（15 请求并发 4） |
| 日线历史 | 手动触发 | 49/54 品种成功，12,740 条日线；东方财富被限流后回退到 `sina_kline`，5 个港股/美股品种当日接口失败但保留历史数据 | 87.7s |
| 新浪行情 | 手动触发 | 54/54 品种快照成功 | 0.7s |
| 东方财富新闻 | 手动触发 | 80 条，新增 80，重复 0；76 个簇，39 个事件，37 条低于重要性阈值 | 0.9s |
| 新闻二次采集 | 手动触发 | 80 条，新增 0，重复 80；0 个新事件（幂等验证通过） | 0.9s |

数据时间标注检查：

- A 股收盘后采集的行情 `data_type=close`，未标为 `realtime`。
- 世界银行 `release_at` 为空（官方未提供逐条发布时间），`period=2025` 与 `fetched_at=2026-10-27` 分离。
- 新闻保留 `published_at`（来源）与 `fetched_at`（采集）两个字段。
- 日线来源写为 `tencent_kline` / `sina_kline` / `eastmoney_kline`，而不是统一伪装成东方财富。

## 4. 接口冒烟测试

| 接口 | 状态 | 说明 |
| --- | --- | --- |
| `GET /`、`/opportunities`、`/allocation`、`/assets/sh000300` | 200 | SPA 路由正常；`/api/*` 未命中返回 404 |
| `GET /api/health` | 200 | `status=ok`，25 张表计数非零 |
| `GET /api/asset-classes` | 200 | 12 个一级类别、36 个节点 |
| `GET /api/instruments?page=1&page_size=5` | 200 | 分页 5 条，总数 64 |
| `GET /api/market/overview` | 200 | 9 个主要品种含最新价、涨跌幅、数据类型 |
| `GET /api/market/regime` | 200 | 风险偏好下降，29.5，覆盖 100% |
| `GET /api/macro/overview` | 200 | 15 个指标卡（已跳过最新期间为空值的指标） |
| `GET /api/events?days=7` | 200 | 40 条事件，重要性 40–81 |
| `GET /api/events/{id}` | 200 | 证据、影响资产、版本记录完整 |
| `GET /api/opportunities` | 200 | 54 个可研究标的，22 个非“暂不判断” |
| `GET /api/portfolios/1` | 200 | 8 条示例持仓，类别权重正确归并 |
| `POST /api/portfolios/1/allocation/recompute` | 200 | 目标权重合计 1.000000 |
| `GET /api/portfolios/1/allocation/history` | 200 | 7 个版本 |
| `POST /api/backtests` | 200 | 真实日线回测成功 |
| `GET /api/sources` | 200 | 11 个数据源，未配置项明确标注 |
| `GET /api/jobs` | 200 | 11 个任务及下次执行时间 |
| `GET /api/usage` | 200 | 预算与调用统计（未配置模型时 0 次） |
| `POST /api/notifications/digest` | 200 | 生成每日简报 |
| `PUT /api/settings` | 200 | 阈值修改生效 |
| `PUT /api/portfolios/1/base-weights`（合计 70%） | 400 | 明确拒绝：“基础权重合计必须为 100%，当前 70.00%” |
| `POST /api/ai/*` | — | 未配置密钥时返回未配置，不产生伪造结果 |

## 5. 配置引擎数值验证

```
turnover 0.0426  (上限 0.12)
volatility current 10.77% → target 10.22%
sum(target) = 1.000000
BOND       base 20.0%  cur 20.6%  tgt 20.60%  score 53.2
CASH       base 10.0%  cur 10.3%  tgt 10.89%  score 58.9
CN_EQ      base 22.0%  cur 22.7%  tgt 21.48%  score 36.7
COMMODITY  base  3.0%  cur  3.1%  tgt  3.64%  score 54.8
FUND       base 20.0%  cur 20.6%  tgt 19.24%  score 33.1
GOLD       base  8.0%  cur  8.3%  tgt  7.31%  score 33.8
OVERSEAS   base 12.0%  cur 12.4%  tgt 11.70%  score 40.2
REIT       base  5.0%  cur  5.2%  tgt  5.13%  score 45.1
```

- 每类目标均在 `min/max` 内；`DERIVATIVE` 目标权重为 0。
- 风险状态为“风险偏好下降”时，权益与黄金分数偏低，目标权重低于基础权重，与评分倾斜方向一致。
- 压力测试：权益快速下跌 20% → 组合 -7.23%；利率上行 100bp → -2.23%。

## 6. 回测验证

```
区间 2025-09-24 ~ 2026-10-27（260 个交易日）
策略累计 -1.53%   年化 -1.41%   波动 11.42%   最大回撤 -10.00%   夏普 -0.25
沪深300  -11.31%  最大回撤 -20.23%
超额收益 +9.78%   换手 0.48   调仓 12 次   成本 0.04%
样本内 +7.76%     样本外 -8.62%
```

- 调仓日以 `series[:i+1]` 计算评分，不使用未来数据。
- 检测到单日收益绝对值 > 40% 时直接失败，防止异常数据输出“漂亮”结果。
- 数据不足 180 天时返回 422 并说明原因。

## 7. 本轮发现并修复的问题

| 问题 | 现象 | 修复 |
| --- | --- | --- |
| 配置接口 500 | `NaN` 不符合 JSON 规范 | 新增 `clean_json`，递归清洗 NaN/Inf |
| 股票类别拿不到评分 | 用 `split("_")[0]` 归并，`CN_EQ_INDEX → CN` | 改为按类别层级映射 |
| 回测收益爆炸 | 对“当日各资产价格”做 `pct_change`（横向比较） | 预计算资产收益矩阵，按日取行 |
| `/assets/sh000300` 404 | 静态目录 `/assets` 抢占了前端路由 | 去掉 StaticFiles 挂载，改由 SPA 优先返回真实文件 |
| 示例持仓只有 1 条 | 循环内 `break` | 改为按品种判重，补齐 8 个类别 |
| 日线全部失败 | 东方财富短时高频访问后断连 | 增加腾讯/新浪回退与来源熔断 |
| 涨跌幅显示 `—` | 日线来源不含涨跌幅 | 用前一日收盘实时计算 |
| 手动采集无通知 | 通知只在调度任务中生成 | 增加 `sync_recent_notifications` 并幂等去重 |
| 未知 `/api` 返回 index.html | SPA catch-all 吞掉 404 | `/api/*` 显式返回 404 |

## 8. 未覆盖 / 待补测试

- AI 调用链路（缓存、预算、JSON 校验失败降级）：当前无密钥，只验证了“未配置”分支；`parse_json` 与预算逻辑为纯函数可后续补单测。
- 邮件通知、Webhook 实际投递。
- 前端组件级测试（Vitest）与端到端测试（Playwright）。
- PostgreSQL 兼容性（模型未使用 SQLite 专有语法，但未在 PG 上实跑）。
- 并发调度与多进程部署场景。

## 9. 截图

`docs/screenshots/` 下为实际运行截图：`overview.png`、`opportunities.png`、`events.png`、`assets.png`、`asset-detail.png`、`allocation.png`、`macro.png`、`backtest.png`、`sources.png`、`settings.png`。

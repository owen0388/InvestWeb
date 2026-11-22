# INVEST P0 评分口径收敛报告

> **执行日期**：2026-11-22
> **任务范围**：梳理并收敛 `/radar` 与 `/opportunities` 两套机会评分口径
> **执行环境**：开发环境（**未部署生产**）
> **最终结论**：**CONDITIONAL PASS** —— 核心口径已收敛，可进入 P1；非 ETF 标的仍走旧口径，属明确遗留

---

## 〇、结论摘要

| 项 | 状态 |
| --- | --- |
| 核心机会分是否统一 | ✅ ETF 全部走雷达口径，两接口同源同值 |
| 动作词是否统一 | ✅ 统一为 4 档研究优先级，交易性措辞已移除 |
| 是否产生第三套评分 | ❌ 没有，只是把两套收敛成「1 套核心 + 1 套遗留」 |
| ETF 质量分是否被改动 | ❌ 未改动（有测试守护） |
| 测试 | **111 passed / 0 failed / 13 skipped** |
| 是否可进入 P1 | ✅ 可以，但需知悉第 17 节的遗留项 |

**最重要的一件事**：改造前，同一只光伏 ETF 在 `/opportunities` 显示「降低风险 20.1 分」，在 `/radar` 显示「积极研究 60+ 分」——**同一只标的、同一天、结论完全相反**。现在两处必然一致。

---

## 一、`/radar` 原评分公式

**文件**：`backend/app/services/opportunity_service.py`

```
趋势强度 = 35×(MA20>MA60) + 35×min(dev20/20%, 1) + 30×min(ret60/30%, 1)   [上限 100]
相对强弱 = 同类分组内按 (ret20×0.5 + ret60×0.5) 排序 → (排名/(n-1))×100   [组内 <5 不分位]
机会分   = 趋势强度 × 0.6 + 相对强弱 × 0.4
```

| 项 | 值 |
| --- | --- |
| 分数范围 | 0–100 |
| 数据缺失 | 序列 <60 根 → 直接 `SCAN`，**不给分**（不补默认值） |
| 风险扣分 | 无独立扣分，但 `vol20 > 55%` 降级、`drawdown ≤ −25%` 退出 |
| 硬性筛选 | 质量分 ≥40、机会分 ≥40、同主题 ≤2、同类别 ≤3、排除货币类 |
| 归一化 | 仅 RS 做了组内分位归一 |
| 依赖 | 仅价格（MA/动量/波动/回撤）+ ETF 质量分（作门槛） |

## 二、`/opportunities` 原评分公式

**文件**：`backend/app/services/scoring_service.py`

```
total = Σ(权重 × 特征分) / Σ(存在的权重) − 风险惩罚
风险惩罚 = 0.5 × max(0, 55 − risk_value) × 0.35
```

权重（`equity_cn`）：`trend 0.30 / valuation 0.15 / fundamental 0.10 / macro 0.15 / catalyst 0.20 / liquidity 0.10`

实测有效权重只有 0.75——**valuation 仅 cash 模块实现、fundamental 代码根本不存在**，这两项 25% 长期空转。

| 项 | 值 |
| --- | --- |
| 分数范围 | 0–100（`clamp`） |
| 数据缺失 | 按存在特征重新归一 + 降 `data_quality`/`confidence`；<60 根直接 `total_score=None` |
| 风险扣分 | ✅ 有（波动+回撤合成惩罚项） |
| 覆盖 | 61 个精管标的（股票/指数/ETF/期货） |

## 三、两套评分差异表

| 维度 | `/radar` | `/opportunities` |
| --- | --- | --- |
| 公式 | 趋势 60% + RS 40% | 6 特征加权 − 风险惩罚 |
| 因子数 | 4 | 6（实际 4） |
| 风险处理 | 状态降级 | 分数扣减 |
| 覆盖 | ETF 候选池（227–249 只） | 61 个精管标的 |
| 数据源 | 行情（InvestData） | 行情 + 世界银行宏观 + 事件 |
| 计算时机 | 请求时实时 | 定时任务算好存表 |
| 持久化 | `radar_snapshots`（仅 code/state/score） | `asset_signals`（完整分项） |
| 归一化 | RS 组内分位 | 无 |

### 实测冲突案例

| 标的 | `/opportunities` | `/radar` | 结论碰撞 |
| --- | --- | --- | --- |
| sh515790 光伏ETF | 20.1「降低风险」 | 趋势达标 60+「积极研究」 | 完全相反 |
| sh510880 红利ETF | 48.2「维持配置」 | 47.6「观察」 | 分数接近但措辞不同 |

根因：`asset_signals` 被年度宏观数据与空转维度拖累，`radar` 只看趋势——**一个在算"宏观景气"，一个在算"当下趋势"，却共用一个"机会"的名义**。

## 四、两套数据来源

| 数据 | radar | opportunities |
| --- | --- | --- |
| 日线 | `market_observations`（InvestData 优先） | 同上 |
| ETF 质量分 | ✅ 作门槛 | ❌ 不用 |
| 宏观 | ❌ | ✅ 世界银行年度（滞后严重） |
| 事件 | ❌ | ✅ `event_impacts`（30 天窗口） |
| 市场状态 | ❌ | ✅ `regime` |

## 五、原动作词与触发条件

**radar**（`radar_card._action`）

| 条件 | 动作 |
| --- | --- |
| CANDIDATE 且 ≥65 | 积极研究 |
| CANDIDATE 且 <65 | 观察等待 |
| EXIT | 降低风险 |
| TRIM | 考虑降低权重 |
| WAIT/WATCH | 观察等待 |

**opportunities**（`scoring_service._action_for`）

| 分数 | 动作 |
| --- | --- |
| ≥72 | 考虑提高权重 |
| ≥62 | 积极研究 |
| ≥45 | 维持配置 |
| ≥35 | 观察等待 |
| ≥25 | 考虑降低权重 |
| <25 | 降低风险 |
| 数据不足/低置信 | 暂不判断 |

**重叠词**：积极研究、观察等待、维持配置、考虑提高权重——同名不同阈值，这是矛盾的直接来源。

## 六、统一后的核心机会分定义

```python
SCORE_VERSION = "radar_trend_v1"

核心机会分 = 趋势强度 × 0.6 + 同类相对强弱 × 0.4
```

| 项 | 定义 |
| --- | --- |
| 来源 | `/radar` 口径（`opportunity_service.opportunity_score`） |
| 计算时机 | 请求时实时计算，进程内缓存 300 秒 |
| 覆盖 | ETF 候选池全部标的 |
| 缺失处理 | 非 CANDIDATE 状态 → `score=None`，等级「暂不关注」，**不假装有机会** |
| 版本 | `radar_trend_v1`；非 ETF 走 `legacy_v1` |

**为什么选雷达口径，而不是相反**：
1. 覆盖候选池，是机会发现的主战场
2. 公式可解释，且已与质量分正确分离
3. `asset_signals` 25% 权重空转 + 宏观数据年度滞后，做"当期机会"不可靠
4. 雷达就是本次统一前的实时口径，回归风险最小

### 统一输出结构

```json
{
  "symbol": "sh510880",
  "score": 47.6,
  "score_version": "radar_trend_v1",
  "level": "观察",
  "state": "WATCH",
  "rs_score": 42.1,
  "quality_score": 78.0,
  "confidence": 1.0,
  "source": "radar",
  "as_of": "2026-11-22",
  "note": "趋势强度 60% + 同类相对强弱 40%"
}
```

### 统一动作词（研究优先级，非交易指令）

| 分数 | 等级 |
| --- | --- |
| ≥65 | 高优先级研究 |
| 50–65 | 重点关注 |
| 35–50 | 观察 |
| <35 | 暂不关注 |
| 缺失 | 数据不足 |

「买入/卖出/降低风险/考虑提高权重」等交易性措辞已从输出中移除（有测试断言守护）。

## 七、修改文件清单

### 新增（2）

| 文件 | 作用 |
| --- | --- |
| `backend/app/services/opportunity_registry.py` | 统一出口：分数、等级、版本、来源 |
| `backend/tests/test_score_convergence.py` | 8 个收敛测试 |

### 修改（5）

| 文件 | 改动 |
| --- | --- |
| `opportunity_radar_service.py` | 抽出 `_evaluate_universe()`，`build_daily` 复用它（消除重复计算） |
| `radar_card.py` | `_action` 改为调用 `level_for`，动作词统一 |
| `api/news_events.py` | `/opportunities` 接入 registry；排序改为核心分降序；筛选下拉改为研究优先级 |
| `frontend/src/utils/format.ts` | `actionClass` 支持新词，保留旧词兼容 |
| `frontend/src/pages/RadarPage.vue` | 本地 `actionClass` 同步更新 |
| `backend/tests/test_radar.py` | 断言动作词集合（**保留测试，仅更新期望值**） |

## 八、数据库变化

**无。** 本任务未新建表、未加列、未改字段。统一分实时计算，不落库（`opportunity_scores` 表留给 P1）。

## 九、API 变化

### `/api/opportunities`（向后兼容，新增字段）

| 字段 | 变化 |
| --- | --- |
| `score` | ⚠️ **语义变化**：现在是统一核心机会分（ETF 为雷达口径） |
| `action` | ⚠️ 值变化：改为研究优先级 |
| `score_version` | ➕ 新增 |
| `score_source` | ➕ 新增（radar / asset_signals / none） |
| `score_note` | ➕ 新增（口径说明） |
| `level` | ➕ 新增（与 action 同值） |
| `confidence` | ETF 用质量置信度，其余用 `asset_signals.confidence` |
| 排序 | ⚠️ 改为核心分降序（原为「偏离中性程度 × 置信度」） |
| `filters.actions` | ⚠️ 改为 4 档研究优先级 |

### `/api/opportunity-radar/daily`

| 字段 | 变化 |
| --- | --- |
| `items[].action` | ⚠️ 值变化：改为研究优先级 |
| 其余字段 | 不变 |

## 十、页面兼容情况

| 页面 | 兼容性 |
| --- | --- |
| `/radar` | ✅ 结构不变，仅动作词与颜色映射更新 |
| `/opportunities` | ✅ 结构不变，新增字段可忽略；动作词更新 |
| `/assets/:symbol` | ✅ `actionClass` 已兼容新旧词 |
| `/`（总览） | ✅ 未触及 |

前端已通过 `vue-tsc` 类型检查与 `npm run build`。

## 十一、ETF 质量分确认未改动

| 项 | 状态 |
| --- | --- |
| 质量分公式（4 维 40/30/20/10） | ❌ 未改 |
| 净值/规模/折溢价数据源（天天基金） | ❌ 未改 |
| 20 日均额口径 | ❌ 未改 |
| 候选池准入规则 | ❌ 未改 |

**测试守护**：`test_quality_score_untouched_by_registry` 断言 registry 调用前后质量分不变。

## 十二、测试结果

```
111 passed, 13 skipped, 0 failed
```

| 类别 | 数量 |
| --- | --- |
| 原有测试 | 103（全部通过，断言未删） |
| 新增收敛测试 | 8 |
| 跳过 | 13（InvestData 服务未启动，属环境依赖） |

**新增测试覆盖**：

| 测试 | 验证 |
| --- | --- |
| `test_level_mapping_is_monotonic` | 分数越高等级不得更低 |
| `test_missing_score_is_not_high_level` | 缺失数据不被赋高等级 |
| `test_levels_are_research_priorities_not_trades` | 动作词不含交易指令 |
| `test_unknown_symbol_returns_na` | 未知标的返回「数据不足」 |
| `test_etf_in_universe_uses_radar_version` | **有旧记录时仍优先雷达口径**（不沿用 20 分） |
| `test_quality_score_untouched_by_registry` | 质量分不被修改 |
| `test_radar_and_opportunities_agree` | **两接口同标的分数一致** |
| `test_opportunities_filters_use_research_levels` | 筛选下拉无交易词 |

**过程中修正的两个自身缺陷**：
1. registry 返回 `round(score, 1)`，与雷达原始值差 0.03 → 去掉二次舍入
2. 测试数据未清理 `Instrument`/`AssetSignal`，污染了 `test_signals_are_not_fabricated_without_prices` → 补 `_cleanup_all`

## 十三、Git 提交记录

```
（待提交：P0 评分口径收敛）
```

## 十四、回滚方案

```bash
cd D:/InvestWeb
git log --oneline            # 找到本次提交
git revert <commit>          # 或在测试分支上 git reset --hard <上一个提交>

# 单文件回退
git checkout HEAD~1 -- backend/app/services/opportunity_registry.py \
    backend/app/services/opportunity_radar_service.py \
    backend/app/services/radar_card.py \
    backend/app/api/news_events.py

# 前端回退
git checkout HEAD~1 -- frontend/src/utils/format.ts frontend/src/pages/RadarPage.vue
cd frontend && npm run build
```

**无数据库变更**，不需要数据回滚。**未部署生产**，线上行为不受影响。

## 十五、P1 可复用模块

### A. 可复用的数据计算能力

| 模块 | 说明 |
| --- | --- |
| `datasource/investdata.py` + `repository.py` | 全市场行情读取（含批量、复权、质量标记） |
| `market_sync_service` / `investdata_sync_service` | 标的与行情同步（幂等） |
| `market_data.load_close_series` | 已优先 InvestData，可直接复用 |
| `etf_history_service` + `tianty_fund` | ETF 净值/规模/折溢价 |
| `quality_service` | 产品质量分（保持独立门槛语义） |

### B. 可复用的基础指标

`trend_features`（MA/dev/ret/vol/drawdown/ma_order）、`relative_strength_scores`（组内分位）、`annualized_vol`、`max_drawdown`、`series_quality`、`regime_service`（6 维市场状态）、`event_engine`（事件与证据链）。

### C. 不应继承的旧评分权重

| 项 | 原因 |
| --- | --- |
| `equity_cn` 的 valuation 0.15 / fundamental 0.10 | 空转，永远不参与计算 |
| 宏观 15%（世界银行年度） | 滞后 1–2 年，不能用于当期机会 |
| 机会分 = 趋势 60% + RS 40% | 四项输入全是价格动量，本质是"追涨" |
| `asset_signals` 的动作阈值 72/62/45/35/25 | 与雷达阈值不一致，已废弃 |

### D. 应在 P1 重新设计的评分逻辑

1. 机会分多因子结构（趋势/RS/结构/量能 × 风险 × 置信）
2. 类别内标准化（z-score / rank）+ 跨类等级
3. 风险独立成乘法系数，而非状态降级或线性扣分
4. 缺失数据降置信度，而非按存在项重新归一
5. 因子贡献分解（解释"为什么 73 分"）

### E. 应在 P1 废弃的旧接口/字段

| 项 | 处置建议 |
| --- | --- |
| `asset_signals.total_score` | P1 新表上线后降为历史字段，不再对外 |
| `asset_signals.action` | 同上 |
| `/api/opportunities` 的 `action` 字段 | 保留但标注 legacy，或改名为 `legacy_action` |
| `radar_snapshots.score` | 可复用为 P1 的快照表结构参考 |

## 十六、P1 应重新研究的因子清单

| 因子 | 现状 | P1 需要做什么 |
| --- | --- | --- |
| 趋势 | dev20 + ret60（重复计权） | 改用均线斜率，消除动量重复 |
| 相对强度 | 组内分位（静态） | 增加排名变化、斜率 |
| 结构 | ❌ 无 | 低点抬高、区间收敛、突破守住 |
| 量能 | ❌ 未进评分 | 涨跌量比、突破量比 |
| 风险 | 状态降级/线性扣分 | 独立乘法系数 |
| 置信度 | 有字段但未参与机会分 | 独立输出并参与 |
| 行业/周期 | ❌ 完全无 | 依赖 P2 行业层 |

## 十七、未解决问题及风险

| # | 问题 | 影响 | 处置 |
| ---: | --- | --- | --- |
| 1 | **非 ETF 标的仍走 legacy_v1** | 股票/指数的分数与 ETF 不同口径，跨类不可比 | 已标注 `score_version`；P1 一并纳入新模型 |
| 2 | `asset_signals` 25% 权重仍空转 | 旧分不可靠 | 本任务不动（属 P2 数据问题）；已在报告中记录 |
| 3 | 统一分实时计算，无持久化 | 页面刷新可能因缓存 TTL 出现 ±0 抖动（同源计算，值不会变） | P1 建 `opportunity_scores` 表解决 |
| 4 | 缓存 300 秒 | 数据刚更新时最多 5 分钟延迟 | 已提供 `invalidate_cache()`；同步任务后可调用 |
| 5 | 动作词变更影响历史数据展示 | 旧记录仍存旧词 | 前端 `actionClass` 保留旧词映射，不会显示异常 |
| 6 | 排序改为分数降序 | 原先"偏离中性程度"排序被替换 | 属预期改进，已在报告说明 |
| 7 | 测试库无真实行情 | 一致性测试需自造数据 | 已用 `_add_etf` 造 260 根上涨序列验证 |

### 最终结论：**CONDITIONAL PASS**

**通过的理由**：
- 核心机会分已收敛到单一来源，两接口同标的必然同值（有测试守护）
- 动作词统一为研究优先级，消除了"同一标的两种结论"
- 未产生第三套评分，ETF 质量分未被污染
- 全部测试通过，可回滚

**遗留条件**：
- 非 ETF 标的仍是 `legacy_v1` 口径，与 ETF 不可直接比较
- `asset_signals` 的空转权重问题需在 P2 解决

**这两项不阻碍 P1**——P1 本就要新建评分表，届时 legacy 口径自然退场。

---

**P0 收尾完成。未部署生产，等待人工审核后再进入 P1。**
